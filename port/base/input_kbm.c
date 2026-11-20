/* The keyboard and mouse layout (player 1): which key or mouse input presses which button of the host pad, before the
 * pad is recorded (input.c read_keyboard calls input_kbm_read once per read). host.h input_kbm_*.
 *
 *   Sources: keys by name (input_key_code: SDL's names, plus UP DOWN LEFT RIGHT RETURN KP_ENTER SPACE ESCAPE BACKSPACE
 *   TAB LSHIFT RSHIFT LCTRL RCTRL LALT), and the mouse: MOUSELEFT, MOUSERIGHT, MOUSEMIDDLE (a middle click: pressed and
 *   let go without moving the mouse), WHEELUP and WHEELDOWN (one press per notch).
 *   Targets, joined by '+': the host pad's buttons (A B X Y START UP DOWN LEFT RIGHT LB RB L3 R3 BACK GUIDE RSUP RSDOWN
 *   RSLEFT RSRIGHT), LT and RT (the trigger fully pressed), LSUP LSDOWN LSLEFT LSRIGHT (the left stick).
 *   The layout: the kit's (below; game.json "arrows": "stick" drops the arrows' d-pad), or the game's whole own table
 *   (game.json "keys": PORT_KEYS), then the player's changes (Input > Keyboard and mouse, kept in the settings as
 *   kbm-<source>, which come back as --kbm-<source> TARGETS). A source with no target does nothing.
 *   Stick keys: game.json "key_ramp" N (PORT_KEY_RAMP) takes a held key N reads to reach full deflection (0: at once).
 *   The middle button held while the mouse moves turns the right stick (mouse look, kept after the button is let go;
 *   a middle click centres it). The pad then carries HOST_PAD_MOUSE_LOOK, so a recording replays it.
 *
 * A replay or a check reads none of this (input_live). --kbm-inject drives it for a test instead of the real devices
 * (input_kbm_inject.c). Mouse steer, the mouse turning the left stick while a game asks, is input_kbm_steer.c; the
 * menu pointer, the mouse picking a menu's entries while a game says one is up, input_kbm_pointer.c.
 * No SDL here (input.c gives the key codes and the keyboard's state), so tests/kbm_test.c builds it against stubs. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "host.h"
#include "game_log.h"
#include "input_kbm_priv.h"
#if __has_include("port_config.h")
#include "port_config.h" /* game.json keys, key_ramp, arrows */
#endif
#ifndef PORT_ARROWS_STICK
#define PORT_ARROWS_STICK 0
#endif
#ifndef PORT_KEY_RAMP
#define PORT_KEY_RAMP 0
#endif

#if PORT_ARROWS_STICK
#define ARROW_KEYS "UP=LSUP,DOWN=LSDOWN,LEFT=LSLEFT,RIGHT=LSRIGHT,"
#else
#define ARROW_KEYS "UP=UP+LSUP,DOWN=DOWN+LSDOWN,LEFT=LEFT+LSLEFT,RIGHT=RIGHT+LSRIGHT,"
#endif
/* The kit's layout: Z/X/A/S the face buttons, Enter Start, the arrows the d-pad and the stick, I/K/J/L the d-pad,
 * Q or Left Shift the L trigger and W or Space the R trigger, E/R LB/RB. */
static const char k_kit_keys[] = "Z=A,X=B,A=X,S=Y,RETURN=START,KP_ENTER=START," ARROW_KEYS
                                 "I=UP,K=DOWN,J=LEFT,L=RIGHT,Q=LT,W=RT,LSHIFT=LT,SPACE=RT,E=LB,R=RB";
static const char *const k_mouse[] = {"MOUSELEFT", "MOUSERIGHT", "MOUSEMIDDLE", "WHEELUP", "WHEELDOWN"};
enum { M_LEFT = -1, M_RIGHT = -2, M_MIDDLE = -3, M_WHEEL_UP = -4, M_WHEEL_DOWN = -5 };

#define MAX_SOURCES 48
#define STICK_TARGETS SCRIPT_LS
#define PULSE_ON 2  /* reads a wheel notch or a middle click holds its target */
#define PULSE_OFF 2 /* reads let go before the next notch, so the game sees each one */
#define WHEEL_QUEUE 8
#define CLICK_SLOP 6.0f   /* pixels: the middle button moved less than this is a click, not a drag */
#define LOOK_PER_PIXEL 0.4f /* of the right stick's 127: about 320 pixels to full */

static const struct {
    const char *name;
    u32 bit; /* 0: input_name_bit(name) */
} k_targets[] = {
    {"none", 0}, {"A", 0}, {"B", 0}, {"X", 0}, {"Y", 0}, {"START", 0}, {"UP", 0}, {"DOWN", 0}, {"LEFT", 0}, {"RIGHT", 0},
    {"LT", SCRIPT_L}, {"RT", SCRIPT_R}, {"LSUP", SCRIPT_LS_UP}, {"LSDOWN", SCRIPT_LS_DOWN}, {"LSLEFT", SCRIPT_LS_LEFT},
    {"LSRIGHT", SCRIPT_LS_RIGHT}, {"LB", 0}, {"RB", 0}, {"L3", 0}, {"R3", 0}, {"BACK", 0}, {"GUIDE", 0},
    {"RSUP", 0}, {"RSDOWN", 0}, {"RSLEFT", 0}, {"RSRIGHT", 0},
};
#define N_TARGETS (sizeof k_targets / sizeof k_targets[0])

typedef struct Source {
    char name[24];
    int code;   /* a key's code (input_key_code), or M_* */
    u32 dflt;   /* target bits (k_targets indexes) of the game's layout */
    u32 now;    /* the player's */
} Source;
static Source g_src[MAX_SOURCES];
static unsigned g_n;
static int g_ready;

static int g_button[3];          /* left, middle, right held */
static float g_mid_moved;        /* the middle button's travel since it went down */
static int g_wheel_queue[2], g_wheel_pulse[2], g_click_pending, g_click_pulse;
static float g_look[2];          /* the mouse look, -127..127 each way */
static int g_ramp[4];            /* reads each stick direction has been held */
static int g_unfocused, g_menu_open; /* the window lost the focus; the kit's menu is open */
static int g_text;               /* the game's text field is open (native_game.h pg_native_text): keys press nothing */

static int same(const char *a, const char *b)
{
    for (; *a && *b; a++, b++) {
        char x = *a >= 'a' && *a <= 'z' ? (char)(*a - 32) : *a, y = *b >= 'a' && *b <= 'z' ? (char)(*b - 32) : *b;
        if (x != y) return 0;
    }
    return *a == *b;
}

int kbm_same(const char *a, const char *b) { return same(a, b); }

static int mouse_code(const char *name)
{
    for (int i = 0; i < 5; i++)
        if (same(name, k_mouse[i])) return -1 - i;
    return 0;
}

static u32 target_bit(unsigned t) { return k_targets[t].bit ? k_targets[t].bit : input_name_bit(k_targets[t].name); }

/* "A+LSLEFT" as target bits (bit t: k_targets[t]); -1 for a word that is not one. */
static long long parse_targets(const char *text, size_t len)
{
    u32 mask = 0;
    while (len) {
        size_t one = 0;
        unsigned t;
        char word[16];
        while (one < len && text[one] != '+') one++;
        if (one >= sizeof word) return -1;
        memcpy(word, text, one), word[one] = 0;
        for (t = 0; t < N_TARGETS && !same(word, k_targets[t].name); t++) {}
        if (t == N_TARGETS) return -1;
        if (t) mask |= 1u << t;
        text += one, len -= one;
        if (len) text++, len--;
    }
    return mask;
}

static Source *find(const char *name)
{
    for (unsigned i = 0; i < g_n; i++)
        if (same(g_src[i].name, name)) return &g_src[i];
    return NULL;
}

static Source *add(const char *name, size_t len)
{
    Source *s;
    char word[24];
    int code;
    if (!len || len >= sizeof word) return NULL;
    memcpy(word, name, len), word[len] = 0;
    if ((s = find(word))) return s;
    code = mouse_code(word);
    if (!code) code = input_key_code(word);
    if (!code || g_n >= MAX_SOURCES) {
        port_log("input: keyboard and mouse: no key called \"%s\"\n", word);
        return NULL;
    }
    s = &g_src[g_n++];
    memset(s, 0, sizeof *s);
    for (size_t i = 0; i <= len; i++) s->name[i] = word[i] >= 'a' && word[i] <= 'z' ? (char)(word[i] - 32) : word[i];
    s->code = code;
    return s;
}

/* "W=RT,S=LT,..." into the game's layout. */
static void load_layout(const char *text)
{
    while (*text) {
        size_t len = strcspn(text, ","), eq = strcspn(text, "=");
        if (eq < len) {
            long long mask = parse_targets(text + eq + 1, len - eq - 1);
            Source *s = mask >= 0 ? add(text, eq) : NULL;
            if (s) s->dflt = s->now = (u32)mask;
            else if (mask < 0) port_log("input: keyboard and mouse: \"%.*s\" is not a layout entry\n", (int)len, text);
        }
        text += len + (text[len] == ',');
    }
}

static void ready(void)
{
    if (g_ready) return;
    g_ready = 1;
#ifdef PORT_KEYS
    load_layout(PORT_KEYS);
#else
    load_layout(k_kit_keys);
#endif
    for (int i = 0; i < 5; i++) add(k_mouse[i], strlen(k_mouse[i])); /* every game can give the mouse a target */
}

int input_kbm_option(const char *arg, const char *value)
{
    Source *s;
    long long mask;
    if (!value) return 0;
    if (!strcmp(arg, "--kbm-inject")) return kbm_inject_option(value);
    if (!strncmp(arg, "--kbm-mouse-", 12)) return kbm_steer_option(arg, value);
    if (strncmp(arg, "--kbm-", 6)) return 0;
    ready();
    mask = parse_targets(value, strlen(value));
    s = mask >= 0 ? add(arg + 6, strlen(arg + 6)) : NULL;
    if (s) s->now = (u32)mask;
    else port_log("input: %s %s: not a key or mouse input and its targets\n", arg, value);
    return 2;
}

/* ---- the mouse, from the window's events (host_window.cpp) or the injection ---- */
void input_kbm_mouse_button(int which, int down)
{
    if (which < 0 || which > 2) return;
    if (kbm_game_mouse()) { /* the game's own (input_kbm_mouse.c), not the layout's */
        kbm_game_button(which, down);
        return;
    }
    if (which == 1 && down && !g_button[1]) g_mid_moved = 0.0f;
    if (which == 1 && !down && g_button[1] && g_mid_moved < CLICK_SLOP) {
        g_click_pending = 1; /* a click: the look comes back to the middle */
        g_look[0] = g_look[1] = 0.0f;
    }
    g_button[which] = down != 0;
}

void input_kbm_mouse_motion(float dx, float dy)
{
    float was;
    if (kbm_game_mouse()) {
        kbm_game_motion(dx, dy);
        return;
    }
    if (!g_button[1]) { /* the middle button up: mouse steer's, if it is on */
        kbm_steer_motion(dx, dy);
        return;
    }
    was = g_mid_moved;
    g_mid_moved += (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
    if (g_mid_moved < CLICK_SLOP) return;
    if (was < CLICK_SLOP) return; /* the slop itself does not turn */
    for (int a = 0; a < 2; a++) {
        g_look[a] += (a ? dy : dx) * LOOK_PER_PIXEL;
        if (g_look[a] > 127.0f) g_look[a] = 127.0f;
        if (g_look[a] < -127.0f) g_look[a] = -127.0f;
    }
}

void input_kbm_mouse_wheel(int notches)
{
    int d = notches > 0 ? 0 : 1, n = notches > 0 ? notches : -notches;
    if (kbm_game_mouse()) {
        kbm_game_wheel(notches);
        return;
    }
    if (kbm_pointer_wheel(notches)) return; /* a menu is up: the notches step its selection */
    g_wheel_queue[d] = g_wheel_queue[d] + n > WHEEL_QUEUE ? WHEEL_QUEUE : g_wheel_queue[d] + n;
}

void input_kbm_focus_lost(void)
{
    g_button[0] = g_button[1] = g_button[2] = 0;
    g_wheel_queue[0] = g_wheel_queue[1] = 0;
    g_click_pending = 0;
    g_unfocused = 1;
    kbm_pointer_forget();
    input_kbm_game_release();
}

void input_kbm_focus_gained(void) { g_unfocused = 0; }
void input_kbm_menu_open(int open)
{
    g_menu_open = open != 0;
    if (open) input_kbm_game_release();
}
void input_kbm_text_mode(int on) { g_text = on != 0; }
int input_kbm_menu_is_open(void) { return g_menu_open; }
int kbm_window_ok(void) { return !g_unfocused && !g_menu_open; }
int input_kbm_capture_wanted(void)
{
    if (kbm_game_mouse()) return input_kbm_game_look() && kbm_window_ok(); /* the game's look keeps it; its pointer never */
    return kbm_window_ok() && (g_button[1] || kbm_steer_active());
}

/* A wheel notch or a click: its target held PULSE_ON reads, then let go PULSE_OFF. */
static int pulse(int *left, int *queue)
{
    if (!*left && *queue > 0) (*queue)--, *left = PULSE_ON + PULSE_OFF;
    if (!*left) return 0;
    return (*left)-- > PULSE_OFF;
}

static int g_key_ramp = PORT_KEY_RAMP; /* a variable: a constant in the test below is warning C4127 at /W4 */

static int stick(int v) { return v < -128 ? -128 : v > 127 ? 127 : v; }

static int ramp(int dir, int held)
{
    if (!held) return g_ramp[dir] = 0;
    if (g_key_ramp <= 0) return 128;
    if (g_ramp[dir] < g_key_ramp) g_ramp[dir]++;
    return 128 * g_ramp[dir] / g_key_ramp;
}

void input_kbm_read(const bool *keys, KbmPad *out)
{
    u32 acc = 0, keys_acc = 0, vb = port_vblank_count();
    int wheel[2], click, sx, sy, mouse_left;
    ready();
    memset(out, 0, sizeof *out);
    if (!keys && input_kbm_injected()) {
        kbm_inject_mouse(vb);
        int ml = kbm_injected_held("MOUSELEFT", vb), mr = kbm_injected_held("MOUSERIGHT", vb);
        if (kbm_game_mouse()) input_kbm_mouse_button(0, ml), input_kbm_mouse_button(2, mr); /* the game's: by its edges */
        else g_button[0] = ml, g_button[2] = mr;
    }
    wheel[0] = pulse(&g_wheel_pulse[0], &g_wheel_queue[0]);
    wheel[1] = pulse(&g_wheel_pulse[1], &g_wheel_queue[1]);
    click = pulse(&g_click_pulse, &g_click_pending);
    mouse_left = kbm_pointer_left(g_button[0]); /* a click in a menu is the menu pointer's */
    for (unsigned i = 0; i < g_n; i++) {
        const Source *s = &g_src[i];
        int held;
        u32 bits = 0;
        switch (s->code) {
        case M_LEFT: held = mouse_left; break;
        case M_RIGHT: held = g_button[2]; break;
        case M_MIDDLE: held = click; break;
        case M_WHEEL_UP: held = wheel[0]; break;
        case M_WHEEL_DOWN: held = wheel[1]; break;
        default: held = g_text ? 0 : keys ? keys[s->code] : kbm_injected_held(s->name, vb); break; /* the field's */
        }
        for (unsigned t = 1; t < N_TARGETS; t++)
            if (s->now >> t & 1u) bits |= target_bit(t);
        if (s->code > 0) game_log_key(i, held, s->name, bits);
        if (held) acc |= bits, out->any = 1;
        if (held && s->code > 0) keys_acc |= bits;
    }
    {
        u32 menu = kbm_pointer_read(keys_acc != 0); /* the menu pointer's steps and confirm */
        if (menu) acc |= menu, out->any = 1;
    }
    out->b = acc & ~(SCRIPT_L | SCRIPT_R | STICK_TARGETS);
    out->l = acc & SCRIPT_L ? 255 : 0;
    out->r = acc & SCRIPT_R ? 255 : 0;
    /* the stick: LEFT then RIGHT (right wins), UP then DOWN (down wins), as the keyboard always had it */
    {
        int left = ramp(0, (acc & SCRIPT_LS_LEFT) != 0), right = ramp(1, (acc & SCRIPT_LS_RIGHT) != 0);
        int up = ramp(2, (acc & SCRIPT_LS_UP) != 0), down = ramp(3, (acc & SCRIPT_LS_DOWN) != 0);
        if (left) out->dirs |= HOST_PAD_LEFT, out->x = -left;
        if (right) out->dirs |= HOST_PAD_RIGHT, out->x = right > 127 ? 127 : right;
        if (up) out->dirs |= HOST_PAD_UP, out->y = -up;
        if (down) out->dirs |= HOST_PAD_DOWN, out->y = down > 127 ? 127 : down;
    }
    kbm_steer_read(&sx, &sy); /* mouse steer (input_kbm_steer.c), added to the keys' */
    if (sx) out->x = stick(out->x + sx), out->dirs |= sx < 0 ? HOST_PAD_LEFT : HOST_PAD_RIGHT, out->any = 1;
    if (sy) out->y = stick(out->y + sy), out->dirs |= sy < 0 ? HOST_PAD_UP : HOST_PAD_DOWN, out->any = 1;
    out->rx = (int)g_look[0];
    out->ry = (int)g_look[1];
    out->look = out->rx || out->ry;
    if (g_button[1] || click) out->any = 1;
}

/* ---- Input > Keyboard and mouse ---- */
unsigned input_kbm_count(void)
{
    ready();
    return g_n;
}

const char *input_kbm_name(unsigned i)
{
    ready();
    return i < g_n ? g_src[i].name : "";
}

unsigned input_kbm_target_count(void) { return (unsigned)N_TARGETS; }
const char *input_kbm_target_name(unsigned t) { return t < N_TARGETS ? k_targets[t].name : ""; }
u32 input_kbm_get(unsigned i)
{
    ready();
    return i < g_n ? g_src[i].now : 0u;
}

void input_kbm_toggle(unsigned i, unsigned t)
{
    ready();
    if (i >= g_n || t >= N_TARGETS) return;
    g_src[i].now = t ? g_src[i].now ^ (1u << t) : 0u;
}

void input_kbm_reset(void)
{
    ready();
    for (unsigned i = 0; i < g_n; i++) g_src[i].now = g_src[i].dflt;
}

const char *input_kbm_text(unsigned i, char *buf, size_t cap)
{
    size_t at = 0;
    buf[0] = 0;
    for (unsigned t = 1; i < g_n && t < N_TARGETS; t++)
        if (g_src[i].now >> t & 1u) at += (size_t)snprintf(buf + at, cap - at, "%s%s", at ? "+" : "", k_targets[t].name);
    if (!at) snprintf(buf, cap, "none");
    return buf;
}

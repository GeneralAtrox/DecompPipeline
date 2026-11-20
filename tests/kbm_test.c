/* The keyboard and mouse layout (port/base/input_kbm.c) against stubs: the kit's layout as the keyboard always had it,
 * a game's own layout (GAME: built with a port_config.h giving PORT_KEYS and PORT_KEY_RAMP), the player's changes, the
 * mouse buttons, wheel notches, middle click and middle-drag look, --kbm-inject, mouse steer (input_kbm_steer.c) and
 * the menu pointer (input_kbm_pointer.c).
 * Run by tests/test_kbm.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "host.h"

static u32 g_vb;
static int g_fail;
static bool g_keys[512];

u32 port_vblank_count(void) { return g_vb; }
void port_log(const char *fmt, ...) { (void)fmt; }
void game_log_key(unsigned slot, int down, const char *name, unsigned bit) { (void)slot, (void)down, (void)name, (void)bit; }
u32 input_name_bit(const char *name)
{
    static const struct { const char *n; u32 b; } k[] = {
        {"A", HOST_PAD_A}, {"B", HOST_PAD_B}, {"X", HOST_PAD_X}, {"Y", HOST_PAD_Y}, {"START", HOST_PAD_START},
        {"UP", HOST_PAD_UP}, {"DOWN", HOST_PAD_DOWN}, {"LEFT", HOST_PAD_LEFT}, {"RIGHT", HOST_PAD_RIGHT},
        {"LB", HOST_PAD_LB}, {"RB", HOST_PAD_RB}, {"L3", HOST_PAD_L3}, {"R3", HOST_PAD_R3}, {"BACK", HOST_PAD_BACK},
        {"GUIDE", HOST_PAD_GUIDE}, {"RSUP", HOST_PAD_RS_UP}, {"RSDOWN", HOST_PAD_RS_DOWN}, {"RSLEFT", HOST_PAD_RS_LEFT},
        {"RSRIGHT", HOST_PAD_RS_RIGHT},
    };
    for (size_t i = 0; i < sizeof k / sizeof k[0]; i++)
        if (!_stricmp(name, k[i].n)) return k[i].b;
    return 0;
}
/* SDL's codes for letters (A = 4); the rest made up */
int input_key_code(const char *name)
{
    static const char *const other[] = {"UP", "DOWN", "LEFT", "RIGHT", "RETURN", "KP_ENTER", "SPACE", "LSHIFT", "ESCAPE"};
    if (name[0] && !name[1] && ((name[0] | 0x20) >= 'a' && (name[0] | 0x20) <= 'z')) return 4 + ((name[0] | 0x20) - 'a');
    for (int i = 0; i < 9; i++)
        if (!_stricmp(name, other[i])) return 100 + i;
    return 0;
}
static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}
static void key(const char *name, int down) { g_keys[input_key_code(name)] = down != 0; }

static KbmPad read_live(void)
{
    KbmPad k;
    input_kbm_read(g_keys, &k);
    return k;
}

static void release_all(void) { memset(g_keys, 0, sizeof g_keys); }

static unsigned source(const char *name)
{
    for (unsigned i = 0; i < input_kbm_count(); i++)
        if (!_stricmp(input_kbm_name(i), name)) return i;
    return 999;
}

#ifndef GAME
static void kit_layout(void)
{
    KbmPad k;
    char text[64];
    key("Z", 1);
    k = read_live();
    check(k.b == HOST_PAD_A && !k.dirs && !k.l && !k.r, "kit: Z presses A alone");
    release_all();
    key("UP", 1);
    k = read_live();
    check(k.b == HOST_PAD_UP && k.dirs == HOST_PAD_UP && k.y == -128, "kit: an arrow presses the d-pad and the stick all the way");
    release_all();
    key("LEFT", 1), key("RIGHT", 1);
    k = read_live();
    check(k.x == 127, "kit: left and right together: right wins, as always");
    release_all();
    key("DOWN", 1);
    k = read_live();
    check(k.y == 127, "kit: down is 127");
    release_all();
    key("Q", 1), key("SPACE", 1);
    k = read_live();
    check(k.l == 255 && k.r == 255 && !k.b, "kit: Q and Space press the triggers fully, no button");
    release_all();
    key("S", 1), key("A", 1), key("E", 1);
    k = read_live();
    check(k.b == (HOST_PAD_Y | HOST_PAD_X | HOST_PAD_LB), "kit: S, A, E are Y, X, LB");
    release_all();
    check(!strcmp(input_kbm_text(source("UP"), text, sizeof text), "UP+LSUP"), "kit: the arrow's words for the settings");
    check(input_kbm_get(source("MOUSELEFT")) == 0 && source("WHEELUP") < 999, "kit: the mouse is in the layout, doing nothing");
    input_kbm_mouse_button(0, 1);
    k = read_live();
    check(k.b == 0 && !k.l && !k.r && !k.dirs, "kit: the left button does nothing by default");
    input_kbm_mouse_button(0, 0);
}
#endif

static void mouse(void)
{
    KbmPad k;
    int seen[10];
    input_kbm_option("--kbm-mouseleft", "rt");
    input_kbm_option("--kbm-wheelup", "y");
    input_kbm_option("--kbm-wheeldown", "none"); /* the game layout gives it Y too */
    input_kbm_option("--kbm-mousemiddle", "start");
    input_kbm_option("--kbm-z", "none");
    input_kbm_mouse_button(0, 1);
    k = read_live();
    check(k.r == 255, "mouse: the player's choice: the left button presses RT");
    input_kbm_mouse_button(0, 0);
    k = read_live();
    check(k.r == 0, "mouse: let go");
    key("Z", 1);
    k = read_live();
    check(k.b == 0, "mouse: Z given none does nothing");
    release_all();
    /* two notches: two presses, each let go before the next */
    input_kbm_mouse_wheel(2);
    for (int i = 0; i < 10; i++) seen[i] = (read_live().b & HOST_PAD_Y) != 0;
    check(seen[0] && seen[1] && !seen[2] && !seen[3] && seen[4] && seen[5] && !seen[6] && !seen[7] && !seen[8],
          "mouse: two wheel notches are two presses of Y, each let go");
    input_kbm_mouse_wheel(-1);
    k = read_live();
    check(!(k.b & HOST_PAD_Y), "mouse: a notch down presses the down target (none), not the up one");
    for (int i = 0; i < 4; i++) read_live();
    /* the middle button: a drag turns the look and keeps it, a click centres it */
    input_kbm_mouse_button(1, 1);
    for (int i = 0; i < 4; i++) input_kbm_mouse_motion(40.0f, -10.0f);
    k = read_live();
    check(k.look && k.rx > 20 && k.ry < 0 && !(k.b & HOST_PAD_START), "mouse: a middle drag turns the look (x right, y up)");
    input_kbm_mouse_button(1, 0);
    k = read_live();
    check(k.look && k.rx > 20 && !(k.b & HOST_PAD_START), "mouse: the look stays after the drag, and a drag is no click");
    input_kbm_mouse_button(1, 1);
    for (int i = 0; i < 40; i++) input_kbm_mouse_motion(100.0f, 0.0f);
    k = read_live();
    check(k.rx == 127, "mouse: the look stops at full");
    input_kbm_mouse_button(1, 0);
    input_kbm_mouse_button(1, 1);
    input_kbm_mouse_motion(1.0f, 1.0f);
    input_kbm_mouse_button(1, 0);
    k = read_live();
    check(!k.look && !k.rx && !k.ry && (k.b & HOST_PAD_START), "mouse: a middle click centres the look and presses its target");
    k = read_live();
    check((k.b & HOST_PAD_START) != 0, "mouse: the click holds two reads");
    k = read_live();
    check(!(k.b & HOST_PAD_START), "mouse: then lets go");
    input_kbm_mouse_button(2, 1);
    input_kbm_focus_lost();
    input_kbm_mouse_wheel(3);
    input_kbm_focus_lost();
    k = read_live();
    check(!k.b && !k.r, "mouse: losing the focus lets go of the buttons and the notches waiting");
    input_kbm_option("--kbm-mouseright", "b");
}

static void injected(void)
{
    KbmPad k;
    int y = 0, right = 0, z = 0;
    input_kbm_option("--kbm-inject", "Z@10+3,WHEELUP@20,MDRAG=200:0@30+5,MOUSERIGHT@40+2,MOUSEMIDDLE@50");
    input_kbm_option("--kbm-z", "a");
    check(input_kbm_injected(), "inject: on");
    for (g_vb = 1; g_vb < 60; g_vb++) {
        input_kbm_read(NULL, &k);
        if (k.b & HOST_PAD_A) z++;
        if (k.b & HOST_PAD_Y && g_vb >= 20 && g_vb < 24) y++;
        if (k.b & HOST_PAD_B) right++;
        if (g_vb == 36) check(k.look && k.rx > 40, "inject: the drag turned the look");
        if (g_vb == 50) check(!k.look && (k.b & HOST_PAD_START), "inject: the middle click centred it and pressed its target");
    }
    check(z == 3, "inject: Z held its three blanks");
    check(y == 2, "inject: the notch pressed Y two reads");
    check(right == 2, "inject: the right button held two blanks");
    /* a game reads its pad every other blank: a notch on an odd blank still comes */
    input_kbm_option("--kbm-inject", "WHEELUP@61");
    y = 0;
    for (g_vb = 60; g_vb < 80; g_vb += 2) {
        input_kbm_read(NULL, &k);
        if (k.b & HOST_PAD_Y) y++;
    }
    check(y == 2, "inject: a notch between two reads still presses Y two reads");
}

/* Mouse steer (input_kbm_steer.c): the mouse moving with the middle button up turns the left stick while the game asks.
 * moves: the mouse moves dx, dy before each of n reads; the last read's pad. */
static KbmPad moves(float dx, float dy, int n)
{
    KbmPad k;
    memset(&k, 0, sizeof k);
    for (int i = 0; i < n; i++) {
        input_kbm_mouse_motion(dx, dy);
        k = read_live();
    }
    return k;
}

static void steer(void)
{
    KbmPad k;
    int x5, y5, x10, ys[3], x[8];
    release_all();
    input_kbm_focus_gained(); /* mouse() lost it */
    k = moves(8.0f, -8.0f, 6);
    check(!k.x && !k.y && !k.dirs && !input_kbm_capture_wanted(), "steer: off until the game asks: nothing turns, the pointer is free");
    input_kbm_mouse_steer(1);
    input_kbm_mouse_steer(1);
    check(input_kbm_capture_wanted() != 0, "steer: asked: the window keeps the pointer");
    k = moves(0.0f, 0.0f, 2);
    check(!k.x && !k.y && !k.dirs && !k.any, "steer: a still mouse: the stick in the middle");
    k = moves(4.0f, 0.0f, 8);
    x5 = k.x;
    printf("steer: 4 pixels a read at sensitivity 5: x %d\n", x5);
    check(x5 > 10 && x5 < 127 && k.dirs == HOST_PAD_RIGHT && !k.y && k.any, "steer: the mouse moved right: the stick right (x > 0)");
    k = moves(0.0f, -4.0f, 8);
    y5 = k.y;
    check(y5 == -x5 && k.dirs == HOST_PAD_UP && !k.x, "steer: pushed forward: the stick up (y < 0, as a stick pushed up), as far; x back");
    for (int i = 0; i < 3; i++) ys[i] = moves(0.0f, 0.0f, 1).y;
    printf("steer: the mouse still: y %d %d %d\n", ys[0], ys[1], ys[2]);
    check(ys[0] < 0 && ys[0] > y5 && ys[1] > ys[0] && ys[2] == 0, "steer: the mouse still: back to the middle by the third read");
    input_kbm_option("--kbm-mouse-sensitivity", "10");
    x10 = moves(4.0f, 0.0f, 8).x;
    printf("steer: sensitivity 10: x %d\n", x10);
    check(x10 >= 2 * x5 - 1 && x10 <= 2 * x5 + 1, "steer: sensitivity 10 turns twice as far as 5");
    input_kbm_option("--kbm-mouse-sensitivity", "5");
    check(moves(200.0f, 0.0f, 8).x == 127, "steer: no further than full");
    moves(0.0f, 0.0f, 8);
    input_kbm_option("--kbm-k", "lsleft");
    key("K", 1);
    k = moves(4.0f, 0.0f, 8);
    check(k.x == -128 + x5, "steer: added to a stick key's (K full left, the mouse right)");
    release_all();
    input_kbm_option("--kbm-k", "none");
    moves(0.0f, 0.0f, 8);
    input_kbm_option("--kbm-mouse-invert", "on");
    check(moves(0.0f, -4.0f, 8).y == -y5, "steer: Invert mouse Y: forward is the stick down");
    input_kbm_option("--kbm-mouse-invert", "off");
    moves(0.0f, 0.0f, 8);
    input_kbm_mouse_button(1, 1);
    k = moves(30.0f, 0.0f, 4);
    check(!k.x && !(k.dirs & (HOST_PAD_LEFT | HOST_PAD_RIGHT)) && k.look && k.rx > 0, "steer: the middle held: the mouse turns the look, not the stick");
    input_kbm_mouse_button(1, 0);
    moves(0.0f, 0.0f, 2);
    input_kbm_option("--kbm-mouse-steer", "off");
    k = moves(4.0f, -4.0f, 4);
    check(!k.x && !k.y && !input_kbm_capture_wanted(), "steer: Mouse steering off: nothing turns, the pointer is free");
    input_kbm_option("--kbm-mouse-steer", "on");
    input_kbm_menu_open(1);
    k = moves(4.0f, 0.0f, 4);
    check(!k.x && !input_kbm_capture_wanted(), "steer: the kit's menu open: nothing turns, the pointer is free");
    input_kbm_menu_open(0);
    check(input_kbm_capture_wanted() != 0 && moves(4.0f, 0.0f, 8).x == x5, "steer: the menu shut: steering and the pointer kept again");
    input_kbm_focus_lost();
    k = moves(4.0f, 0.0f, 1);
    check(!k.x && !input_kbm_capture_wanted(), "steer: the focus lost: the stick back at once, the pointer free");
    input_kbm_focus_gained();
    check(input_kbm_capture_wanted() != 0 && moves(4.0f, 0.0f, 8).x == x5, "steer: focused again: steering");
    input_kbm_mouse_steer(0);
    k = read_live();
    check(!k.x && !k.dirs && !input_kbm_capture_wanted(), "steer: the game lets go: the stick back at once, the pointer free");
    check(!moves(4.0f, 0.0f, 4).x, "steer: and the mouse turns nothing");
    /* --kbm-inject MOUSEMOVE: 4 reads of 4 pixels right, by a game reading every other blank */
    input_kbm_mouse_steer(1);
    input_kbm_option("--kbm-inject", "MOUSEMOVE=4:0@300+4");
    for (int i = 0; i < 8; i++) {
        g_vb = 296u + 2u * (u32)i;
        input_kbm_read(NULL, &k);
        x[i] = k.x;
    }
    printf("steer: injected: x %d %d %d %d %d %d %d %d\n", x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7]);
    check(!x[1] && x[2] > 0 && x[3] > x[2] && x[5] >= x[4] && x[6] < x[5], "steer: MOUSEMOVE moves the mouse at four reads from its blank");
    input_kbm_mouse_steer(0);
}

/* The menu pointer (input_kbm_pointer.c), against a menu as a game runs one: the selection steps on each new press of
 * the next or previous direction (wrapping, or not), skips the entry g_skip, and counts A's new presses. */
static PortMenuPointer g_m;
static int g_sel, g_skip = -1, g_steps_seen, g_cross, g_confirms, g_confirm_at = -1;
static u32 g_prevb;

static void menu_init(int count, int horizontal, int wrap)
{
    memset(&g_m, 0, sizeof g_m);
    g_m.count = count, g_m.horizontal = horizontal, g_m.wrap = wrap;
    for (int i = 0; i < count; i++) {
        s16 *r = g_m.rect[i];
        if (horizontal) r[0] = (s16)(40 + 120 * i), r[1] = 300, r[2] = 100, r[3] = 60;
        else r[0] = 200, r[1] = (s16)(100 + 50 * i), r[2] = 240, r[3] = 40;
    }
    g_sel = 0, g_steps_seen = g_cross = g_confirms = 0, g_confirm_at = -1;
}

static int menu_step(int s, int d)
{
    s += d;
    if (s < 0) s = g_m.wrap ? g_m.count - 1 : 0;
    if (s >= g_m.count) s = g_m.wrap ? 0 : g_m.count - 1;
    return s;
}

static KbmPad frame_read(int injected)
{
    KbmPad k;
    u32 pressed, next = g_m.horizontal ? HOST_PAD_RIGHT : HOST_PAD_DOWN, prev = g_m.horizontal ? HOST_PAD_LEFT : HOST_PAD_UP;
    g_m.selected = g_sel;
    input_kbm_menu_pointer(&g_m);
    if (injected) input_kbm_read(NULL, &k);
    else k = read_live();
    pressed = k.b & ~g_prevb;
    g_prevb = k.b;
    if (pressed & (next | prev)) {
        int d = pressed & next ? 1 : -1;
        g_sel = menu_step(g_sel, d);
        if (g_sel == g_skip) g_sel = menu_step(g_sel, d);
        g_steps_seen++;
    }
    if (pressed & (g_m.horizontal ? HOST_PAD_UP | HOST_PAD_DOWN : HOST_PAD_LEFT | HOST_PAD_RIGHT)) g_cross++;
    if (pressed & HOST_PAD_A) g_confirms++, g_confirm_at = g_sel;
    return k;
}

static KbmPad frame(void) { return frame_read(0); }
static void frames(int n)
{
    for (int i = 0; i < n; i++) frame();
}
static void at_entry(int i) { input_kbm_mouse_position(320.0f, (float)(120 + 50 * i)); }

static void pointer(void)
{
    KbmPad k;
    int y = 0, before;
    release_all();
    input_kbm_focus_gained();
    input_kbm_menu_open(0);
    for (int i = 0; i < 8; i++) read_live();
    menu_init(5, 0, 1);
    at_entry(3); /* the pointer over entry 3 before the menu is up */
    frames(10);
    check(g_sel == 0 && !g_steps_seen, "pointer: a pointer resting where the menu opens moves nothing");
    input_kbm_mouse_position(321.0f, 271.0f);
    frames(30);
    check(g_sel == 3 && g_steps_seen == 2 && !g_confirms, "pointer: moved onto entry 3 of 5 that wrap: two steps up (the short way), nothing confirmed");
    at_entry(1);
    frames(30);
    check(g_sel == 1 && g_steps_seen == 4 && !g_cross, "pointer: onto entry 1: two steps up");
    g_sel = 0, g_steps_seen = 0;
    at_entry(4);
    frames(20);
    check(g_sel == 4 && g_steps_seen == 1, "pointer: a menu that wraps: first to last the short way, one step up");
    key("DOWN", 1);
    frame();
    release_all();
    frames(20);
    check(g_sel == 0, "pointer: a key moves the selection off the entry under the pointer, which leaves it there");
    /* a key while the pointer is on its way: the pointer stops */
    menu_init(5, 0, 0);
    input_kbm_mouse_position(20.0f, 20.0f); /* off the entries, so entry 4 is a new hover */
    frame();
    at_entry(4);
    frame();
    check(g_sel == 1, "pointer: on its way down a menu that does not wrap");
    key("UP", 1);
    frame();
    release_all();
    frames(30);
    check(g_sel == 0, "pointer: a key pressed on the way stops the pointer's steps");
    /* a click: the selection there, then A once */
    menu_init(5, 0, 1);
    frame();
    input_kbm_mouse_position(320.0f, 220.0f);
    input_kbm_mouse_button(0, 1);
    frames(30);
    check(g_sel == 2 && g_confirms == 1 && g_confirm_at == 2, "pointer: a click on entry 2: the selection there, then A once");
    check(!g_cross, "pointer: no other button on the way");
    input_kbm_menu_pointer(NULL); /* the menu shuts, the button still held */
    k = read_live();
    check(!(k.b & HOST_PAD_A) && !k.r, "pointer: the button held on as the menu shuts presses nothing through the layout");
    input_kbm_mouse_button(0, 0);
    read_live();
    input_kbm_mouse_button(0, 1);
    k = read_live();
    check(k.r == 255, "pointer: no menu: the left button is the layout's again (RT, mouse() above)");
    input_kbm_mouse_button(0, 0);
    read_live();
    /* an entry with a press of its own: the selection there, then that, not A */
    menu_init(5, 0, 1);
    g_m.press[3] = HOST_PAD_RIGHT;
    frame();
    at_entry(3);
    input_kbm_mouse_button(0, 1);
    frames(30);
    input_kbm_mouse_button(0, 0);
    frames(4);
    check(g_sel == 3 && !g_confirms && g_cross == 1, "pointer: a click on an entry with its own press: there, then that press, not A");
    /* a click off every entry */
    menu_init(5, 0, 1);
    frame();
    input_kbm_mouse_position(20.0f, 20.0f);
    input_kbm_mouse_button(0, 1);
    frames(3);
    input_kbm_mouse_button(0, 0);
    frames(10);
    check(!g_confirms && !g_steps_seen, "pointer: a click off every entry does nothing");
    /* the wheel */
    g_sel = 2;
    input_kbm_mouse_wheel(-1);
    for (int i = 0; i < 10; i++) y |= (frame().b & HOST_PAD_Y) != 0;
    check(g_sel == 3 && g_steps_seen == 1 && !y, "pointer: a notch down: one step down, not the layout's Y");
    input_kbm_mouse_wheel(2);
    frames(20);
    check(g_sel == 1 && g_steps_seen == 3, "pointer: two notches up: two steps up");
    input_kbm_mouse_button(2, 1);
    k = frame();
    check((k.b & HOST_PAD_B) != 0, "pointer: the right button stays the layout's (B)");
    input_kbm_mouse_button(2, 0);
    frames(4);
    /* an entry with no size is not picked; one the game skips is given up after a lap */
    g_m.rect[3][2] = 0;
    at_entry(3);
    before = g_steps_seen;
    frames(20);
    check(g_sel == 1 && g_steps_seen == before, "pointer: an entry with no size is not picked");
    g_m.rect[3][2] = 240;
    g_skip = 3;
    at_entry(0);
    frames(20);
    at_entry(3);
    before = g_steps_seen;
    frames(80);
    printf("pointer: to a skipped entry: %d steps\n", g_steps_seen - before);
    check(g_sel != 3 && g_steps_seen - before <= 6, "pointer: an entry the game skips: given up after one lap");
    g_skip = -1;
    /* a row */
    menu_init(4, 1, 0);
    frame();
    input_kbm_mouse_position(330.0f, 330.0f);
    frames(20);
    check(g_sel == 2 && g_steps_seen == 2 && !g_cross, "pointer: a row: steps right, not down");
    /* mouse steer waits while a menu is up */
    input_kbm_mouse_steer(1);
    frame();
    check(!input_kbm_capture_wanted(), "pointer: mouse steer waits while a menu is up: the pointer free");
    for (int i = 0; i < 10; i++) read_live(); /* the game stops giving the menu */
    check(input_kbm_capture_wanted() != 0, "pointer: the menu not given again: off, steer back");
    input_kbm_mouse_steer(0);
    input_kbm_mouse_wheel(1);
    y = 0;
    for (int i = 0; i < 6; i++) y |= (read_live().b & HOST_PAD_Y) != 0;
    check(y, "pointer: no menu: the wheel is the layout's Y again");
    /* losing the focus drops a click on its way */
    menu_init(5, 0, 0);
    frame();
    input_kbm_mouse_position(320.0f, 320.0f);
    input_kbm_mouse_button(0, 1);
    frame();
    input_kbm_focus_lost();
    input_kbm_focus_gained();
    frames(30);
    check(!g_confirms, "pointer: losing the focus drops a click on its way");
    /* --kbm-inject MOUSEPOS and MOUSELEFT */
    menu_init(5, 0, 1);
    input_kbm_option("--kbm-inject", "MOUSEPOS=320:170@900,MOUSELEFT@902+3");
    for (g_vb = 896; g_vb < 940; g_vb++) frame_read(1);
    check(g_sel == 1 && g_confirms == 1 && g_confirm_at == 1, "pointer: injected MOUSEPOS and a click pick entry 1");
    input_kbm_menu_pointer(NULL);
}

#ifdef GAME
static void game_layout(void)
{
    KbmPad k;
    int x[5];
    key("W", 1);
    k = read_live();
    check(k.r == 255 && !k.b, "game: W is RT");
    release_all();
    key("UP", 1);
    k = read_live();
    check(k.b == HOST_PAD_UP && !k.dirs, "game: an arrow is the d-pad alone");
    release_all();
    key("Z", 1);
    k = read_live();
    check(k.b == 0, "game: a key the game's layout leaves out does nothing");
    release_all();
    key("Q", 1);
    k = read_live();
    check(k.b == HOST_PAD_X, "game: Q is X");
    release_all();
    key("A", 1);
    for (int i = 0; i < 5; i++) x[i] = read_live().x;
    check(x[0] == -32 && x[1] == -64 && x[2] == -96 && x[3] == -128 && x[4] == -128, "game: a stick key ramps over key_ramp reads");
    release_all();
    k = read_live();
    check(!k.dirs && !k.x, "game: let go, the stick is back");
    key("A", 1);
    check(read_live().x == -32, "game: and ramps again from the start");
    release_all();
    input_kbm_mouse_button(0, 1);
    k = read_live();
    check(k.b == HOST_PAD_A, "game: the left button is A");
    input_kbm_mouse_button(0, 0);
    input_kbm_toggle(source("MOUSELEFT"), 2); /* B on */
    input_kbm_toggle(source("MOUSELEFT"), 1); /* A off */
    input_kbm_mouse_button(0, 1);
    check(read_live().b == HOST_PAD_B, "game: the menu's change: the left button is B");
    input_kbm_reset();
    check(read_live().b == HOST_PAD_A, "game: reset: the game's layout again");
    input_kbm_mouse_button(0, 0);
    read_live();
}
#endif

int kbm_game_checks(u32 *vb); /* kbm_game_test.c: the game's own mouse and text field */
int main(void)
{
#ifdef GAME
    game_layout();
#else
    kit_layout();
#endif
    mouse();
    injected();
    steer();
    pointer();
    g_fail |= kbm_game_checks(&g_vb);
    printf("%s\n", g_fail ? "FAILED" : "PASSED");
    return g_fail;
}

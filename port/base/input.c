/* Host input for players 1..8: every SDL3 gamepad and joystick and the keyboard, plus scripted presses for automated
 * runs. A game's own pad layer has players 1..4 (sdk/maple_pad.c's Maple ports A..D); it takes 5..8 from port_pad_player.
 *   Players: a device becomes the next free player the first time it is used (a button, the stick or a trigger),
 *   so an idle virtual pad (Parsec, Steam Input, ViGEm) never takes a player's place. The keyboard is always player 1.
 *   A device unplugged frees its player; the next device used takes the lowest free one. --pads N (1..8; default
 *   game.json "pads", else 4) caps the players: --pads 1 merges every device into player 1 (buttons together, the
 *   stick and triggers from whichever device moves them most). A player 2..8 with no device and no script has no
 *   controller connected. Players 5..8 are read only when the cap is over 4: a 4-pad run reads, records and logs
 *   what it did before there were eight.
 *   Gamepad (Xbox layout): A/B/X/Y are the Dreamcast's (the same places), Start, the d-pad, the left stick, LT/RT
 *   as the analog triggers. Keyboard and mouse (input_kbm.c, player 1): by default Z/X/A/S = A/B/X/Y, Enter =
 *   Start, the arrows = the d-pad and also the stick at full deflection (a keyboard has no stick), I/K/J/L = the
 *   d-pad alone, W or Space = the R trigger and Q or Left Shift = the L trigger, fully pressed (driving games usually
 *   accelerate on R and brake on L), the mouse nothing. A game whose d-pad does something else than steering sets
 *   game.json "arrows": "stick" (PORT_ARROWS_STICK): the arrows then move only the stick. A game can give its own
 *   whole layout (game.json "keys"), and the player changes it in Input > Keyboard and mouse.
 *   --input [Pn.]NAME@VBLANK[+N],..   NAME: A B X Y START UP DOWN LEFT RIGHT L R LB RB L3 R3 BACK GUIDE RSUP RSDOWN
 *                                RSLEFT RSRIGHT LSUP LSDOWN LSLEFT LSRIGHT (the left stick alone, no button),
 *                                or a name of the console's own pad (console_actions: C and Z on a
 *                                Saturn: the action's default host button), held from that vertical blank
 *                                for N (default 6); scripted directions move the stick like the arrows. L=V and R=V
 *                                press a trigger part of the way (V 1..255; plain L and R are 255). Pn. (P1..P8)
 *                                is the player (default P1); a player a script names is connected from the start
 *                                (P5..P8 when the cap is that high).
 *   A joystick SDL has no gamepad mapping for is read with a generic layout: buttons 0-3 = A/B/X/Y, 4/5 = L/R
 *   fully pressed, 9 (7 on pads with fewer than 10 buttons) = Start, the first hat = the d-pad, axes 0/1 = the stick.
 * The devices themselves (opening, dropping, rumble) are input_device.c.
 * Headless runs (no SDL video) read only the script. Every player and the right sticks are recorded and replayed
 * (session.c: player 1's pad, then session_pad_other for players 2..8 and the right sticks).
 * Everything runs on the game thread, which pumps the events. */
#include <stdlib.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "base.h"
#include "console.h"
#include "host.h"
#include "session.h"
#include "game_log.h"
#include "input_priv.h"

#define DIRS (HOST_PAD_UP | HOST_PAD_DOWN | HOST_PAD_LEFT | HOST_PAD_RIGHT)
#define STICK_DEAD 12  /* of 128: a worn host stick's centring noise (the Dreamcast pad reports raw values) */
#define RS_ON 64 /* of 128: the right stick counts as a direction pressed */
#define TRIGGER_USED 64 /* of 255: a trigger pressed this far claims a player */

static u32 g_script_players = 1u; /* bit p: the script names player p + 1 (player 1 is always connected) */

typedef struct PadState {
    u32 b;
    int x, y, l, r;
    int rx, ry; /* the right stick */
} PadState;

static s8 g_right[HOST_PADS][2]; /* the last host_pads_read's right sticks, recorded or the replay's */
static HostPad g_pads[HOST_PADS]; /* the last host_pads_read's pads, for port_pad_player */
static u32 g_pads_on = 1u;        /* and who had a controller */

static const struct {
    SDL_GamepadButton button;
    u32 bit;
} g_pad_map[] = {
    {SDL_GAMEPAD_BUTTON_SOUTH, HOST_PAD_A},       {SDL_GAMEPAD_BUTTON_EAST, HOST_PAD_B},
    {SDL_GAMEPAD_BUTTON_WEST, HOST_PAD_X},        {SDL_GAMEPAD_BUTTON_NORTH, HOST_PAD_Y},
    {SDL_GAMEPAD_BUTTON_START, HOST_PAD_START},   {SDL_GAMEPAD_BUTTON_DPAD_UP, HOST_PAD_UP},
    {SDL_GAMEPAD_BUTTON_DPAD_DOWN, HOST_PAD_DOWN}, {SDL_GAMEPAD_BUTTON_DPAD_LEFT, HOST_PAD_LEFT},
    {SDL_GAMEPAD_BUTTON_DPAD_RIGHT, HOST_PAD_RIGHT}, {SDL_GAMEPAD_BUTTON_MISC1, HOST_PAD_SHARE},
    {SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, HOST_PAD_LB}, {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, HOST_PAD_RB},
    {SDL_GAMEPAD_BUTTON_LEFT_STICK, HOST_PAD_L3},  {SDL_GAMEPAD_BUTTON_RIGHT_STICK, HOST_PAD_R3},
    {SDL_GAMEPAD_BUTTON_BACK, HOST_PAD_BACK},      {SDL_GAMEPAD_BUTTON_GUIDE, HOST_PAD_GUIDE},
};

/* A key's code by name: SDL's names, and short ones for the keys a layout names most. */
int input_key_code(const char *name)
{
    static const struct {
        const char *name;
        SDL_Scancode code;
    } k_short[] = {
        {"UP", SDL_SCANCODE_UP}, {"DOWN", SDL_SCANCODE_DOWN}, {"LEFT", SDL_SCANCODE_LEFT}, {"RIGHT", SDL_SCANCODE_RIGHT},
        {"RETURN", SDL_SCANCODE_RETURN}, {"KP_ENTER", SDL_SCANCODE_KP_ENTER}, {"SPACE", SDL_SCANCODE_SPACE},
        {"ESCAPE", SDL_SCANCODE_ESCAPE}, {"BACKSPACE", SDL_SCANCODE_BACKSPACE}, {"TAB", SDL_SCANCODE_TAB},
        {"LSHIFT", SDL_SCANCODE_LSHIFT}, {"RSHIFT", SDL_SCANCODE_RSHIFT}, {"LCTRL", SDL_SCANCODE_LCTRL},
        {"RCTRL", SDL_SCANCODE_RCTRL}, {"LALT", SDL_SCANCODE_LALT},
    };
    for (size_t i = 0; i < sizeof k_short / sizeof k_short[0]; i++)
        if (!_stricmp(name, k_short[i].name)) return (int)k_short[i].code;
    return (int)SDL_GetScancodeFromName(name); /* SDL_SCANCODE_UNKNOWN is 0 */
}

int input_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--rumble") && value) {
        input_set_rumble(strcmp(value, "off") != 0);
        return 2;
    }
    if (!strcmp(arg, "--pad-art") && value) {
        pad_art_set(strcmp(value, "off") != 0);
        return 2;
    }
    if (!strcmp(arg, "--pads") && value) {
        int n = atoi(value);
        if (n >= 1 && n <= HOST_PADS) g_pads_max = n;
        else port_log("input: --pads takes 1..%d, not \"%s\"\n", HOST_PADS, value);
        return 2;
    }
    if (!strncmp(arg, "--bind-", 7)) return input_bind_option(arg, value);
    if (!strncmp(arg, "--kbm-", 6)) return input_kbm_option(arg, value);
    return port_twin_option(arg, value) || pad_virtual_option(arg, value) ? 2 : input_script_option(arg, value); /* pad_twin.c, pad_virtual.c */
}

static int stick(Sint16 v)
{
    int s = v / 256; /* -128..127 */
    return abs(s) < STICK_DEAD ? 0 : s;
}

static int trigger(Sint16 v) { return v <= 0 ? 0 : (v * 255 + 16383) / 32767; }

static int further(int a, int b) { return abs(b) > abs(a) ? b : a; }

static int joy_button(SDL_Joystick *j, int n, int i) { return i < n && SDL_GetJoystickButton(j, i); }

/* One device's state now. */
static PadState read_device(const HostDevice *d)
{
    PadState s = {0};
    if (d->pad) {
        for (size_t i = 0; i < sizeof g_pad_map / sizeof g_pad_map[0]; i++)
            if (SDL_GetGamepadButton(d->pad, g_pad_map[i].button)) s.b |= g_pad_map[i].bit;
        s.x = stick(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFTX));
        s.y = stick(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFTY));
        {
            int rx = stick(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_RIGHTX)), ry = stick(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_RIGHTY));
            s.rx = rx;
            s.ry = ry;
            if (rx < -RS_ON) s.b |= HOST_PAD_RS_LEFT;
            if (rx > RS_ON) s.b |= HOST_PAD_RS_RIGHT;
            if (ry < -RS_ON) s.b |= HOST_PAD_RS_UP;
            if (ry > RS_ON) s.b |= HOST_PAD_RS_DOWN;
        }
        s.l = trigger(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFT_TRIGGER));
        s.r = trigger(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER));
    } else {
        static const u32 face[] = {HOST_PAD_A, HOST_PAD_B, HOST_PAD_X, HOST_PAD_Y};
        SDL_Joystick *j = d->joy;
        int nb = SDL_GetNumJoystickButtons(j), axes = SDL_GetNumJoystickAxes(j);
        for (int i = 0; i < 4; i++)
            if (joy_button(j, nb, i)) s.b |= face[i];
        if (joy_button(j, nb, nb >= 10 ? 9 : 7)) s.b |= HOST_PAD_START;
        if (SDL_GetNumJoystickHats(j) > 0) {
            Uint8 h = SDL_GetJoystickHat(j, 0);
            s.b |= (h & SDL_HAT_UP ? HOST_PAD_UP : 0u) | (h & SDL_HAT_DOWN ? HOST_PAD_DOWN : 0u) |
                   (h & SDL_HAT_LEFT ? HOST_PAD_LEFT : 0u) | (h & SDL_HAT_RIGHT ? HOST_PAD_RIGHT : 0u);
        }
        s.x = axes >= 2 ? stick(SDL_GetJoystickAxis(j, 0)) : 0;
        s.y = axes >= 2 ? stick(SDL_GetJoystickAxis(j, 1)) : 0;
        s.l = joy_button(j, nb, 4) ? 255 : 0;
        s.r = joy_button(j, nb, 5) ? 255 : 0;
    }
    return s;
}

/* A device that reports buttons held from its very first read (this laptop's XInput device holds Start, A, Down, Left,
 * Right, L and R) is a virtual pad nobody uses: it counts from the first time its state changes. */
static int device_in_use(HostDevice *d, const PadState *s)
{
    if (d->live) return 1;
    if (!d->seen) {
        d->seen = 1;
        d->first.b = s->b, d->first.x = s->x, d->first.y = s->y, d->first.l = s->l, d->first.r = s->r;
        if (!s->b && !s->x && !s->y && s->l < TRIGGER_USED && s->r < TRIGGER_USED) d->live = 1; /* resting: a real pad */
        else port_log("input: device %u reports buttons held from the start: ignored until its state changes\n", (unsigned)d->id);
        return d->live;
    }
    if (s->b != d->first.b || s->x != d->first.x || s->y != d->first.y || s->l != d->first.l || s->r != d->first.r) {
        d->live = 1;
        port_log("input: device %u changed state: it counts from here\n", (unsigned)d->id);
    }
    return d->live;
}

/* Adds one device's state to a player's. */
static void merge(PadState *into, const PadState *s)
{
    into->b |= s->b;
    into->x = further(into->x, s->x);
    into->y = further(into->y, s->y);
    into->rx = further(into->rx, s->rx);
    into->ry = further(into->ry, s->ry);
    if (s->l > into->l) into->l = s->l;
    if (s->r > into->r) into->r = s->r;
}

/* Gives a device that is used for the first time the lowest free player (none when all are taken). */
static void claim_player(HostDevice *d, const PadState *s)
{
    u32 taken = 0;
    if (d->player != NO_PLAYER || !(s->b || s->x || s->y || s->l >= TRIGGER_USED || s->r >= TRIGGER_USED)) return;
    for (int i = 0; i < g_dev_n; i++)
        if (g_dev[i].player != NO_PLAYER) taken |= 1u << g_dev[i].player;
    for (int p = 0; p < g_pads_max; p++)
        if (!(taken & (1u << p))) {
            d->player = p;
            port_log("input: device %u is player %d\n", (unsigned)d->id, p + 1);
            return;
        }
}

/* The keyboard and the mouse (player 1), by the layout (input_kbm.c); keys: SDL's state, or NULL for --kbm-inject. */
static PadState read_keyboard(const bool *keys, KbmPad *k)
{
    PadState s = {0};
    if (keys && SDL_GetModState() & SDL_KMOD_ALT) { /* Alt+Enter is the host's full-screen switch */
        memset(k, 0, sizeof *k);
        return s;
    }
    input_kbm_read(keys, k);
    s.b = k->b;
    s.l = k->l;
    s.r = k->r;
    return s;
}

/* One player's pad from its devices' merged state, its script and (player 1, k) the keyboard's stick. */
static void make_pad(u32 p, const PadState *s, const KbmPad *k, HostPad *pad)
{
    u32 trig[2], b = input_script_buttons(p, trig) | s->b, keys = b & DIRS & ~s->b; /* scripted directions: the stick */
    int x = s->x, y = s->y;
    pad->l = (u8)s->l;
    pad->r = (u8)s->r;
    if (b & SCRIPT_L) pad->l = (u8)(trig[0] > pad->l ? trig[0] : pad->l);
    if (b & SCRIPT_R) pad->r = (u8)(trig[1] > pad->r ? trig[1] : pad->r);
    if (k && k->dirs & (HOST_PAD_LEFT | HOST_PAD_RIGHT)) x = k->x;
    if (k && k->dirs & (HOST_PAD_UP | HOST_PAD_DOWN)) y = k->y;
    if (keys & HOST_PAD_LEFT) x = -128;
    if (keys & HOST_PAD_RIGHT) x = 127;
    if (keys & HOST_PAD_UP) y = -128;
    if (keys & HOST_PAD_DOWN) y = 127;
    if (b & SCRIPT_LS_LEFT) x = -128;
    if (b & SCRIPT_LS_RIGHT) x = 127;
    if (b & SCRIPT_LS_UP) y = -128;
    if (b & SCRIPT_LS_DOWN) y = 127;
    pad->x = (s8)x;
    pad->y = (s8)y;
    pad->buttons = b & ~(SCRIPT_L | SCRIPT_R | SCRIPT_LS);
}

/* The game's text, mouse and menu picks (native_game.h), taken at the tick's pad read only (never in an in-between
 * frame, which leaves them for the next tick) and recorded with it (session_events.c); a replay's come from the file. */
static void events_read(void)
{
    static int said;
    SessionEvents ev;
    int tag, index;
    if (port_extra_drawing()) return;
    memset(&ev, 0, sizeof ev);
    ev.text_n = host_text_drain(ev.text, SESSION_TEXT_MAX);
    ev.mouse_on = input_kbm_game_mouse(&ev.mouse);
    while (ev.menu_n < SESSION_MENU_MAX && port_menu_drain(&tag, &index))
        ev.menu[ev.menu_n][0] = tag, ev.menu[ev.menu_n][1] = index, ev.menu_n++;
    session_events_read(&ev);
    if (!said && session_events_replayed()) said = 1, game_log_line("session: replay uses text/mouse events");
}

u32 host_pads_read(HostPad pad[HOST_PADS])
{
    PadState s[HOST_PADS] = {0};
    const u32 n = g_pads_max > HOST_GAME_PADS ? (u32)g_pads_max : (u32)HOST_GAME_PADS; /* players 1..4, or as many as the cap */
    KbmPad kbm = {0};
    u32 connected = input_script_players(), own = 0, load = autoload_buttons(&own); /* autoload.c: a load it drives */
    pad_style_tick();
    if (input_live() && input_sdl_ready()) {
        pad_virtual_tick(); /* --virtual-pad: a test driver's SDL gamepad, pressed before the devices are read */
        s[0] = read_keyboard(SDL_GetKeyboardState(NULL), &kbm);
        if (kbm.any) pad_style_used(0u);
        input_scan_devices(g_pads_max), console_pads_changed();
        for (int i = 0; i < g_dev_n; i++) {
            PadState one = read_device(&g_dev[i]);
            if (!device_in_use(&g_dev[i], &one)) one = (PadState){0};
            claim_player(&g_dev[i], &one);
            if (g_dev[i].player == NO_PLAYER) continue;
            if (one.b || abs(one.x) >= RS_ON || abs(one.y) >= RS_ON || one.l >= TRIGGER_USED || one.r >= TRIGGER_USED) pad_style_used(g_dev[i].id);
            merge(&s[g_dev[i].player], &one);
            connected |= 1u << g_dev[i].player;
        }
        if (input_kbm_menu_is_open()) { /* the game runs on under an open menu (host_modal.cpp), but the controller and keys
                                          drive the menu (the slim bar's Guide, A, B, stick): the game reads none of them */
            memset(s, 0, sizeof s);
            kbm = (KbmPad){0};
        }
    } else if (input_kbm_injected()) {
        s[0] = read_keyboard(NULL, &kbm); /* a test's keys and mouse, not the real ones */
    }
    if (own) s[0] = (PadState){.b = load}, kbm = (KbmPad){0}; else s[0].b |= load; /* while it holds the pad, the player waits */
    if (kbm.look && !s[0].rx && !s[0].ry) s[0].rx = kbm.rx, s[0].ry = kbm.ry; /* the mouse look, unless a stick moves */
    for (u32 p = 0; p < HOST_PADS; p++) {
        if (p >= n) { /* past the players read: nothing held, and nothing recorded */
            pad[p] = (HostPad){0};
            g_right[p][0] = g_right[p][1] = 0;
            continue;
        }
        make_pad(p, &s[p], p == 0 ? &kbm : NULL, &pad[p]);
        if (p == 0 && kbm.look && s[0].rx == kbm.rx && s[0].ry == kbm.ry) pad[0].buttons |= HOST_PAD_MOUSE_LOOK;
        /* the right stick, analog; a scripted RS direction (no stick) moves it all the way */
        g_right[p][0] = (s8)(s[p].rx ? s[p].rx : pad[p].buttons & HOST_PAD_RS_LEFT ? -128 : pad[p].buttons & HOST_PAD_RS_RIGHT ? 127 : 0);
        g_right[p][1] = (s8)(s[p].ry ? s[p].ry : pad[p].buttons & HOST_PAD_RS_UP ? -128 : pad[p].buttons & HOST_PAD_RS_DOWN ? 127 : 0);
    }
    input_binds_apply(pad);
    session_pad(&pad[0]); /* recorded, or the replay's */
    for (u32 p = 0; p < n; p++) /* players 2..8 and the right sticks too, so a game several play replays */
        connected = session_pad_other((int)p, &pad[p], g_right[p], (connected >> p) & 1u) ? connected | 1u << p
                                                                                            : connected & ~(1u << p);
    events_read();
    port_pad_stick_apply(pad);
    input_ex_note(pad);
    connected &= (1u << g_pads_max) - 1u;
    game_log_pads(pad, connected);
    memcpy(g_pads, pad, sizeof g_pads);
    g_pads_on = connected;
    return connected;
}

void host_pad_right_stick(u32 player, s8 *x, s8 *y)
{
    *x = player < HOST_PADS ? g_right[player][0] : 0;
    *y = player < HOST_PADS ? g_right[player][1] : 0;
}

/* Player (0..7)'s pad from the last host_pads_read: 1 and the pad while that player has a controller, else 0 and none. */
int port_pad_player(int player, HostPad *pad)
{
    const HostPad none = {0};
    int on = player >= 0 && player < HOST_PADS && (g_pads_on >> player & 1u);
    if (pad) *pad = on ? g_pads[player] : none;
    return on;
}

int port_pads_max(void) { return g_pads_max; }

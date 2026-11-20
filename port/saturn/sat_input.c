/* The control pad: the Saturn digital pad's 16 buttons, from the base's controllers (base/input.c, console.h
 * "input"). The base reads the keyboard and every gamepad (and the --input script) into one HostPad; the Saturn's
 * thirteen buttons are console_actions, each taking one host button, set on the Input > Controls menu and kept in
 * the settings. The SMPC hands the result to the game (INTBACK peripheral data, sat_smpc.c).
 *
 * Defaults without game.json "controller" (the buttons this kit always used; with "saturn", the layout in k_actions): the D-pad and left stick, A B X Y = A B X Y, Start, LB RB = L R,
 * RT = C, LT = Z. Keyboard (the base's layout): arrows, Z X A S = A B X Y, Enter = Start, E R = L R, W or Space =
 * C, Q or Left Shift = Z. --input NAME@V[+N],... uses the action names (up down left right start a b c x y z l r):
 * a scripted press is the action's default button, whatever is assigned, so a regression run does not depend on
 * the player's settings. Pads 3 and 4 are read with the first two; the SMPC hands the game two, and a team fight's seats
 * past them take theirs from sat_pad_seats (sat_team.c).
 *
 * game.json "confirm" and "cancel" then move the game's own buttons onto host A and B (sat_layout).
 *
 * Nearest existing: the old sat_pad.c (XInput and the window's key messages) this replaces. */
#include <string.h>
#include "sat.h"
#include "sat_game.h"  /* SAT_CONTROLLER_SATURN: the game.json switch */
#include "sat_sys.h"
#include "console.h"
#include "sat_net.h"

/* Saturn button bits in the order of the pad's two data bytes (1 = pressed here; the SMPC inverts) */
enum {
    B_B = 1u << 0, B_C = 1u << 1, B_A = 1u << 2, B_START = 1u << 3, B_UP = 1u << 4, B_DOWN = 1u << 5, B_LEFT = 1u << 6, B_RIGHT = 1u << 7,
    B_L = 1u << 11, B_Z = 1u << 12, B_Y = 1u << 13, B_X = 1u << 14, B_R = 1u << 15
};

static const PortAction k_actions[] = {
#ifdef SAT_CONTROLLER_SATURN
    /* game.json "controller": "saturn": the Saturn pad's layout on an Xbox pad, A B X Y as they are, RB C, LB Z, LT L, RT R,
     * Start and the d-pad. The second column is the host button the action reads, so a scripted press of C is RB here; the
     * keyboard (the game's "keys") keeps Q W E R and Space as L R Z C. The stick clicks, Back, Guide and the right stick take
     * nothing until the player assigns them (RUNTIME_TARGET.md, Modern controllers). */
    {"A", "A"},     {"B", "B"},       {"C", "RB"},    {"X", "X"},         {"Y", "Y"},         {"Z", "LB"},       {"L", "LT"},
    {"R", "RT"},    {"START", "START"}, {"UP", "UP"}, {"DOWN", "DOWN"}, {"LEFT", "LEFT"}, {"RIGHT", "RIGHT"},
#else
    {"A", "A"},     {"B", "B"},       {"C", "RT"},    {"X", "X"},         {"Y", "Y"},         {"Z", "LT"},       {"L", "LB"},
    {"R", "RB"},    {"START", "START"}, {"UP", "UP"}, {"DOWN", "DOWN"}, {"LEFT", "LEFT"}, {"RIGHT", "RIGHT"},
#endif
};
static const u32 k_bits[] = {B_A, B_B, B_C, B_X, B_Y, B_Z, B_L, B_R, B_START, B_UP, B_DOWN, B_LEFT, B_RIGHT};

#define N_ACTIONS (sizeof k_actions / sizeof k_actions[0])

/* Put the game's own button `action` on the host button `host`: the action that had `host` takes the host button `action`
 * had, so every Saturn button keeps one (none dropped, none doubled). An unknown name changes nothing. */
static void sat_layout_put(PortAction *t, unsigned n, const char *action, const char *host)
{
    unsigned i, a = n, h = n;
    const char *was;
    for (i = 0; i < n; i++) {
        if (!strcmp(t[i].name, action)) a = i;
        if (!strcmp(t[i].dflt, host)) h = i;
    }
    if (a == n || h == n) return;
    was = t[a].dflt, t[a].dflt = t[h].dflt, t[h].dflt = was;
}

/* game.json "confirm" and "cancel" (sat_game.h SAT_CONFIRM, SAT_CANCEL; AutoOnboard's menu probe finds them): the game's
 * own move-on and back-out buttons on the host's A and B (keyboard Z and X through the game's "keys"), so host A always
 * confirms and host B always cancels. Without them the table above as it is. */
static void sat_layout(PortAction *t, unsigned n, const char *confirm, const char *cancel)
{
    if (confirm) sat_layout_put(t, n, confirm, "A");
    if (cancel) sat_layout_put(t, n, cancel, "B");
}

#ifndef SAT_CONFIRM
#define SAT_CONFIRM NULL
#endif
#ifndef SAT_CANCEL
#define SAT_CANCEL NULL
#endif

const PortAction *console_actions(unsigned *count)
{
    static PortAction t[N_ACTIONS];
    static int made;
    if (!made) {
        memcpy(t, k_actions, sizeof t);
        sat_layout(t, (unsigned)N_ACTIONS, SAT_CONFIRM, SAT_CANCEL);
        made = 1;
    }
    *count = (unsigned)N_ACTIONS;
    return t;
}

/* the Saturn buttons held on one pad (1 = pressed) */
u32 sat_pad_word(const HostPad *pad)
{
    u32 held = host_actions(pad), b = 0;
    for (unsigned i = 0; i < sizeof k_bits / sizeof k_bits[0]; i++)
        if (held & (1u << i)) b |= k_bits[i];
    return b;
}

/* the pads as the SMPC reports them: byte 0 (R L D U Start A C B) then byte 1 (R X Y Z L 1 1 1), a pressed button reading 0.
 * Returns bit p: port p + 1 has a pad (port 1 always). Robo Pit opens VS MODE only with a pad in port 2. In online play
 * both come from sat_net.c: this frame's pads as the host decided them, port 2 the other player's. */
static u32 g_seat[4] = {0xffffu, 0xffffu, 0xffffu, 0xffffu}, g_seats = 1u;
u32 sat_pad_data(u32 data[2])
{
    HostPad pad[HOST_PADS];
    u32 there;
    if (sat_net_on()) return sat_net_pads(data);
    there = host_pads_read(pad) | 1u;
    for (u32 p = 0; p < 4; p++) g_seat[p] = (~sat_pad_word(&pad[p]) & 0xffffu) | 0x0700u;
    data[0] = g_seat[0], data[1] = g_seat[1], g_seats = there;
    sat_game_pad(data); /* the game's own change to what it reads (sat_hooks.h), after the seats kept the host's */
    return there;
}

/* all four pads as the last SMPC read took them, in sat_pad_data's form, for a team fight's seats (sat_team.c); bit p: pad p + 1 */
u32 sat_pad_seats(u32 data[4])
{
    for (u32 p = 0; p < 4; p++) data[p] = g_seat[p];
    return g_seats;
}

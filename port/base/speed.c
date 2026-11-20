/* Fast forward (base.h port_speed*; the user, 2026-11-17, about Dragon Force: "Button to accelerate the game."). While the
 * keyboard's Tab or the controller button the player chose (Input > Controls > Fast forward; R3 until then) is held, or
 * after one press with Game > Fast forward: toggle, a console that offers it (PORT_CONSOLE_SPEED) runs its game
 * port_speed() times as fast on the real clock: 2x, 3x (the default), 4x or as fast as the computer goes, chosen in
 * Game > Fast forward speed. Both are the base's own options (port_speed_options, game_options.c: the menu entries, the
 * settings keys fast-forward and fast-forward-toggle and their flags), live and never recorded; the button is
 * bind-fast-forward.
 *
 * The game's logic is not touched: the console runs its machine's own clock that much faster (port_speed_clock), so the
 * game sees the same time pass between two of its ticks as at normal speed, only sooner; more of its ticks then come
 * between two shown frames. Nothing of it is recorded (the button's bit in a pad is one the game does not read, Tab is no
 * pad's), and it is never on in a check, a replay, a fixed-clock run, online play or wherever the console refuses it
 * (port_speed_allow), so a replay plays exactly as one of a run without it.
 *
 * --fast-forward 2x|3x|4x|max, --fast-forward-toggle on|off (the options'), --fast-forward-test VB+N (a test aid: held from
 * vertical blank VB for N blanks, in a run where it is allowed). Each start and end is logged, with the blanks a second it
 * reached, which is how a run measures it.
 *
 * Nearest existing: clock.c host_fast_begin/host_fast_end (a stretch the player does not see, on the virtual clock) and the
 * Saturn kit's black-screen blanks (sat_sys.c sat_black_frame): both run blanks without waiting, but only where nothing is
 * seen or heard and without the player's say; this is the player's, at a chosen speed, with the picture shown. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "base.h"
#include "console.h"
#include "game_log.h"
#include "host.h"
#include "session.h"

static const u32 k_speeds[PORT_SPEED_CHOICES] = {2u, 3u, 4u, PORT_SPEED_FREE};
static const char *const k_words[PORT_SPEED_CHOICES] = {"2x", "3x", "4x", "max"};
static const char *const k_labels[PORT_SPEED_CHOICES] = {"&2x", "&3x", "&4x", "&As fast as it goes"};
static int offered(void) { return port_speed_offered(); }
static const PortOption k_options[] = {
    {"fast-forward", "Game", "Fast &forward speed (hold Tab or its button)", PORT_MENU_CHOICE, k_labels, k_words,
     PORT_SPEED_CHOICES, 1u, 1u, 0u, offered},
    {"fast-forward-toggle", "Game", "Fast forward: &toggle (one press on, the next off)", PORT_MENU_CHECK, NULL, NULL, 0u, 0u, 0u,
     0u, offered},
};
const PortOptions port_speed_options = {k_options, 2u}; /* the base's own (game_options.c lists them first) */
static int g_ix_choice = -1, g_ix_toggle = -1, g_was_toggle, g_allowed;
static volatile LONG g_key, g_pad, g_latched, g_gen;
static u32 g_test_from, g_test_n;
static int g_on;
static u32 g_on_vb;
static u64 g_on_us;
static const char *g_why = "";

static unsigned choice(void)
{
    int c;
    if (g_ix_choice < 0) g_ix_choice = port_option_find("fast-forward");
    c = port_option_at(g_ix_choice);
    return c >= 0 && c < (int)PORT_SPEED_CHOICES ? (unsigned)c : 1u;
}
static int toggled(void) /* a change in the menu lets go of a press latched before it */
{
    int t;
    if (g_ix_toggle < 0) g_ix_toggle = port_option_find("fast-forward-toggle");
    t = port_option_at(g_ix_toggle) == 1;
    if (t != g_was_toggle) g_was_toggle = t, g_latched = 0;
    return t;
}

int port_speed_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--fast-forward-test")) {
        char *end;
        g_test_from = (u32)strtoul(value, &end, 10);
        g_test_n = *end == '+' ? (u32)strtoul(end + 1, NULL, 10) : 600u;
        return 2;
    }
    return 0;
}

void port_speed_allow(int on)
{
    g_allowed = on && (port_console.flags & PORT_CONSOLE_SPEED) && !host_fixed_clock() && !session_replaying();
    port_log("speed: fast forward %s (%s, Tab or %s held%s)\n", g_allowed ? "offered" : "not offered in this run",
             k_words[choice()], port_speed_button(), toggled() ? ", toggled" : "");
}

int port_speed_offered(void) { return (port_console.flags & PORT_CONSOLE_SPEED) != 0; }

/* The controller's button as the player set it ("R3", "R3+BACK", "none"). */
const char *port_speed_button(void)
{
    static char text[64];
    return input_bind_text(INPUT_BIND_SPEED, text, sizeof text);
}

static void press(volatile LONG *flag, int down, const char *why)
{
    int was = g_key || g_pad;
    if ((*flag != 0) == (down != 0)) return;
    *flag = down != 0;
    if (down) g_why = why;
    if (toggled() && down && !was) g_latched = !g_latched;
}

void port_speed_key(int down) { press(&g_key, down, "Tab"); }

void port_speed_pads(const HostPad *pad, u32 n)
{
    int held = 0;
    for (u32 p = 0; p < n && !held; p++) held = input_bind_held(INPUT_BIND_SPEED, &pad[p]);
    press(&g_pad, held, "the controller");
}

static void changed(int on, u32 vb)
{
    u64 us = host_ticks(1000000u);
    g_on = on;
    InterlockedIncrement(&g_gen);
    if (on) {
        g_on_vb = vb, g_on_us = us;
        port_log("speed: fast forward on at vertical blank %u (%s, %s)\n", vb, k_words[choice()], g_test_n ? "--fast-forward-test" : g_why);
        game_log_line("speed: fast forward on (%s, %s)", k_words[choice()], g_test_n ? "--fast-forward-test" : g_why);
    } else {
        double ms = (double)(us - g_on_us) / 1000.0, rate = ms > 0 ? (vb - g_on_vb) * 1000.0 / ms : 0;
        port_log("speed: fast forward off at vertical blank %u: %u vertical blanks in %.0f ms, %.1f a second (%.2f times the game's %u)\n",
                 vb, vb - g_on_vb, ms, rate, rate / (port_console.tick_hz ? port_console.tick_hz : 60u), port_console.tick_hz);
        game_log_line("speed: fast forward off (%u vertical blanks in %.0f ms)", vb - g_on_vb, ms);
    }
}

u32 port_speed(void)
{
    u32 vb;
    unsigned c;
    int on, t;
    static unsigned shown = 1u;
    if (!g_allowed) return 1u;
    vb = port_vblank_count();
    t = toggled(), c = choice();
    if (c != shown) shown = c, InterlockedIncrement(&g_gen); /* the title bar's label */
    on = t ? g_latched != 0 : (g_key || g_pad);
    if (g_test_n && vb >= g_test_from && vb - g_test_from < g_test_n) on = 1;
    if (on != g_on) changed(on, vb);
    return on ? k_speeds[c] : 1u;
}

long port_speed_gen(void) { return g_gen; }

const char *port_speed_label(void)
{
    if (!g_on) return "";
    switch (choice()) {
    case 0: return "Fast forward 2x";
    case 1: return "Fast forward 3x";
    case 2: return "Fast forward 4x";
    default: return "Fast forward (as fast as it goes)";
    }
}

/* The machine's time from the real clock's: at speed 1 the real time exactly (ahead stays where it was, 0 in a run that
 * never sped up), at N the time since the last read counted N times; PORT_SPEED_FREE moves at 1x here, and its kit jumps
 * the clock to the time it would have waited for (c->ahead += the wait). */
s64 port_speed_clock(PortSpeedClock *c, s64 now, u32 speed)
{
    if (c->last && speed > 1u && speed != PORT_SPEED_FREE && now > c->last) c->ahead += (now - c->last) * (s64)(speed - 1u);
    c->last = now;
    return now + c->ahead;
}

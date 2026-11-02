/* Whether a run reads the real keyboard and gamepads ("live input"). A run that plays by itself must not: a key the
 * user types in another window, or a pad on the desk, would reach the game and change what the run shows.
 *   live input is OFF for: --input (scripted presses), --replay, --lockstep, --headless, --no-window,
 *                          --fixed-clock other than 0, --fast-until
 *   live input is ON for every other run (a person playing)
 *   --live-input on|off   decides it either way: "on" to watch a replay or a scripted run and play on
 * With it off the keyboard and the SDL gamepads are never read (even a pad SDL would deliver while another window
 * has the focus), and a window the run opens cannot take the focus (host_window.cpp), so only scripted presses and
 * the replay's pad reach the game. main.c asks this module first about every argument; it never consumes one
 * except --live-input, so the options keep their other readers. */
#include <string.h>
#include "base.h"
#include "console.h"

static int g_override = -1; /* --live-input: 1 on, 0 off, -1 not given */
static const char *g_scripted; /* the first argument that makes this a run that plays by itself */

int input_live_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--live-input") && value) {
        g_override = strcmp(value, "off") != 0;
        return 2;
    }
    if (!strcmp(arg, "--input") || !strcmp(arg, "--replay") || !strcmp(arg, "--headless") ||
        !strcmp(arg, "--no-window") || !strcmp(arg, "--fast-until") || !strncmp(arg, "--lockstep", 10) ||
        (!strcmp(arg, "--fixed-clock") && value && strcmp(value, "0") != 0)) {
        if (!g_scripted) g_scripted = arg;
    }
    return 0;
}

/* The answer, logged once with its reason: a run's log shows whether the real devices could reach it. */
int input_live(void)
{
    static int said;
    int live = g_override >= 0 ? g_override : !g_scripted;
    if (!said++)
        port_log("input: real keyboard and gamepads %s (%s)\n", live ? "read" : "not read",
                 g_override >= 0 ? "--live-input" : g_scripted ? g_scripted : "a run a person plays");
    return live;
}

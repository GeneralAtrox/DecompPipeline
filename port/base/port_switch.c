/* A port switch, for a change a recording would show (a screen drawn or moved differently): --NAME on|off (or the
 * settings' NAME=off) when asked; otherwise on in play and off in a check run (--lockstep, --fixed-clock), and a replay as
 * its recording was made, by the session mark NAME, so a recording from before the change replays with it off. base.h
 * port_switch. Nearest existing: a game's own menu switch with the same rule; this is the kit's, for any game. */
#include <string.h>
#include "base.h"
#include "session.h"

int port_switch(const char *name, const char *label)
{
    int argc, asked = -1, check = 0, on;
    char *const *argv = host_args(&argc);
    for (int i = 1; i < argc; i++) {
        if (!strncmp(argv[i], "--lockstep", 10) || !strcmp(argv[i], "--fixed-clock")) check = 1;
        else if (!strncmp(argv[i], "--", 2) && !strcmp(argv[i] + 2, name) && i + 1 < argc) asked = strcmp(argv[i + 1], "off") != 0;
    }
    on = asked >= 0 ? asked : session_replaying() ? session_marked(name) : !check;
    if (on) session_mark(name);
    port_log("%s: %s\n", label, on ? "on" : "off");
    return on;
}

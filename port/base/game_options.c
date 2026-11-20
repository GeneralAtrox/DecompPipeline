/* Per-game options (console.h PortOption): a game declares its options in one table in its own code (and the base and a
 * console kit theirs, the same way), and the base gives each one everything else:
 *   - its menu entry (a check or a choice, in the menu the table names: host_menu_extra.cpp adds them to the extras, so
 *     the plain menu and the Phangrade title bar both show them, greyed while available() says no),
 *   - its settings key (the id: settings.txt "id=word", written when the menu changes it, read back at the next start),
 *   - its command-line flag (--id word; the menu's words: on/off for a check, values[] for a choice),
 *   - its note in the session recording (PORT_OPTION_NOTE: "*id", one byte, kept only when the value is not the
 *     original's, so a replay does what the recording did and a recording made before the option replays the original),
 *   - and its value (port_option, port_option_at).
 * So a feature writes no option parsing, menu entry, settings key or note of its own.
 *
 * Two kinds of option:
 *   - a noted option (PORT_OPTION_NOTE) is fixed at the run's start (port_options_start; or at the first port_option
 *     call if a kit does not call it) and a menu change applies from the next start. Its value: the recording's note in a
 *     replay (a recording without it: the original); else the flag or setting when one was given; else the default in a
 *     play run (an unscripted run of the player's: an improvement is on there) and the original everywhere else (checks,
 *     scripted runs, --det), so the goldens and the judge never see an improvement they did not ask for.
 *   - a live option (no flag) changes as soon as it is picked, and is never recorded: it must not change what the game
 *     does (fast forward's speed, base/speed.c).
 * `original` is the value that is the original game exactly: a game's code does nothing at all for it.
 *
 * A table: `const PortOptions port_game_options = {k_items, N};` in the game's code (port_console_options in a console
 * kit's); a build without one links an empty table (/alternatename, as runtime/overlay.c does).
 *
 * Nearest existing: menu_data.c (game.json menu_choices: choices a game reads from the settings itself, built only by
 * gen_port.py's kits: no flag, no note, no play-run default) and autoload.c (one start-time choice by hand: its flag, play
 * run default and note - the pattern made general here). */
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"
#include "session.h"
#include "game_log.h"

const PortOptions port_options_none = {NULL, 0u};
#if defined(_M_IX86)
#pragma comment(linker, "/alternatename:_port_game_options=_port_options_none")
#pragma comment(linker, "/alternatename:_port_console_options=_port_options_none")
#pragma comment(linker, "/alternatename:_port_state_options=_port_options_none") /* a build without save_state_slots.c */
#else
#pragma comment(linker, "/alternatename:port_game_options=port_options_none")
#pragma comment(linker, "/alternatename:port_console_options=port_options_none")
#pragma comment(linker, "/alternatename:port_state_options=port_options_none")
#endif
extern const PortOptions port_speed_options; /* the base's own (speed.c) */
extern const PortOptions port_state_options; /* the base's own (save_state_slots.c) */

#define MAX_OPTIONS 32
static const char *const k_onoff[2] = {"off", "on"};
static const PortOption *g_opt[MAX_OPTIONS];
static int g_want[MAX_OPTIONS], g_value[MAX_OPTIONS]; /* g_want: -1 not given; g_value: a noted option's value this run */
static unsigned g_n;
static int g_listed, g_started;

static void list(void)
{
    const PortOptions *tables[4] = {&port_speed_options, &port_state_options, &port_console_options, &port_game_options};
    if (g_listed) return;
    g_listed = 1;
    for (unsigned t = 0; t < 4; t++)
        for (unsigned i = 0; tables[t]->items && i < tables[t]->n; i++) {
            if (g_n == MAX_OPTIONS) {
                port_log("options: more than %d, %s left out\n", MAX_OPTIONS, tables[t]->items[i].id);
                continue;
            }
            g_opt[g_n] = &tables[t]->items[i], g_want[g_n] = -1, g_value[g_n] = -1, g_n++;
        }
}

static unsigned count(const PortOption *o) { return o->kind == PORT_MENU_CHECK ? 2u : o->n; }
static const char *word(const PortOption *o, unsigned v) { return o->kind == PORT_MENU_CHECK ? k_onoff[v & 1u] : o->values[v]; }

int port_option_find(const char *id)
{
    list();
    for (unsigned i = 0; id && i < g_n; i++)
        if (!strcmp(g_opt[i]->id, id)) return (int)i;
    return -1;
}

/* --ID WORD: a check takes on/off (1/0), a choice one of its values; 2 taken (a bad word is logged), 0 not an option's */
int port_options_option(const char *arg, const char *value)
{
    int i;
    if (strncmp(arg, "--", 2) || (i = port_option_find(arg + 2)) < 0) return 0;
    if (!value) return 1;
    for (unsigned v = 0; v < count(g_opt[i]); v++)
        if (!strcmp(value, word(g_opt[i], v)) || (g_opt[i]->kind == PORT_MENU_CHECK && !strcmp(value, v ? "1" : "0"))) {
            g_want[i] = (int)v;
            return 2;
        }
    port_log("options: %s %s: not one of its words\n", arg, value);
    return 2;
}

void port_options_start(int play_run)
{
    list();
    g_started = 1;
    for (unsigned i = 0; i < g_n; i++) {
        const PortOption *o = g_opt[i];
        char note[48];
        u8 v = 0;
        const char *why;
        if (!(o->flags & PORT_OPTION_NOTE)) continue;
        snprintf(note, sizeof note, "*%s", o->id);
        if (session_note_replayed(note, &v, 1)) why = "the recording's";
        else if (session_replaying()) v = (u8)o->original, why = "the original: a recording without it";
        else if (g_want[i] >= 0) v = (u8)g_want[i], why = "asked";
        else if (play_run && !input_scripted()) v = (u8)o->dflt, why = "a play run's default";
        else v = (u8)o->original, why = "the original: not a play run";
        if (v >= count(o)) v = (u8)o->original;
        g_value[i] = v;
        if (v != o->original) session_save_read(note, &v, 1, 1);
        port_log("options: %s = %s (%s)\n", o->id, word(o, v), why);
    }
}

int port_option_at(int i)
{
    if (i < 0 || (unsigned)i >= g_n) return -1;
    if (!(g_opt[i]->flags & PORT_OPTION_NOTE)) return g_want[i] >= 0 ? g_want[i] : (int)g_opt[i]->dflt;
    if (!g_started) port_options_start(!host_fixed_clock()); /* a kit that does not call it: its play run as far as the base knows */
    return g_value[i];
}

int port_option(const char *id) { return port_option_at(port_option_find(id)); }

/* ---- the menu: one entry per option (host_menu_extra.cpp) ---- */
static int get(unsigned i)
{
    const PortOption *o = g_opt[i];
    if (!(o->flags & PORT_OPTION_NOTE)) return port_option_at((int)i);
    return g_want[i] >= 0 ? g_want[i] : (int)o->dflt; /* what the next start takes */
}
static void set(unsigned i, int v)
{
    const PortOption *o = g_opt[i];
    if (v < 0 || (unsigned)v >= count(o)) return;
    g_want[i] = v;
    game_log_line("menu: %s = %s%s", o->id, word(o, (unsigned)v), o->flags & PORT_OPTION_NOTE ? " (from the next start)" : "");
}
#define OPT_FNS(n)                                                                                                     \
    static int get##n(void) { return get(n##u); }                                                                     \
    static void set##n(int v) { set(n##u, v); }
OPT_FNS(0) OPT_FNS(1) OPT_FNS(2) OPT_FNS(3) OPT_FNS(4) OPT_FNS(5) OPT_FNS(6) OPT_FNS(7)
OPT_FNS(8) OPT_FNS(9) OPT_FNS(10) OPT_FNS(11) OPT_FNS(12) OPT_FNS(13) OPT_FNS(14) OPT_FNS(15)
OPT_FNS(16) OPT_FNS(17) OPT_FNS(18) OPT_FNS(19) OPT_FNS(20) OPT_FNS(21) OPT_FNS(22) OPT_FNS(23)
OPT_FNS(24) OPT_FNS(25) OPT_FNS(26) OPT_FNS(27) OPT_FNS(28) OPT_FNS(29) OPT_FNS(30) OPT_FNS(31)
static int (*const k_get[MAX_OPTIONS])(void) = {get0,  get1,  get2,  get3,  get4,  get5,  get6,  get7,  get8,  get9,  get10,
                                                get11, get12, get13, get14, get15, get16, get17, get18, get19, get20, get21,
                                                get22, get23, get24, get25, get26, get27, get28, get29, get30, get31};
static void (*const k_set[MAX_OPTIONS])(int) = {set0,  set1,  set2,  set3,  set4,  set5,  set6,  set7,  set8,  set9,  set10,
                                                set11, set12, set13, set14, set15, set16, set17, set18, set19, set20, set21,
                                                set22, set23, set24, set25, set26, set27, set28, set29, set30, set31};

const PortMenuItem *port_options_menu(unsigned *n)
{
    static PortMenuItem items[MAX_OPTIONS];
    list();
    for (unsigned i = 0; i < g_n; i++) {
        const PortOption *o = g_opt[i];
        PortMenuItem e = {o->menu, o->label, o->kind, o->id, k_get[i], k_set[i], o->choices, o->values, o->n, o->available, 0};
        items[i] = e;
    }
    *n = g_n;
    return items;
}

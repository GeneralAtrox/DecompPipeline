/* A game's own menu-bar entries as data (game.json "menu_choices" and "extra_buttons"): the base builds them and serves
 * them as game_menu_extra (console.h), so a game whose entries are choices kept in its settings writes no menu code.
 * game_config.py refuses these keys together with "game_menu": true (a game that defines game_menu_extra itself).
 *
 * menu_choices: each entry is one choice in the menu it names, kept as the settings file's game.NAME (one of its "values"
 * words). The game reads it with port_setting_get_int or port_setting_get_str at its own time, so a choice applies from
 * the game's next read ("from the next start" in a label says when that is). A choice writes the file at once, so the
 * game's own screens that change the same setting and the menu always agree. A run with no settings file (a scripted
 * run) shows the entry's default, or what was picked in that run, and the game reads its default.
 *
 * extra_buttons: Input > Extra buttons for a game with no key screen of its own: a player, then one entry per modern button
 * (LB, RB, the stick clicks, Back, Guide, the right stick's four directions), each taking one of the game's actions or none.
 * The choices are input_ex_assign.c's (game.padex.p<N>.<row>, in the recording's "padex" note), so what a player assigned
 * before still loads. The game loads them (port_pad_ex_assign_load) and turns them into its actions
 * (port_pad_ex_assign_codes); until it has loaded them, a choice goes only into the settings file, which the load reads,
 * so the menu never writes the recording from the window thread.
 *
 * menu_commands: the game's own commands, each with its tag (console.h PortMenuItem tag): a pick goes to the game as
 * (tag, index) at its next tick (native_game.h pg_native_menu_take), recorded. Without a list a single command; with
 * one a submenu of its labels; "runtime" the list the game gives (pg_native_menu_list). */
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "game_log.h"
#if __has_include("port_config.h")
#include "port_config.h"
#endif
#ifndef PORT_MENU_DATA
#define PORT_MENU_DATA 0
#endif

#if PORT_MENU_DATA

#define MAX_CHOICES 8 /* play_keys.py's limit */

/* the label without its '&', for the log */
static const char *plain(const char *label)
{
    static char out[96];
    unsigned n = 0;
    for (; *label && n + 1 < sizeof out; label++)
        if (*label != '&') out[n++] = *label;
    out[n] = 0;
    return out;
}

#ifdef PORT_MENU_CHOICES
typedef struct MenuChoice {
    const char *menu, *label, *setting;
    unsigned n, dflt;
    const char *choices[16], *values[16];
} MenuChoice;
static const MenuChoice k_choices[] = {PORT_MENU_CHOICES};
enum { N_CHOICES = (int)(sizeof k_choices / sizeof k_choices[0]) };
static int g_picked[MAX_CHOICES]; /* picked in this run, + 1; 0 none */

static int choice_get(int i)
{
    const MenuChoice *c = &k_choices[i];
    char v[64];
    if (port_setting_get_str(c->setting, v, (int)sizeof v))
        for (unsigned n = 0; n < c->n; n++)
            if (!strcmp(v, c->values[n])) return (int)n;
    return g_picked[i] ? g_picked[i] - 1 : (int)c->dflt;
}
static void choice_set(int i, int value)
{
    const MenuChoice *c = &k_choices[i];
    if (value < 0 || (unsigned)value >= c->n) return;
    g_picked[i] = value + 1;
    port_setting_set_str(c->setting, c->values[value]);
    game_log_line("menu: %s = %s (%s)", c->setting, c->values[value], plain(c->label));
}
#define CHOICE_FNS(n)                                                                                                  \
    static int choice_get##n(void) { return choice_get(n); }                                                          \
    static void choice_set##n(int v) { choice_set(n, v); }
CHOICE_FNS(0)
CHOICE_FNS(1)
CHOICE_FNS(2)
CHOICE_FNS(3)
CHOICE_FNS(4)
CHOICE_FNS(5)
CHOICE_FNS(6)
CHOICE_FNS(7)
static int (*const k_choice_get[MAX_CHOICES])(void) = {choice_get0, choice_get1, choice_get2, choice_get3,
                                                       choice_get4, choice_get5, choice_get6, choice_get7};
static void (*const k_choice_set[MAX_CHOICES])(int) = {choice_set0, choice_set1, choice_set2, choice_set3,
                                                       choice_set4, choice_set5, choice_set6, choice_set7};
#else
enum { N_CHOICES = 0 };
#endif

#ifdef PORT_MENU_COMMANDS
typedef struct MenuCommand {
    const char *menu, *label;
    int tag, list; /* list: 0 a command, 1 its labels (k_command_labels[first..]), 2 the game's runtime list */
    unsigned first, n;
} MenuCommand;
static const MenuCommand k_commands[] = {PORT_MENU_COMMANDS};
#ifdef PORT_MENU_COMMAND_LABELS
static const char *const k_command_labels[] = {PORT_MENU_COMMAND_LABELS};
#else
static const char *const k_command_labels[1] = {NULL};
#endif
enum { N_COMMANDS = (int)(sizeof k_commands / sizeof k_commands[0]) };
#else
enum { N_COMMANDS = 0 };
#endif

#ifdef PORT_EXTRA_BUTTONS
#ifndef PORT_EXTRA_BUTTONS_PLAYERS
#define PORT_EXTRA_BUTTONS_PLAYERS 4
#endif
#ifndef PORT_EXTRA_BUTTONS_MENU
#define PORT_EXTRA_BUTTONS_MENU "Input"
#endif
#define ROWS 10u /* the modern buttons before Share */
static const char *const k_actions[] = {"&Not used", PORT_EXTRA_BUTTONS};
enum { N_ACTIONS = (int)(sizeof k_actions / sizeof k_actions[0]) };
static const char *const k_numbers[17] = {"0", "1", "2",  "3",  "4",  "5",  "6",  "7", "8",
                                          "9", "10", "11", "12", "13", "14", "15", "16"};
static const char *const k_players[8] = {"Player &1", "Player &2", "Player &3", "Player &4",
                                         "Player &5", "Player &6", "Player &7", "Player &8"};
static const char *const k_row_labels[ROWS] = {
    "Extra button: LB",
    "Extra button: RB",
    "Extra button: left stick click",
    "Extra button: right stick click",
    "Extra button: Back",
    "Extra button: Guide",
    "Extra button: right stick up",
    "Extra button: right stick down",
    "Extra button: right stick left",
    "Extra button: right stick right",
};
static int g_player; /* the menu's player */
static char g_row_setting[ROWS][32];

static void row_settings(void)
{
    for (unsigned r = 0; r < ROWS; r++)
        snprintf(g_row_setting[r], sizeof g_row_setting[r], "game.padex.p%d.%s", g_player + 1, port_pad_ex_row_key(r));
}
static int player_get(void) { return g_player; }
static void player_set(int value)
{
    g_player = value >= 0 && value < PORT_EXTRA_BUTTONS_PLAYERS ? value : 0;
    row_settings();
}
static int row_get(unsigned r)
{
    int a = port_pad_ex_assign_loaded() ? port_pad_ex_assign_get(g_player, r) : port_setting_get_int(g_row_setting[r], 0);
    return a >= 0 && a < N_ACTIONS ? a : 0;
}
static void row_set(unsigned r, int action)
{
    if (action < 0 || action >= N_ACTIONS) return;
    if (port_pad_ex_assign_loaded()) port_pad_ex_assign_set(g_player, r, action);
    else port_setting_set_int(g_row_setting[r], action);
    game_log_line("extra buttons: P%d %s = %s (menu)", g_player + 1, port_pad_ex_row_key(r), plain(k_actions[action]));
}
#define ROW_FNS(n)                                                                                                     \
    static int row_get##n(void) { return row_get(n##u); }                                                             \
    static void row_set##n(int v) { row_set(n##u, v); }
ROW_FNS(0)
ROW_FNS(1)
ROW_FNS(2)
ROW_FNS(3)
ROW_FNS(4)
ROW_FNS(5)
ROW_FNS(6)
ROW_FNS(7)
ROW_FNS(8)
ROW_FNS(9)
static int (*const k_row_get[ROWS])(void) = {row_get0, row_get1, row_get2, row_get3, row_get4,
                                             row_get5, row_get6, row_get7, row_get8, row_get9};
static void (*const k_row_set[ROWS])(int) = {row_set0, row_set1, row_set2, row_set3, row_set4,
                                             row_set5, row_set6, row_set7, row_set8, row_set9};
#define N_BUTTON_ITEMS (1u + ROWS)
#else
#define N_BUTTON_ITEMS 0u
#endif

const PortMenuItem *game_menu_extra(unsigned *count)
{
    static PortMenuItem menu[N_CHOICES + N_BUTTON_ITEMS + N_COMMANDS + 1u];
    unsigned n = 0;
#ifdef PORT_MENU_CHOICES
    for (int i = 0; i < N_CHOICES && i < MAX_CHOICES; i++) {
        const MenuChoice *c = &k_choices[i];
        PortMenuItem e = {c->menu, c->label, PORT_MENU_CHOICE, c->setting, k_choice_get[i], k_choice_set[i],
                          c->choices, c->values, c->n, NULL};
        menu[n++] = e;
    }
#endif
#ifdef PORT_EXTRA_BUTTONS
    {
        PortMenuItem p = {PORT_EXTRA_BUTTONS_MENU, "Extra buttons: pla&yer", PORT_MENU_CHOICE, "game.padex.menu_player",
                          player_get, player_set, k_players, k_numbers + 1, PORT_EXTRA_BUTTONS_PLAYERS, NULL};
        row_settings();
        menu[n++] = p;
        for (unsigned r = 0; r < ROWS; r++) {
            PortMenuItem e = {PORT_EXTRA_BUTTONS_MENU, k_row_labels[r], PORT_MENU_CHOICE, g_row_setting[r], k_row_get[r],
                              k_row_set[r], k_actions, k_numbers, (unsigned)N_ACTIONS, NULL};
            menu[n++] = e;
        }
    }
#endif
#ifdef PORT_MENU_COMMANDS
    for (int i = 0; i < N_COMMANDS; i++) {
        const MenuCommand *c = &k_commands[i];
        PortMenuItem e = {c->menu, c->label, c->list ? PORT_MENU_SUBMENU : PORT_MENU_COMMAND, NULL, NULL, NULL,
                          c->list == 1 ? k_command_labels + c->first : NULL, NULL, c->n, NULL, c->tag};
        menu[n++] = e;
    }
#endif
    *count = n;
    return menu;
}

#endif /* PORT_MENU_DATA */

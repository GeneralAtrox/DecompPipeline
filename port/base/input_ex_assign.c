/* A game's own key screen gives the modern buttons its own actions: the rows, what each player chose, and the settings
 * they stay in, apart from the game's save (game.padex.p<player>.<row>=<action>, 0 or no line: unassigned; base.h
 * port_pad_ex_assign_*). Split from input_ex.c, which says what is held. The action numbers are the game's.
 *
 * A recording keeps the player's pad as it was, so a replay needs the choices too: a run that has any writes them into
 * the recording with the save files it read, under the name below, and a replay takes them from there and not from any
 * settings file. A run with none adds nothing, so its recording is unchanged. Players 1..4's are that entry as it always
 * was; players 5..8's follow under a second name only when one of them has any. The last row is no button: the left stick
 * as movement (pad_stick.c), 1 when chosen, kept and recorded with the rest. game.json "left_stick": "on" makes that the
 * choice until a player changes it.
 *
 * Share came later (row PORT_PAD_EX_SHARE_ROW). The first record keeps its size and order (the ten buttons, then the left
 * stick), so every recording made before still loads; Share has a record of its own after it, written only when a player
 * has one. The row is offered behind the switch pad-share (port_switch: on in play, off in a check run, and a replay as
 * it was made), so a key screen's page has the rows it had in a recording from before. */
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"

#define RECORD_NAME "padex"  /* the first rows: the ten buttons, then the left stick */
#define SHARE_RECORD "padsh" /* Share's, a byte a player */
#define FIRST_ROWS 11u
#define RECORD_MORE "padex5" /* players 5..8: their rows whole */
#if __has_include("port_config.h")
#include "port_config.h" /* game.json left_stick */
#endif
#ifndef PORT_LEFT_STICK
#define PORT_LEFT_STICK 0 /* 0 not offered, 1 a choice, off until picked, 2 a choice, on until changed */
#endif
#define ALL_ROWS (PORT_PAD_EX_ROWS + 1u)

static const struct {
    u32 bit;
    const char *key;
} k_rows[ALL_ROWS] = {
    {HOST_PAD_LB, "lb"},       {HOST_PAD_RB, "rb"},           {HOST_PAD_L3, "l3"},           {HOST_PAD_R3, "r3"},
    {HOST_PAD_BACK, "back"},   {HOST_PAD_GUIDE, "guide"},     {HOST_PAD_RS_UP, "rsup"},      {HOST_PAD_RS_DOWN, "rsdown"},
    {HOST_PAD_RS_LEFT, "rsleft"}, {HOST_PAD_RS_RIGHT, "rsright"}, {HOST_PAD_SHARE, "share"},
    {0u, "lsmove"},
};

static u8 g_action[HOST_PADS][ALL_ROWS];
static int g_share = -1;
static int g_loaded; /* port_pad_ex_assign_load has run */

int port_pad_ex_assign_loaded(void) { return g_loaded; }
const char *port_pad_ex_row_key(unsigned row) { return row < ALL_ROWS ? k_rows[row].key : ""; }

int port_pad_ex_share_on(void)
{
    if (g_share < 0) g_share = port_switch("pad-share", "input: the Share row");
    return g_share;
}

static void row_key(char *key, size_t cap, int player, unsigned row)
{
    snprintf(key, cap, "game.padex.p%d.%s", player + 1, k_rows[row].key);
}

/* A row's name on a game's key screen: the maker's for the pad in hand, in capitals, or a plain one when the style is the
 * console's own. The right stick's are plain on any pad, and short enough for a narrow panel. */
const char *port_pad_ex_row_name(unsigned row, int player)
{
    static const char *const plain[PORT_PAD_EX_ROWS] = {"LB", "RB", "LS", "RS", "BACK", "GUIDE", "RS UP", "RS DOWN", "RS LEFT", "RS RIGHT", "SHARE"};
    enum { NAMED_BY_PAD = 6, NAME_CAP = 16 };
    static char name[NAME_CAP];
    const char *from;
    if (row >= PORT_PAD_EX_ROWS) return "";
    from = (row < NAMED_BY_PAD || row == PORT_PAD_EX_SHARE_ROW) && pad_style() ? port_pad_ex_name(k_rows[row].bit, player) : plain[row];
    for (unsigned i = 0; i < NAME_CAP; i++) {
        name[i] = from[i] >= 'a' && from[i] <= 'z' ? (char)(from[i] - 'a' + 'A') : from[i];
        if (!from[i]) break;
    }
    name[NAME_CAP - 1] = 0;
    return name;
}

u32 port_pad_ex_row_bit(unsigned row) { return row < PORT_PAD_EX_ROWS ? k_rows[row].bit : 0u; }

void port_pad_ex_assign_load(unsigned actions)
{
    u8 first[HOST_GAME_PADS][FIRST_ROWS], share[HOST_GAME_PADS]; /* players 1..4: the entries as they always were */
    const u32 rest = (u32)(sizeof g_action[0] * (HOST_PADS - HOST_GAME_PADS));
    int any = 0, shared = 0, more = 0, offered = port_pad_ex_share_on(), got_first, got_share;
    memset(g_action, 0, sizeof g_action);
    g_loaded = 1;
    got_first = session_note_replayed(RECORD_NAME, first, sizeof first);
    got_share = session_note_replayed(SHARE_RECORD, share, sizeof share);
    if (got_first || got_share || session_replaying()) { /* a recording with none: every row unassigned */
        for (int p = 0; p < HOST_GAME_PADS; p++) {
            if (got_first) {
                memcpy(g_action[p], first[p], PORT_PAD_EX_SHARE_ROW);
                g_action[p][PORT_PAD_EX_STICK_ROW] = first[p][FIRST_ROWS - 1u];
            }
            if (got_share) g_action[p][PORT_PAD_EX_SHARE_ROW] = share[p];
        }
        if (got_first) session_note_replayed(RECORD_MORE, g_action[HOST_GAME_PADS], rest);
        return;
    }
    for (int p = 0; p < HOST_PADS; p++)
        for (unsigned r = 0; r < ALL_ROWS; r++) {
            char key[48];
            int action, stick = r == PORT_PAD_EX_STICK_ROW;
            if (r == PORT_PAD_EX_SHARE_ROW && !offered) continue;
            row_key(key, sizeof key, p, r);
            action = port_setting_get_int(key, stick && PORT_LEFT_STICK == 2 ? 1 : 0);
            if (stick && !PORT_LEFT_STICK) action = 0;
            g_action[p][r] = (u8)(action >= 0 && (unsigned)action <= (stick ? 1u : actions) ? action : 0);
            if (p >= HOST_GAME_PADS) more |= g_action[p][r];
        }
    for (int p = 0; p < HOST_GAME_PADS; p++) {
        memcpy(first[p], g_action[p], PORT_PAD_EX_SHARE_ROW);
        first[p][FIRST_ROWS - 1u] = g_action[p][PORT_PAD_EX_STICK_ROW];
        share[p] = g_action[p][PORT_PAD_EX_SHARE_ROW];
        for (unsigned r = 0; r < FIRST_ROWS; r++) any |= first[p][r];
        shared |= share[p];
    }
    if (any || more) session_save_read(RECORD_NAME, first, sizeof first, 1);
    if (shared) session_save_read(SHARE_RECORD, share, sizeof share, 1);
    if (more) session_save_read(RECORD_MORE, g_action[HOST_GAME_PADS], rest, 1);
}

int port_pad_ex_assign_get(int player, unsigned row)
{
    return player >= 0 && player < HOST_PADS && row < ALL_ROWS ? g_action[player][row] : 0;
}

void port_pad_ex_assign_set(int player, unsigned row, int action)
{
    char key[48];
    if (player < 0 || player >= HOST_PADS || row >= ALL_ROWS || action < 0 || g_action[player][row] == action) return;
    g_action[player][row] = (u8)action;
    row_key(key, sizeof key, player, row);
    port_setting_set_int(key, action);
}

/* What the rows this player holds now press: the action codes of their actions, ORed. codes[i] is action i + 1's. */
u32 port_pad_ex_assign_codes(int player, const u32 *codes, unsigned actions)
{
    u32 held = port_pad_ex_held(player), out = 0;
    if (!held) return 0;
    for (unsigned r = 0; r < PORT_PAD_EX_ROWS; r++) {
        unsigned action = g_action[player][r];
        if (held & k_rows[r].bit && action && action <= actions) out |= codes[action - 1];
    }
    return out;
}

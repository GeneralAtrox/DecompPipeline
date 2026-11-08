/* A game's own key screen gives the modern buttons its own actions: the rows, what each player chose, and the settings
 * they stay in, apart from the game's save (game.padex.p<player>.<row>=<action>, 0 or no line: unassigned; base.h
 * port_pad_ex_assign_*). Split from input_ex.c, which says what is held. The action numbers are the game's.
 *
 * A recording keeps the player's pad as it was, so a replay needs the choices too: a run that has any writes them into
 * the recording with the save files it read, under the name below, and a replay takes them from there and not from any
 * settings file. A run with none adds nothing, so its recording is unchanged. The last row is no button: the left stick
 * as movement (pad_stick.c), 1 when chosen, kept and recorded with the rest. game.json "left_stick": "on" makes that the
 * choice until a player changes it. */
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"

#define RECORD_NAME "padex"
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
    {HOST_PAD_RS_LEFT, "rsleft"}, {HOST_PAD_RS_RIGHT, "rsright"},
    {0u, "lsmove"},
};

static u8 g_action[HOST_PADS][ALL_ROWS];

static void row_key(char *key, size_t cap, int player, unsigned row)
{
    snprintf(key, cap, "game.padex.p%d.%s", player + 1, k_rows[row].key);
}

/* A row's name on a game's key screen: the maker's for the pad in hand, in capitals, or a plain one when the style is the
 * console's own. The right stick's are plain on any pad, and short enough for a narrow panel. */
const char *port_pad_ex_row_name(unsigned row, int player)
{
    static const char *const plain[PORT_PAD_EX_ROWS] = {"LB", "RB", "LS", "RS", "BACK", "GUIDE", "RS UP", "RS DOWN", "RS LEFT", "RS RIGHT"};
    enum { NAMED_BY_PAD = 6, NAME_CAP = 16 };
    static char name[NAME_CAP];
    const char *from;
    if (row >= PORT_PAD_EX_ROWS) return "";
    from = row < NAMED_BY_PAD && pad_style() ? port_pad_ex_name(k_rows[row].bit, player) : plain[row];
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
    int any = 0;
    if (session_note_replayed(RECORD_NAME, g_action, sizeof g_action)) return;
    if (session_replaying()) return; /* a recording with none: every row unassigned */
    for (int p = 0; p < HOST_PADS; p++)
        for (unsigned r = 0; r < ALL_ROWS; r++) {
            char key[48];
            int action, stick = r == PORT_PAD_EX_STICK_ROW;
            row_key(key, sizeof key, p, r);
            action = port_setting_get_int(key, stick && PORT_LEFT_STICK == 2 ? 1 : 0);
            if (stick && !PORT_LEFT_STICK) action = 0;
            g_action[p][r] = (u8)(action >= 0 && (unsigned)action <= (stick ? 1u : actions) ? action : 0);
            any |= g_action[p][r];
        }
    if (any) session_save_read(RECORD_NAME, g_action, sizeof g_action, 1);
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

/* The page of a game's key screen that holds what its own rows have no place for: a row for each of the modern buttons,
 * then (game.json "left_stick") the left stick as movement. One player's page at a time per panel: what the rows show
 * while the page is open, moving along them, and keeping, cancelling and clearing them by the screen's own rules. They
 * stay in port settings, apart from the game's save (input_ex_assign.c). The game draws the page with its own font and
 * supplies its choices' words, its presses and when its screen opens, is left, cancels and resets; base.h port_pad_page_*.
 * Nearest existing: input_ex_assign.c (what is kept; this is the screen's working copy, and the names).
 *
 * The rows: the buttons, Share while it is offered (port_pad_ex_share_on), the left stick when the game has it. A page is
 * opened and closed by the game (Y, then Y or B); in a list (port_pad_page_list) the rows follow the screen's own instead:
 * the game opens them at the first row from its last, or at the last from the row after, and UP past the first, DOWN past
 * the last or BACK closes them, which port_pad_page_press says (PORT_PAD_PAGE_OUT_*) so the game moves its own cursor.
 * port_pad_list_top keeps the cursor's row in view when the list scrolls in a place shorter than it. */
#include <string.h>
#include "base.h"
#include "game_log.h"

#if __has_include("port_config.h")
#include "port_config.h" /* game.json left_stick */
#endif
#ifndef PORT_LEFT_STICK
#define PORT_LEFT_STICK 0
#endif

/* A pad with no maker's name, and the right stick on any pad: short enough for a panel. */
static const char *const k_names[PORT_PAD_EX_ROWS + 1u] = {"LB", "RB", "LS", "RS", "BACK", "GUIDE", "RS UP", "RS DOWN", "RS LEFT", "RS RIGHT", "SHARE", "L STICK"};
static const char *const k_stick_values[2] = {"OFF", "MOVES"};

static const char *const *g_choices;
static unsigned g_actions;
static u8 g_open[HOST_PADS], g_row[HOST_PADS], g_work[HOST_PADS][PORT_PAD_EX_ROWS + 1u];
static u8 g_kept_row[PORT_PAD_EX_ROWS + 1u], g_rows, g_list; /* the page's row i is the kept row g_kept_row[i] */

unsigned port_pad_page_rows(void)
{
    if (!g_rows)
        for (unsigned row = 0; row <= PORT_PAD_EX_ROWS; row++)
            if ((row != PORT_PAD_EX_SHARE_ROW || port_pad_ex_share_on()) && (row != PORT_PAD_EX_STICK_ROW || PORT_LEFT_STICK))
                g_kept_row[g_rows++] = (u8)row;
    return g_rows;
}

static unsigned kept_row(unsigned row) { return row < port_pad_page_rows() ? g_kept_row[row] : 0u; }

int port_pad_page_is_stick(unsigned row) { return row < port_pad_page_rows() && kept_row(row) == PORT_PAD_EX_STICK_ROW; }

void port_pad_page_list(int on) { g_list = (u8)(on != 0); }

unsigned port_pad_list_top(unsigned top, unsigned at, unsigned shown)
{
    return at < top ? at : shown && at >= top + shown ? at + 1u - shown : top;
}

static void take_kept(int player)
{
    for (unsigned row = 0; row <= PORT_PAD_EX_ROWS; row++) g_work[player][row] = (u8)port_pad_ex_assign_get(player, row);
}

static const char *value_of(unsigned row, unsigned value)
{
    return row == PORT_PAD_EX_STICK_ROW ? k_stick_values[value != 0u] : g_choices[value];
}

/* choices[0] is the word for no choice, choices[i] action i's. */
void port_pad_page_init(const char *const *choices, unsigned actions)
{
    g_choices = choices;
    g_actions = actions;
    port_pad_ex_assign_load(actions);
    port_pad_page_rows();
    for (int player = 0; player < HOST_PADS; player++)
        for (unsigned row = 0; row <= PORT_PAD_EX_ROWS; row++)
            if (port_pad_ex_assign_get(player, row))
                game_log_line("keyconfig: P%d %s = %s (saved)", player + 1, k_names[row], value_of(row, (unsigned)port_pad_ex_assign_get(player, row)));
}

void port_pad_page_enter(void)
{
    for (int player = 0; player < HOST_PADS; player++) {
        g_open[player] = 0;
        take_kept(player);
    }
}

int port_pad_page_showing(int player) { return player >= 0 && player < HOST_PADS && g_open[player]; }

void port_pad_page_open(int player)
{
    g_open[player] = 1;
    g_row[player] = 0;
    game_log_line("keyconfig: P%d shows the modern buttons", player + 1);
}

void port_pad_page_open_at(int player, unsigned row)
{
    unsigned rows = port_pad_page_rows();
    g_open[player] = 1;
    g_row[player] = (u8)(row < rows ? row : rows - 1u);
    game_log_line("keyconfig: P%d on %s", player + 1, k_names[kept_row(g_row[player])]);
}

unsigned port_pad_page_cursor(int player) { return g_row[player]; }

const char *port_pad_page_row_name(int player, unsigned row)
{
    unsigned kept = kept_row(row);
    return kept < PORT_PAD_EX_ROWS ? port_pad_ex_row_name(kept, player) : k_names[kept];
}

const char *port_pad_page_row_value(int player, unsigned row, int *set)
{
    unsigned kept = kept_row(row);
    *set = g_work[player][kept] != 0;
    return value_of(kept, g_work[player][kept]);
}

/* One player's presses; 1 if the page did something the game should sound, in a list PORT_PAD_PAGE_OUT_* when it left. */
int port_pad_page_press(int player, u32 press)
{
    unsigned rows = port_pad_page_rows();
    u8 *row = &g_row[player];
    unsigned kept = kept_row(*row), values = kept == PORT_PAD_EX_STICK_ROW ? 2u : g_actions + 1u;
    u8 *choice = &g_work[player][kept];
    if (press & PORT_PAD_PAGE_BACK) {
        g_open[player] = 0;
        game_log_line("keyconfig: P%d back to the panel", player + 1);
        if (g_list) return PORT_PAD_PAGE_OUT_UP;
    } else if (press & PORT_PAD_PAGE_DOWN) {
        if (g_list && *row + 1u >= rows) {
            g_open[player] = 0;
            game_log_line("keyconfig: P%d down past the modern buttons", player + 1);
            return PORT_PAD_PAGE_OUT_DOWN;
        }
        *row = (u8)((*row + 1u) % rows);
    } else if (press & PORT_PAD_PAGE_UP) {
        if (g_list && *row == 0) {
            g_open[player] = 0;
            game_log_line("keyconfig: P%d up to the panel's rows", player + 1);
            return PORT_PAD_PAGE_OUT_UP;
        }
        *row = (u8)((*row + rows - 1u) % rows);
    } else if (press & (PORT_PAD_PAGE_RIGHT | PORT_PAD_PAGE_OK)) {
        *choice = (u8)((*choice + 1u) % values);
    } else if (press & PORT_PAD_PAGE_LEFT) {
        *choice = (u8)((*choice + values - 1u) % values);
    } else {
        return 0;
    }
    if (press & (PORT_PAD_PAGE_RIGHT | PORT_PAD_PAGE_OK | PORT_PAD_PAGE_LEFT))
        game_log_line("keyconfig: P%d %s shows %s", player + 1, k_names[kept], value_of(kept, *choice));
    return 1;
}

void port_pad_page_reset(int player)
{
    memset(g_work[player], 0, sizeof g_work[player]);
    game_log_line("keyconfig: P%d RESET clears the modern buttons", player + 1);
}

void port_pad_page_cancel(int player)
{
    take_kept(player);
    game_log_line("keyconfig: P%d CANCEL puts them back", player + 1);
}

void port_pad_page_keep(void)
{
    for (int player = 0; player < HOST_PADS; player++)
        for (unsigned row = 0; row <= PORT_PAD_EX_ROWS; row++)
            if (port_pad_ex_assign_get(player, row) != g_work[player][row]) {
                port_pad_ex_assign_set(player, row, g_work[player][row]);
                game_log_line("keyconfig: P%d %s = %s (kept)", player + 1, k_names[row], value_of(row, g_work[player][row]));
            }
}

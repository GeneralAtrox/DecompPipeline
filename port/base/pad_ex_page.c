/* The page of a game's key screen that holds what its own rows have no place for: a row for each of the modern buttons,
 * then (game.json "left_stick") the left stick as movement. One player's page at a time per panel: what the rows show
 * while the page is open, moving along them, and keeping, cancelling and clearing them by the screen's own rules. They
 * stay in port settings, apart from the game's save (input_ex_assign.c). The game draws the page with its own font and
 * supplies its choices' words, its presses and when its screen opens, is left, cancels and resets; base.h port_pad_page_*.
 * Nearest existing: input_ex_assign.c (what is kept; this is the screen's working copy, and the names). */
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
static const char *const k_names[PORT_PAD_EX_ROWS + 1u] = {"LB", "RB", "LS", "RS", "BACK", "GUIDE", "RS UP", "RS DOWN", "RS LEFT", "RS RIGHT", "L STICK"};
static const char *const k_stick_values[2] = {"OFF", "MOVES"};

static const char *const *g_choices;
static unsigned g_actions;
static u8 g_open[HOST_PADS], g_row[HOST_PADS], g_work[HOST_PADS][PORT_PAD_EX_ROWS + 1u];

unsigned port_pad_page_rows(void) { return PORT_PAD_EX_ROWS + (PORT_LEFT_STICK ? 1u : 0u); }

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

unsigned port_pad_page_cursor(int player) { return g_row[player]; }

const char *port_pad_page_row_name(int player, unsigned row)
{
    return row < PORT_PAD_EX_ROWS ? port_pad_ex_row_name(row, player) : k_names[row];
}

const char *port_pad_page_row_value(int player, unsigned row, int *set)
{
    *set = g_work[player][row] != 0;
    return value_of(row, g_work[player][row]);
}

/* One player's presses; 1 if the page did something the game should sound. */
int port_pad_page_press(int player, u32 press)
{
    u8 *row = &g_row[player], *choice = &g_work[player][*row];
    unsigned rows = port_pad_page_rows(), values = *row == PORT_PAD_EX_STICK_ROW ? 2u : g_actions + 1u;
    if (press & PORT_PAD_PAGE_BACK) {
        g_open[player] = 0;
        game_log_line("keyconfig: P%d back to the panel", player + 1);
    } else if (press & PORT_PAD_PAGE_DOWN) {
        *row = (u8)((*row + 1u) % rows);
    } else if (press & PORT_PAD_PAGE_UP) {
        *row = (u8)((*row + rows - 1u) % rows);
    } else if (press & (PORT_PAD_PAGE_RIGHT | PORT_PAD_PAGE_OK)) {
        *choice = (u8)((*choice + 1u) % values);
    } else if (press & PORT_PAD_PAGE_LEFT) {
        *choice = (u8)((*choice + values - 1u) % values);
    } else {
        return 0;
    }
    if (press & (PORT_PAD_PAGE_RIGHT | PORT_PAD_PAGE_OK | PORT_PAD_PAGE_LEFT))
        game_log_line("keyconfig: P%d %s shows %s", player + 1, k_names[*row], value_of(*row, *choice));
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

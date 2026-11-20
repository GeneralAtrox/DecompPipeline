/* A co-op session: who plays and with what (game.json "coop": the most players, 2..4). The game opens the join screen
 * from one of its own screens, feeds it each pad's presses from the game's own input (so recordings and replays carry
 * them) and draws it with its own font from port_coop_join_line; the kit keeps who joined, what each picked and whether
 * each is ready. A session starts when the player who opened the screen confirms with everyone joined ready, and ends
 * when the game says so (port_coop_end). The game wires the players into its play; nothing here touches the game.
 *
 * The screen: the player who opened it is in. Another pad joins with OK and leaves with BACK. LEFT and RIGHT choose a
 * choice (a vehicle, say) no other player holds, unless the choices are shared (everyone may take the same one); OK
 * marks a player ready and BACK unmarks. The opener's BACK while choosing closes the screen. With 2 or more players
 * and every one ready, the opener's OK or START starts the session.
 *
 * Off: game.json coop 0, --coop off, or the per-game setting game.coop 0. Then the screen never opens and a game's
 * hooks see no session, so play is the game's own.
 * Nearest existing: pad_ex_page.c (a page of a game's own screen fed the game's presses and drawn by the game; it keeps
 * button rows for one player, this keeps the players of a session). */
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "game_log.h"

#if __has_include("port_config.h")
#include "port_config.h" /* game.json coop */
#endif
#ifndef PORT_COOP
#define PORT_COOP 0
#endif

#define CHOICES_MAX 32u

enum { OUT, CHOOSING, READY };

static int g_showing, g_opener, g_shared;
static unsigned g_choices;
static const char *const *g_names;
static u8 g_state[HOST_PADS], g_pick[HOST_PADS];
static int g_players;                   /* the session's: 0 = none */
static u8 g_session_port[HOST_PADS], g_session_pick[HOST_PADS];

static const int g_max = PORT_COOP; /* a variable, so a game without co-op compiles without a constant test */

int port_coop_max(void) { return g_max; }

/* Read once: the setting is a line of the settings file, and a game asks every frame it draws its own screen. */
int port_coop_enabled(void)
{
    static int enabled = -1; /* read once, from the tick: a game asks every frame, and a replay gets the recording's */
    if (enabled < 0) {
        const char *cli = host_arg("--coop");
        int on;
        if (g_max < 2) return enabled = 0;
        on = cli ? strcmp(cli, "off") != 0 : port_setting_get_int("game.coop", 1) != 0;
        if (port_extra_drawing()) return on;
        enabled = port_play_option("coop", on) != 0;
    }
    return enabled;
}

static int taken(unsigned pick, int except)
{
    if (g_shared) return 0;
    for (int p = 0; p < HOST_PADS; p++)
        if (p != except && g_state[p] != OUT && g_pick[p] == pick) return 1;
    return 0;
}

/* The next choice from `from` stepping by `step` that no other player holds; `from` itself when none is free. */
static unsigned next_free(int player, unsigned from, int step)
{
    unsigned at = from;
    for (unsigned i = 0; i < g_choices; i++) {
        at = (at + g_choices + (unsigned)step) % g_choices;
        if (!taken(at, player)) return at;
    }
    return from;
}

static int joined(void)
{
    int n = 0;
    for (int p = 0; p < HOST_PADS; p++) n += g_state[p] != OUT;
    return n;
}

int port_coop_join_open(int opener, const char *const *names, unsigned choices, int shared)
{
    if (!port_coop_enabled() || opener < 0 || opener >= HOST_PADS || choices == 0) return 0;
    g_names = names;
    g_choices = choices < CHOICES_MAX ? choices : CHOICES_MAX;
    g_shared = shared != 0;
    g_opener = opener;
    memset(g_state, OUT, sizeof g_state);
    memset(g_pick, 0, sizeof g_pick);
    g_state[opener] = CHOOSING;
    g_showing = 1;
    game_log_line("coop: join screen opened by player %d, %u choices%s", opener + 1, g_choices,
                   g_shared ? " (shared: copies)" : "");
    return 1;
}

int port_coop_join_showing(void) { return g_showing; }

int port_coop_join_press(int player, u32 press)
{
    if (!g_showing || player < 0 || player >= HOST_PADS || press == 0) return 0;
    u8 *st = &g_state[player];
    if (*st == OUT) {
        if ((press & PORT_PAD_PAGE_OK) && (g_shared || joined() < (int)g_choices) && joined() < g_max) {
            *st = CHOOSING;
            g_pick[player] = (u8)(taken(0, player) ? next_free(player, 0, 1) : 0);
            game_log_line("coop: player %d joined", player + 1);
        }
        return 0;
    }
    if (*st == READY) {
        if (press & PORT_PAD_PAGE_BACK) *st = CHOOSING;
        else if ((press & (PORT_PAD_PAGE_OK | PORT_COOP_START)) && player == g_opener && joined() >= 2) {
            for (int p = 0; p < HOST_PADS; p++)
                if (g_state[p] == CHOOSING) return 0;
            g_players = 0;
            g_session_port[g_players] = (u8)g_opener; /* the opener is player 1 */
            g_session_pick[g_players++] = g_pick[g_opener];
            for (int p = 0; p < HOST_PADS; p++)
                if (p != g_opener && g_state[p] != OUT) {
                    g_session_port[g_players] = (u8)p;
                    g_session_pick[g_players++] = g_pick[p];
                }
            g_showing = 0;
            game_log_line("coop: session of %d players started", g_players);
            return 1;
        }
        return 0;
    }
    if (press & PORT_PAD_PAGE_BACK) {
        if (player == g_opener) {
            g_showing = 0;
            game_log_line("coop: join screen closed");
            return -1;
        }
        *st = OUT;
        game_log_line("coop: player %d left", player + 1);
    } else if (press & PORT_PAD_PAGE_LEFT) {
        g_pick[player] = (u8)next_free(player, g_pick[player], -1);
    } else if (press & PORT_PAD_PAGE_RIGHT) {
        g_pick[player] = (u8)next_free(player, g_pick[player], 1);
    } else if (press & PORT_PAD_PAGE_OK) {
        if (!taken(g_pick[player], player)) *st = READY;
    }
    return 0;
}

int port_coop_join_line(int player, char *out, unsigned cap)
{
    if (!out || cap == 0) return 0;
    out[0] = 0;
    if (player < 0 || player >= HOST_PADS) return 0;
    const char *name = g_names && g_pick[player] < g_choices ? g_names[g_pick[player]] : "";
    switch (g_state[player]) {
    case OUT: snprintf(out, cap, "P%d  PRESS A TO JOIN", player + 1); break;
    case CHOOSING: snprintf(out, cap, "P%d  < %s >", player + 1, name); break;
    default: snprintf(out, cap, "P%d  %s  READY", player + 1, name); break;
    }
    return g_state[player];
}

int port_coop_players(void) { return g_players; }
int port_coop_port(int i) { return i >= 0 && i < g_players ? g_session_port[i] : -1; }
int port_coop_pick(int i) { return i >= 0 && i < g_players ? g_session_pick[i] : -1; }

void port_coop_end(void)
{
    if (g_players) game_log_line("coop: session of %d players ended", g_players);
    g_players = 0;
    g_showing = 0;
}

/* The opening logos skipped (game.json movie "skip_logos"; Game > Skip the opening logos; --skip-logos on|off). The game
 * plays its publisher's and its own logo as movies through its movie routine (game.json movie "call"), and the original
 * lets START skip each one (Robo Pit: KOKO.CPK, then LOGO.CPK; a skipped LOGO.CPK also leaves out the opening movie, so the
 * game goes on to the title). With the option on, each movie game.json names is skipped at its first call in the run: the
 * routine is not run and returns what the original returns when START skips it (game.json "skipped"), so the game takes
 * its own skip path from there; the CD is left as a skip at the first picture leaves it (the native player's way). A later
 * call of the same movie (an attract loop) plays as before.
 *
 * On by default in an unscripted play run; off in checks, scripted and --det runs unless asked for. Read at start and kept
 * in the recording as a session note when on, so a replay skips the same and an older recording (no note) plays them.
 * Online play: both copies must skip alike, so the joiner takes the host's (sat_net_setup.c) and the entry is greyed.
 *
 * Nearest existing: sat_movie.c (the same routine, played on the host instead of skipped) and sat_main.c's
 * fast_loads_start (a start-time choice kept as a session note). */
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_cd.h"
#include "sat_game.h"
#include "sat_net.h"
#include "console.h"
#include "session.h"
#include "game_log.h"
#include "host.h"

u32 sat_logo_fn; /* the movie routine while logos are skipped (0: off; sat_call.c compares every call with it) */

#if defined(SAT_MOVIE_FN) && defined(SAT_MOVIE_LOGOS)
#define NOTE "*skip-logos"
static const char *const k_logos[] = SAT_MOVIE_LOGOS;
enum { NLOGOS = sizeof k_logos / sizeof k_logos[0] };
static int g_want = -1; /* -1: not given (on in a play run) */
static u32 g_done;      /* bit i: logo i was skipped once */

void sat_logos_option(const char *value) { g_want = strcmp(value, "off") != 0; }
int sat_logos_on(void) { return sat_logo_fn != 0; }

void sat_logos_start(int play_run)
{
    u8 on = 0;
    if (session_note_replayed(NOTE, &on, 1)) on = on != 0;
    else if (session_replaying()) on = 0;
    else on = (u8)(g_want < 0 ? play_run && !input_scripted() : g_want);
    if (on) session_save_read(NOTE, &on, 1, 1);
    sat_logo_fn = on ? SAT_MOVIE_FN : 0u, g_done = 0;
    port_log("logos: %s\n", on ? "the opening logos are skipped (the game's own skip)" : "the opening logos play");
}

int sat_logos_skip(const TcArgs *a, u32 *ret)
{
    char name[64];
    u32 i, k, fad, sectors = 0;
    for (i = 0; i < sizeof name - 1 && (name[i] = (char)rd8(a->r[SAT_MOVIE_NAME_ARG] + i)) != 0; i++) {}
    name[i] = 0;
    for (k = 0; k < NLOGOS && _stricmp(name, k_logos[k]); k++) {}
    if (k == NLOGOS || (g_done >> k & 1u)) return 0;
    g_done |= 1u << k;
    if ((fad = sat_movie_find(name, &sectors)) != 0) sat_cd_played(fad, fad, fad + sectors); /* the drive stopped at the start */
    game_log_line("logos: %s skipped at vertical blank %u (the routine answers %d, as when START skips it)", name, sat_vblank_count(),
                  (int)SAT_MOVIE_SKIPPED);
    *ret = SAT_MOVIE_SKIPPED;
    return 1;
}

static int alone(void) { return !sat_net_on(); }
static int get(void) { return g_want != 0; }
static void set(int on) { g_want = on != 0; }
static const PortMenuItem k_item = {"Game", "Skip the opening &logos (from the next start)", PORT_MENU_CHECK, "skip-logos", get, set,
                                    NULL, NULL, 0, alone};
const PortMenuItem *sat_logos_menu(void) { return &k_item; }
#else
void sat_logos_option(const char *value) { (void)value; }
int sat_logos_on(void) { return 0; }
void sat_logos_start(int play_run) { (void)play_run; }
int sat_logos_skip(const TcArgs *a, u32 *ret) { (void)a, (void)ret; return 0; }
const PortMenuItem *sat_logos_menu(void) { return NULL; }
#endif

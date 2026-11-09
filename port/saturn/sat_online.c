/* While a game the game's own online screens started (sat_lobby.c, --net-menu on) runs: the game hosted in the background,
 * what the screen says about it, and the ways out of it. Online play itself is sat_net.c's lockstep.
 *
 *   Hosting   HOST GAME starts the game again as a host that plays on alone (--net on --net-menu on; no wait for a
 *             player): every menu is the player's as before and a player can join at any time. A joiner replays the
 *             host's frames from boot as fast as it can (sat_net.c), then is the game's player 2 (its second pad) from the
 *             frame the host takes it in. At the menus that is at once (VS MODE opens); a fight already running goes on
 *             as it was, so a joiner plays from the next fight. The hub's line under its items reads STOP HOSTING on the
 *             host's copy (the game again on its own, the joiner told) and HOSTED GAME on the joiner's (the host's
 *             cursor moves on both; there it does nothing). Under the hub two lines (game.json netplay "show" status) say
 *             HOSTING and the open spaces (the game's one against one has two seats, less the players in) and the address
 *             to give out; on the joiner's copy PLAYER 2 and the host's address.
 *   Notices   PLAYER 2 JOINED and PLAYER 2 LEFT (JOINED AS PLAYER 2 on the joiner's copy) for 5 s, in the status line and,
 *             in a fight, on a line of their own (show "notice"). A host that turned away a player with another version
 *             of the game says so in its status line only (the joiner's copy writes the same cells then: its own words).
 *   Leaving   This player holding the buttons of game.json netplay "leave" (A+B+C+START in Robo Pit, the game's own soft
 *             reset, for 1 s) anywhere, a fight too, or the host's STOP HOSTING: the other copy is told (M_LEAVE) and this
 *             one goes back to its own game's menu saying YOU LEFT THE GAME (HOSTING STOPPED). The hub is the host's pad's
 *             on both copies, so player 2's way out is the buttons. A joiner told the host left goes back the same way (THE HOST LEFT THE GAME); a host told player 2
 *             left keeps hosting (PLAYER 2 LEFT). The game's own reset runs on both copies alike (the pads are shared).
 *
 * Both copies write the same text cells at the same frames (the words may differ): the text map is not in the RAM hash,
 * but the emulated machine's writes are counted, so the two copies stay in step. Joins and leaves come from the frames'
 * pads as the host decided them, the same on both.
 *
 * Nearest existing: sat_lobby.c (the screens before a match, which this continues) and sat_menus.c's text (the game's
 * small type written into its map, which sat_menu_line extends to any palette). */
#include <stdio.h>
#include <string.h>
#define SAT_NET_INTERNAL
#include "sat.h"
#include "sat_sys.h"
#include "sat_game.h"
#include "sat_net.h"
#include "game_log.h"
#include "host.h"

#ifdef SAT_NETPLAY
enum { NOTICE = 300, SEATS = 2 }; /* frames a notice shows; the game's one against one */
enum { N_NONE, N_JOINED, N_LEFT, N_REFUSED };
static char g_addr[64];
static int g_port2, g_notice, g_drawn;
static u32 g_until, g_frame, g_refused; /* g_refused: the frame a refusal shows until (the host's copy only) */
static u32 g_held;                      /* frames this player has held netplay "leave"'s buttons */

/* the address to give out, in the small type: capitals, and a space for the ':' it has no letter for */
void sat_online_begin(const char *addr)
{
    snprintf(g_addr, sizeof g_addr, "%s", addr && *addr && !strchr(addr, ' ') ? addr : "UNKNOWN");
    for (char *p = g_addr; *p; p++) *p = *p >= 'a' && *p <= 'z' ? (char)(*p - 32) : *p == ':' ? ' ' : *p;
}
int sat_online_host(void) { return sat_net.on && sat_net.host; }

static void say(int what, u32 f)
{
    static const char *const k_log[] = {"", "player 2 joined", "player 2 left", "a player with another version was turned away"};
    if (what == N_REFUSED) g_refused = f + NOTICE;
    else g_notice = what, g_until = f + NOTICE;
    game_log_line("online: notice at frame %u: %s", f, k_log[what]);
}

/* after each frame's pads are decided (sat_net.c): a join or a leave, the same frame on both copies */
void sat_online_frame(u32 f, int port2)
{
    g_frame = f;
    if (port2 != g_port2) g_port2 = port2, say(port2 ? N_JOINED : N_LEFT, f);
}

static const char *notice(void)
{
    if (!g_notice || g_frame >= g_until) return NULL;
    return g_notice == N_LEFT ? "PLAYER 2 LEFT" : sat_net.host ? "PLAYER 2 JOINED" : "JOINED AS PLAYER 2";
}

#if defined(SAT_NET_SHOW) && defined(SAT_MENUS)
static const u32 V[] = SAT_NET_SHOW; /* the fight's frame call; status x, y, width; notice x, y, width */

/* the hub's draw (sat_menus.c), the hub's line under its items at (x, y); on 0 the hub ends: the status lines go */
void sat_online_hub(u32 x, u32 y, int on)
{
    char a[40], b[40];
    const char *n = sat_net.host && g_frame < g_refused ? "REFUSED ANOTHER VERSION" : notice();
    int open = SEATS - 1 - g_port2;
    if (!on) {
        sat_menu_line(V[1], V[2], "", V[3], ~0u), sat_menu_line(V[1], V[2] + 1u, "", V[3], ~0u);
        return;
    }
    sat_menu_line(x, y, sat_net.host ? "STOP HOSTING" : "HOSTED GAME", 12, ~0u);
    if (sat_net.host) {
        snprintf(a, sizeof a, "HOSTING  %d OPEN SPACE%s", open, open == 1 ? "" : "S");
        snprintf(b, sizeof b, "ADDRESS %s", g_addr);
    } else {
        snprintf(a, sizeof a, "ONLINE AS PLAYER 2");
        snprintf(b, sizeof b, "HOST %s", g_addr);
    }
    sat_menu_line(V[1], V[2], n ? n : a, V[3], ~0u), sat_menu_line(V[1], V[2] + 1u, b, V[3], ~0u);
}

/* a call returned (sat_menus.c): after the fight's frame, a join or leave notice on its line, spaces once it is over */
void sat_online_call(u32 fn)
{
    const char *n = notice();
    if (fn != V[0] || (!n && !g_drawn)) return;
    sat_menu_line(V[4], V[5], n ? n : "", V[6], ~0u), g_drawn = n != NULL;
}
#else
void sat_online_hub(u32 x, u32 y, int on) { (void)x, (void)y, (void)on; }
void sat_online_call(u32 fn) { (void)fn; }
#endif

/* this copy's game ends: the link closes once what was sent has gone, then the game again on its own, saying why */
static void end_match(const char *note, const char *why) /* lock held; does not return */
{
    net_udp_unlock();
    net_udp_finish(2000);
    sat_lobby_back(note);
    port_exit(why, 0);
    for (;;) pace_wait_us(1000000); /* another thread is ending the run */
}

/* this player leaves (lock held): the other copy is told, then this one ends */
static void leave(const char *how)
{
    u8 m = M_LEAVE;
    game_log_line("online: %s: %s leaves the game%s", how, sat_net.host ? "the host" : "player 2", sat_net.host && g_port2 ? "; player 2 is told" : "");
    net_udp_send(1, &m, 1);
    end_match(sat_net.host && !strcmp(how, "STOP HOSTING") ? "stopped" : "left", sat_net.host ? "online: hosting stopped" : "online: player 2 left");
}

/* sat_net.c, lock held: at each frame with this player's pad, and after each wait for the other copy (SAT_NET_NO_PAD) */
void sat_online_watch(u32 f, u32 pad)
{
#ifdef SAT_NET_LEAVE
    static const u32 L[] = SAT_NET_LEAVE; /* the buttons, the frames they are held */
    if (pad != SAT_NET_NO_PAD && (g_held = (pad & L[0]) == L[0] ? g_held + 1u : 0u) == L[1]) {
        game_log_line("online: frame %u: the leave buttons held %u frames", f, L[1]);
        leave("the leave buttons");
    }
#else
    (void)pad;
#endif
    if (sat_net.host && pad != SAT_NET_NO_PAD) { /* a caller with another version turned away (the link listens on) */
        static u32 refused;
        u32 r = net_udp_stats()->refused;
        if (r > refused) game_log_line("netplay: frame %u: a caller with another version of the game was turned away", f), sat_online_refused();
        refused = r;
    }
    if (!sat_net.left) return;
    if (!sat_net.host) {
        game_log_line("netplay: frame %u: the host left the game", f);
        end_match("they-left", "netplay: the host left the game");
    }
    game_log_line("netplay: frame %u: player 2 said goodbye; port 2 is empty from here, and the game stays hosted", f);
    sat_net.left = 0, sat_net.j0 = 0, sat_net.ready_from = 0;
}

/* the hub's line under its items was chosen (sat_menus.c): STOP HOSTING on the host's copy */
void sat_online_choose(void)
{
    if (!sat_net.host) {
        game_log_line("online: HOSTED GAME: the host's line; nothing on player 2's copy");
        return;
    }
    net_udp_lock();
    leave("STOP HOSTING");
}

/* sat_lobby.c, the host's copy: a caller with another version of the game was turned away (its status line says so) */
void sat_online_refused(void) { say(N_REFUSED, g_frame); }
#endif

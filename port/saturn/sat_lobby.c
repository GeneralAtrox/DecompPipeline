/* The game's own online screens (game.json netplay "menu": "in-game", menus "online"): HOST GAME and JOIN GAME in the
 * hub (sat_menus.c), drawn in the game's box and small type, and the way a match they start ends back in the hub. Online
 * play itself is sat_net.c's, which keeps two copies in step from boot, so a match is a new start of the game: these
 * screens start the game again at once with --net (base/host_online.cpp, nothing to answer); the host's settings and
 * save go to the joiner there, as before, and the hosted game runs on in sat_online.c.
 *
 *   HOST GAME  the game starts again at once as a host that plays on alone (--net on --net-menu on): every menu is the
 *              player's, and a player can join at any time (sat_online.c: STOP HOSTING, the open spaces, the address).
 *   JOIN GAME  the host's address, one letter at a time: LEFT and RIGHT pick the letter, UP and DOWN change it (digits, the
 *              dot, letters, the dash); it starts as the last one used (game.json-free kit setting game.join-address, or
 *              --join-address); the keyboard types into it (digits . : and Backspace: base host_text.cpp) and Ctrl+V or
 *              Shift+Insert pastes an address (IP or IP:PORT, spaces left out); a port shows on the line under it. A, C,
 *              START or Enter connects, once the field is an address (else NOT AN ADDRESS, and it stays open). B goes back. Connecting waits up to 20 s for the hosted game
 *              to answer, B goes back; when it does, the game starts again as player 2 (--net ADDRESS --net-menu on) and
 *              catches up with the host's game. B or Esc goes back.
 *   A message  when it fails (no host, another copy of the game, the network did not start), with OK, then the hub.
 *
 * A match these screens started (--net-menu on) that cannot go on comes back here instead of closing: the joiner finds no
 * host in 60 s or is refused, the copies go out of step, the host stops hosting or leaves, or hosting could not start. The
 * game starts again on its own with --online-note WHY and the hub shows why once. The titlebar's Online menu is not built
 * for such a game; a saved --net from it is set back to off (host_online_forget).
 *
 * The hosted game's own lines and notices are sat_online.c's.
 *
 * Nearest existing: sat_net_setup.c's join_setup (the same wait for a host, there before the game starts) and sat_menus.c's
 * FIGHT choice (a kit box drawn and read through the game's own routines). */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define SAT_NET_INTERNAL
#include "sat.h"
#include "sat_sys.h"
#include "sat_game.h"
#include "sat_net.h"
#include "session.h"
#include "game_log.h"
#include "host.h"

int sat_lobby_match; /* --net-menu on: a match the game's own screens started */
static const char *g_note, *g_addr, *g_keys; /* g_keys: --text-test */
void sat_online_note_option(const char *value) { g_note = value && *value ? value : NULL; }
void sat_join_address_option(const char *value) { g_addr = value; }
void sat_text_test_option(const char *value) { g_keys = value; }

/* a match that cannot go on: the game again on its own, back in its menu, saying why; returns if it could not start */
void sat_lobby_back(const char *note)
{
    char extra[64];
    if (!sat_lobby_match) return;
    snprintf(extra, sizeof extra, "--online-note %s", note);
    game_log_line("online: %s: the game starts again on its own, back in its menu", note);
    if (!host_online_restart("off", extra)) return;
    port_exit("online: back to the game's own menu", 0);
    for (;;) pace_wait_us(1000000); /* another thread is ending the run */
}

#if defined(SAT_MENUS) && defined(SAT_MENUS_ONLINE) && defined(SAT_NETPLAY)
enum { RIGHT = 0x8000u, LEFT = 0x4000u, DOWN = 0x2000u, UP = 0x1000u, START = 0x0800u, A = 0x0400u, C = 0x0200u, B = 0x0100u };
enum { LEN = 18, WAIT_MS = 20000, BAD = 180 };
static const char k_chars[] = " 0123456789.abcdefghijklmnopqrstuvwxyz-";
static const char *const k_why[][3] = {
    {"no-host", "NO HOST ANSWERED", ""},          {"other-version", "HOST HAS ANOTHER", "COPY OF THE GAME"},
    {"no-player", "NO PLAYER JOINED", ""},        {"out-of-step", "THE TWO GAMES WENT", "OUT OF STEP"},
    {"link-lost", "THE OTHER PLAYER", "HAS GONE"}, {"no-network", "THE NETWORK DID", "NOT START"},
    {"no-restart", "THE GAME COULD NOT", "START AGAIN"}, {"they-left", "THE HOST LEFT", "THE GAME"},
    {"stopped", "HOSTING STOPPED", ""},           {"left", "YOU LEFT", "THE GAME"},
};

static int on_msg(void *ctx, const u8 *m, int len, int reliable) { (void)ctx, (void)m, (void)len, (void)reliable; return 1; }
static void on_state(void *ctx, NetState from, NetState to) { (void)ctx, (void)from, (void)to; }
static NetState state(void)
{
    NetState s;
    net_udp_lock(), s = net_udp_state(), net_udp_unlock();
    return s;
}

/* why, in a box with OK, until a button */
static void message(u32 row, const u32 box[3], const char *why)
{
    const char *l[4] = {"ONLINE", "ONLINE GAME ENDED", "", "OK"};
    for (size_t i = 0; i < sizeof k_why / sizeof k_why[0]; i++)
        if (!strcmp(why, k_why[i][0])) l[1] = k_why[i][1], l[2] = k_why[i][2];
    game_log_line("online: message: %s (%s)", why, l[1]);
    sat_menu_keep(box, 4, 0);
    while (!(sat_menu_frame(row, box, l, 4, 3) & (A | C | START | B))) {}
    sat_menu_keep(box, 4, 1);
}

static void go(u32 row, const u32 box[3], const char *what, const char *net, const char *extra)
{
    const char *l[2] = {"ONLINE", what};
    sat_menu_frame(row, box, l, 2, -1);
    game_log_line("online: the match starts: the game starts again at once with --net %s %s", net, extra);
    if (host_online_restart(net, extra)) {
        port_exit("online: the match starts in a new copy of the game", 0);
        for (;;) pace_wait_us(1000000);
    }
    message(row, box, "no-restart");
}

void sat_lobby_note(u32 row, const u32 box[3])
{
    const char *n = g_note;
    if (!n) return;
    g_note = NULL;
    message(row, box, n);
}

void sat_lobby_host(u32 row, const u32 box[3])
{
    game_log_line("online: HOST GAME: the game starts again as a host; its menus stay the player's (vertical blank %u)", sat_vblank_count());
    go(row, box, "STARTING TO HOST", "on", "--net-menu on");
}

/* an address as typed or pasted, spaces left out: HOST or HOST:PORT, HOST four numbers to 255 or a name of the field's
 * letters; 1 with the host (lower case) and the port (0: the game's own) when it is one */
static int parse(const char *s, char host[LEN + 1], int *port)
{
    char t[80], *c, *e = NULL;
    int n = 0, dots = 0, digits = 1, v = 0, k = 0;
    for (; *s && n < 79; s++)
        if (*s != ' ' && *s != '\t' && *s != '\r' && *s != '\n') t[n++] = *s >= 'A' && *s <= 'Z' ? (char)(*s + 32) : *s;
    t[n] = 0, *port = 0;
    if ((c = strchr(t, ':'))) {
        long p = c[1] >= '0' && c[1] <= '9' ? strtol(c + 1, &e, 10) : 0;
        if (!p || !e || *e || p > 65535) return 0;
        *port = (int)p, n = (int)(c - t), t[n] = 0;
    }
    if (!n || n > LEN || strchr(".-", t[0]) || strchr(".-", t[n - 1])) return 0;
    for (int i = 0; i < n; i++) {
        if (!strchr(k_chars + 1, t[i])) return 0;
        if (t[i] == '.') dots++;
        else if (t[i] < '0' || t[i] > '9') digits = 0;
    }
    for (int i = 0; digits && i <= n; i++) { /* all digits: an IPv4 address */
        if (i == n || t[i] == '.') {
            if (dots != 3 || !k || v > 255) return 0;
            v = 0, k = 0;
        } else if (++k > 3) return 0;
        else v = v * 10 + t[i] - '0';
    }
    memcpy(host, t, (size_t)n + 1);
    return 1;
}

/* the next key into the field: --text-test's (one each 6 frames once the field has been open 30; ^X is control-X: ^V paste,
 * ^H Backspace, ^M Enter, ^[ Esc), else the keyboard's (host_text.cpp) */
static int typed(u32 t)
{
    int c;
    if (!g_keys) return host_text_take();
    if (!*g_keys || t < 30 || t % 6) return -1;
    c = (unsigned char)*g_keys++;
    if (c == '^' && *g_keys) c = *g_keys++ & 31;
    return c == 13 ? '\n' : c;
}

/* the field: addr (LEN letters, spaces where none) and *port; 1 to connect, 0 back */
static int pick(u32 row, const u32 box[3], char addr[LEN + 1], int *port)
{
    char shown[LEN + 1], info[24], text[40], host[LEN + 1], clip[256];
    const char *l[4] = {"JOIN GAME", "HOST ADDRESS", shown, info};
    int pos = 0, n = (int)sizeof k_chars - 1, r, fresh = 1, in_port = 0, bad = 0;
    u32 t = 0;
    sat_menu_keep(box, 4, 0);
    host_text_capture(1);
    for (;; t++) {
        u32 e;
        int c, end = LEN;
        while (end > 0 && addr[end - 1] == ' ') end--;
        memcpy(shown, addr, LEN + 1);
        if (!((t >> 4) & 1u) && !in_port) shown[pos] = addr[pos] == ' ' ? '-' : ' '; /* where the letter changes, blinking */
        if (bad) bad--, snprintf(info, sizeof info, "NOT AN ADDRESS");
        else if (in_port || *port) snprintf(info, sizeof info, "PORT %.0d%s", *port, in_port && !((t >> 4) & 1u) ? "-" : "");
        else snprintf(info, sizeof info, "CTRL V PASTES");
        e = sat_menu_frame(row, box, l, 4, 2);
        c = typed(t);
        if (c == 22) { /* paste: an address replaces the field */
            int got = host_text_clipboard(clip, sizeof clip), p = 0;
            if (got && parse(clip, host, &p)) {
                memset(addr, ' ', LEN), memcpy(addr, host, strlen(host)), *port = p, in_port = 0, fresh = 0, bad = 0;
                pos = (int)strlen(host) < LEN ? (int)strlen(host) : LEN - 1;
                game_log_line("online: JOIN GAME: pasted %s%s%.0d", host, p ? ":" : "", p);
            } else {
                bad = BAD;
                game_log_line("online: JOIN GAME: the pasted text is not an address (%d letters); the field is as it was", got ? (int)strlen(clip) : 0);
            }
        } else if (c == ':' || (c >= '0' && c <= '9') || c == '.' || c == '-') { /* typing: on at the end */
            if (fresh) memset(addr, ' ', LEN), *port = 0, end = 0;
            fresh = 0, bad = 0;
            if (c == ':') in_port = 1, *port = 0;
            else if (in_port && c >= '0' && c <= '9' && *port * 10 + (c - '0') <= 65535) *port = *port * 10 + (c - '0');
            else if (!in_port && end < LEN) addr[end] = (char)c, pos = end + 1 < LEN ? end + 1 : LEN - 1;
        } else if (c == '\b') {
            fresh = 0, bad = 0;
            if (in_port && *port) *port /= 10;
            else if (in_port) in_port = 0;
            else if (end > 0) addr[end - 1] = ' ', pos = end - 1;
        }
        if (e & LEFT && pos > 0) pos--, t = 0, fresh = 0;
        if (e & RIGHT && pos < LEN - 1) pos++, t = 0, fresh = 0;
        if (e & (UP | DOWN)) {
            int k = (int)(strchr(k_chars, addr[pos]) - k_chars);
            addr[pos] = k_chars[(k + (e & UP ? 1 : n - 1)) % n], t = 16, fresh = 0, sat_menu_sound();
        }
        if (e & B || c == 27) { r = 0; break; }
        if (e & (A | C | START) || c == '\n') {
            int k = 0, p;
            for (int i = 0; i < LEN; i++) if (addr[i] != ' ') text[k++] = addr[i];
            snprintf(text + k, sizeof text - (size_t)k, *port ? ":%d" : "", *port);
            if (parse(text, host, &p)) { r = 1; break; }
            bad = BAD;
            game_log_line("online: JOIN GAME: %s is not an address; the field stays open", k ? text : "an empty field");
        }
    }
    host_text_capture(0);
    sat_menu_keep(box, 4, 1);
    return r;
}

void sat_lobby_join(u32 row, const u32 box[3])
{
    char addr[LEN + 1], host[LEN + 1], at[32], ps[16] = "", saved[64] = "";
    const char *l[4] = {"JOIN GAME", "CONNECTING TO", host, ps}, *s = g_addr, *why = NULL;
    int port = 0, q;
    ULONGLONG t0;
    memset(addr, ' ', LEN), addr[LEN] = 0;
    if (!s && port_setting_get_str("game.join-address", saved, sizeof saved)) s = saved;
    if (s && parse(s, host, &port)) memcpy(addr, host, strlen(host)); /* the last one, offered first */
    if (!pick(row, box, addr, &port)) {
        game_log_line("online: JOIN GAME: back to the hub");
        return;
    }
    snprintf(at, sizeof at, "%s", addr), parse(at, host, &q); /* the field: an address (pick) */
    if (port) snprintf(at, sizeof at, "%s:%d", host, port), snprintf(ps, sizeof ps, "PORT %d", port);
    else snprintf(at, sizeof at, "%s", host);
    port_setting_set_str("game.join-address", at);
    game_log_line("online: JOIN GAME: connecting to %s (vertical blank %u)", at, sat_vblank_count());
    if (sat_net_open(at, on_msg, on_state)) {
        message(row, box, "no-network");
        return;
    }
    t0 = GetTickCount64();
    sat_menu_keep(box, 4, 0);
    for (;;) {
        u32 e = sat_menu_frame(row, box, l, 4, -1);
        ULONGLONG ms = GetTickCount64() - t0;
        NetState st;
        net_udp_lock(), st = net_udp_state();
        if ((st == NET_TIMED_OUT || st == NET_CLOSED) && ms < WAIT_MS) net_udp_reconnect(); /* the host was not up yet */
        net_udp_unlock();
        if (st == NET_ESTABLISHED) break; /* the hosted game answered: this link was only the knock */
        if (st == NET_REFUSED) { why = "other-version"; break; }
        if (ms >= WAIT_MS) { why = "no-host"; break; }
        if (e & B) { why = ""; break; }
    }
    sat_menu_keep(box, 4, 1);
    net_udp_stop();
    if (!why) {
        game_log_line("online: JOIN GAME: the hosted game at %s answered", at);
        go(row, box, "JOINING THE GAME", at, "--net-menu on");
    } else if (*why) message(row, box, why);
    else game_log_line("online: JOIN GAME: back to the hub");
}
#else
void sat_lobby_note(u32 row, const u32 box[3]) { (void)row, (void)box; }
void sat_lobby_host(u32 row, const u32 box[3]) { (void)row, (void)box; }
void sat_lobby_join(u32 row, const u32 box[3]) { (void)row, (void)box; }
#endif

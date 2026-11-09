/* sat_net_setup.c: online play's options and start (sat_net.h). The link; the digest that keeps two different copies apart
 * (the build and the code image); and what a joiner takes from the host before its game starts: the settings that change
 * the game (fast disc loads, auto-load, the part choice, the delay and the desync check) and the host's save, which the
 * joiner keeps in a copy beside its own (SAVE.netplay) and never writes into its own. Settings that change only the picture
 * or the sound (shadows, widescreen, in-between frames, volume) stay each player's. A build without game.json "netplay"
 * takes the options and says it has no online play. The Online menu (base/host_online.cpp) saves --net; a match tells it
 * what this run is and locks the entries that would change the game. A match is a play run: sat_net_idle draws the
 * in-between frames while a copy waits.
 *
 * Nearest existing: sat_main.c's fast_loads_start and the notes session.c keeps (the same settings, carried into a replay
 * instead of to another player); targets/xbox360_ppc/port/runtime/xb_net.c's digest (SHA-256 of the image, the same way). */
#include <windows.h>
#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define SAT_NET_INTERNAL
#include "sat.h"
#include "sat_sys.h"
#include "sat_cd.h"
#include "sat_part.h"
#include "sat_game.h"
#include "sat_net.h"
#include "console.h"
#include "game_log.h"
#include "session.h"
#include "host.h"

static struct {
    const char *host, *join;
    int port, delay, wait, every, pace, loss, lag, jitter, test_frame;
    u32 test_addr;
    int saved; /* host and join came from --net (the Online menu's saved choice), not --net-host or --net-join */
} g_opt = {NULL, NULL, 0, -1, 0, -1, 1, 0, 0, 0, 0, 0, 0};

int sat_net_option(const char *a, const char *v)
{
    if (strncmp(a, "--net", 5) || (a[5] && a[5] != '-') || !v) return 0;
    if (!strcmp(a, "--net")) { /* the Online menu's: on (host), off, or the host to join */
        int host = !strcmp(v, "on") || !strcmp(v, "host");
        g_opt.host = host ? "on" : NULL, g_opt.join = host || !strcmp(v, "off") || !*v ? NULL : v, g_opt.saved = 1;
    } else if (!strcmp(a, "--net-host")) g_opt.host = strcmp(v, "off") && *v ? v : NULL, g_opt.join = g_opt.host ? NULL : g_opt.join, g_opt.saved = 0;
    else if (!strcmp(a, "--net-join")) g_opt.join = strcmp(v, "off") && *v ? v : NULL, g_opt.host = g_opt.join ? NULL : g_opt.host, g_opt.saved = 0;
    else if (!strcmp(a, "--net-menu")) sat_lobby_match = strcmp(v, "off") != 0;
    else if (!strcmp(a, "--net-port")) g_opt.port = atoi(v);
    else if (!strcmp(a, "--net-delay")) g_opt.delay = atoi(v);
    else if (!strcmp(a, "--net-wait")) g_opt.wait = strcmp(v, "off") != 0;
    else if (!strcmp(a, "--net-hash-every")) g_opt.every = atoi(v);
    else if (!strcmp(a, "--net-pace")) g_opt.pace = strcmp(v, "off") != 0;
    else if (!strcmp(a, "--net-test-loss")) g_opt.loss = atoi(v);
    else if (!strcmp(a, "--net-test-lag")) g_opt.lag = atoi(v);
    else if (!strcmp(a, "--net-test-jitter")) g_opt.jitter = atoi(v);
    else if (!strcmp(a, "--net-test-desync")) {
        char *e;
        g_opt.test_frame = (int)strtoul(v, &e, 10);
        if (*e == ',') g_opt.test_addr = (u32)strtoul(e + 1, NULL, 16);
    } else return 0;
    return 2;
}

#ifndef SAT_NETPLAY
int sat_net_on(void) { return 0; }
void sat_net_start(const char *image)
{
    (void)image;
    if (g_opt.host || g_opt.join) port_log("netplay: this build has no online play (game.json netplay)\n");
}
void sat_net_started(void) {}
void sat_net_vblank(void) {}
u32 sat_net_pads(u32 data[2]) { (void)data; return 1u; }
void sat_net_end(void) {}
#else
#define S sat_net
static u8 *g_snap;
static u32 g_snap_len;
static char g_copy[MAX_PATH + 16];
static u8 g_buf[65536];
static LONGLONG g_deadline;

void sat_net_plant(u32 f) /* --net-test-desync: this side only */
{
    u32 a = S.test_addr, o = a & (SAT_WRAM_SIZE - 1u);
    u8 *p = (a & 0x0ff00000u) == SAT_WRAM_H_BASE ? sat_ram_h() + o : (a & 0x0ff00000u) == SAT_WRAM_L_BASE ? sat_ram_l() + o : NULL;
    if (!p) {
        game_log_line("netplay: TEST: %08x is not in work RAM; nothing flipped", a);
        return;
    }
    *p ^= 0x10u;
    game_log_line("netplay: TEST: this copy flipped a bit of %08x at frame %u (a planted desync)", a, f);
}

void sat_net_pace(void) /* the host: the game's own rate on the real clock (the --det timeline has none) */
{
    LARGE_INTEGER c, fq;
    LONGLONG t, per;
    QueryPerformanceCounter(&c), QueryPerformanceFrequency(&fq);
    t = c.QuadPart, per = fq.QuadPart / (LONGLONG)SAT_FRAME_HZ;
    if (!g_deadline || t > g_deadline + 6 * per) g_deadline = t; /* far behind: start again from now */
    sat_net_idle(g_deadline);
    g_deadline += per;
}

/* Online play waits on the --det timeline, outside sat_spin, so the in-between frames (sat_inb.c) that come due are drawn
 * here as sat_spin's wait does on the real clock: until `until` (the host's pace), or with until 0 one slice of a wait for
 * the other copy (the window's events, then up to 2 ms or until a frame is due, and that frame). The waiting is not the
 * game's work (sat_perf.c); a frame drawn keeps its own time. */
void sat_net_idle(LONGLONG until)
{
    LARGE_INTEGER c, fq;
    LONGLONG due;
    QueryPerformanceFrequency(&fq);
    if (!until) {
        u32 wait = 2;
        sat_perf_wait_begin();
        if (!sat_video_headless()) gfx_pump();
        QueryPerformanceCounter(&c);
        if ((due = sat_inb_due()) != 0) wait = c.QuadPart >= due ? 0u : (u32)((due - c.QuadPart) * 1000 / fq.QuadPart);
        if (wait) net_udp_wait(wait < 2u ? wait : 2u);
        sat_perf_wait_end();
        QueryPerformanceCounter(&c);
        sat_inb_poll(c.QuadPart, sat_net_next());
        return;
    }
    for (;;) {
        LONGLONG to = until;
        QueryPerformanceCounter(&c);
        if ((due = sat_inb_due()) != 0 && c.QuadPart >= due) { /* drawn, or skipped when there is no time: either moves on */
            sat_inb_poll(c.QuadPart, until);
            continue;
        }
        if (c.QuadPart >= until) return;
        if (due && due < to) to = due;
        sat_perf_wait_begin();
        pace_wait_us((u64)((to - c.QuadPart) * 1000000 / fq.QuadPart));
        sat_perf_wait_end();
    }
}

static const char *save_path(void)
{
    const char *s = host_arg("--save");
    return s && *s ? s : "saturn_backup.bin";
}

static const char *g_image; /* the code image, for the digest */
static int digest(const char *image, u8 out[NET_DIGEST])
{
    static const char tag[] = "saturn netplay 1";
    BCRYPT_ALG_HANDLE alg = NULL;
    BCRYPT_HASH_HANDLE h = NULL;
    FILE *f;
    size_t n;
    int ok = 0;
    memset(out, 0, NET_DIGEST);
    if (!BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, NULL, 0))) return 0;
    if (BCRYPT_SUCCESS(BCryptCreateHash(alg, &h, NULL, 0, NULL, 0, 0))) {
        BCryptHashData(h, (PUCHAR)tag, sizeof tag, 0);
        if (port_console.version) BCryptHashData(h, (PUCHAR)port_console.version, (ULONG)strlen(port_console.version), 0);
        BCryptHashData(h, (PUCHAR)SAT_GAME_NAME, sizeof SAT_GAME_NAME, 0);
        f = fopen(image, "rb");
        if (f) {
            while ((n = fread(g_buf, 1, sizeof g_buf, f)) > 0) BCryptHashData(h, g_buf, (ULONG)n, 0);
            fclose(f);
            ok = 1;
        }
        BCryptFinishHash(h, out, NET_DIGEST, 0);
        BCryptDestroyHash(h);
    }
    BCryptCloseAlgorithmProvider(alg, 0);
    return ok;
}

static void snapshot(void) /* the host's save as it is before the game reads it */
{
    FILE *f = fopen(save_path(), "rb");
    long n;
    if (!f) return;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n > 0 && n < (1L << 24)) g_snap = (u8 *)malloc((size_t)n);
    if (g_snap && fread(g_snap, 1, (size_t)n, f) == (size_t)n) g_snap_len = (u32)n;
    fclose(f);
}

/* the host, lock held: {SETUP delay, hash_every, fast, auto-load, part[16], save bytes, frames so far, logos skipped}, the
 * save, the end */
void sat_net_send_setup(void)
{
    u8 m[NET_MAX_MSG];
    m[0] = M_SETUP, m[1] = (u8)S.delay, sat_net_put32(m + 2, (u32)S.hash_every), m[6] = (u8)S.fast, m[7] = (u8)S.autoload;
    memcpy(m + 8, S.part, 16);
    sat_net_put32(m + 24, g_snap_len), sat_net_put32(m + 28, S.last_final), m[32] = (u8)S.logos, m[33] = (u8)S.online;
    net_udp_send(1, m, 34); /* m[33]: the hub's online lines (the two hubs must be alike) */
    for (u32 o = 0; o < g_snap_len; o += 1300u) {
        u32 n = g_snap_len - o < 1300u ? g_snap_len - o : 1300u;
        m[0] = M_SAVE;
        memcpy(m + 1, g_snap + o, n);
        net_udp_send(1, m, (int)n + 1);
    }
    m[0] = M_SETUP_END;
    net_udp_send(1, m, 1);
}

/* the joiner, worker thread, lock held */
void sat_net_setup_msg(const u8 *m, int len)
{
    if (S.setup_end) return;
    if (m[0] == M_SETUP && len >= 32) {
        S.delay = m[1], S.hash_every = (int)sat_net_get32(m + 2), S.fast = m[6], S.autoload = m[7];
        memcpy(S.part, m + 8, 16), S.part[15] = 0;
        S.save_len = sat_net_get32(m + 24), S.last_final = sat_net_get32(m + 28), S.save_got = 0, S.logos = len >= 33 && m[32], S.online = len >= 34 && m[33];
        free(S.save);
        S.save = S.save_len && S.save_len < (1u << 24) ? (u8 *)malloc(S.save_len) : NULL;
        if (!S.save) S.save_len = 0;
    } else if (m[0] == M_SAVE && S.save && S.save_got + (u32)(len - 1) <= S.save_len) {
        memcpy(S.save + S.save_got, m + 1, (size_t)len - 1), S.save_got += (u32)(len - 1);
    } else if (m[0] == M_SETUP_END) {
        S.setup_end = 1;
    }
}

static void join_setup(void)
{
    ULONGLONG t0 = GetTickCount64();
    int said = 0;
    FILE *f;
    for (;;) {
        NetState st;
        int end;
        ULONGLONG t = GetTickCount64() - t0;
        net_udp_lock();
        st = net_udp_state(), end = S.setup_end;
        if (!end && (st == NET_TIMED_OUT || st == NET_CLOSED) && t < 60000) net_udp_reconnect(); /* the host was not up yet */
        net_udp_unlock();
        if (end) break;
        if (st == NET_REFUSED) sat_lobby_back("other-version"), port_exit("netplay: the host runs a different copy of the game", 7);
        if (t >= 60000) sat_lobby_back("no-host"), port_exit("netplay: no host answered in 60 s", 7);
        if (!said && t > 2000) said = 1, game_log_line("netplay: waiting for the host at %s", g_opt.join);
        if (!sat_video_headless()) gfx_pump(); /* the window stays answering */
        net_udp_wait(50);
    }
    sat_cd_fast_loads = S.fast;
    autoload_option("--auto-load", S.autoload ? "on" : "off");
    sat_part_option(S.part[0] ? S.part : "original");
    sat_logos_option(S.logos ? "on" : "off");
    sat_online_menu_option(S.online ? "on" : "off");
    snprintf(g_copy, sizeof g_copy, "%s.netplay", save_path());
    remove(g_copy);
    if (S.save_len && S.save_got == S.save_len) {
        f = fopen(g_copy, "wb");
        if (f) fwrite(S.save, 1, S.save_len, f), fclose(f);
    }
    sat_bup_path(g_copy);
    game_log_line("netplay: joined %s as player 2 (port 2): delay %d frames, desync check every %d frames, fast disc loads %s, "
                  "auto-load %s, part %s; the host's save (%u bytes) is played from %s; the host is at frame %u",
                  g_opt.join, S.delay, S.hash_every, S.fast ? "on" : "off", S.autoload ? "on" : "off", S.part[0] ? S.part : "original",
                  S.save_len, g_copy, S.last_final);
}

/* sat_lobby.c: the link on the game's port with the game's digest (join NULL: a host) */
int sat_net_open(const char *join, NetUdpMessage on_msg, NetUdpState on_state)
{
    u8 dg[NET_DIGEST];
    if (!digest(g_image ? g_image : "", dg)) port_log("netplay: can't read the code image for the digest\n");
    return net_udp_start(g_opt.port > 0 ? g_opt.port : SAT_NET_PORT, join, dg, on_msg, on_state, NULL);
}

void sat_net_start(const char *image)
{
    u8 dg[NET_DIGEST];
    int port = g_opt.port > 0 ? g_opt.port : SAT_NET_PORT;
    g_image = image;
#ifdef SAT_NET_IN_GAME /* online play starts in the game's own menu: a saved --net from the old Online menu is not taken */
    if (g_opt.saved && !sat_lobby_match && (g_opt.host || g_opt.join))
        port_log("netplay: --net is taken only from the game's own online screens; this run plays alone\n"), g_opt.host = g_opt.join = NULL;
    if (!sat_lobby_match) host_online_forget();
#endif
    if (!g_opt.host && !g_opt.join) return;
    if (host_arg("--replay")) {
        port_log("netplay: off while a recording is replayed\n");
        return;
    }
    S.on = 1, S.host = g_opt.join == NULL;
    if (S.host && atoi(g_opt.host) > 0) port = atoi(g_opt.host);
    S.delay = g_opt.delay >= 0 && g_opt.delay <= 30 ? g_opt.delay : SAT_NET_DELAY;
    S.hash_every = g_opt.every >= 0 ? g_opt.every : SAT_NET_HASH_EVERY;
    S.pace = g_opt.pace, S.wait = g_opt.wait, S.test_frame = g_opt.test_frame;
    S.test_addr = g_opt.test_addr ? g_opt.test_addr : SAT_NET_TEST_BYTE;
    S.own_next = 1;
    net_inputs_init(&S.out), net_inputs_init(&S.in);
    host_window_option("--pause-unfocused", "off"); /* a paused copy would stop the other player's game too */
    sat_movie_option("--movie-native", "off");      /* the game's own movie player: the native one keeps the real clock's time */
    session_option("--record", "off");              /* a recording would hold this side's pad only */
    if (!digest(image, dg)) port_log("netplay: can't read the code image %s for the digest\n", image);
    if (S.host) snapshot();
    net_udp_test(g_opt.loss, g_opt.lag, g_opt.jitter);
#ifdef SAT_NET_DROP_MS
    net_udp_drop(SAT_NET_DROP_MS); /* game.json netplay drop_ms */
    port_log("netplay: the other player is gone after %u ms without a packet (game.json netplay drop_ms)\n", (u32)SAT_NET_DROP_MS);
#endif
    if (net_udp_start(port, g_opt.join, dg, sat_net_msg, sat_net_link, NULL))
        sat_lobby_back("no-network"), port_exit("netplay: the network did not start", 7);
    if (g_opt.loss || g_opt.lag || g_opt.jitter)
        game_log_line("netplay: TEST network: %d%% of packets lost, %d ms late, up to %d ms more", g_opt.loss, g_opt.lag, g_opt.jitter);
    if (S.test_frame) game_log_line("netplay: TEST: a planted desync at frame %d (%08x)", S.test_frame, S.test_addr);
    if (S.host) {
        char at[64], now[160];
        if (!net_udp_address(at, sizeof at)) snprintf(at, sizeof at, "this computer's address");
        game_log_line("netplay: hosting on UDP port %d at %s: delay %d frames, desync check every %d frames, %s, %s", port, at,
                      S.delay, S.hash_every, S.wait ? "frame 1 waits for player 2" : "player 2 can join at any time",
                      S.pace ? "at the game's rate" : "unpaced (a test)");
        snprintf(now, sizeof now, "hosting: player 2 joins %s:%d", at, port);
        sat_online_begin(at);
        host_online_match(1, now);
    } else {
        char now[160];
        join_setup();
        sat_online_begin(g_opt.join);
        snprintf(now, sizeof now, "player 2, joined %s", g_opt.join);
        host_online_match(0, now);
    }
}

void sat_net_started(void)
{
    const char *p = host_arg("--part-choice");
    if (!S.on || !S.host) return;
    net_udp_lock();
    S.fast = sat_cd_fast_loads > 0, S.autoload = autoload_fn != 0, S.logos = sat_logos_on(), S.online = sat_menus_online();
    snprintf(S.part, sizeof S.part, "%s", p && *p ? p : "original");
    S.started = 1;
    net_udp_unlock();
    game_log_line("netplay: the host's settings for player 2: fast disc loads %s, auto-load %s, part %s, save %u bytes",
                  S.fast ? "on" : "off", S.autoload ? "on" : "off", S.part, g_snap_len);
}
#endif

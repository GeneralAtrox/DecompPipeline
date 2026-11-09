/* sat_net.c: see sat_net.h. The frame loop's side of online play: each frame's pads, the waits for the other copy, the RAM
 * hash and the summary. The worker thread (base/net_udp.c) hands messages to sat_net_msg with the lock held; everything
 * here that both threads touch is read and written under that lock. A frame f is the one the next vertical blank starts:
 * its pads are what the game reads until the blank after. */
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#define SAT_NET_INTERNAL
#include "sat.h"
#include "sat_sys.h"
#include "sat_game.h"
#include "sat_net.h"
#include "game_log.h"
#include "host.h"

#ifdef SAT_NETPLAY
SatNet sat_net;
#define S sat_net
enum { BATCH_R = 200, BATCH_J = 250, BATCH_H = 150, BINS = 2000 };
enum { W_FIRST, W_JOINER, W_HOST };
static u8 g_flags;
static u16 g_w0, g_w1;               /* this frame's pads, as decided */
static LARGE_INTEGER g_freq;
static LONGLONG g_waited, g_last; /* g_last: when the last vertical blank went */
static u32 g_bins[BINS + 1], g_frames, g_wait_frames, g_hashes;
static double g_cost_max, g_wait_ms, g_wait_max, g_hash_ms, g_hash_max;

int sat_net_on(void) { return S.on; }
long long sat_net_next(void) { return g_freq.QuadPart ? g_last + g_freq.QuadPart / sat_sys_hz() : 0; }
u32 sat_net_get32(const u8 *p) { return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24; }
void sat_net_put32(u8 *p, u32 v) { p[0] = (u8)v, p[1] = (u8)(v >> 8), p[2] = (u8)(v >> 16), p[3] = (u8)(v >> 24); }
static u16 get16(const u8 *p) { return (u16)(p[0] | p[1] << 8); }
static void put16(u8 *p, u32 v) { p[0] = (u8)v, p[1] = (u8)(v >> 8); }
static u64 get64(const u8 *p) { return (u64)sat_net_get32(p) | (u64)sat_net_get32(p + 4) << 32; }
static void put64(u8 *p, u64 v) { sat_net_put32(p, (u32)v), sat_net_put32(p + 4, (u32)(v >> 32)); }
static LONGLONG now(void)
{
    LARGE_INTEGER c;
    if (!g_freq.QuadPart) QueryPerformanceFrequency(&g_freq);
    QueryPerformanceCounter(&c);
    return c.QuadPart;
}
static double ms(LONGLONG t) { return (double)t * 1000.0 / (double)g_freq.QuadPart; }

/* lock held: the frame's record, made when first asked for */
static NetRec *rec(u32 f)
{
    if (f >= S.cap) {
        u32 n = S.cap ? S.cap : 1u << 16;
        NetRec *r;
        while (n <= f) n *= 2;
        r = (NetRec *)realloc(S.rec, n * sizeof *r);
        if (!r) port_log("netplay: out of memory at frame %u\n", f), ExitProcess(5);
        memset(r + S.cap, 0, (n - S.cap) * sizeof *r);
        S.rec = r, S.cap = n;
    }
    return &S.rec[f];
}

static NetHash *hash_at(u32 i)
{
    if (i >= S.hcap) {
        u32 n = S.hcap ? S.hcap : 1024u;
        NetHash *h;
        while (n <= i) n *= 2;
        h = (NetHash *)realloc(S.hash, n * sizeof *h);
        if (!h) port_log("netplay: out of memory at hash %u\n", i), ExitProcess(5);
        memset(h + S.hcap, 0, (n - S.hcap) * sizeof *h);
        S.hash = h, S.hcap = n;
    }
    return &S.hash[i];
}

static void hash_check(u32 i)
{
    NetHash *h = hash_at(i);
    if ((h->have & 3) != 3) return;
    if (h->mine == h->theirs) S.matched++;
    else if (!S.desync) S.desync = i * (u32)S.hash_every, S.desync_a = h->mine, S.desync_b = h->theirs;
}

/* a message of the in-order stream (net_inputs): a fast copy on the unreliable channel, the sure one on the reliable */
static void stream_send(const u8 *msg, int len, int fast)
{
    u8 f[NET_MAX_MSG], s[NET_MAX_MSG];
    int fl = 0, sl = 0;
    if (net_inputs_pack(&S.out, msg, len, f + 1, NET_MAX_MSG - 1, &fl, s + 1, NET_MAX_MSG - 1, &sl)) return;
    s[0] = M_SURE;
    net_udp_send(1, s, sl + 1);
    if (fast && fl) f[0] = M_FAST, net_udp_send(0, f, fl + 1);
}

/* the host: every decided frame not sent yet, {first, n, n x {port 2 there, pad 1, pad 2}} */
static void send_records(void)
{
    u8 m[5 + BATCH_R * 5];
    while (S.r_sent && S.r_sent <= S.last_final) {
        u32 first = S.r_sent, n = S.last_final - first + 1u;
        if (n > BATCH_R) n = BATCH_R;
        sat_net_put32(m, first), m[4] = (u8)n;
        for (u32 i = 0; i < n; i++) {
            const NetRec *r = rec(first + i);
            m[5 + 5 * i] = (u8)(r->flags & R_P1), put16(m + 6 + 5 * i, r->w0), put16(m + 8 + 5 * i, r->w1);
        }
        S.r_sent += n;
        stream_send(m, 5 + 5 * (int)n, S.r_sent > S.last_final);
    }
}

/* the joiner: its pad for each frame up to upto, {first, n, n x pad} */
static void send_words(u32 upto)
{
    u8 m[5 + BATCH_J * 2];
    while (S.j_sent <= upto) {
        u32 first = S.j_sent, n = upto - first + 1u;
        if (n > BATCH_J) n = BATCH_J;
        sat_net_put32(m, first), m[4] = (u8)n;
        for (u32 i = 0; i < n; i++) put16(m + 5 + 2 * i, rec(first + i)->jw);
        S.j_sent += n;
        stream_send(m, 5 + 2 * (int)n, S.j_sent > upto);
    }
}

static void send_hashes(u32 first, u32 last)
{
    u8 m[6 + BATCH_H * 8];
    while (first <= last) {
        u32 n = last - first + 1u;
        if (n > BATCH_H) n = BATCH_H;
        m[0] = M_HASH, sat_net_put32(m + 1, first), m[5] = (u8)n;
        for (u32 i = 0; i < n; i++) put64(m + 6 + 8 * i, hash_at(first + i)->mine);
        net_udp_send(1, m, 6 + 8 * (int)n);
        first += n;
    }
}

static void take_stream(void)
{
    u8 m[NET_INPUT_MSG];
    int n;
    while ((n = net_inputs_next(&S.in, m, sizeof m)) > 0) {
        u32 first = n >= 5 ? sat_net_get32(m) : 0u, k = n >= 5 ? m[4] : 0u;
        if (!first || first > (1u << 26) || n < 5 + (int)k * (S.host ? 2 : 5)) continue;
        for (u32 i = 0; i < k; i++) {
            NetRec *r = rec(first + i);
            if (S.host) {
                if (first + i > S.last_final) r->jw = get16(m + 5 + 2 * i), r->flags = (u8)(r->flags | R_JHAVE);
            } else {
                r->flags = (u8)(R_HAVE | (m[5 + 5 * i] & R_P1)), r->w0 = get16(m + 6 + 5 * i), r->w1 = get16(m + 8 + 5 * i);
            }
        }
    }
}

int sat_net_msg(void *ctx, const u8 *m, int len, int reliable)
{
    (void)ctx;
    if (len < 1) return 1;
    switch (m[0]) {
    case M_FAST:
    case M_SURE:
        if (reliable && !net_inputs_fits(&S.in, m + 1, len - 1)) return 0;
        net_inputs_on_packet(&S.in, m + 1, len - 1);
        take_stream();
        break;
    case M_HASH:
        if (len >= 6 && len >= 6 + 8 * m[5] && sat_net_get32(m + 1) < (1u << 22))
            for (u32 i = 0, at = sat_net_get32(m + 1); i < m[5]; i++) {
                NetHash *h = hash_at(at + i);
                h->theirs = get64(m + 6 + 8 * i), h->have = (u8)(h->have | 2u);
                hash_check(at + i);
            }
        break;
    case M_READY:
        if (S.host && len >= 5) S.ready_from = sat_net_get32(m + 1);
        break;
    case M_DESYNC:
        if (len >= 5 && !S.desync) S.desync = sat_net_get32(m + 1), S.desync_theirs = 1;
        break;
    case M_LEAVE: S.left = 1; break;
    default:
        if (!S.host) sat_net_setup_msg(m, len);
    }
    return 1;
}

void sat_net_link(void *ctx, NetState from, NetState to)
{
    (void)ctx;
    if (to == NET_ESTABLISHED) {
        S.link_up = 1, S.link_new = 1, S.setup_sent = 0;
        if (S.host) { /* a new player: both streams start again, and the new player gets the whole game so far */
            net_inputs_init(&S.out), net_inputs_init(&S.in);
            S.r_sent = 0, S.j0 = 0, S.ready_from = 0;
            for (u32 f = S.last_final + 1u; f < S.cap; f++) S.rec[f].flags &= (u8)~R_JHAVE;
            for (u32 i = 0; i < S.hcap; i++) S.hash[i].have = (u8)(S.hash[i].have & 1u);
        }
    } else if (from == NET_ESTABLISHED) {
        S.link_up = 0, S.link_lost = 1, S.gone = *net_udp_stats(); /* the host listens again on a fresh link */
    }
}

/* the host, lock held: a new player gets the settings, the save, the hashes and every frame so far; then each new frame */
static void host_service(void)
{
    if (S.link_new && S.started && S.link_up) {
        S.link_new = 0;
        sat_net_send_setup();
        if (S.h_last) send_hashes(1, S.h_last);
        S.r_sent = 1, S.setup_sent = 1;
        game_log_line("netplay: player 2 connected; they replay the %u frames so far, then join", S.last_final);
    }
    if (S.link_up && S.setup_sent) send_records();
}

static int waited_enough(u32 f, int what)
{
    if (S.desync || S.left) return 1;
    if (what == W_FIRST) return S.ready_from != 0;
    if (what == W_JOINER) return !S.j0 || !S.link_up || (rec(f)->flags & R_JHAVE);
    return !S.link_up || (rec(f)->flags & R_HAVE);
}

/* lock held on entry and exit: until the frame can go on. The time is waiting, not the frame's cost */
static void wait_for(u32 f, int what)
{
    static const char *const words[] = {"a player to join", "player 2's pad", "the host's pads"};
    LONGLONG t0 = now(), w;
    int told = 0;
    while (!waited_enough(f, what)) {
        if (S.host) host_service();
        net_udp_unlock();
        sat_net_idle(0); /* the window, then an in-between frame that is due or a slice of the wait (sat_net_setup.c) */
        net_udp_lock();
        if (!told && now() - t0 > g_freq.QuadPart) told = 1, game_log_line("netplay: frame %u: waiting for %s", f, words[what]);
        if (what == W_FIRST && now() - t0 > 60 * g_freq.QuadPart) net_udp_unlock(), sat_lobby_back("no-player"), net_udp_lock();
    }
    sat_online_watch(f, SAT_NET_NO_PAD);
    w = now() - t0;
    g_waited += w, g_wait_frames++, g_wait_ms += ms(w);
    if (ms(w) > g_wait_max) g_wait_max = ms(w);
}

static void stop(const char *why, int code) /* lock held */
{
    net_udp_unlock();
    sat_lobby_back(code == 6 ? "out-of-step" : "link-lost"); /* a match the game's own menu started goes back to it */
    port_exit(why, code);
    for (;;) pace_wait_us(1000000); /* another thread is ending the run */
}

static void desync_stop(void)
{
    u8 m[5];
    if (S.desync_theirs) {
        game_log_line("netplay: DESYNC at frame %u, found by the other copy: the two games went out of step", S.desync);
    } else {
        m[0] = M_DESYNC, sat_net_put32(m + 1, S.desync);
        net_udp_send(1, m, 5);
        game_log_line("netplay: DESYNC at frame %u: this copy's RAM hash %016llx, the other's %016llx: the two games went out of step",
                      S.desync, S.desync_a, S.desync_b);
    }
    stop("netplay: the two copies went out of step", 6);
}

static void host_frame(u32 f, u16 w)
{
    NetRec *r;
    while (S.own_next < f + (u32)S.delay) rec(S.own_next++)->w0 = 0; /* the first frames, before any pad was taken */
    rec(f + (u32)S.delay)->w0 = w, S.own_next = f + (u32)S.delay + 1u;
    host_service();
    if (f == 1 && S.wait && !S.ready_from) wait_for(f, W_FIRST);
    if (S.ready_from && S.link_up) {
        S.j0 = S.ready_from > f ? S.ready_from : f, S.ready_from = 0;
        game_log_line("netplay: player 2 is in from frame %u (port 2), delay %d frames", S.j0, S.delay);
    }
    if (S.j0 && f >= S.j0 && !(rec(f)->flags & R_JHAVE)) wait_for(f, W_JOINER);
    r = rec(f);
    r->w1 = 0, r->flags &= (u8)~R_P1;
    if (S.j0 && f >= S.j0) {
        if (r->flags & R_JHAVE) r->w1 = r->jw, r->flags = (u8)(r->flags | R_P1);
        else game_log_line("netplay: frame %u: player 2 is gone; port 2 is empty from here", f), S.j0 = 0;
    }
    r->flags = (u8)(r->flags | R_HAVE), S.last_final = f;
    host_service();
}

static void join_frame(u32 f, u16 w)
{
    if (!S.ready && f > S.last_final && !(rec(f)->flags & R_HAVE)) { /* last_final: the host's frames when this one joined */
        u8 m[5];
        S.ready = 1, S.j_sent = f;
        for (u32 g = f; g < f + (u32)S.delay; g++) rec(g)->jw = 0;
        m[0] = M_READY, sat_net_put32(m + 1, f);
        net_udp_send(1, m, 5);
        game_log_line("netplay: caught up with the host at frame %u: playing live as player 2", f);
    }
    if (S.ready) {
        rec(f + (u32)S.delay)->jw = (u16)(autoload_fn ? 0u : w); /* the auto-load drives player 1's pad only */
        send_words(f + (u32)S.delay);
    }
    if (!(rec(f)->flags & R_HAVE)) wait_for(f, W_HOST);
    if (!(rec(f)->flags & R_HAVE) && !S.desync) {
        game_log_line("netplay: frame %u: the host left", f);
        stop("netplay: the host left", 0);
    }
}

static void ram_hash(u32 f)
{
    LONGLONG t = now();
    u32 l[8] = {0x811c9dc5u, 0x050c5d1fu, 0x9e3779b9u, 0x7f4a7c15u, 0x85ebca6bu, 0xc2b2ae35u, 0x27d4eb2fu, 0x165667b1u};
    const u32 *a = (const u32 *)sat_ram_h(), *b = (const u32 *)sat_ram_l();
    u32 i = f / (u32)S.hash_every;
    u64 h = 0;
    /* FNV-1a in 32-bit lanes (the kit is a 32-bit build, where a 64-bit multiply is a call): eight chains side by side */
    for (u32 k = 0; k < SAT_WRAM_SIZE / 4u; k += 4)
        for (u32 j = 0; j < 4; j++) l[j] = (l[j] ^ a[k + j]) * 16777619u, l[j + 4] = (l[j + 4] ^ b[k + j]) * 16777619u;
    for (u32 j = 0; j < 8; j++) h = (h ^ l[j]) * 0x100000001b3ull;
    net_udp_lock();
    hash_at(i)->mine = h, hash_at(i)->have = (u8)(hash_at(i)->have | 1u), S.h_last = i;
    if (S.link_up && (!S.host || S.setup_sent)) send_hashes(i, i);
    hash_check(i);
    if (S.desync) desync_stop();
    net_udp_unlock();
    g_hashes++, g_hash_ms += ms(now() - t);
    if (ms(now() - t) > g_hash_max) g_hash_max = ms(now() - t);
}

void sat_net_vblank(void)
{
    HostPad pad[HOST_PADS];
    u32 f = sat_vblank_count() + 1u, bin;
    u16 w;
    LONGLONG t0;
    double cost;
    if (!S.on) return;
    if (S.host && S.pace) sat_net_pace();
    t0 = now(), g_waited = 0;
    host_pads_read(pad);
    w = (u16)sat_pad_word(&pad[0]);
    net_udp_lock();
    sat_online_watch(f, w);
    if (S.link_lost) {
        S.link_lost = 0;
        if (S.host && (S.j0 || S.ready_from)) game_log_line("netplay: player 2 left at frame %u", f);
        if (S.host) S.j0 = 0, S.ready_from = 0;
    }
    if (S.host) host_frame(f, w);
    else join_frame(f, w);
    if (S.desync) desync_stop();
    g_flags = rec(f)->flags, g_w0 = rec(f)->w0, g_w1 = rec(f)->w1;
    net_udp_unlock();
    if (S.hash_every > 0 && f % (u32)S.hash_every == 0) ram_hash(f);
    if (S.test_frame && f == (u32)S.test_frame) sat_net_plant(f);
    sat_online_frame(f, (g_flags & R_P1) != 0);
    cost = ms(now() - t0 - g_waited);
    bin = (u32)(cost * 100.0);
    g_bins[bin < BINS ? bin : BINS]++, g_frames++;
    if (cost > g_cost_max) g_cost_max = cost;
    g_last = now(); /* the blank goes now */
}

u32 sat_net_pads(u32 data[2])
{
    data[0] = (~(u32)g_w0 & 0xffffu) | 0x0700u;
    data[1] = (~(u32)g_w1 & 0xffffu) | 0x0700u;
    return 1u | (g_flags & R_P1 ? 2u : 0u);
}

static double pct(double p)
{
    u32 want = (u32)(p * g_frames), sum = 0;
    for (u32 b = 0; b <= BINS; b++)
        if ((sum += g_bins[b]) > want) return b == BINS ? g_cost_max : (b + 1) * 0.01;
    return g_cost_max;
}

void sat_net_end(void)
{
    static int done;
    NetStats t;
    u32 matched, last, lost = net_udp_lost();
    if (!S.on || done) return;
    done = 1;
    net_udp_lock();
    t = S.link_up || !S.gone.sent ? *net_udp_stats() : S.gone, matched = S.matched, last = sat_vblank_count();
    net_udp_unlock();
    game_log_line("netplay: end at frame %u as the %s: %u desync checks matched%s; waited for the other copy in %u frames (%.0f ms in all, longest %.0f ms)",
                  last, S.host ? "host" : "joiner", matched, S.desync ? ", then one differed" : ", none differed", g_wait_frames, g_wait_ms, g_wait_max);
    game_log_line("netplay: netplay's frame cost p50 %.2f ms, p99 %.2f ms, worst %.2f ms over %u frames; RAM hash %.2f ms on average, worst %.2f ms",
                  pct(0.5), pct(0.99), g_cost_max, g_frames, g_hashes ? g_hash_ms / g_hashes : 0.0, g_hash_max);
    game_log_line("netplay: link: round trip %u ms, %u packets sent, %u received, %u resent, %u bad; the test network dropped %u",
                  t.rtt_ms, t.sent, t.received, t.retransmits, t.bad, lost);
    net_udp_finish(2000);
}
#endif

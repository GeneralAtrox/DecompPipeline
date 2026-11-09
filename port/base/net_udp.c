/* See net_udp.h. The worker wakes for each packet and at least every 2 ms, so the link's clock (net_link_tick) never lags. */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "net_udp.h"
#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "bcrypt.lib")

#define OUT_QUEUE 8192 /* reliable messages past the link's window: a long session's history for a player who joins late */
#define TEST_HELD 2048

static SRWLOCK s_lock = SRWLOCK_INIT;
static SOCKET s_udp = INVALID_SOCKET;
static struct sockaddr_in s_peer, s_join_addr;
static int s_peer_set, s_hosting, s_joining, s_running;
static NetLink s_link;
static u8 s_digest[NET_DIGEST];
static struct { u16 len; u8 data[NET_MAX_MSG]; } *s_out;
static unsigned s_out_head, s_out_count;
static NetState s_last = NET_IDLE;
static u8 s_held[NET_MAX_MSG];
static int s_held_len, s_held_rel;
static NetUdpMessage s_on_msg;
static NetUdpState s_on_state;
static void *s_ctx;
static HANDLE s_heard, s_thread;
static volatile LONG s_stop; /* net_udp_stop: the worker ends */
static LARGE_INTEGER s_freq;

void net_udp_lock(void) { AcquireSRWLockExclusive(&s_lock); }
void net_udp_unlock(void) { ReleaseSRWLockExclusive(&s_lock); }
static u32 now_ms(void)
{
    LARGE_INTEGER c;
    if (!s_freq.QuadPart) QueryPerformanceFrequency(&s_freq);
    QueryPerformanceCounter(&c);
    return (u32)(c.QuadPart * 1000 / s_freq.QuadPart);
}
static u32 rnd(u32 below)
{
    u32 r = 0;
    BCryptGenRandom(NULL, (PUCHAR)&r, sizeof r, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    return below ? r % below : r;
}
static const char *state_name(NetState s)
{
    static const char *const n[] = {"idle", "listening", "connecting", "up", "closed", "timed out", "refused"};
    return (unsigned)s < sizeof n / sizeof n[0] ? n[s] : "?";
}

/* ---- the test network: XB_NET_TEST_*'s, as options ---- */
static struct { u32 due; int len; u8 data[NET_MAX_PACKET]; } *s_test;
static int s_loss, s_lag, s_jitter;
static u32 s_lost;
void net_udp_test(int loss_pct, int lag_ms, int jitter_ms) { s_loss = loss_pct, s_lag = lag_ms, s_jitter = jitter_ms; }
static u32 s_drop_ms;
void net_udp_drop(u32 ms) { s_drop_ms = ms; }
u32 net_udp_lost(void) { return s_lost; }

static void raw_send(const u8 *p, int len)
{
    if (s_peer_set) sendto(s_udp, (const char *)p, len, 0, (const struct sockaddr *)&s_peer, sizeof s_peer);
}

static void test_tick(u32 now, int all)
{
    for (unsigned i = 0; s_test && i < TEST_HELD; i++)
        if (s_test[i].len && (all || (int)(now - s_test[i].due) >= 0)) raw_send(s_test[i].data, s_test[i].len), s_test[i].len = 0;
}

static void carrier(void *ctx, const uint8_t *p, int len)
{
    (void)ctx;
    if (!s_test) { raw_send(p, len); return; }
    if (rnd(100) < (u32)s_loss) { s_lost++; return; }
    for (unsigned i = 0; i < TEST_HELD; i++) {
        if (s_test[i].len) continue;
        s_test[i].due = now_ms() + (u32)s_lag + rnd((u32)s_jitter + 1u);
        s_test[i].len = len;
        memcpy(s_test[i].data, p, (size_t)len);
        return;
    }
    s_lost++;
}

/* ---- the link ---- */
int net_udp_send(int reliable, const void *msg, int len)
{
    unsigned slot;
    if (net_link_state(&s_link) != NET_ESTABLISHED || len <= 0 || len > NET_MAX_MSG) return -1;
    if (!reliable) return net_link_send(&s_link, 0, msg, len, now_ms());
    if (!s_out_count && net_link_send(&s_link, 1, msg, len, now_ms()) == 0) return 0;
    if (s_out_count == OUT_QUEUE) {
        port_log("network: the other player stopped taking messages, closing the link\n");
        net_link_close(&s_link);
        return -1;
    }
    slot = (s_out_head + s_out_count++) % OUT_QUEUE;
    s_out[slot].len = (u16)len;
    memcpy(s_out[slot].data, msg, (size_t)len);
    return 0;
}

static void out_flush(void)
{
    while (s_out_count && net_link_send(&s_link, 1, s_out[s_out_head].data, s_out[s_out_head].len, now_ms()) == 0)
        s_out_head = (s_out_head + 1) % OUT_QUEUE, s_out_count--;
}

static void drain(void)
{
    u8 b[NET_MAX_MSG];
    int rel, n;
    if (s_held_len) {
        if (!s_on_msg(s_ctx, s_held, s_held_len, s_held_rel)) return;
        s_held_len = 0;
    }
    while ((n = net_link_recv(&s_link, &rel, b, sizeof b)) > 0) {
        SetEvent(s_heard);
        if (!s_on_msg(s_ctx, b, n, rel)) {
            memcpy(s_held, b, (size_t)n), s_held_len = n, s_held_rel = rel;
            return;
        }
    }
}

static void link_reset(void)
{
    net_link_init(&s_link, carrier, NULL, s_digest, rnd(0));
    if (s_drop_ms) s_link.timeout_ms = s_drop_ms;
    s_out_head = s_out_count = 0;
    s_held_len = 0;
    s_peer_set = s_joining;
    s_peer = s_join_addr;
}

static void state_watch(u32 now)
{
    NetState st = net_link_state(&s_link), was = s_last;
    const NetStats *t = net_link_stats(&s_link);
    if (st == was) return;
    port_log("network: link %s -> %s (sent %u, got %u, resent %u, bad %u, round trip %u ms)\n", state_name(was), state_name(st), t->sent,
             t->received, t->retransmits, t->bad, t->rtt_ms);
    if (st == NET_REFUSED)
        port_log("network: refused: the two copies differ (%s)\n", net_link_status(&s_link) == NET_REFUSED_CONTENT ? "game data or build" : "network version");
    s_last = st;
    SetEvent(s_heard);
    if (s_on_state) s_on_state(s_ctx, was, st);
    if (s_hosting && (st == NET_CLOSED || st == NET_TIMED_OUT || st == NET_REFUSED)) { /* the session goes on: the next player can come */
        link_reset();
        net_link_listen(&s_link, now);
        s_last = NET_LISTENING;
        if (s_on_state) s_on_state(s_ctx, st, NET_LISTENING);
    }
}

static DWORD WINAPI worker(void *unused)
{
    u8 p[NET_MAX_PACKET + 64];
    (void)unused;
    while (!s_stop) {
        fd_set r;
        struct timeval tv = {0, 2000};
        u32 now;
        FD_ZERO(&r);
        FD_SET(s_udp, &r);
        select(0, &r, NULL, NULL, &tv);
        net_udp_lock();
        now = now_ms();
        for (;;) {
            struct sockaddr_in from;
            int fl = sizeof from, n = recvfrom(s_udp, (char *)p, sizeof p, 0, (struct sockaddr *)&from, &fl);
            if (n == SOCKET_ERROR) {
                if (WSAGetLastError() == WSAECONNRESET) continue; /* Windows reports a port that said no as an error here */
                break;
            }
            if (net_link_state(&s_link) == NET_LISTENING) s_peer = from, s_peer_set = 1;
            else if (!s_peer_set || from.sin_addr.s_addr != s_peer.sin_addr.s_addr || from.sin_port != s_peer.sin_port) continue;
            net_link_on_packet(&s_link, p, n, now);
        }
        net_link_tick(&s_link, now);
        test_tick(now, 0);
        out_flush();
        drain();
        state_watch(now);
        net_udp_unlock();
    }
    return 0;
}

static int resolve(const char *addr, int port, struct sockaddr_in *out)
{
    char host[256], ps[16];
    const char *colon = strrchr(addr, ':');
    struct addrinfo hints, *res = NULL;
    if (colon) snprintf(host, sizeof host, "%.*s", (int)(colon - addr), addr), snprintf(ps, sizeof ps, "%s", colon + 1);
    else snprintf(host, sizeof host, "%s", addr), snprintf(ps, sizeof ps, "%d", port);
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    if (getaddrinfo(host, ps, &hints, &res) || !res) {
        port_log("network: can't find the host '%s'\n", host);
        return -1;
    }
    memcpy(out, res->ai_addr, sizeof *out);
    freeaddrinfo(res);
    return 0;
}

int net_udp_start(int port, const char *join, const u8 digest[NET_DIGEST], NetUdpMessage on_msg, NetUdpState on_state, void *ctx)
{
    WSADATA w;
    struct sockaddr_in a;
    u_long nb = 1;
    if (s_running) return 0;
    memcpy(s_digest, digest, NET_DIGEST);
    s_on_msg = on_msg, s_on_state = on_state, s_ctx = ctx;
    s_out = malloc(OUT_QUEUE * sizeof *s_out);
    if (s_loss || s_lag || s_jitter) s_test = calloc(TEST_HELD, sizeof *s_test);
    s_heard = CreateEventA(NULL, FALSE, FALSE, NULL);
    if (!s_out || !s_heard || WSAStartup(MAKEWORD(2, 2), &w)) {
        port_log("network: Winsock did not start\n");
        return -1;
    }
    if (join && resolve(join, port, &s_join_addr)) return -1;
    s_joining = join != NULL;
    s_udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = INADDR_ANY;
    a.sin_port = htons(join ? 0 : (u_short)port);
    if (s_udp == INVALID_SOCKET || bind(s_udp, (struct sockaddr *)&a, sizeof a)) {
        port_log("network: can't open UDP port %d (in use?)\n", join ? 0 : port);
        return -1;
    }
    ioctlsocket(s_udp, FIONBIO, &nb);
    link_reset();
    if (join) {
        net_link_connect(&s_link, now_ms());
        s_last = NET_CONNECTING;
    } else {
        s_hosting = 1;
        net_link_listen(&s_link, now_ms());
        s_last = NET_LISTENING;
    }
    s_running = 1;
    s_thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
    port_log("network: %s, UDP port %d\n", join ? "joining" : "hosting", join ? 0 : port);
    if (s_test) port_log("network: TEST NETWORK: %d%% of packets lost, %d ms late, up to %d ms more\n", s_loss, s_lag, s_jitter);
    return 0;
}

NetState net_udp_state(void) { return net_link_state(&s_link); }
const NetStats *net_udp_stats(void) { return net_link_stats(&s_link); }

void net_udp_reconnect(void)
{
    if (!s_joining) return;
    link_reset();
    net_link_connect(&s_link, now_ms());
    s_last = NET_CONNECTING;
}

void net_udp_wait(u32 ms)
{
    if (s_heard) WaitForSingleObject(s_heard, ms);
    else Sleep(ms);
}

void net_udp_finish(u32 ms)
{
    u32 t0 = now_ms();
    if (!s_running) return;
    for (;;) {
        int busy = 0;
        net_udp_lock();
        if (net_link_state(&s_link) == NET_ESTABLISHED) {
            busy = s_out_count != 0;
            for (int i = 0; i < NET_WINDOW && !busy; i++) busy = s_link.out[i].used;
        }
        if (!busy || now_ms() - t0 >= ms) {
            s_hosting = 0;
            if (net_link_state(&s_link) == NET_ESTABLISHED) port_log("network: closing the link%s\n", busy ? " (some messages were not acknowledged)" : "");
            net_link_close(&s_link);
            test_tick(0, 1); /* the test network's held packets, the goodbye too, go now */
            net_udp_unlock();
            return;
        }
        net_udp_unlock();
        Sleep(2);
    }
}

/* The link closes, the worker ends and the port is let go: net_udp_start may open another (a game's own online screens
 * try a lobby, give up, and may try again in the same run) */
void net_udp_stop(void)
{
    if (!s_running) return;
    net_udp_finish(0);
    InterlockedExchange(&s_stop, 1);
    if (s_thread) WaitForSingleObject(s_thread, 5000), CloseHandle(s_thread);
    closesocket(s_udp), WSACleanup();
    free(s_out), free(s_test), CloseHandle(s_heard);
    s_thread = NULL, s_udp = INVALID_SOCKET, s_out = NULL, s_test = NULL, s_heard = NULL;
    s_running = 0, s_hosting = 0, s_joining = 0, s_last = NET_IDLE, s_stop = 0;
    s_out_head = 0, s_out_count = 0, s_peer_set = 0, s_held_len = 0;
}

/* The address of the interface the default route leaves by: a UDP socket "connected" to a public address (nothing is
 * sent) is bound to it. */
int net_udp_address(char *out, int cap)
{
    WSADATA w;
    struct sockaddr_in to = {0}, me = {0};
    int len = sizeof me, ok = 0;
    SOCKET s;
    if (WSAStartup(MAKEWORD(2, 2), &w)) return 0;
    s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    to.sin_family = AF_INET, to.sin_port = htons(53);
    inet_pton(AF_INET, "8.8.8.8", &to.sin_addr);
    if (s != INVALID_SOCKET && !connect(s, (struct sockaddr *)&to, sizeof to) && !getsockname(s, (struct sockaddr *)&me, &len))
        ok = inet_ntop(AF_INET, &me.sin_addr, out, (size_t)cap) != NULL && me.sin_addr.s_addr != 0;
    if (s != INVALID_SOCKET) closesocket(s);
    WSACleanup();
    return ok;
}

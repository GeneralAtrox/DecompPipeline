/* The game's sockets over one link to the other player: XAM's NetDll_ imports answered by a table of sockets here, carried by
 * net_link on a single real UDP port (XB_NET_PORT). Set XB_NET=1 to turn it on; off, every call is left to the code before this.
 *
 * Mega Man 10 (session code 821b2250..821b5668, lockstep 821b6ea8..821b79a0) talks on three kinds of traffic, and each is
 * carried for what the game needs from it rather than what the console gave it:
 * - "TCP" between members. 821b3a78 reads recv(8) then recv(0x4e8) as if a stream were messages, which breaks on any split or
 *   joined read. Here a stream keeps message boundaries, and a recv never crosses the end of a message.
 * - UDP signalling (join, member info, keep-alive), never retried by the game: the link's reliable channel.
 * - UDP lockstep (type 7): net_inputs, because 821b74f8 marks every frame up to a packet's newest as received, so a burst of
 *   lost packets longer than the game's repeats is a silent desync. Delivered in order with nothing skipped, it can't happen. */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb_net.h"
#include "net_link.h"

#define PORT_DEFAULT 29710
#define OUT_QUEUE 3600                /* a minute of the game's 60 a second, since it goes on sending while the other copy has stopped: more than GAME_SILENCE_MS */
#define GAME_SILENCE_MS 30000u        /* the worker answers even with its game hung, and the longest load is far less than this */

static SRWLOCK s_lock = SRWLOCK_INIT;
static int s_on = -1;
static SOCKET s_udp = INVALID_SOCKET;
static struct sockaddr_in s_peer;
static int s_peer_set, s_hosting, s_running;
static NetLink s_link;
static u8 s_digest[NET_DIGEST];
static struct { u16 len; u8 data[NET_MAX_MSG]; } *s_out;
static unsigned s_out_head, s_out_count;
static NetState s_last_state = NET_IDLE;
static u8 s_held[NET_MAX_MSG];
static int s_held_len;
static u32 s_game_at;

void xb_net_lock(void) { AcquireSRWLockExclusive(&s_lock); }
void xb_net_unlock(void) { ReleaseSRWLockExclusive(&s_lock); }
void xb_net_random(void *out, unsigned len) { BCryptGenRandom(NULL, (PUCHAR)out, len, BCRYPT_USE_SYSTEM_PREFERRED_RNG); }
static u32 now_ms(void) { return (u32)GetTickCount64(); }

int xb_net_on(void)
{
    if (s_on < 0) s_on = getenv("XB_NET") != NULL || getenv("XB_NET_JOIN") != NULL;
    return s_on;
}

/* A bad network to test on: XB_NET_TEST_LOSS (percent of packets lost), XB_NET_TEST_LAG and XB_NET_TEST_JITTER (ms). Off in play */
#define TEST_HELD 1024
static struct { u32 due; int len; u8 data[NET_MAX_PACKET]; } *s_test;
static int s_test_loss, s_test_lag, s_test_jitter;

static void test_network_start(void)
{
    const char *loss = getenv("XB_NET_TEST_LOSS"), *lag = getenv("XB_NET_TEST_LAG"), *jitter = getenv("XB_NET_TEST_JITTER");
    if (!loss && !lag && !jitter) return;
    s_test_loss = loss ? atoi(loss) : 0;
    s_test_lag = lag ? atoi(lag) : 0;
    s_test_jitter = jitter ? atoi(jitter) : 0;
    s_test = calloc(TEST_HELD, sizeof *s_test);
    if (!s_test) xb_stop("network", "no memory for the test network", 0);
    port_log("network: TEST NETWORK: %d%% lost, %d ms late, up to %d ms more\n", s_test_loss, s_test_lag, s_test_jitter);
}

static u32 test_random(u32 below)
{
    u32 r;
    xb_net_random(&r, sizeof r);
    return below ? r % below : 0;
}

static void test_network_send(const uint8_t *p, int len)
{
    unsigned i;
    if (test_random(100) < (u32)s_test_loss) return;
    for (i = 0; i < TEST_HELD; i++) {
        if (s_test[i].len) continue;
        s_test[i].due = now_ms() + (u32)s_test_lag + test_random((u32)s_test_jitter + 1);
        s_test[i].len = len;
        memcpy(s_test[i].data, p, (size_t)len);
        return;
    }
}

static void test_network_tick(u32 now)
{
    unsigned i;
    if (!s_test) return;
    for (i = 0; i < TEST_HELD; i++) {
        if (s_test[i].len && (int)(now - s_test[i].due) >= 0) {
            if (s_peer_set) sendto(s_udp, (const char *)s_test[i].data, s_test[i].len, 0, (const struct sockaddr *)&s_peer, sizeof s_peer);
            s_test[i].len = 0;
        }
    }
}

/* XB_NET_TEST_STALL=frame,ms: this copy stops for that long once, as a slow disk or a busy machine can make it. Off in play */
void xb_net_test_stall(void)
{
    static int frame = -1, ms;
    if (frame == -1) {
        const char *e = getenv("XB_NET_TEST_STALL");
        frame = -2;
        if (e && sscanf_s(e, "%d,%d", &frame, &ms) != 2) frame = -2;
    }
    if (frame < 0 || xb_frame_number() < (u32)frame) return;
    frame = -2;
    port_log("network: TEST: stopping for %d ms\n", ms);
    Sleep((DWORD)ms);
}

static void carrier(void *ctx, const uint8_t *p, int len)
{
    (void)ctx;
    if (s_test) { test_network_send(p, len); return; }
    if (s_peer_set) sendto(s_udp, (const char *)p, len, 0, (const struct sockaddr *)&s_peer, sizeof s_peer);
}

/* ---- the link ---- */
int xb_net_link_write(int reliable, const u8 *d, unsigned len)
{
    if (net_link_state(&s_link) != NET_ESTABLISHED) return -1;
    if (!reliable) return net_link_send(&s_link, 0, d, (int)len, now_ms());
    if (!s_out_count && net_link_send(&s_link, 1, d, (int)len, now_ms()) == 0) return 0;
    if (s_out_count == OUT_QUEUE) {
        port_log("network: the other player stopped taking messages, closing the link\n");
        net_link_close(&s_link);
        return -1;
    }
    {
        unsigned slot = (s_out_head + s_out_count) % OUT_QUEUE;
        s_out[slot].len = (u16)len;
        memcpy(s_out[slot].data, d, len);
        s_out_count++;
    }
    return 0;
}

static void out_flush(void)
{
    while (s_out_count && net_link_send(&s_link, 1, s_out[s_out_head].data, s_out[s_out_head].len, now_ms()) == 0) {
        s_out_head = (s_out_head + 1) % OUT_QUEUE;
        s_out_count--;
    }
}

int xb_net_send_session(int kind, const void *data, unsigned len)
{
    u8 b[NET_MAX_MSG];
    if (len + 1 > sizeof b) return -1;
    b[0] = (u8)kind;
    if (len) memcpy(b + 1, data, len);
    return xb_net_link_write(1, b, len + 1);
}

static void link_drain(void)
{
    u8 b[NET_MAX_MSG];
    int reliable;
    if (s_held_len && xb_net_sock_deliver(s_held, (unsigned)s_held_len, 1)) s_held_len = 0;
    while (!s_held_len) {
        int n = net_link_recv(&s_link, &reliable, b, sizeof b);
        if (n <= 0) break;
        if (!xb_net_sock_deliver(b, (unsigned)n, reliable)) { memcpy(s_held, b, (size_t)n); s_held_len = n; }
    }
    xb_net_sock_drain();
}

static void link_reset(void)
{
    u32 nonce;
    xb_net_random(&nonce, sizeof nonce);
    net_link_init(&s_link, carrier, NULL, s_digest, nonce);
    xb_net_sock_reset();
    xb_net_check_reset();
    s_out_head = s_out_count = 0;
    s_held_len = 0;
    s_peer_set = 0;
    s_game_at = now_ms();
}

static void state_watch(u32 now)
{
    NetState st = net_link_state(&s_link);
    const NetStats *t;
    if (st == s_last_state) return;
    t = net_link_stats(&s_link);
    port_log("network: link %d -> %d, sent %u got %u retries %u bad %u rtt %u ms\n", (int)s_last_state, (int)st, t->sent, t->received, t->retransmits, t->bad, t->rtt_ms);
    if (st == NET_REFUSED) port_log("network: the two copies differ (%s)\n", net_link_status(&s_link) == NET_REFUSED_CONTENT ? "game data" : "program version");
    s_last_state = st;
    if (st == NET_CLOSED || st == NET_TIMED_OUT || st == NET_REFUSED) {
        xb_net_sock_streams_end();
        if (s_hosting) {                 /* the session goes on: the next player can come */
            link_reset();
            net_link_listen(&s_link, now);
            s_last_state = NET_LISTENING;
        }
    }
}

static DWORD WINAPI worker(void *unused)
{
    u8 p[NET_MAX_PACKET + 64];
    (void)unused;
    for (;;) {
        fd_set r;
        struct timeval tv = { 0, 5000 };
        u32 now;
        FD_ZERO(&r);
        FD_SET(s_udp, &r);
        select(0, &r, NULL, NULL, &tv);
        xb_net_lock();
        now = now_ms();
        for (;;) {
            struct sockaddr_in from;
            int fl = sizeof from;
            int n = recvfrom(s_udp, (char *)p, sizeof p, 0, (struct sockaddr *)&from, &fl);
            NetState st = net_link_state(&s_link);
            if (n == SOCKET_ERROR) {
                if (WSAGetLastError() == WSAECONNRESET) continue;   /* Windows reports a port that said no as an error here */
                break;
            }
            if (st == NET_LISTENING) {
                s_peer = from;
                s_peer_set = 1;
            } else if (!s_peer_set || from.sin_addr.s_addr != s_peer.sin_addr.s_addr || from.sin_port != s_peer.sin_port) {
                continue;
            }
            net_link_on_packet(&s_link, p, n, now);
        }
        net_link_tick(&s_link, now);
        test_network_tick(now);
        out_flush();
        link_drain();
        state_watch(now);
        xb_net_session_tick(now);
        xb_net_game_watch();
        xb_net_unlock();
    }
}

/* The content digest: both sides must run the same game image */
static void digest_make(void)
{
    const char *path = host_arg("--image");
    FILE *f = NULL;
    BCRYPT_ALG_HANDLE alg;
    BCRYPT_HASH_HANDLE h;
    static u8 buf[1 << 16];
    size_t n;
    if (!path || fopen_s(&f, path, "rb") || !f) xb_stop("network", "no game image to compare", 0);
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, NULL, 0) || BCryptCreateHash(alg, &h, NULL, 0, NULL, 0, 0)) xb_stop("network", "no SHA-256", 0);
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) BCryptHashData(h, buf, (ULONG)n, 0);
    fclose(f);
    BCryptFinishHash(h, s_digest, NET_DIGEST, 0);
    BCryptDestroyHash(h);
    BCryptCloseAlgorithmProvider(alg, 0);
}

static void start(void)   /* lock held */
{
    WSADATA w;
    struct sockaddr_in a;
    const char *ps = getenv("XB_NET_PORT");
    u_long nb = 1;
    if (s_running) return;
    s_running = 1;
    digest_make();
    test_network_start();
    s_out = malloc(OUT_QUEUE * sizeof *s_out);
    if (!s_out || WSAStartup(MAKEWORD(2, 2), &w)) xb_stop("network", "Winsock did not start", 0);
    s_udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = INADDR_ANY;
    a.sin_port = htons(xb_net_join_named() ? 0 : (u_short)(ps ? atoi(ps) : PORT_DEFAULT));
    if (s_udp == INVALID_SOCKET || bind(s_udp, (struct sockaddr *)&a, sizeof a)) {
        port_log("network: can't open UDP port %d\n", ntohs(a.sin_port));
        xb_stop("network", "the UDP port is taken", ntohs(a.sin_port));
    }
    ioctlsocket(s_udp, FIONBIO, &nb);
    link_reset();
    CloseHandle(CreateThread(NULL, 0, worker, NULL, 0, NULL));
    port_log("network: on, UDP port %d\n", ntohs(a.sin_port));
}

void xb_net_start(void)
{
    if (s_running) return;
    xb_net_lock();
    start();
    xb_net_unlock();
}

int xb_net_join_named(void) { return getenv("XB_NET_JOIN") != NULL; }
int xb_net_link_up(void) { return net_link_state(&s_link) == NET_ESTABLISHED; }
void xb_net_game_heard(void) { s_game_at = now_ms(); }
int xb_net_game_alive(void) { return xb_net_link_up() && now_ms() - s_game_at < GAME_SILENCE_MS; }
u16 xb_net_link_rtt(void) { u32 ms = net_link_stats(&s_link)->rtt_ms; return (u16)(ms > 0xFFFFu ? 0xFFFFu : ms); }
int xb_net_link_failed(void) { NetState st = net_link_state(&s_link); return st == NET_CLOSED || st == NET_TIMED_OUT || st == NET_REFUSED || st == NET_IDLE; }

/* Lock held by the caller, like the two below */
void xb_net_host_start(void)
{
    s_hosting = 1;
    if (net_link_state(&s_link) == NET_ESTABLISHED || net_link_state(&s_link) == NET_LISTENING) return;
    link_reset();
    net_link_listen(&s_link, now_ms());
}

void xb_net_join_start(void)
{
    const char *addr = getenv("XB_NET_JOIN"), *ps = getenv("XB_NET_PORT");
    char host[256], port[16];
    const char *colon;
    struct addrinfo hints, *res = NULL;
    NetState st = net_link_state(&s_link);
    if (st == NET_ESTABLISHED || st == NET_CONNECTING) return;
    link_reset();
    if (!addr) return;
    colon = strrchr(addr, ':');
    if (colon) {
        snprintf(host, sizeof host, "%.*s", (int)(colon - addr), addr);
        snprintf(port, sizeof port, "%s", colon + 1);
    } else {
        snprintf(host, sizeof host, "%s", addr);
        snprintf(port, sizeof port, "%d", ps ? atoi(ps) : PORT_DEFAULT);
    }
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    if (getaddrinfo(host, port, &hints, &res) || !res) {
        port_log("network: can't find the host '%s'\n", host);
        return;
    }
    memcpy(&s_peer, res->ai_addr, sizeof s_peer);
    freeaddrinfo(res);
    s_peer_set = 1;
    port_log("network: reaching %s port %s\n", host, port);
    net_link_connect(&s_link, now_ms());
}

void xb_net_link_stop(void)
{
    s_hosting = 0;
    if (net_link_state(&s_link) == NET_ESTABLISHED) net_link_close(&s_link);
    xb_net_sock_streams_end();
    link_reset();
    s_last_state = NET_IDLE;
}


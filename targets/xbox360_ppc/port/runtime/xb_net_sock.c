/* The game's sockets: the NetDll_ imports answered by a table of sockets, what comes off the link put into them, and the checks on what
 * the game may see. xb_net.c carries it all, and says why each kind of traffic goes the way it does. */
#include <stdlib.h>
#include <string.h>
#include "xb_net.h"
#include "net_link.h"
#include "net_inputs.h"

#define SOCK_BASE 0x100u
#define SOCKETS 16
#define RING 128
#define GAME_MSG 0x4F0                /* the game's largest: its packet buffer (+0xf8) */
#define GUEST_STREAM 1
#define GUEST_DGRAM 2
#define WSA_WOULDBLOCK 0x2733u
#define WSA_NOTCONN 0x2749u
#define WSA_BADF 0x2736u
#define WSA_CONNRESET 0x2746u
#define TYPE_LOCKSTEP 0x7000u

enum { K_DGRAM, K_INPUT, K_OPEN, K_DATA, K_CLOSE };   /* then the kinds in xb_net.h */

typedef struct { u16 len, off; u16 from_port; u32 from_ina; u8 data[GAME_MSG]; } Msg;
typedef struct {
    u8 used, type, listening, connected, peer_closed, accepted, mid;
    u16 port, remote;                 /* a stream: this end, the other end */
    unsigned head, count;
    Msg *ring;
} Sock;

static Sock s_sock[SOCKETS];
static __declspec(thread) u32 t_error;
static NetInputs s_inputs;

/* ---- sockets ---- */
static Sock *sock_of(u32 h) { u32 i = h - SOCK_BASE; return i < SOCKETS && s_sock[i].used ? &s_sock[i] : NULL; }
static u32 sock_handle(const Sock *s) { return SOCK_BASE + (u32)(s - s_sock); }

static Sock *sock_new(u8 type, u16 port)
{
    unsigned i;
    for (i = 0; i < SOCKETS; i++) {
        if (s_sock[i].used) continue;
        memset(&s_sock[i], 0, sizeof s_sock[i]);
        s_sock[i].used = 1;
        s_sock[i].type = type;
        s_sock[i].port = port;
        s_sock[i].ring = (Msg *)malloc(RING * sizeof(Msg));
        if (!s_sock[i].ring) xb_stop("out_of_memory", "a socket's queue", i);
        return &s_sock[i];
    }
    xb_stop("network", "more sockets than the table holds", SOCKETS);
    return NULL;
}

static void sock_free(Sock *s) { free(s->ring); memset(s, 0, sizeof *s); }

static Sock *find_dgram(u16 port)
{
    unsigned i;
    for (i = 0; i < SOCKETS; i++) if (s_sock[i].used && s_sock[i].type == GUEST_DGRAM && s_sock[i].port == port) return &s_sock[i];
    return NULL;
}

static Sock *find_stream(u16 port, u16 remote)
{
    unsigned i;
    for (i = 0; i < SOCKETS; i++) {
        Sock *s = &s_sock[i];
        if (s->used && s->type == GUEST_STREAM && !s->listening && s->port == port && s->remote == remote && !s->peer_closed) return s;
    }
    return NULL;
}

/* 0 when the ring is full and the message has to wait */
static int ring_put(Sock *s, const u8 *d, unsigned len, u16 from_port, u32 from_ina)
{
    Msg *m;
    if (s->count == RING) return 0;
    m = &s->ring[(s->head + s->count) % RING];
    m->len = (u16)len;
    m->off = 0;
    m->from_port = from_port;
    m->from_ina = from_ina;
    memcpy(m->data, d, len);
    s->count++;
    return 1;
}

static void put_ports(u8 *b, u8 kind, u16 dst, u16 src)
{
    b[0] = kind;
    b[1] = (u8)(dst >> 8);
    b[2] = (u8)dst;
    b[3] = (u8)(src >> 8);
    b[4] = (u8)src;
}

/* 0: the message has to wait for room */
int xb_net_sock_deliver(const u8 *b, unsigned n, int reliable)
{
    u16 dst, src;
    Sock *s;
    if (n == 0) return 1;
    if (b[0] <= K_CLOSE) xb_net_game_heard();
    if (b[0] == XB_NET_SEARCH_REQ || b[0] == XB_NET_SEARCH_REPLY) {
        xb_net_session_message(b[0], b + 1, n - 1);
        return 1;
    }
    if (b[0] == XB_NET_STATE_HASH) {
        xb_net_check_message(b + 1, n - 1);
        return 1;
    }
    if (b[0] == K_INPUT) {
        if (reliable && !net_inputs_fits(&s_inputs, b + 1, (int)n - 1)) return 0;
        net_inputs_on_packet(&s_inputs, b + 1, (int)n - 1);
        return 1;
    }
    if (n < 5) return 1;
    dst = (u16)(b[1] << 8 | b[2]);
    src = (u16)(b[3] << 8 | b[4]);
    switch (b[0]) {
    case K_DGRAM:
        s = find_dgram(dst);
        if (!s) return 1;
        if (!xb_net_game_message_ok(b + 5, n - 5)) { port_log("network: dropped a bad message, %u bytes\n", n - 5); return 1; }
        return ring_put(s, b + 5, n - 5, src, XB_NET_PEER_INA);
    case K_OPEN: {
        unsigned i, room = 0;
        u8 no[5];
        for (i = 0; i < SOCKETS; i++) room += !s_sock[i].used;
        for (i = 0; room && i < SOCKETS; i++) {
            if (s_sock[i].used && s_sock[i].listening && s_sock[i].port == dst) {
                /* Made now, so what the other side sends before the game's accept isn't lost */
                s = sock_new(GUEST_STREAM, dst);
                s->remote = src;
                s->connected = 1;
                return 1;
            }
        }
        put_ports(no, K_CLOSE, src, dst);   /* nobody here to take it: the caller's reads end rather than wait */
        xb_net_link_write(1, no, 5);
        return 1;
    }
    case K_DATA:
        s = find_stream(dst, src);
        if (!s) return 1;
        if (!xb_net_game_message_ok(b + 5, n - 5)) { port_log("network: dropped a bad message, %u bytes\n", n - 5); return 1; }
        return ring_put(s, b + 5, n - 5, src, XB_NET_PEER_INA);
    case K_CLOSE:
        s = find_stream(dst, src);
        if (s && !s->accepted) sock_free(s);   /* the game never saw it */
        else if (s) s->peer_closed = 1;
        return 1;
    }
    return 1;
}

void xb_net_sock_drain(void)
{
    Sock *s = find_dgram(1000);
    u8 b[NET_INPUT_MSG];
    while (s && s->count < RING) {
        int n = net_inputs_next(&s_inputs, b, sizeof b);
        if (n <= 0) return;
        if (!xb_net_game_message_ok(b, (unsigned)n)) { port_log("network: dropped a bad lockstep message, %d bytes\n", n); continue; }
        ring_put(s, b, (unsigned)n, 1000, XB_NET_PEER_INA);
    }
}

void xb_net_sock_streams_end(void)
{
    unsigned i;
    for (i = 0; i < SOCKETS; i++) {
        Sock *s = &s_sock[i];
        if (!s->used || s->type != GUEST_STREAM || s->listening) continue;
        if (s->accepted) s->peer_closed = 1;
        else sock_free(s);
    }
}

/* A new link, so a new other player: nothing of the last one's reaches the game, and the input stream starts again from nothing on both sides */
void xb_net_sock_reset(void)
{
    unsigned i;
    net_inputs_init(&s_inputs);
    for (i = 0; i < SOCKETS; i++) {
        if (s_sock[i].used && s_sock[i].type == GUEST_DGRAM) s_sock[i].head = s_sock[i].count = s_sock[i].mid = 0;
    }
}

/* ---- the game's calls: r3 is 1, the arguments from r4 ---- */
static u64 fail(u32 err) { t_error = err; return XB_STATUS(-1); }

static u64 do_sendto(Sock *s, u32 buf, u32 len, u32 to)
{
    u8 b[NET_MAX_MSG];
    u32 ina = rd32(to + 4);
    u16 dport = rd16(to + 2);
    if (!len || len > GAME_MSG) return fail(0x2738);
    if (ina == XB_NET_SELF_INA || ina == 0) {
        Sock *d = find_dgram(dport);
        if (d) ring_put(d, tc_base + buf, len, s->port, XB_NET_SELF_INA);
        return len;
    }
    if (ina != XB_NET_PEER_INA) xb_stop("network", "a send to an address nobody gave out", ina);
    if ((rd16(buf + 4) & 0xF000u) == TYPE_LOCKSTEP) {
        u8 fast[NET_MAX_MSG], sure[NET_INPUT_MSG + 16];
        int fl, sl;
        if (!xb_net_link_up()) return len;   /* nobody to hear it, and it mustn't take a number in the next player's stream */
        fast[0] = sure[0] = K_INPUT;
        if (net_inputs_pack(&s_inputs, tc_base + buf, (int)len, fast + 1, sizeof fast - 1, &fl, sure + 1, sizeof sure - 1, &sl)) return fail(0x2738);
        xb_net_link_write(0, fast, (unsigned)fl + 1);
        xb_net_link_write(1, sure, (unsigned)sl + 1);
    } else {
        put_ports(b, K_DGRAM, dport, s->port);
        memcpy(b + 5, tc_base + buf, len);
        xb_net_link_write(1, b, len + 5);
    }
    return len;
}

/* A recv takes from one message and stops at its end. The game's two-part read (821b3a78) sees an empty second part as a 0, and its
 * second recv never starts on the next message */
static u64 do_recv(Sock *s, u32 buf, u32 cap, u32 from, u32 fromlen)
{
    Msg *m;
    u32 n;
    if (!s->count) {
        if (s->mid) { s->mid = 0; return 0; }
        return s->type == GUEST_STREAM && s->peer_closed ? 0 : fail(WSA_WOULDBLOCK);
    }
    m = &s->ring[s->head];
    if (s->type == GUEST_DGRAM && xb_net_game_hold(m->data, m->len)) return fail(WSA_WOULDBLOCK);
    if (s->mid && m->off == 0) { s->mid = 0; return 0; }
    n = (u32)m->len - m->off;
    if (n > cap) n = cap;
    memcpy(tc_base + buf, m->data + m->off, n);
    m->off = (u16)(m->off + n);
    if (from) {
        wr16(from, 2);
        wr16(from + 2, m->from_port);
        wr32(from + 4, m->from_ina);
        if (fromlen) wr32(fromlen, 16);
    }
    if (s->type == GUEST_DGRAM || m->off == m->len) {
        s->mid = s->type == GUEST_STREAM && n == cap && m->off == cap;   /* the whole message fitted the first part exactly */
        s->head = (s->head + 1) % RING;
        s->count--;
    } else {
        s->mid = 0;
    }
    return n;
}

static int readable(const Sock *s)
{
    unsigned i;
    if (s->listening) {
        for (i = 0; i < SOCKETS; i++) if (s_sock[i].used && s_sock[i].type == GUEST_STREAM && !s_sock[i].listening && !s_sock[i].accepted && !s_sock[i].peer_closed && s_sock[i].connected && s_sock[i].port == s->port && s_sock[i].remote) return 1;
        return 0;
    }
    if (s->count && s->type == GUEST_DGRAM && xb_net_game_hold(s->ring[s->head].data, s->ring[s->head].len)) return 0;
    return s->count != 0 || s->mid || (s->type == GUEST_STREAM && s->peer_closed);
}

static u32 select_set(u32 set, int (*ready)(const Sock *))
{
    u32 n = rd32(set), i, out = 0;
    if (n > 64) n = 64;
    for (i = 0; i < n; i++) {
        u32 h = rd32(set + 4 + 4 * i);
        const Sock *s = sock_of(h);
        if (s && ready(s)) wr32(set + 4 + 4 * out++, h);
    }
    wr32(set, out);
    return out;
}

static int writable(const Sock *s) { return s->type == GUEST_DGRAM || (s->connected && xb_net_link_up()); }

int xb_net_import(unsigned ord, const TcArgs *a, u64 *r3)
{
    u32 h = (u32)a->r[1];
    Sock *s;
    if (!xb_net_on()) return 0;
    xb_net_start();
    switch (ord) {
    case 1: case 2: case 51: case 52:  /* WSAStartup, WSACleanup, XNetStartup, XNetCleanup: xb_net_start did the work */
        *r3 = 0;
        return 1;
    case 27: *r3 = t_error; return 1;  /* WSAGetLastError: no r3 of 1 */
    case 28: t_error = (u32)a->r[0]; *r3 = 0; return 1;
    case 29: *r3 = xb_token_new(); return 1;   /* WSACreateEvent: a handle 821b2a70 keeps, then closes unused */
    case 30: *r3 = 1; return 1;
    case 34: {                         /* __WSAFDIsSet(socket, set): no r3 of 1 */
        u32 n = rd32((u32)a->r[1]), i;
        *r3 = 0;
        for (i = 0; i < n && i < 64; i++) if (rd32((u32)a->r[1] + 4 + 4 * i) == (u32)a->r[0]) *r3 = 1;
        return 1;
    }
    case 3: case 4: case 5: case 6: case 7: case 11: case 12: case 13: case 14: case 15: case 18: case 20: case 22: case 24:
        break;
    default:
        return xb_net_session_import(ord, a, r3);
    }
    xb_net_lock();
    s = sock_of(h);
    if (ord != 3 && ord != 15 && !s) { *r3 = fail(WSA_BADF); xb_net_unlock(); return 1; }
    switch (ord) {
    case 3: {                          /* socket(1, family, type, protocol): 821b23c0 */
        u32 type = (u32)a->r[2];
        if (type != GUEST_STREAM && type != GUEST_DGRAM) xb_stop("network", "a socket of another type", type);
        *r3 = sock_handle(sock_new((u8)type, 0));
        break;
    }
    case 4:                            /* closesocket */
        if (s->type == GUEST_STREAM && s->remote && !s->listening && !s->peer_closed) {
            u8 b[5];
            put_ports(b, K_CLOSE, s->remote, s->port);
            xb_net_link_write(1, b, 5);
        }
        if (s->listening) {            /* what it never accepted goes with it */
            unsigned i;
            for (i = 0; i < SOCKETS; i++) if (&s_sock[i] != s && s_sock[i].used && s_sock[i].type == GUEST_STREAM && !s_sock[i].accepted && !s_sock[i].listening && s_sock[i].port == s->port) sock_free(&s_sock[i]);
        }
        sock_free(s);
        *r3 = 0;
        break;
    case 5: *r3 = 0; break;            /* shutdown: close says it */
    case 6:                            /* ioctlsocket(1, s, FIONBIO, &on): 821b23c0 asks for non-blocking on every socket */
        if ((u32)a->r[2] != 0x8004667Eu || !rd32((u32)a->r[3])) xb_stop("network", "an ioctl other than non-blocking", (u32)a->r[2]);
        *r3 = 0;
        break;
    case 7: *r3 = 0; break;            /* setsockopt: buffers and broadcast, which mean nothing here */
    case 11:                           /* bind(1, s, addr, length) */
        s->port = rd16((u32)a->r[2] + 2);
        *r3 = 0;
        break;
    case 12: {                         /* connect(1, s, addr, length): done once the link carries it, so select's write set says when */
        u8 b[5];
        s->remote = rd16((u32)a->r[2] + 2);
        s->connected = 1;
        s->accepted = 1;
        put_ports(b, K_OPEN, s->remote, s->port);
        if (xb_net_link_write(1, b, 5)) s->peer_closed = 1;
        *r3 = fail(WSA_WOULDBLOCK);
        break;
    }
    case 13: s->listening = 1; *r3 = 0; break;
    case 14: {                         /* accept(1, s, addr, &length): 821b2520 finds the member by the address */
        unsigned i;
        *r3 = fail(WSA_WOULDBLOCK);
        for (i = 0; i < SOCKETS; i++) {
            Sock *c = &s_sock[i];
            if (c->used && c->type == GUEST_STREAM && !c->listening && !c->accepted && !c->peer_closed && c->connected && c->port == s->port && c->remote) {
                c->accepted = 1;
                if ((u32)a->r[2]) {
                    wr16((u32)a->r[2], 2);
                    wr16((u32)a->r[2] + 2, c->remote);
                    wr32((u32)a->r[2] + 4, XB_NET_PEER_INA);
                }
                *r3 = sock_handle(c);
                break;
            }
        }
        break;
    }
    case 15: {                         /* select(1, n, read, write, except, timeout): the game always polls */
        u32 n = 0;
        if ((u32)a->r[2]) n += select_set((u32)a->r[2], readable);
        if ((u32)a->r[3]) n += select_set((u32)a->r[3], writable);
        if ((u32)a->r[4]) wr32((u32)a->r[4], 0);
        *r3 = n;
        break;
    }
    case 18:                           /* recv(1, s, buf, len, flags) */
        *r3 = do_recv(s, (u32)a->r[2], (u32)a->r[3], 0, 0);
        break;
    case 20:                           /* recvfrom(1, s, buf, len, flags, from, &fromlen) */
        *r3 = do_recv(s, (u32)a->r[2], (u32)a->r[3], (u32)a->r[5], (u32)a->r[6]);
        break;
    case 22: {                         /* send(1, s, buf, len, flags) */
        u32 len = (u32)a->r[3];
        u8 b[NET_MAX_MSG];
        if (!s->connected) { *r3 = fail(WSA_NOTCONN); break; }
        if (s->peer_closed) { *r3 = fail(WSA_CONNRESET); break; }
        if (!len || len > GAME_MSG) { *r3 = fail(0x2738); break; }
        put_ports(b, K_DATA, s->remote, s->port);
        memcpy(b + 5, tc_base + (u32)a->r[2], len);
        *r3 = xb_net_link_write(1, b, len + 5) ? fail(WSA_CONNRESET) : len;
        break;
    }
    case 24:                           /* sendto(1, s, buf, len, flags, to, tolen) */
        *r3 = do_sendto(s, (u32)a->r[2], (u32)a->r[3], (u32)a->r[5]);
        break;
    }
    xb_net_unlock();
    return 1;
}

/* See net_link.h. A packet is a 24-byte header and one message:
 *   0 magic, 4 token (0 in the handshake), 8 checksum (of the whole packet with this field 0), 12 kind, 13 zero,
 *   14 next reliable message wanted, 16 which of the 32 after it have arrived, 20 this message's number, 22 its length.
 * Numbers are little-endian. Every packet carries the acknowledgement, so a busy link needs no separate ones. */
#include <string.h>
#include "net_link.h"

#define NET_MAGIC 0x314E4452u   /* "RDN1" */
enum { K_HELLO = 1, K_WELCOME, K_DGRAM, K_REL, K_ACK, K_PING, K_PONG, K_BYE };
enum { HELLO_LEN = 8 + NET_DIGEST, WELCOME_LEN = 12 };
#define PING_MS 250u
#define HELLO_MS 200u
#define BYE_SENDS 3                   /* nothing answers a goodbye, and without it the other side waits out NET_TIMEOUT_MS */
#define RTO_MIN 30u
#define RTO_MAX 1000u

static void w16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void w32(uint8_t *p, uint32_t v) { w16(p, (uint16_t)v); w16(p + 2, (uint16_t)(v >> 16)); }
static uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t r32(const uint8_t *p) { return r16(p) | (uint32_t)r16(p + 2) << 16; }

static uint32_t crc_add(uint32_t c, const uint8_t *p, int n)
{
    int i, b;
    for (i = 0; i < n; i++) {
        c ^= p[i];
        for (b = 0; b < 8; b++) c = c >> 1 ^ (0xEDB88320u & (0u - (c & 1)));
    }
    return c;
}

static uint32_t crc32_of(const uint8_t *p, int n) { return ~crc_add(0xFFFFFFFFu, p, n); }

/* Both nonces decide the token, so neither side alone can pick it and a packet from an earlier session is refused */
static uint32_t token_of(uint32_t a, uint32_t b)
{
    uint8_t m[8];
    uint32_t t;
    w32(m, a);
    w32(m + 4, b);
    t = crc32_of(m, 8);
    return t ? t : 1;
}

static int in_window(uint16_t seq, uint16_t base) { return (uint16_t)(seq - base) < NET_WINDOW; }

/* The first message not yet here, and which of the 32 after it are */
static uint16_t ack_next(const NetLink *l, uint32_t *bits)
{
    uint16_t next = l->read_rel;
    uint32_t b = 0;
    int i;
    while (in_window(next, l->read_rel) && l->in[next % NET_WINDOW].used) next++;
    for (i = 0; i < 32; i++) {
        uint16_t s = (uint16_t)(next + 1 + i);
        if (in_window(s, l->read_rel) && l->in[s % NET_WINDOW].used) b |= 1u << i;
    }
    *bits = b;
    return next;
}

static void send_packet(NetLink *l, int kind, uint16_t seq, const void *data, int len, uint32_t now)
{
    uint8_t p[NET_MAX_PACKET];
    uint32_t bits;
    uint16_t next = ack_next(l, &bits);
    w32(p, NET_MAGIC);
    w32(p + 4, kind == K_HELLO || kind == K_WELCOME ? 0 : l->token);
    w32(p + 8, 0);
    p[12] = (uint8_t)kind;
    p[13] = 0;
    w16(p + 14, next);
    w32(p + 16, bits);
    w16(p + 20, seq);
    w16(p + 22, (uint16_t)len);
    if (len) memcpy(p + NET_HEADER, data, (size_t)len);
    w32(p + 8, crc32_of(p, NET_HEADER + len));
    l->carrier(l->ctx, p, NET_HEADER + len);
    l->last_tx = now;
    l->stats.sent++;
}

void net_link_init(NetLink *l, NetCarrier carrier, void *ctx, const uint8_t digest[NET_DIGEST], uint32_t nonce)
{
    memset(l, 0, sizeof *l);
    l->carrier = carrier;
    l->ctx = ctx;
    memcpy(l->digest, digest, NET_DIGEST);
    l->nonce = nonce;
    l->state = NET_IDLE;
}

void net_link_listen(NetLink *l, uint32_t now) { l->state = NET_LISTENING; l->last_rx = now; }

static void send_hello(NetLink *l, uint32_t now)
{
    uint8_t h[HELLO_LEN];
    w32(h, NET_VERSION);
    w32(h + 4, l->nonce);
    memcpy(h + 8, l->digest, NET_DIGEST);
    send_packet(l, K_HELLO, 0, h, HELLO_LEN, now);
    l->last_hello = now;
}

void net_link_connect(NetLink *l, uint32_t now)
{
    l->state = NET_CONNECTING;
    l->last_rx = now;
    send_hello(l, now);
}

void net_link_close(NetLink *l)
{
    int i;
    for (i = 0; i < BYE_SENDS && l->state == NET_ESTABLISHED; i++) send_packet(l, K_BYE, 0, NULL, 0, l->last_tx);
    l->state = NET_CLOSED;
}

static void send_welcome(NetLink *l, NetStatus status, uint32_t peer_nonce, uint32_t now)
{
    uint8_t w[WELCOME_LEN];
    w32(w, (uint32_t)status);
    w32(w + 4, peer_nonce);
    w32(w + 8, l->nonce);
    send_packet(l, K_WELCOME, 0, w, WELCOME_LEN, now);
}

static void on_hello(NetLink *l, const uint8_t *m, int n, uint32_t now)
{
    uint32_t peer;
    NetStatus st = NET_OK;
    if (n != HELLO_LEN) { l->stats.bad++; return; }
    peer = r32(m + 4);
    if (r32(m) != NET_VERSION) st = NET_REFUSED_VERSION;
    else if (memcmp(m + 8, l->digest, NET_DIGEST)) st = NET_REFUSED_CONTENT;
    if (l->state == NET_LISTENING) {
        if (st != NET_OK) {   /* this caller stays out; the host goes on waiting for one with the same game */
            l->stats.bad++;
            l->status = st;
            send_welcome(l, st, peer, now);
            return;
        }
        l->status = NET_OK;
        l->peer_nonce = peer;
        l->token = token_of(peer, l->nonce);
        l->state = NET_ESTABLISHED;
        l->last_rx = l->last_ping = now;
        send_welcome(l, NET_OK, peer, now);
    } else if (l->state == NET_ESTABLISHED && peer == l->peer_nonce) {
        send_welcome(l, NET_OK, peer, now);   /* our answer was lost */
    } else if (l->state == NET_ESTABLISHED && st == NET_OK) {
        l->state = NET_CLOSED;                /* a new start from the other side, which the caller only lets through from the same address: it has gone without a word and come back */
    }
}

static void on_welcome(NetLink *l, const uint8_t *m, int n, uint32_t now)
{
    if (n != WELCOME_LEN || l->state != NET_CONNECTING || r32(m + 4) != l->nonce) { l->stats.bad++; return; }
    if (r32(m) != NET_OK) {
        l->status = r32(m) == NET_REFUSED_VERSION ? NET_REFUSED_VERSION : NET_REFUSED_CONTENT;
        l->state = NET_REFUSED;
        return;
    }
    l->peer_nonce = r32(m + 8);
    l->token = token_of(l->nonce, l->peer_nonce);
    l->state = NET_ESTABLISHED;
    l->last_rx = l->last_ping = now;
}

static void rtt_sample(NetLink *l, uint32_t r)
{
    if (!l->srtt) {
        l->srtt = r ? r : 1;
        l->rttvar = r / 2;
    } else {
        uint32_t d = r > l->srtt ? r - l->srtt : l->srtt - r;
        l->rttvar = (3 * l->rttvar + d) / 4;
        l->srtt = (7 * l->srtt + r) / 8;
        if (!l->srtt) l->srtt = 1;
    }
    l->stats.rtt_ms = l->srtt;
}

static uint32_t rto_of(const NetLink *l)
{
    uint32_t r = l->srtt ? l->srtt + 4 * l->rttvar : 200;
    return r < RTO_MIN ? RTO_MIN : r > RTO_MAX ? RTO_MAX : r;
}

static void resend(NetLink *l, NetSlot *s, uint32_t now)
{
    send_packet(l, K_REL, s->seq, s->data, s->len, now);
    s->sent_ms = now;
    s->retries = (uint8_t)(s->retries < 255 ? s->retries + 1 : 255);
    s->skipped = 0;
    s->rto = s->rto * 2 > RTO_MAX ? RTO_MAX : s->rto * 2;
    l->stats.retransmits++;
}

static void on_ack(NetLink *l, uint16_t next, uint32_t bits, uint32_t now)
{
    int i;
    /* An answer can only mention what was sent: anything else is not from a working peer */
    if ((uint16_t)(l->next_rel - next) > NET_WINDOW) { l->stats.bad++; return; }
    for (i = 0; i < NET_WINDOW; i++) {
        NetSlot *s = &l->out[i];
        uint16_t after;
        if (!s->used) continue;
        after = (uint16_t)(s->seq - next);
        if (after >= 0x8000u || (after >= 1 && after <= 32 && bits >> (after - 1) & 1)) {
            if (!s->retries) rtt_sample(l, now - s->sent_ms);   /* a resent one could be answering either copy */
            s->used = 0;
        }
    }
    /* Three acknowledgements past the wanted message: it was lost, no need to wait out the timer. Not again within a
     * round trip, since the answers to the copy just resent are still on their way */
    if (bits) {
        NetSlot *s = &l->out[next % NET_WINDOW];
        if (s->used && s->seq == next && ++s->skipped >= 3 && now - s->sent_ms >= l->srtt) resend(l, s, now);
    }
}

static void on_dgram(NetLink *l, uint16_t seq, const uint8_t *m, int n)
{
    NetHeld *d;
    if (l->dgram_any) {
        int16_t ahead = (int16_t)(seq - l->dgram_top);
        if (ahead > 0) {
            l->dgram_seen = ahead >= 64 ? 0 : l->dgram_seen << ahead;
            l->dgram_top = seq;
            l->dgram_seen |= 1;
        } else {
            if (ahead <= -64) { l->stats.duplicates++; return; }   /* too old to tell: refused */
            if (l->dgram_seen >> -ahead & 1) { l->stats.duplicates++; return; }
            l->dgram_seen |= 1ull << -ahead;
        }
    } else {
        l->dgram_any = 1;
        l->dgram_top = seq;
        l->dgram_seen = 1;
    }
    if (l->dgram_count == NET_DGRAM_QUEUE) {   /* the oldest goes: for inputs, the newer packet repeats it anyway */
        l->dgram_head = (l->dgram_head + 1) % NET_DGRAM_QUEUE;
        l->dgram_count--;
        l->stats.dropped_dgrams++;
    }
    d = &l->dgrams[(l->dgram_head + l->dgram_count) % NET_DGRAM_QUEUE];
    d->len = (uint16_t)n;
    memcpy(d->data, m, (size_t)n);
    l->dgram_count++;
}

static void on_rel(NetLink *l, uint16_t seq, const uint8_t *m, int n, uint32_t now)
{
    NetHeld *h = &l->in[seq % NET_WINDOW];
    if (!in_window(seq, l->read_rel)) {
        l->stats.duplicates += (uint16_t)(l->read_rel - seq) < 0x8000u;   /* already read: our acknowledgement was lost */
    } else if (h->used) {
        l->stats.duplicates++;
    } else {
        h->used = 1;
        h->len = (uint16_t)n;
        memcpy(h->data, m, (size_t)n);
    }
    send_packet(l, K_ACK, 0, NULL, 0, now);
}

void net_link_on_packet(NetLink *l, const uint8_t *p, int len, uint32_t now)
{
    static const uint8_t zero[4] = { 0, 0, 0, 0 };
    int kind, n;
    uint16_t seq;
    if (len < NET_HEADER || len > NET_MAX_PACKET || r32(p) != NET_MAGIC || r16(p + 22) != len - NET_HEADER) { l->stats.bad++; return; }
    /* the checksum, with its own field zero */
    if (~crc_add(crc_add(crc_add(0xFFFFFFFFu, p, 8), zero, 4), p + 12, len - 12) != r32(p + 8)) { l->stats.bad++; return; }
    kind = p[12];
    n = len - NET_HEADER;
    seq = r16(p + 20);
    if (kind == K_HELLO) { on_hello(l, p + NET_HEADER, n, now); return; }
    if (kind == K_WELCOME) { on_welcome(l, p + NET_HEADER, n, now); return; }
    if (l->state != NET_ESTABLISHED || r32(p + 4) != l->token) { l->stats.bad++; return; }
    if ((kind == K_DGRAM || kind == K_REL) && n == 0) { l->stats.bad++; return; }   /* net_link_send sends no empty message */
    l->stats.received++;
    l->last_rx = now;
    on_ack(l, r16(p + 14), r32(p + 16), now);
    switch (kind) {
    case K_DGRAM: on_dgram(l, seq, p + NET_HEADER, n); break;
    case K_REL: on_rel(l, seq, p + NET_HEADER, n, now); break;
    case K_ACK: break;
    case K_PING: send_packet(l, K_PONG, seq, NULL, 0, now); break;
    case K_PONG:
        if (seq == l->ping_id) rtt_sample(l, now - l->ping_sent);
        break;
    case K_BYE: l->state = NET_CLOSED; break;
    default: l->stats.bad++; break;
    }
}

void net_link_tick(NetLink *l, uint32_t now)
{
    int i;
    if (l->state == NET_CONNECTING) {
        if (now - l->last_rx >= NET_TIMEOUT_MS) l->state = NET_TIMED_OUT;
        else if (now - l->last_hello >= HELLO_MS) send_hello(l, now);
        return;
    }
    if (l->state != NET_ESTABLISHED) return;
    if (now - l->last_rx >= NET_TIMEOUT_MS) { l->state = NET_TIMED_OUT; return; }
    for (i = 0; i < NET_WINDOW; i++) {
        NetSlot *s = &l->out[i];
        if (s->used && now - s->sent_ms >= s->rto) resend(l, s, now);
    }
    if (now - l->last_ping >= PING_MS) {
        l->ping_id++;
        l->ping_sent = l->last_ping = now;
        send_packet(l, K_PING, l->ping_id, NULL, 0, now);
    }
}

int net_link_send(NetLink *l, int reliable, const void *data, int len, uint32_t now)
{
    if (l->state != NET_ESTABLISHED || len <= 0 || len > NET_MAX_MSG) return -1;
    if (reliable) {
        NetSlot *s = &l->out[l->next_rel % NET_WINDOW];
        if (s->used) return -1;   /* the window is full: the oldest in flight sits in this slot */
        s->used = 1;
        s->seq = l->next_rel++;
        s->len = (uint16_t)len;
        s->retries = s->skipped = 0;
        s->sent_ms = s->first_ms = now;
        s->rto = rto_of(l);
        memcpy(s->data, data, (size_t)len);
        send_packet(l, K_REL, s->seq, s->data, s->len, now);
    } else {
        send_packet(l, K_DGRAM, l->next_dgram++, data, len, now);
    }
    return 0;
}

int net_link_recv(NetLink *l, int *reliable, void *buf, int cap)
{
    NetHeld *h = &l->in[l->read_rel % NET_WINDOW];
    if (h->used) {
        if (h->len > cap) return -1;
        *reliable = 1;
        memcpy(buf, h->data, h->len);
        h->used = 0;
        l->read_rel++;
        return h->len;
    }
    if (l->dgram_count) {
        NetHeld *d = &l->dgrams[l->dgram_head];
        if (d->len > cap) return -1;
        *reliable = 0;
        memcpy(buf, d->data, d->len);
        l->dgram_head = (l->dgram_head + 1) % NET_DGRAM_QUEUE;
        l->dgram_count--;
        return d->len;
    }
    return 0;
}

NetState net_link_state(const NetLink *l) { return l->state; }
NetStatus net_link_status(const NetLink *l) { return l->status; }
const NetStats *net_link_stats(const NetLink *l) { return &l->stats; }

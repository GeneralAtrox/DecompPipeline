/* One link between two players: messages over any datagram carrier. No sockets and no clock of its own, so a test
 * can run it through a planted bad network and get the same result every time; a kit supplies the carrier.
 *
 * What it promises the caller:
 * - reliable messages arrive once, in order, with their boundaries kept;
 * - unreliable messages arrive at most once, in any order, or not at all;
 * - only the other player of this session reaches the caller: a packet that fails its checks is counted and dropped;
 * - two builds with different game data never connect: the handshake compares the caller's content digest. */
#ifndef NET_LINK_H
#define NET_LINK_H
#include <stdint.h>

#define NET_MAX_MSG 1400         /* the largest message; with the header it stays under a common 1500-byte path */
#define NET_HEADER 24
#define NET_MAX_PACKET (NET_MAX_MSG + NET_HEADER)
#define NET_WINDOW 64            /* reliable messages in flight, and the most held back waiting for an earlier one */
#define NET_DGRAM_QUEUE 32
#define NET_DIGEST 32
#define NET_VERSION 1
#define NET_TIMEOUT_MS 8000      /* nothing heard for this long: the other player is gone */

typedef enum {
    NET_IDLE,
    NET_LISTENING,               /* a host waiting for a player */
    NET_CONNECTING,              /* a joiner saying hello */
    NET_ESTABLISHED,
    NET_CLOSED,                  /* the other player left */
    NET_TIMED_OUT,
    NET_REFUSED                  /* the handshake failed: see net_link_status */
} NetState;

typedef enum {
    NET_OK = 0,
    NET_REFUSED_VERSION,
    NET_REFUSED_CONTENT          /* the two players' game data differ */
} NetStatus;

typedef void (*NetCarrier)(void *ctx, const uint8_t *packet, int len);

typedef struct {
    uint32_t sent, received, retransmits, duplicates, bad, dropped_dgrams;
    uint32_t rtt_ms;             /* smoothed round trip, 0 until measured */
} NetStats;

typedef struct {
    uint8_t used, retries, skipped;
    uint16_t seq, len;
    uint32_t sent_ms, first_ms, rto;
    uint8_t data[NET_MAX_MSG];
} NetSlot;

typedef struct {
    uint8_t used;
    uint16_t len;
    uint8_t data[NET_MAX_MSG];
} NetHeld;

typedef struct {
    NetState state;
    NetStatus status;
    NetCarrier carrier;
    void *ctx;
    uint8_t digest[NET_DIGEST];
    uint32_t nonce, peer_nonce, token;
    uint32_t last_rx, last_tx, last_ping, last_hello;
    uint16_t next_rel, read_rel, next_dgram, ping_id;
    uint16_t dgram_top;
    uint64_t dgram_seen;
    int dgram_any;
    uint32_t ping_sent;
    uint32_t srtt, rttvar;
    NetSlot out[NET_WINDOW];
    NetHeld in[NET_WINDOW];
    NetHeld dgrams[NET_DGRAM_QUEUE];
    int dgram_head, dgram_count;
    NetStats stats;
} NetLink;

/* nonce: a value this side chose at random, for this session */
void net_link_init(NetLink *l, NetCarrier carrier, void *ctx, const uint8_t digest[NET_DIGEST], uint32_t nonce);
void net_link_listen(NetLink *l, uint32_t now_ms);
void net_link_connect(NetLink *l, uint32_t now_ms);
void net_link_close(NetLink *l);          /* tells the other player, once; the link is then closed */

/* A packet off the carrier. The caller passes every packet from the bound address, and while listening from any */
void net_link_on_packet(NetLink *l, const uint8_t *packet, int len, uint32_t now_ms);
/* Retries, keep-alives and the time-out: call at least every 10 ms */
void net_link_tick(NetLink *l, uint32_t now_ms);

/* 0 sent or queued; -1 not now (too many in flight, or the link is not up), so the caller tries again later */
int net_link_send(NetLink *l, int reliable, const void *data, int len, uint32_t now_ms);
/* The next message's length, 0 when there is none. *reliable says which kind; a message longer than cap is an error (-1) and stays */
int net_link_recv(NetLink *l, int *reliable, void *buf, int cap);

NetState net_link_state(const NetLink *l);
NetStatus net_link_status(const NetLink *l);
const NetStats *net_link_stats(const NetLink *l);
#endif

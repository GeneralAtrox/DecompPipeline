/* A stream of messages that must all arrive, in order, and quickly: a lockstep game's inputs. Each packet repeats the last
 * few messages, so one lost packet costs one frame; the sender also sends each message on the link's reliable channel, which
 * covers any longer outage. The receiver hands out each message once, in order, and never skips one.
 *
 * Skipping matters: a game that takes "the newest N frames" from a packet and marks everything older as received turns a burst
 * of lost packets into a silent desync. No sockets and no clock, so a test can run it through any loss. */
#ifndef NET_INPUTS_H
#define NET_INPUTS_H
#include <stdint.h>

#define NET_INPUT_MSG 1280       /* the largest message */
#define NET_INPUT_REPEAT 4       /* messages one packet can carry */
#define NET_INPUT_HELD 64        /* messages kept back waiting for an earlier one */

typedef struct { uint16_t len; uint8_t data[NET_INPUT_MSG]; } NetInputMsg;

typedef struct {
    uint32_t next_send;          /* the sequence number of the next message */
    NetInputMsg sent[NET_INPUT_REPEAT];
    uint32_t next_recv;          /* the only message net_inputs_next may hand out */
    uint8_t held_used[NET_INPUT_HELD];
    NetInputMsg held[NET_INPUT_HELD];
    uint32_t bad, repeats;
} NetInputs;

void net_inputs_init(NetInputs *n);
/* Takes a new message. *fast: the packet for the unreliable channel, with what room allows of the last few messages.
 * *sure: the packet for the reliable channel, this message alone. Each returns its length through the pointer; 0 from the
 * call is done, -1 means the message is too long or the buffers are too small and nothing changed. */
int net_inputs_pack(NetInputs *n, const void *msg, int len, uint8_t *fast, int fast_cap, int *fast_len, uint8_t *sure, int sure_cap, int *sure_len);
/* A packet off either channel. A bad one is counted and ignored */
void net_inputs_on_packet(NetInputs *n, const uint8_t *packet, int len);
/* Whether on_packet has room for this packet's newest message. A fast packet with none can go, its reliable copy comes later. A reliable one
 * must wait: it is the only copy left, and without it the stream stops for good */
int net_inputs_fits(const NetInputs *n, const uint8_t *packet, int len);
/* The next message in order: its length, 0 when it hasn't arrived. Nothing is ever skipped */
int net_inputs_next(NetInputs *n, void *buf, int cap);
#endif

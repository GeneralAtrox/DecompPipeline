/* See net_inputs.h. A packet: u32 the newest message's sequence number, u8 how many messages, then each one newest first as a
 * u16 length and its bytes. Sequence numbers run for 2^32 messages, two years at 60 a second, so they don't wrap in a session. */
#include <string.h>
#include "net_inputs.h"

static void w32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v; }
static uint32_t r32(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }

void net_inputs_init(NetInputs *n) { memset(n, 0, sizeof *n); }

int net_inputs_pack(NetInputs *n, const void *msg, int len, uint8_t *fast, int fast_cap, int *fast_len, uint8_t *sure, int sure_cap, int *sure_len)
{
    uint32_t seq = n->next_send;
    int at = 5, count = 0, i;
    if (len <= 0 || len > NET_INPUT_MSG || fast_cap < 7 + len || sure_cap < 7 + len) return -1;
    n->sent[seq % NET_INPUT_REPEAT].len = (uint16_t)len;
    memcpy(n->sent[seq % NET_INPUT_REPEAT].data, msg, (size_t)len);
    n->next_send = seq + 1;
    for (i = 0; i < NET_INPUT_REPEAT && (uint32_t)i <= seq; i++) {
        const NetInputMsg *m = &n->sent[(seq - (uint32_t)i) % NET_INPUT_REPEAT];
        if (at + 2 + m->len > fast_cap) break;
        fast[at] = (uint8_t)(m->len >> 8);
        fast[at + 1] = (uint8_t)m->len;
        memcpy(fast + at + 2, m->data, m->len);
        at += 2 + m->len;
        count++;
    }
    w32(fast, seq);
    fast[4] = (uint8_t)count;
    *fast_len = at;
    w32(sure, seq);
    sure[4] = 1;
    sure[5] = (uint8_t)(len >> 8);
    sure[6] = (uint8_t)len;
    memcpy(sure + 7, msg, (size_t)len);
    *sure_len = 7 + len;
    return 0;
}

void net_inputs_on_packet(NetInputs *n, const uint8_t *packet, int len)
{
    uint32_t seq;
    int at = 5, count, i;
    if (len < 5) { n->bad++; return; }
    seq = r32(packet);
    count = packet[4];
    if (count == 0 || count > NET_INPUT_REPEAT || seq < (uint32_t)count - 1) { n->bad++; return; }
    /* The whole packet is checked before any of it is believed */
    for (i = 0; i < count; i++) {
        int m;
        if (at + 2 > len) { n->bad++; return; }
        m = packet[at] << 8 | packet[at + 1];
        if (m == 0 || m > NET_INPUT_MSG || at + 2 + m > len) { n->bad++; return; }
        at += 2 + m;
    }
    if (at != len) { n->bad++; return; }
    at = 5;
    for (i = 0; i < count; i++, seq--) {
        int m = packet[at] << 8 | packet[at + 1];
        if (seq < n->next_recv) {
            n->repeats++;
        } else if (seq - n->next_recv < NET_INPUT_HELD) {
            /* Further ahead than that can only be a fast packet, if the caller keeps to net_inputs_fits; the reliable copy comes later */
            unsigned slot = seq % NET_INPUT_HELD;
            if (n->held_used[slot]) {
                n->repeats++;
            } else {
                n->held_used[slot] = 1;
                n->held[slot].len = (uint16_t)m;
                memcpy(n->held[slot].data, packet + at + 2, (size_t)m);
            }
        }
        at += 2 + m;
    }
}

int net_inputs_fits(const NetInputs *n, const uint8_t *packet, int len)
{
    uint32_t seq;
    if (len < 5) return 1;             /* on_packet's to count as bad */
    seq = r32(packet);
    return seq < n->next_recv || seq - n->next_recv < NET_INPUT_HELD;
}

int net_inputs_next(NetInputs *n, void *buf, int cap)
{
    unsigned slot = n->next_recv % NET_INPUT_HELD;
    int len;
    if (!n->held_used[slot]) return 0;
    len = n->held[slot].len;
    if (len > cap) return -1;
    memcpy(buf, n->held[slot].data, (size_t)len);
    n->held_used[slot] = 0;
    n->next_recv++;
    return len;
}

/* net_udp: one net_link (net_link.h) over a real UDP port, for a kit's netplay. The socket; a worker thread that keeps the
 * link (packets in, retries, keep-alives, the time-out) while the game runs, draws or waits; a queue for reliable messages
 * beyond the link's window; and a planted bad network for tests. A host waits on its port for one player at a time and
 * listens again when that player leaves; a joiner reaches the host it names. What the messages mean is the kit's: each one
 * goes to its handler on the worker thread with the lock held, as does each change of the link's state.
 *
 * Nearest existing: targets/xbox360_ppc/port/runtime/xb_net.c (the same socket, worker and test network for the Xbox 360
 * kit, tied there to Mega Man 10's sockets and the XB_NET_* variables); this is that part on its own and console-free. */
#ifndef NET_UDP_H
#define NET_UDP_H
#include "base.h"
#include "net_link.h"

typedef int (*NetUdpMessage)(void *ctx, const u8 *msg, int len, int reliable); /* 0: no room now, offered again later */
typedef void (*NetUdpState)(void *ctx, NetState from, NetState to);

void net_udp_test(int loss_pct, int lag_ms, int jitter_ms); /* a planted bad network (a test aid): before net_udp_start */
void net_udp_drop(u32 ms);       /* before net_udp_start: nothing heard for ms and the other player is gone (0: the default) */
/* join NULL: a host waiting on port. join "host[:port]": reach that host (port when it names none). 0: the port is open */
int net_udp_start(int port, const char *join, const u8 digest[NET_DIGEST], NetUdpMessage on_msg, NetUdpState on_state, void *ctx);
void net_udp_lock(void);
void net_udp_unlock(void);
int net_udp_send(int reliable, const void *msg, int len); /* lock held: 0 sent or queued; -1 the link is not up */
NetState net_udp_state(void);                             /* lock held */
const NetStats *net_udp_stats(void);                      /* lock held */
void net_udp_reconnect(void);    /* lock held: a joiner says hello again (the host was not up yet) */
void net_udp_wait(u32 ms);       /* lock not held: until something comes from the other side, or ms */
void net_udp_finish(u32 ms);     /* lock not held: up to ms for the other side to have every reliable message, then the link closes */
void net_udp_stop(void);         /* lock not held: the link closes and the port is let go; net_udp_start may open another */
u32 net_udp_lost(void);          /* packets the test network dropped */
int net_udp_address(char *out, int cap); /* this computer's LAN address (the default route's), 0 if none: a host gives it to the joiner */
#endif

/* The kit's network, shared by xb_net.c (the link), xb_net_sock.c (the game's sockets on it) and xb_net_session.c (Xbox Live's answers). */
#ifndef XB_NET_H
#define XB_NET_H
#include "xb.h"

#define XB_NET_SELF_INA 0x7F000001u   /* the address the game gives its own member slot (821b54d8, 821b5a18) */
#define XB_NET_PEER_INA 0x0A000002u   /* what the other player looks like: two players, so one address is enough */

/* Link kinds that belong to the session layer */
enum { XB_NET_SEARCH_REQ = 5, XB_NET_SEARCH_REPLY = 6, XB_NET_STATE_HASH = 7 };

/* xb_net.c. The session layer calls the link ones with the lock held */
void xb_net_lock(void);
void xb_net_unlock(void);
void xb_net_host_start(void);         /* wait for a player */
void xb_net_join_start(void);         /* reach the host named by XB_NET_JOIN; 0 when none is named */
int xb_net_join_named(void);
void xb_net_link_stop(void);          /* tell the other player, and stop */
int xb_net_link_up(void);
void xb_net_game_heard(void);         /* something from the other player's game has come */
int xb_net_game_alive(void);          /* the link is up and the other game has spoken lately */
u16 xb_net_link_rtt(void);            /* the link's measured round trip, ms */
int xb_net_link_failed(void);         /* a joiner's link has ended: refused, timed out or closed */
int xb_net_send_session(int kind, const void *data, unsigned len);  /* a message for the other side's session layer; lock held */
void xb_net_random(void *out, unsigned len);

int xb_net_on(void);
void xb_net_start(void);              /* opens the real port, the first time; takes the lock */
int xb_net_link_write(int reliable, const u8 *d, unsigned len);  /* lock held */

/* xb_net_sock.c. Lock held, like the session layer's below */
void xb_net_sock_reset(void);         /* a new link: its input stream starts again */
int xb_net_sock_deliver(const u8 *b, unsigned n, int reliable);  /* a message off the link; 0: it has to wait for room */
void xb_net_sock_drain(void);         /* what the input stream has ready goes to the game's socket */
void xb_net_sock_streams_end(void);   /* the link has gone: the game's streams read as closed */

/* xb_net_game.c */
int xb_net_game_message_ok(const u8 *d, unsigned len);
int xb_net_game_hold(const u8 *d, unsigned len);
void xb_net_game_watch(void);

/* xb_net_check.c. Lock held */
void xb_net_check_message(const u8 *d, unsigned len);
void xb_net_check_reset(void);

/* xb_net_session.c. The worker calls these with the lock held */
void xb_net_session_message(int kind, const u8 *data, unsigned len);
void xb_net_session_tick(unsigned now);
int xb_net_session_import(unsigned ord, const TcArgs *a, u64 *r3);
#endif

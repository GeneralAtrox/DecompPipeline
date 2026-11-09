/* sat_net: online play for two on the Saturn kit (game.json "netplay"; README.md "Online play"). Delay (lockstep) netplay:
 * both copies run the --det timeline, which depends only on the pads each frame, so equal pads give equal games. The
 * host decides every frame's pads: its own pad, taken `delay` frames ahead, and the joiner's, which the joiner sends the
 * same `delay` frames ahead; it sends each frame's pads to the joiner, and neither side runs a frame before it has that
 * frame's pads. The joiner's pad is the game's second pad (port 2). A player who joins late replays every frame from boot,
 * as fast as it can, then joins. Every `hash_every` frames each side hashes the game's work RAM and sends the hash; two
 * hashes that differ stop both copies (exit 6). Carried by base/net_udp.c (net_link and net_inputs). A match is a play run:
 * in-between frames are drawn while a copy waits (sat_net_idle), and the menu's entries that would change the game for both
 * are locked (base/host_online.cpp, which also hosts and joins from the Online menu).
 *
 *   --net on|off|HOST[:PORT]  the Online menu's saved choice (base/host_online.cpp): host, play alone, or join that host
 *   --net-host PORT|on  wait for a player on this UDP port (on: game.json's port)    --net-join HOST[:PORT]  join a host
 *   --net-delay N  frames of input delay (the host's counts)   --net-wait on|off  the host waits for a player before frame 1 (off)
 *   --net-hash-every N  frames between desync checks           --net-pace on|off  the host keeps the game's rate (off: tests)
 *   --net-test-loss PCT, --net-test-lag MS, --net-test-jitter MS  a planted bad network (test aid)
 *   --net-test-desync FRAME[,ADDR]  flip a byte of this side's RAM at FRAME (test aid; ADDR default game.json test_byte)
 *   --net-menu on  the match was started by the game's own online screens (sat_lobby.c): one that cannot go on goes back
 *                  there; a game whose online play is in its own menus (netplay "menu": "in-game") takes --net only with it
 *
 * Nearest existing: targets/xbox360_ppc/port/runtime/xb_net_session.c and xb_net_check.c (Mega Man 10's online play on the
 * Xbox 360 kit: its game's own net messages carried by the base's link, with the same RAM-hash desync check); the Saturn
 * games have no net code of their own, so here the kit itself keeps the two copies in step. */
#ifndef SAT_NET_H
#define SAT_NET_H
#include "sat.h"

int sat_net_option(const char *arg, const char *value); /* the arguments taken, 0 if not ours */
void sat_net_start(const char *image); /* after the game log opens: the link; a joiner takes the host's settings and save */
void sat_net_started(void);            /* after the settings are applied (auto-load): a host can send them */
int sat_net_on(void);
void sat_net_vblank(void);             /* sat_sys.c, --det: before each vertical blank, the frame's pads */
u32 sat_net_pads(u32 data[2]);         /* sat_input.c: this frame's pads, as sat_pad_data gives them */
void sat_net_end(void);                /* port_exit: the summary, then the link closes */
u32 sat_pad_word(const HostPad *pad);  /* sat_input.c: the Saturn buttons held (1 = pressed) */
long long sat_net_next(void);         /* when the next vertical blank is due (QueryPerformanceCounter): the last one's + a period */

#ifdef SAT_NET_INTERNAL /* sat_net.c and sat_net_setup.c */
#include "net_inputs.h"
#include "net_udp.h"
enum { M_FAST = 1, M_SURE, M_SETUP, M_SAVE, M_SETUP_END, M_HASH, M_READY, M_DESYNC, M_LEAVE };
enum { R_HAVE = 1, R_P1 = 2, R_JHAVE = 4 }; /* the frame's pads are decided; port 2 is there; the joiner's pad came */
typedef struct NetRec { u8 flags; u16 w0, w1, jw; } NetRec;
typedef struct NetHash { u64 mine, theirs; u8 have; } NetHash; /* have: 1 mine, 2 theirs */
typedef struct SatNet {
    int on, host, delay, hash_every, pace, wait, test_frame;
    u32 test_addr;
    int started, link_up, link_new, link_lost, setup_sent, setup_end, ready;
    int fast, autoload, logos, online;   /* the host's settings (a joiner: as the host sent them) */
    int left;                    /* the other copy said goodbye (M_LEAVE) */
    char part[16];
    u8 *save;
    u32 save_len, save_got;
    NetInputs out, in;
    NetRec *rec;
    u32 cap, last_final, r_sent, j0, ready_from, j_sent, own_next;
    NetHash *hash;
    u32 hcap, h_last, h_sent, matched;
    u32 desync, desync_theirs;   /* the frame whose hashes differed (0: none); 1: the other side found it */
    u64 desync_a, desync_b;
    NetStats gone;               /* the link as it was when the other player left */
} SatNet;
extern SatNet sat_net;
int sat_net_msg(void *ctx, const u8 *m, int len, int reliable);   /* sat_net.c */
void sat_net_link(void *ctx, NetState from, NetState to);
void sat_net_setup_msg(const u8 *m, int len);                     /* sat_net_setup.c */
void sat_net_send_setup(void);
void sat_net_pace(void);
void sat_net_idle(long long until);
void sat_net_plant(u32 f);
int sat_net_open(const char *join, NetUdpMessage on_msg, NetUdpState on_state); /* sat_lobby.c: the link on the game's port */
#define SAT_NET_NO_PAD 0xffffffffu
void sat_online_watch(u32 f, u32 pad); /* sat_online.c, lock held: each frame (this player's pad) and after a wait */
u32 sat_net_get32(const u8 *p);
void sat_net_put32(u8 *p, u32 v);
#endif
#endif

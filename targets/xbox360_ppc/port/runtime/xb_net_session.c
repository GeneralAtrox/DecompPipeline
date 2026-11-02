/* What Xbox Live answered, answered between the two players. XNet addresses are constants (xb_net.h), a session is whatever the host
 * says it is, and a search is a question to the host named by XB_NET_JOIN over the link. The game's own session code runs unchanged
 * on top: it never learns the console's service is gone. */
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include "xb_net.h"

#define INFO_SIZE 0x3Cu       /* XSESSION_INFO: session id 8, XNADDR 0x24, key 0x10 (821b2a70 points into a result at +0, +8 and +0x2c) */
#define RESULT_SIZE 0x5Cu     /* a search result: XSESSION_INFO then slot counts and properties (821b2a70 steps by this) */
#define XNADDR_SIZE 0x24u
#define QOS_MAX 1024u
#define SEARCH_MS 6000u
#define JOIN_MS 30000u        /* a joiner that found a session and then did nothing lets it go */
#define ERR_INSUFFICIENT 0x7Au
#define ERR_IO_PENDING 0x3E5u
#define APP_SESSION 0xFBu
#define MSG_SET_PROPERTY 0xB0007u
#define MSG_GET_CONTEXT 0xB0041u
#define MSG_CREATE 0xB0010u
#define MSG_DELETE 0xB0011u
#define MSG_JOIN 0xB0012u
#define MSG_LEAVE 0xB0013u
#define MSG_START 0xB0014u
#define MSG_END 0xB0015u
#define MSG_SEARCH 0xB0016u
#define MSG_MODIFY 0xB0025u

static u8 s_self_xna[XNADDR_SIZE];
static int s_self_made;
static u64 s_xuid;
static u8 s_info[INFO_SIZE];
static int s_hosted;
static u8 s_qos[QOS_MAX];              /* the host's: what it set with XNetQosListen. A joiner's: what the host sent */
static unsigned s_qos_len;
static u32 s_qos_block;
static struct { int active, asked; u32 overlapped, results, size, deadline; } s_search;
static u32 s_join_deadline;

static void self_make(void)
{
    if (s_self_made) return;
    s_self_made = 1;
    xb_net_random(s_self_xna, sizeof s_self_xna);
    s_self_xna[0] = 0x0A;
    s_self_xna[1] = s_self_xna[2] = 0;
    s_self_xna[3] = 1;
    xb_net_random(&s_xuid, sizeof s_xuid);
    s_xuid = 0x0009000000000000ull | (s_xuid & 0xFFFFFFFFFFFFull);   /* the top bytes an online xuid has; the two players must differ */
}

/* The answer to a search. Called once for each search */
static void search_finish(const u8 *info, unsigned open_slots)
{
    u32 r = s_search.results;
    u32 count = info && s_search.size >= 8 + RESULT_SIZE ? 1 : 0;
    wr32(r, count);
    wr32(r + 4, r + 8);
    if (count) {
        memset(tc_base + r + 8, 0, RESULT_SIZE);
        memcpy(tc_base + r + 8, info, INFO_SIZE);
        wr32(r + 8 + INFO_SIZE, open_slots);       /* open public */
        wr32(r + 8 + INFO_SIZE + 8, 1);            /* filled public: the host */
    }
    s_search.active = 0;
    port_log("network: search found %u, session data %u bytes, name length %u\n", count, s_qos_len, s_qos_len > 0x11 ? s_qos[0x11] : 0);
    if (count) {
        s_join_deadline = (u32)GetTickCount64() + JOIN_MS;
        if (!s_join_deadline) s_join_deadline = 1;
    } else {
        xb_net_link_stop();
    }
    xb_overlapped_finish(s_search.overlapped, 0, 0);
}

void xb_net_session_message(int kind, const u8 *d, unsigned len)
{
    if (kind == XB_NET_SEARCH_REQ) {
        u8 b[2 + INFO_SIZE + 2 + QOS_MAX];
        b[0] = (u8)s_hosted;
        b[1] = 1;
        memcpy(b + 2, s_info, INFO_SIZE);
        b[2 + INFO_SIZE] = (u8)(s_qos_len >> 8);
        b[3 + INFO_SIZE] = (u8)s_qos_len;
        memcpy(b + 4 + INFO_SIZE, s_qos, s_qos_len);
        xb_net_send_session(XB_NET_SEARCH_REPLY, b, 4 + INFO_SIZE + s_qos_len);
    } else if (kind == XB_NET_SEARCH_REPLY && s_search.active) {
        unsigned q;
        if (len < 4 + INFO_SIZE) { port_log("network: a short search reply\n"); return; }
        q = (unsigned)d[2 + INFO_SIZE] << 8 | d[3 + INFO_SIZE];
        if (q > QOS_MAX || len != 4 + INFO_SIZE + q) { port_log("network: a bad search reply\n"); return; }
        memcpy(s_qos, d + 4 + INFO_SIZE, q);
        s_qos_len = q;
        search_finish(d[0] ? d + 2 : NULL, d[1]);
    }
}

void xb_net_session_tick(unsigned now)
{
    if (s_search.active) {
        if (xb_net_link_up() && !s_search.asked) s_search.asked = xb_net_send_session(XB_NET_SEARCH_REQ, NULL, 0) == 0;
        if (xb_net_link_failed() || (int)(now - s_search.deadline) >= 0) {
            port_log("network: no host answered\n");
            search_finish(NULL, 0);
        }
    }
    if (s_join_deadline && (int)(now - s_join_deadline) >= 0) {
        s_join_deadline = 0;
        port_log("network: the session found was not joined, letting it go\n");
        xb_net_link_stop();
    }
}

static int session_message(const TcArgs *a, u64 *r3)
{
    u32 msg = (u32)a->r[1], overlapped = (u32)a->r[2], block = (u32)a->r[3];
    self_make();
    if (msg != 0xB0006u) port_log("network: session message %05x\n", msg);   /* that one is the presence thread's, over and over */
    /* Without an overlapped the callers (822063c0, 821b97b0) take the result from the thread's last error (821b9d88), which an earlier
     * failed call may have left set. The console's call sets it; every answer here is a success */
    if (!overlapped) wr32(rd32(0x7FFF0100u) + 0x160, 0);
    switch (msg) {
    case MSG_CREATE: {         /* block: session, flags, public slots, private slots, user, XSESSION_INFO *, u64 *nonce (82206160) */
        u32 flags = rd32(block + 4), info = rd32(block + 0x14), nonce = rd32(block + 0x18);
        xb_net_random(tc_base + nonce, 8);
        if (flags & 1) {       /* XSESSION_CREATE_HOST */
            u8 *i = tc_base + info;
            xb_net_random(i, 8);
            memcpy(i + 8, s_self_xna, XNADDR_SIZE);
            xb_net_random(i + 8 + XNADDR_SIZE, 0x10);
            memcpy(s_info, i, INFO_SIZE);
            s_hosted = 1;
            s_join_deadline = 0;
            xb_net_host_start();
        } else {
            s_join_deadline = 0;
        }
        break;
    }
    case MSG_DELETE:
        s_hosted = 0;
        s_qos_len = 0;
        xb_net_link_stop();
        break;
    case MSG_SET_PROPERTY:     /* 821b7e38, from the host's 821b4d00: a stat friends would see on Live, and nothing reads it back */
        break;
    case MSG_GET_CONTEXT:      /* block +0x14: { context, value } (821b97b0). 821b2918 asks for the game mode, 0x800b, for a search's contexts. The game never sets it, so it's the console's 0 */
        wr32(rd32(block + 0x14) + 4, 0);
        break;
    case MSG_JOIN: case MSG_LEAVE: case MSG_START: case MSG_END: case MSG_MODIFY:
        break;                 /* the game's members and states are its own: the console only kept a copy */
    case MSG_SEARCH:           /* block: user, procedure, results wanted, properties, contexts, size, results (82206718) */
        if (!overlapped) xb_stop("network", "a search with no overlapped", 0);
        s_search.results = rd32(block + 0x1C);
        s_search.size = rd32(block + 0x18);
        s_search.overlapped = overlapped;
        s_search.asked = 0;
        s_search.deadline = (u32)GetTickCount64() + SEARCH_MS;
        s_search.active = 1;
        s_join_deadline = 0;
        wr32(overlapped, ERR_IO_PENDING);   /* 821b8780 reads this: anything else is a search that has ended, with whatever the results hold */
        if (!xb_net_join_named()) {
            port_log("network: nothing to search: no host was named\n");
            search_finish(NULL, 0);
        } else {
            xb_net_join_start();
        }
        *r3 = ERR_IO_PENDING;
        return 1;
    default:
        return 0;
    }
    /* A call without an overlapped reads its result from what this returns; with one, the game waits on it (822065c8) */
    xb_overlapped_finish(overlapped, 0, 0);
    *r3 = 0;
    return 1;
}

int xb_net_session_import(unsigned ord, const TcArgs *a, u64 *r3)
{
    int done = 1;
    xb_net_lock();
    self_make();
    switch (ord) {
    case 53:                   /* XNetRandom(1, buffer, length) */
        xb_net_random(tc_base + (u32)a->r[1], (u32)a->r[2]);
        *r3 = 0;
        break;
    case 54:                   /* XNetCreateKey(1, session id, key) */
        xb_net_random(tc_base + (u32)a->r[1], 8);
        xb_net_random(tc_base + (u32)a->r[2], 0x10);
        *r3 = 0;
        break;
    case 55: case 56: case 63: case 65:   /* XNetRegisterKey, XNetUnregisterKey, XNetUnregisterInAddr, XNetConnect: no secure links to manage */
        *r3 = 0;
        break;
    case 57:                   /* XNetXnAddrToInAddr(1, xnaddr, session id, &in_addr): 821b5668, 821b4238 */
        wr32((u32)a->r[3], memcmp(tc_base + (u32)a->r[1], s_self_xna, XNADDR_SIZE) ? XB_NET_PEER_INA : XB_NET_SELF_INA);
        *r3 = 0;
        break;
    case 60:                   /* XNetInAddrToXnAddr(1, in_addr, xnaddr, session id): 821b4238 sends this to the other members */
        memcpy(tc_base + (u32)a->r[2], s_self_xna, XNADDR_SIZE);
        *r3 = 0;
        break;
    case 66:                   /* XNetGetConnectStatus: connected */
        *r3 = 2;
        break;
    case 69: {                 /* XNetQosListen(1, session id, data, length, bits per second, flags): 821b54d8 sets the host's data */
        u32 flags = (u32)a->r[5], len = (u32)a->r[3];
        if (flags & 4) {
            if (len > QOS_MAX) xb_stop("network", "more QoS data than the table holds", len);
            memcpy(s_qos, tc_base + (u32)a->r[2], len);
            s_qos_len = len;
        }
        *r3 = 0;
        break;
    }
    case 70: {                 /* XNetQosLookup(1, count, addrs, ids, keys, 0, 0, 0, probes, bps, flags, event, XNQOS **): 821b2a70, after a search.
                                * The block: count, pending, then one XNQOSINFO: flags, probes sent, probes back, data length, data, rtt, rtt, up, down */
        u32 o, d;
        u16 rtt;
        if ((u32)a->r[1] != 1) xb_stop("network", "a QoS lookup of other than the one result", (u32)a->r[1]);
        if (!s_qos_block) s_qos_block = xb_alloc(0x10000);
        o = s_qos_block + 8;
        d = s_qos_block + 8 + 0x18;
        memcpy(tc_base + d, s_qos, s_qos_len);
        wr32(s_qos_block, 1);
        wr32(s_qos_block + 4, 0);
        wr8(o, 1 | 2 | 8);     /* complete, contacted, data received: 821b5668 keeps a result only with the last two */
        wr16(o + 2, 8);
        wr16(o + 4, 8);
        wr16(o + 6, (u16)s_qos_len);   /* 821b2028 reads it here, and takes less than 0x20 as nobody there */
        wr32(o + 8, d);
        rtt = xb_net_link_rtt();
        wr16(o + 0xC, rtt);
        wr16(o + 0xE, rtt);            /* 821b1968 turns this into the quality that picks the input delay (820c51a0 to DAT_875bec26, table 824b085c) */
        wr32(o + 0x10, 1000000);
        wr32(o + 0x14, 1000000);
        wr32((u32)a->s[4], s_qos_block);
        *r3 = 0;
        break;
    }
    case 72:                   /* XNetQosRelease: the block is kept for the next lookup */
        *r3 = 0;
        break;
    case 73:                   /* XNetGetTitleXnAddr(1, xnaddr): ethernet, static, gateway, DNS, online */
        memcpy(tc_base + (u32)a->r[1], s_self_xna, XNADDR_SIZE);
        *r3 = 0xE6;
        break;
    case 75:                   /* XNetGetEthernetLinkStatus: active, 100 Mbps, full duplex */
        *r3 = 0xB;
        break;
    case 503:
        done = (u32)a->r[0] == APP_SESSION && session_message(a, r3);
        break;
    case 790:                  /* XamSessionCreateHandle(&handle): 82206160 */
        wr32((u32)a->r[0], xb_token_new());
        *r3 = 0;
        break;
    case 791:                  /* XamSessionRefObjByHandle(handle, &object): the token stands for both */
        wr32((u32)a->r[1], (u32)a->r[0]);
        *r3 = 0;
        break;
    case 522:                  /* XamUserGetXUID, online: the same user as the save, with an xuid the other player doesn't have */
        if ((u32)a->r[0]) { done = 0; break; }
        wr64((u32)a->r[2], s_xuid);
        *r3 = 0;
        break;
    case 528:                  /* XamUserGetSigninState: 2, signed in to Live, is what the game's network menu asks for */
        *r3 = (u32)a->r[0] == 0 ? 2u : 0u;
        break;
    case 530:                  /* XamUserCheckPrivilege(user, privilege, &result): first stop 82329064 under 8209bec0, 0xfe multiplayer. Everything is allowed */
        wr32((u32)a->r[2], 1);
        *r3 = 0;
        break;
    case 551: {                /* XamUserGetSigninInfo(user, flags, record): as the plain one in xb_imports_xam.h, signed in to Live */
        u32 o = (u32)a->r[2];
        const char *name = getenv("XB_NET_NAME");
        unsigned i, n;
        memset(tc_base + o, 0, 40);
        if ((u32)a->r[0]) { *r3 = 0x525; break; }
        if (!name) name = "Player";
        n = (unsigned)strlen(name);
        wr64(o, s_xuid);
        wr32(o + 8, 1);        /* live enabled */
        wr32(o + 12, 2);
        for (i = 0; i < 15 && i < n; i++) wr8(o + 24 + i, (u8)name[i]);
        *r3 = 0;
        break;
    }
    default:
        done = 0;
    }
    xb_net_unlock();
    return done;
}

/* The stage select's message handler, 82141248, called through the table at 8752e948. Everything but one line is the game's.
 *
 * The two players leave stage select by each sending message 7 and leaving when the other's arrives (82140b10). Each sends it only on a
 * tick after it has seen the other is ready. 821b66b8 hands over every queued packet in one go, so a player who gets "ready" and the
 * other's 7 together leaves without having sent its own, and the other waits for ever: seen at 12400 in a run on one machine, where the
 * joiner went to the stage and the host stayed on the screen. So the first 7 from the other player is answered here. 821b73f8 flushes
 * right after that hand-over, so the answer goes out on this tick, whatever the screen does next. */
#include <string.h>
#include "xb_net.h"

/* Offsets in the screen's block, the handler's fourth argument */
#define CURSOR_HOST   0x1234
#define CURSOR_ASSIST 0x1236
#define MY_CURSOR     0x1238
#define READY         0x123A
#define GO            0x123B
#define MSG_HEAD      0x1254
#define MSG_LIFE      0x1258
#define TIMER         0x1250
#define MY_SLOT       0x2300

enum { MSG_READY = 1, MSG_CANCEL, MSG_PAUSE, MSG_RESUME, MSG_COUNT, MSG_ASK, MSG_GO };

/* As 82140b10 sends: type, my cursor, my slot, a word */
static void send_message(const TcArgs *a, u32 screen, unsigned type, u32 word)
{
    u32 msg = (u32)a->sp[0] - 0x20;
    TcArgs n;
    wr8(msg, (u8)type);
    wr8(msg + 1, rd8(screen + MY_CURSOR));
    wr8(msg + 2, rd8(screen + MY_CURSOR + 1));
    wr8(msg + 3, rd8(screen + MY_SLOT));
    wr32(msg + 4, word);
    wr32(screen + MSG_LIFE, 600);
    memset(&n, 0, sizeof n);
    n.r[0] = screen + MSG_HEAD;
    n.r[1] = msg;
    n.r[2] = 0xFFFF;
    n.sp[0] = a->sp[0] - 0x80;
    (void)tc_call(0x821B6B00u, &n);
}

static int is_host(const TcArgs *a)
{
    TcArgs n;
    memset(&n, 0, sizeof n);
    n.r[0] = 1;
    n.r[1] = 0x100;
    n.sp[0] = a->sp[0] - 0x80;
    return tc_call(0x821AF5A8u, &n) != 0;
}

/* Message 5: the options, counted by the host only */
static void count_option(u32 screen, u32 which)
{
    if (which == 0) wr8(screen + 0x2374, (u8)(rd8(screen + 0x2374) + 1));
    else if (which == 7) wr8(screen + 0x2427, rd8(screen + 0x2427) | 0x80);
    else if (which == 9) wr8(screen + 0x2427, rd8(screen + 0x2427) ^ 0x20);
    else {
        u32 at = screen + 0x242C + rd32(0x824919C8u + which * 4);
        wr8(at, (u8)(rd8(at) + 1));
    }
}

int xb_net_stage_message(const TcArgs *a, u64 *r3)
{
    u32 head = (u32)a->r[0], m = (u32)a->r[1], screen = (u32)a->r[3];
    unsigned type, player, bit;
    *r3 = 0;
    if (rd16(head + 2) != 8) return 1;
    type = rd8(m);
    player = rd8(m + 3);
    bit = 1u << (player & 0x3F);
    wr8(screen + (player == 0 ? CURSOR_HOST : CURSOR_ASSIST), rd8(m + 1));
    wr8(screen + (player == 0 ? CURSOR_HOST : CURSOR_ASSIST) + 1, rd8(m + 2));
    switch (type) {
    case MSG_READY:
        if (rd32(m + 4) < rd32(screen + TIMER)) wr32(screen + TIMER, rd32(m + 4));
        wr8(screen + READY, (u8)(rd8(screen + READY) | bit));
        break;
    case MSG_CANCEL:
        wr32(screen + TIMER, rd32(m + 4));
        wr8(screen + READY, (u8)(rd8(screen + READY) & ~bit));
        break;
    case MSG_PAUSE:
        wr32(screen + 0x1218, rd32(screen + 0x1218) | 0x1000);
        break;
    case MSG_RESUME:
        wr32(screen + 0x1218, rd32(screen + 0x1218) & 0xFFFFEFFFu);
        wr32(screen + 0x243C, rd32(m + 4));
        break;
    case MSG_COUNT:
        if (!is_host(a)) count_option(screen, rd32(m + 4));
        break;
    case MSG_ASK:
        if (rd8(screen + READY) >> (rd8(screen + MY_SLOT) & 0x3F) & 1) send_message(a, screen, MSG_READY, rd32(screen + TIMER));
        else send_message(a, screen, 0, 0);
        break;
    case MSG_GO:
        if (player == rd8(screen + MY_SLOT)) break;
        if (!rd8(screen + GO)) {
            port_log("network: stage select: the other player is going, answering\n");
            send_message(a, screen, MSG_GO, 0);
        }
        wr8(screen + GO, 1);
        wr8(screen + READY, (u8)(rd8(screen + READY) | bit));
        break;
    }
    return 1;
}

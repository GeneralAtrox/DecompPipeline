/* Are the two players still playing the same game? Each second of lockstep play both hash what the game decides and compare over the link.
 * The game has no check of its own: a desync (821b74f8 can make one, see xb_net.c) just goes on as two different games. This only tells:
 * a difference goes in the log with the tick it was first seen at, which is where to start looking, and play carries on.
 *
 * The ranges come from comparing the guest's memory at equal ticks of a two-player run through Chill Man's stage (all of 85000000..87700000,
 * 18 samples). They were the same on both sides at every sample and they changed in play. 875a9000..875aa100 ends in the list of hit boxes
 * with every player and enemy's position (first entry 875a9fd4), 87543000 holds the score and lives. Left out, because they differ by
 * design: the camera (8755cf40) and all that's drawn from it, the sprite list (8754d6c0), the pool at 875aa884, whose free slots are filled
 * with a value each copy of the game picks for itself, and the table of which sound plays on which voice (874c0eb0..874c0ec8, 8209d3d8), which
 * depends on when each copy's sounds happened to end. */
#include <stdlib.h>
#include <string.h>
#include "xb_net.h"

#define EVERY 60u                     /* ticks */
#define KEPT 8u                       /* samples of ours and theirs: more than the link can be behind */
#define NOTE_EVERY 3600u
#define LOCKSTEP_CURSOR 0x8752E8D8u   /* the tick both players have reached (821b79a0); starts again with each stage */

typedef struct { u32 tick; u64 hash; int used; } Sample;
typedef struct { u32 tick; u32 pad; u64 hash; } Wire;

static const struct { u32 lo, hi; } k_range[] = {{0x874C0000u, 0x874C0EB0u}, {0x874C0EC8u, 0x874C3000u}, {0x87543000u, 0x87544000u}, {0x875A9000u, 0x875AA100u}};
static Sample s_mine[KEPT], s_theirs[KEPT];
static u32 s_last, s_mine_at, s_theirs_at;
static int s_told;

static u64 state_hash(void)
{
    u64 h = 0xCBF29CE484222325ull;
    unsigned r;
    for (r = 0; r < sizeof k_range / sizeof k_range[0]; r++) {
        const u8 *p = tc_base + k_range[r].lo, *e = tc_base + k_range[r].hi;
        while (p < e) h = (h ^ *p++) * 0x100000001B3ull;
    }
    return h;
}

static void compare(const Sample *mine, const Sample *theirs)
{
    if (mine->hash == theirs->hash) {
        if (mine->tick % NOTE_EVERY == 0) port_log("network: in step at tick %u\n", mine->tick);
        return;
    }
    if (s_told) return;
    s_told = 1;
    port_log("network: DESYNC: the two games differ at tick %u (ours %016llx, theirs %016llx)\n", mine->tick, mine->hash, theirs->hash);
}

static const Sample *find(const Sample *list, u32 tick)
{
    unsigned i;
    for (i = 0; i < KEPT; i++) if (list[i].used && list[i].tick == tick) return &list[i];
    return NULL;
}

/* The end of a shown frame, on the game's thread */
void xb_net_check_tick(void)
{
    static int test_at = -1;
    u32 tick;
    if (!xb_net_on()) return;
    tick = rd32(LOCKSTEP_CURSOR);
    if (tick == s_last) return;
    if (test_at == -1) {               /* a planted difference, to prove this check catches one */
        const char *e = getenv("XB_NET_TEST_DESYNC");
        test_at = e ? atoi(e) : -2;
    }
    if (test_at >= 0 && tick >= (u32)test_at) {
        wr8(0x874C0000u, (u8)(rd8(0x874C0000u) ^ 0x10));   /* a byte that stays as it is through a stage, so the difference stays too */
        test_at = -2;
    }
    xb_net_lock();
    if (tick < s_last) {               /* the stage started again: both copies at the same tick of the game, and the same tick number means something new */
        memset(s_mine, 0, sizeof s_mine);
        memset(s_theirs, 0, sizeof s_theirs);
    }
    s_last = tick;
    if (tick && tick % EVERY == 0) {
        Sample *m = &s_mine[s_mine_at++ % KEPT];
        const Sample *t;
        Wire w;
        m->tick = tick;
        m->hash = state_hash();
        m->used = 1;
        w.pad = 0;
        w.tick = m->tick;
        w.hash = m->hash;
        (void)xb_net_send_session(XB_NET_STATE_HASH, &w, sizeof w);
        t = find(s_theirs, m->tick);
        if (t) compare(m, t);
    }
    xb_net_unlock();
}

/* Lock held. Both copies are the same program on the same kind of machine, so the words go as they are */
void xb_net_check_message(const u8 *d, unsigned len)
{
    Wire w;
    Sample *t;
    const Sample *m;
    if (len != sizeof w) return;
    memcpy(&w, d, sizeof w);
    t = &s_theirs[s_theirs_at++ % KEPT];
    t->tick = w.tick;
    t->hash = w.hash;
    t->used = 1;
    m = find(s_mine, w.tick);
    if (m) compare(m, t);
}

/* Lock held: a new link, a new other player */
void xb_net_check_reset(void)
{
    memset(s_mine, 0, sizeof s_mine);
    memset(s_theirs, 0, sizeof s_theirs);
    s_last = s_mine_at = s_theirs_at = 0;
    s_told = 0;
}

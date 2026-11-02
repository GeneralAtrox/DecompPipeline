/* What the kit's network knows of this game's own code: what it may let through to it, and the voice chat it leaves out. */
#include "xb_net.h"

#define GAME_MSG 0x4F0

/* The session block, as 821af320 finds block 1; 0 when there is none */
static u32 session_block(void)
{
    u32 slot;
    for (slot = 0x8752E984u; slot < 0x8752E994u; slot += 4) {
        u32 block = rd32(slot);
        if (block && rd8(block) != 3 && (rd32(block + 8) & 0xF) == 1) return block;
    }
    return 0;
}

/* Its receive callback */
static u32 game_receiver(void)
{
    u32 block = session_block();
    return block ? rd32(rd32(block + 0xC) + 0x28) : 0;
}

/* 821b6ea8 unpacks runs into a slot of 0x204 bytes (821b7088) without asking how long they come out */
static int lockstep_fits(const u8 *p, unsigned n)
{
    unsigned at = 4, out = 4, room = rd16(0x8752E910u);
    if (n < 4) return 0;
    if (!(p[3] & 1)) return n <= room;
    while (at < n) {
        unsigned code = p[at++], count = code & 0x7F;
        if (code & 0x80) {
            if (at >= n) return 0;
            at++;
        } else {
            if (count > n - at) return 0;
            at += count;
        }
        out += count;
        if (out > room) return 0;
    }
    return 1;
}

/* What the guest may see of a game message. The game trusts every field: the sender slot indexes 821b74f8's table, the type indexes
 * 821b2c88's, whose entries 10 and 11 aren't functions, and the runs above. A link only carries the other copy of the same game, so a
 * failure here is a bug to find, not an attack to survive */
int xb_net_game_message_ok(const u8 *d, unsigned len)
{
    unsigned type;
    if (len < 8 || len > GAME_MSG) return 0;
    type = (unsigned)d[4] >> 4;
    if (type == 7) return (d[3] & 0x1F) < 2 && (game_receiver() != 0x821B6EA8u || lockstep_fits(d + 8, len - 8));
    return type != 10 && type != 11;
}

/* 821b6ea8 drops a packet without a word when its ring of 8752e900 slots is full, and the game has no way to send it again. So a lockstep
 * packet waits on the socket while the ring is full. 821b73f8 empties the ring and then at once adds this player's own packets, one per stream,
 * so those always have room. It waits only while 821b73f8 is running, or it would wait for ever and hold up what's behind it */
int xb_net_game_hold(const u8 *d, unsigned len)
{
    if (len < 8 || (unsigned)d[4] >> 4 != 7 || game_receiver() != 0x821B6EA8u || rd32(0x8752E8E8u) != 1) return 0;
    return (u8)(rd8(0x8752E904u) - rd8(0x8752E902u)) >= rd8(0x8752E900u);
}

/* Tells the log when the session block changes: its state byte and flags answer most "why did it end" questions without a debugger */
void xb_net_game_watch(void)
{
    static u32 last_state = 0xFFFFFFFFu, last_flags;
    u32 block = session_block(), state = block ? rd8(block) : 0xFFu, flags = block ? rd32(block + 8) : 0;
    if (state == last_state && flags == last_flags) return;
    port_log("network: session state %u flags %08x\n", state, flags);
    last_state = state;
    last_flags = flags;
}

/* 821b58c8 drops a member it hasn't heard from for 5 seconds, by the time 821b2d48 and 821b46c0 keep in the member's +0xc, and the game then
 * plays on alone without a word. That's a guess made for a network that gives no other sign, and it guesses wrong: a keep-alive read late
 * because this copy or the other stalled, or waiting in the link for a resend, is a player lost for good. The link knows better: it hears the
 * other side four times a second and gives up after 8 seconds of nothing (NET_TIMEOUT_MS), and a hung game is one that has sent nothing for
 * GAME_SILENCE_MS. So while xb_net_game_alive says yes each member counts as just heard, and when it says no the game's rule ends the session 5
 * seconds later, as it always did. On the game's thread, the one that runs 821b58c8 */
void xb_net_game_members_heard(u32 now)
{
    u32 block, inner, members, mask, slot;
    int up;
    if (!xb_net_on()) return;
    xb_net_lock();
    up = xb_net_game_alive();
    xb_net_unlock();
    block = session_block();
    if (!up || !block || !(rd32(block + 8) & 0x200)) return;
    inner = rd32(block + 0xC);
    members = rd32(inner + 0xDC);
    mask = rd32(inner + 0xC);          /* the low half of the mask at +8: at most 4 players */
    for (slot = 0; slot < rd8(inner + 1) && slot < 32; slot++) {
        if (!(mask >> slot & 1) || slot == rd8(inner + 3)) continue;
        wr32(members + slot * 0x18 + 0xC, now);
    }
}

/* 821b09f8 starts the session's voice chat, "COM AVCHAT" in its messages, from the module table at 824b072c: XAudio2, a voice engine of
 * its own (822ab2e0) and a thread. Voice isn't carried, so it stops as the game's own path does when a part of that fails (the block
 * stays at state 1, returns 0). In that state 821b0fc0 and 821b1078 do nothing, and the teardown 821b0190 and the leaving member's
 * 821b04f8 read only the words cleared here */
int xb_net_voice_start(const TcArgs *a, u64 *r3)
{
    u32 in = rd32((u32)a->r[0] + 0xC), i;
    wr32(in, rd16((u32)a->r[1] + 2));
    wr32(in + 4, 0x80);
    for (i = 8; i < 0x70; i += 4) wr32(in + i, 0);
    *r3 = 0;
    return 1;
}

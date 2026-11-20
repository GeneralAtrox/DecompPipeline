/* The session recording's fifth section (session.c's file comment): players 2..8's pads and whether each is connected,
 * and every player's right stick, kept only when they change, so a run only player 1 plays without the right stick
 * writes the four-section file it always did. Split from session.c at its 400-line cap. */
#include <string.h>
#include "base.h"
#include "session.h"
#include "session_priv.h"

static HostPad g_other_last[OTHERS_MAX], g_other_play[OTHERS_MAX];
static u8 g_other_last_on[OTHERS_MAX] = {1}, g_other_play_on[OTHERS_MAX];
static s8 g_other_last_rs[OTHERS_MAX][2], g_other_play_rs[OTHERS_MAX][2];

int session_pad_other(int player, HostPad *pad, s8 rs[2], int connected)
{
    u32 read = session_reads() ? session_reads() - 1u : 0u; /* session_pad has counted this read */
    if (player < 0 || player >= OTHERS_MAX) return connected;
    if (player == 0) connected = 1;
    if (g_session_replay && session_play[OTHERS].n && read < session_play_reads) {
        OtherEntry e;
        while (session_play[OTHERS].at + sizeof e <= session_play[OTHERS].n) {
            memcpy(&e, session_play[OTHERS].p + session_play[OTHERS].at, sizeof e);
            if (e.read > read || !session_take(OTHERS, sizeof e)) break;
            if (e.player >= OTHERS_MAX) continue;
            g_other_play[e.player].buttons = e.buttons;
            g_other_play[e.player].l = e.l;
            g_other_play[e.player].r = e.r;
            g_other_play[e.player].x = e.x;
            g_other_play[e.player].y = e.y;
            g_other_play_rs[e.player][0] = e.rx;
            g_other_play_rs[e.player][1] = e.ry;
            g_other_play_on[e.player] = e.flags & 1u;
        }
        if (player) {
            *pad = g_other_play[player];
            connected = g_other_play_on[player];
        }
        rs[0] = g_other_play_rs[player][0];
        rs[1] = g_other_play_rs[player][1];
    }
    connected = connected != 0;
    if (connected != g_other_last_on[player] || rs[0] != g_other_last_rs[player][0] ||
        rs[1] != g_other_last_rs[player][1] || (player && memcmp(pad, &g_other_last[player], sizeof *pad))) {
        OtherEntry e = {read, port_vblank_count(), 0, 0, 0, 0, 0, (u8)player, (u8)connected, rs[0], rs[1]};
        if (player) {
            e.buttons = pad->buttons;
            e.l = pad->l;
            e.r = pad->r;
            e.x = pad->x;
            e.y = pad->y;
            g_other_last[player] = *pad;
        }
        session_put(&session_rec[OTHERS], &e, sizeof e);
        g_other_last_on[player] = (u8)connected;
        g_other_last_rs[player][0] = rs[0];
        g_other_last_rs[player][1] = rs[1];
    }
    return connected;
}

/* --present-cost on (frame_cost.c): where the thread of in-between frames (frame_thread.c) spends its time. */
#ifndef FRAME_COST_H
#define FRAME_COST_H

#include "rd_rt.h"

enum {
    XC_PAIR,    /* in a frame: the scene's pairs worked out (pvr_blend.c, the first frame after a render) */
    XC_BLEND,   /* in a frame: the vertices blended (pvr_blend.c) */
    XC_PRESENT, /* in a frame: the swap chain's Present */
    XC_LOCK,    /* between frames: giving way to the game's thread, then waiting for gfx_lock */
    XC_SWAP,    /* between frames: waiting for the swap chain to take a frame */
    XC_IDLE,    /* between frames: nothing to draw (no render yet, nothing left to blend, --fps) */
    XC_N
};

extern int g_xcost;
u64 xcost_now(void);                                /* microseconds, the performance counter */
int xcost_option(const char *arg, const char *value); /* --present-cost on|off */
void xcost_add_slow(int part, u64 us);
static inline void xcost_add(int part, u64 us)
{
    if (g_xcost) xcost_add_slow(part, us);
}
void xcost_frame(u64 total_us); /* an in-between frame was drawn and shown, at that cost in all */
void xcost_report(void);        /* at exit */

#endif

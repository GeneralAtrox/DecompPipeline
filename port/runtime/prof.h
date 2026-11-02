/* The frame-time profiler (--prof on): where each vertical blank's host time goes. Sections nest and are timed
 * exclusively (a section's time leaves its parent's), so the rows of a frame add up to its length; what no section
 * covers is the game's own code (translated and readable C running against the kit). Off by default: a call is
 * one test of a flag. Game thread only. */
#ifndef PROF_H
#define PROF_H

#include "rd_rt.h"

enum {
    PROF_WAIT,       /* sleeping or spinning for the next vertical blank (real time only) */
    PROF_AUDIO,      /* the sound tick at the blank: the AICA's voices and the CD, bar the next two rows */
    PROF_ARM,        /* the ARM7 running the game's sound driver, and the AICA's timers and interrupts */
    PROF_MEDIA,      /* ADX and Sofdec decoders mixed into the tick */
    PROF_TA,         /* reading the TA lists into batches (pvr/ta_parse.c) */
    PROF_D3D,        /* the renderer's Direct3D calls bar the TA parse (pvr_render) */
    PROF_DONE,       /* gfx_frame_done: movie, frame capture, game log, scene dump */
    PROF_PRESENT,    /* gfx_show: the swap chain present */
    PROF_X_COPY,     /* in-between frames: guest RAM and state copied before and after */
    PROF_X_DRAW,     /* in-between frames: the game's draw function (its code, bar TA parse and D3D) */
    PROF_READ,       /* a frame buffer or pixel read back from the GPU (pvr_read.c) */
    PROF_N
};

extern int g_prof_on;
u64 prof_now(void);

/* prof_begin returns a token (0 when off); prof_end(id, token) closes it. They must nest. */
u64 prof_enter(int id);
void prof_leave(u64 token);
static inline u64 prof_begin(int id) { return g_prof_on ? prof_enter(id) : 0u; }
static inline void prof_end(u64 token)
{
    if (token) prof_leave(token);
}

int prof_option(const char *arg, const char *value); /* --prof on|off, --prof-from VBLANK, --prof-csv FILE */
void prof_frame(void);                               /* at the end of each vertical blank */
void prof_scene(u32 headers, u32 triangles);         /* this frame's TA scene size */
void prof_gpu_begin(void);                           /* around the scene's Direct3D work: D3D11 timestamps */
void prof_gpu_end(void);
void prof_call(u32 target);                          /* tx_rt.c: a tc_call by guest address (counted by target) */
void prof_calls_on(int on);                          /* prof.c: counting from --prof-from on */
void prof_calls_report(u32 blanks);                  /* prof.c, at exit: the most called targets */
void prof_report(void);                              /* at exit */

#endif

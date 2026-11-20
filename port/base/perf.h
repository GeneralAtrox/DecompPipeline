/* The Core's performance record, the same on every console (docs/PERF_TOOLKIT.md; read by port/tools/perf.py):
 *   --perf-sample US     sample the game thread and the thread that shows frames every US microseconds
 *   --perf-samples FILE  where the samples go at exit (binary words, see perf.c)
 *   --perf-from VBLANK   samples and frame rows from that vertical blank on; load+N: N blanks after a save state loads,
 *                        and the play summary and verdict then count from there too (a segment measured on its own)
 *   --play-frames FILE   one row per shown frame: when, its tick, the gap before it, its make time, its in-between draw
 *                        and present time; beside it FILE's .ticks.csv, one row per tick with the kit's parts (perf_tick).
 *                        Both are rings: a long run keeps its last rows.
 *   --perf-plant US      a planted slow function, US microseconds in every tick (proves the report finds one)
 * Off by default. Off, the cost is a test of g_perf_on at each call by guest address and of g_perf_frames at each
 * shown frame. Each sample holds where the thread was (an offset in the exe, which port.map names), the kit's
 * section at that moment and the game's call stack by original address, so no unwinder or debug file is needed. */
#ifndef PERF_H
#define PERF_H

#include "base.h"

#define PERF_DEPTH 48u
enum { PERF_GAME, PERF_SHOW, PERF_THREADS };

extern int g_perf_on;     /* --perf-sample given */
extern int g_perf_frames; /* --play-frames given */
extern u32 g_perf_tick;   /* ticks begun (play_stats.c counts them: the end of each wait for the blank) */
extern u32 g_perf_depth, g_perf_stack[PERF_DEPTH];

/* A call by the game's original address, around the call: the stack a sample copies. Callers test g_perf_on. */
static inline void perf_push(u32 target)
{
    if (g_perf_depth < PERF_DEPTH) g_perf_stack[g_perf_depth] = target;
    g_perf_depth++;
}
static inline void perf_pop(void)
{
    if (g_perf_depth) g_perf_depth--;
}

/* Named timers on the Core's own system calls. A sample of a thread busy inside the system cannot be trusted to name its
 * caller (32-bit builds, .work/core-perf SAMPLER_FINDINGS), so that time is measured here instead: per thread, total,
 * calls and the longest, written to the log at exit as "perf: timer ..." lines. Off (no --perf-sample): one test. */
enum { PERF_T_SLEEP, PERF_T_PRESENT, PERF_T_SHOW_WAIT, PERF_T_FILE, PERF_T_AUDIO, PERF_T_PLANT, PERF_T_PLANT_SYS, PERF_TIMERS };
/* A kit's own timers (its renderer's steps) take ids PERF_T_KIT + 0 .. PERF_KIT_TIMERS - 1 and are named by the kit with
 * perf_kit_timers, so the Core keeps no kit's names. Each timer's time is also split by the shown frame it fell in: made
 * within the bar (6.67 ms) or over it, written on the same log line. */
#define PERF_T_KIT PERF_TIMERS
#define PERF_KIT_TIMERS 12
void perf_kit_timers(const char *const *names, int count);
u64 perf_timer_clock(void);               /* 0 before --perf-from */
void perf_timer_add(int which, u64 began);
static inline u64 perf_timer_begin(void) { return g_perf_on | g_perf_frames ? perf_timer_clock() : 0; }
/* a timer whose start the caller read on the performance counter itself (present.c's Present) */
static inline void perf_timer_since(int which, u64 began)
{
    if ((g_perf_on | g_perf_frames) && perf_timer_clock()) perf_timer_add(which, began);
}
static inline void perf_timer_end(int which, u64 began)
{
    if (began) perf_timer_add(which, began);
}

/* The per-tick trace (--play-frames), for every console: a kit names its parts once (the work of the tick, its waits, its
 * chips: up to PERF_PARTS), then gives each tick's microseconds in that order, on the game's thread at the end of the
 * tick. Off (no --play-frames): one test. port/tools/perf.py segment ties a late frame to its tick by these rows. */
#define PERF_PARTS 16
void perf_parts(const char *const *names, int count);
void perf_tick(const u32 *us);
/* A kit that keeps the game's call stack itself (the Saturn's g_cpu) gives the sampler a copy of it: at most max original
 * addresses, outermost first, read while the game's thread is held. The Dreamcast kit uses perf_push / perf_pop instead. */
void perf_stack_source(u32 (*copy)(u32 *out, u32 max));
/* A save state was loaded (base/save_state_slots.c): --perf-from load+N starts the segment N blanks later. */
void perf_loaded(void);
/* play_stats.c, at each tick and shown frame: 1 once, when the segment starts (it forgets what it counted then). */
int perf_segment_due(void);
extern u32 g_perf_restarts; /* segments started: a kit's own series (sat_perf.c) start again with it */
extern u32 g_perf_from; /* --perf-from's vertical blank: nothing is sampled or traced before it */
/* Between perf.c and perf_trace.c (the trace, split out for the 400-line cap). */
int perf_trace_option(const char *arg, const char *value);
void perf_trace_write(void);
u32 perf_now_us(void); /* the sampler's clock: rows and samples line up */
void perf_frame_timers(u32 make_us, u32 *present_us, u32 *show_wait_us);

int perf_option(const char *arg, const char *value); /* 2 if taken, 0 if not ours */
void perf_thread(int which);                         /* the calling thread is the game's or the one showing frames */
/* The kit's section the game thread is in right now (read while that thread is held), and the sections' names. */
void perf_sections(int (*now)(void), const char *const *names, int count);
void perf_frame(u32 gap_us, u32 make_us, u32 tick_us, u32 tick, u32 flags, u32 draw_us); /* a shown frame (play_stats.c) */
void perf_plant(void);                               /* once a tick, on the game thread */
void perf_report(void);                              /* at exit: writes the files */

#endif

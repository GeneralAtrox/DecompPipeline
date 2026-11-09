/* The play session's summary (rule point 5 of docs/RUNTIME_TARGET.md): the last lines of a play run's game log say how
 * the run went, so the 144 fps target is read from the log of a session the user played, not from a special run:
 *   play summary: shown_fps 143.9 output 2560x1600 frames 8640 tick_ms 1.10/2.30/9.10 draw_ms 2.00/3.10/12.00 frame_ms 6.90/7.20/14.00 make_ms 1.90/5.10/12.00
 *   play verdict: pass make_ms p99 5.10 (bar 6.67) late 4 of 8600 shown (0.47 per 1000, bar 1) loading 40 shown 9 late machine LAPTOP-NAME
 * shown_fps: shown frames over the time from the first to the last. tick: the host's work between two vertical blanks,
 * the wait for the blank left out. draw: the renderer and in-between draws behind one shown frame. frame: the time from
 * one shown frame to the next. make: the time to make one shown frame until it is ready to show: its draws outside a
 * tick, plus the work of the tick it is the first to show (the tick's own render is in that work). The times are
 * median / 99th percentile / worst, in milliseconds (the median and the percentile to 0.01 ms).
 * The verdict is the bar ("150 fps headroom", docs/RUNTIME_TARGET.md): make p99 at most 6.67 ms, and at most 1 in 1,000
 * shown frames late, where a gap between shown frames that spans n slots of 144 (to the nearest) is n - 1 late. Shown-frame
 * spacing is not the test: under the cap it can't go below 6.94 ms. A loading screen is left out of both and counted
 * apart: a gap that starts within half a second after 256 KB came from the disc within half a second (play_stats_disc; a
 * music stream reads far less). The verdict is "none" when every frame was a loading screen's. It names the machine
 * (the computer's name): only the user's laptop's verdict passes or fails a game; other machines' compare builds.
 * Always compiled and on in a real-time play run; off in a check run (--fixed-clock, --replay, --headless), where the
 * numbers would be the host's and the wall clock would only be cost; --play-summary on|off forces it. A hook is one
 * compare when off and two clock reads and a counter when on; nothing is allocated after start. The wait, tick and disc
 * hooks are the game's thread's; draw and shown may come from the thread of in-between frames, under gfx_lock.
 * Not the profiler (prof.c, --prof on: where the time goes by section, off by default): this says whether the target
 * was met and costs nothing worth measuring. */
#ifndef PLAY_STATS_H
#define PLAY_STATS_H

#include "base.h"
#include "console.h"

extern int g_play_stats; /* 1 on, -1 off, 0 not decided yet (the first hook decides) */

void play_stats_wait_slow(int begin);
void play_stats_tick_slow(double ms);
void play_stats_draw_slow(int enter);
void play_stats_shown_slow(void);
static inline void play_stats_wait(int begin) { if (g_play_stats >= 0) play_stats_wait_slow(begin); }
/* A console that measures its own tick (the Saturn's, which has no single place where the game waits) reports it here:
 * the host's work of one vertical blank in milliseconds, the wait for the clock left out; instead of the wait hooks. */
static inline void play_stats_tick(double ms) { if (g_play_stats >= 0) play_stats_tick_slow(ms); }
static inline void play_stats_draw(int enter) { if (g_play_stats >= 0) play_stats_draw_slow(enter); }
static inline void play_stats_shown(void) { if (g_play_stats >= 0) play_stats_shown_slow(); }
void play_stats_disc_slow(u32 bytes);
/* A read from the disc (host_disc_read): enough close together mark a loading screen. Only once the run is decided: a
 * read before the first frame decides nothing. */
static inline void play_stats_disc(u32 bytes) { if (g_play_stats > 0) play_stats_disc_slow(bytes); }
#define PS_MAKE_BAR_MS 6.67                /* the bar: 99% of frames made within this (150 fps of headroom) */
#define PS_LOADING_BYTES (256u * 1024u)    /* read from the disc within half a second: a load, not a stream */

int play_stats_option(const char *arg, const char *value); /* --play-summary on|off|auto; 0 if not ours */
void play_stats_report(void);                               /* at exit: logs the summary and the verdict */
void play_stats_restart(void); /* forgets what was counted: a --fast-until run counts from where real time starts */

/* The pure part, for tests/test_play_stats.py. */
#define PS_BUCKET_US 10u
#define PS_BUCKETS 10001u /* 0 .. 100 ms; a longer one lands in the last bucket (the worst is kept exactly) */
double play_stats_percentile(const u32 *hist, u64 count, double p); /* ms: the upper edge of the bucket holding rank p */

#endif

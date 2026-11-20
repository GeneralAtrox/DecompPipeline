/* The play session's summary and verdict: see play_stats.h. Four histograms of 10 us buckets (tick, draw, shown frame,
 * make), the worst of each, the first and last shown frame's time and the missed 144 slots; the percentile is read from the
 * histogram, so a session of any length costs the same few kilobytes. The clock is the QueryPerformanceCounter, not
 * prof_now(): that one can be the thread's cycle count (--prof-clock cycles), and this must be wall time. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "base.h"
#include "console.h"
#include "perf.h"
#include "play_stats.h"
#include "session.h"

int g_play_stats;

enum { TICK, DRAW, FRAME, MAKE, SERIES };
static struct {
    u32 hist[PS_BUCKETS];
    u64 worst_us;
} g_s[SERIES];
static u64 g_freq, g_frames, g_first, g_last, g_wait_end, g_draw_start, g_draw_acc, g_xdraw_acc;
static u64 g_idle, g_idle_start; /* clock ticks of sleep inside the current tick (play_stats_idle), and the one going on */
static u64 g_late, g_late_loading, g_shown_loading, g_disc_start, g_disc_bytes;
static u64 g_repeat; /* the frame-rate cap's slots (144 uncapped) that showed no new frame, loading screens left out */
static volatile LONG g_tick_pending;    /* the last tick's work in us + 1, until a shown frame takes it; 0 none */
static volatile LONG g_tick_id;         /* that tick's number (perf.h), for --play-frames */
static volatile LONG64 g_loading_until; /* clock ticks: a gap that starts before this is a loading screen's */
static DWORD g_game_tid;                /* the thread of the wait and tick hooks */
static u32 g_depth, g_gaps_more;
static int g_force, g_in_wait, g_draw_in_tick; /* g_force: --play-summary 1 on, -1 off, 0 auto */
static u64 g_gap_logged;
static DWORD g_span_tid; /* a native frame's span open on this thread (play_stats_native), 0 none */
void *g_play_by[2];
static u32 g_by_prev[2], g_gaps_named, g_late_from; /* g_late_from: --play-late-from, the first blank a gap line names */
static struct { u32 by, prev, gaps, late, long_tick; } g_paths[8]; /* late gaps by the path that showed its two ends */
static u64 g_by_gaps[4], g_by_late[4], g_by_tick[4]; /* late gaps by slots missed (1, 2, 3-7, 8+): gaps, slots, a long tick's */

static u64 now(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (u64)t.QuadPart;
}

static u64 us_of(u64 ticks) { return ticks * 1000000u / g_freq; }

static void add(int s, u64 us)
{
    u64 b = us / PS_BUCKET_US;
    g_s[s].hist[b < PS_BUCKETS ? b : PS_BUCKETS - 1u]++;
    if (us > g_s[s].worst_us) g_s[s].worst_us = us;
}

static int view_headless(void)
{
    PortView v;
    console_view(&v);
    return v.headless;
}

/* Real-time play runs only: a check run has a fixed clock, a replay or no window, and its numbers are not the player's. */
static int decide(void)
{
    LARGE_INTEGER f;
    int on = g_force ? g_force > 0 : !host_fixed_clock() && !session_replaying() && !view_headless();
    QueryPerformanceFrequency(&f);
    g_freq = (u64)f.QuadPart;
    return g_play_stats = on ? 1 : -1;
}

int play_stats_option(const char *arg, const char *value)
{
    if (perf_option(arg, value)) return 2; /* --perf-*, --play-frames: every console takes them through this */
    if (!strcmp(arg, "--play-late-from") && value) return g_late_from = (u32)strtoul(value, NULL, 10), 2;
    if (strcmp(arg, "--play-summary") || !value) return 0;
    g_force = !strcmp(value, "on") ? 1 : !strcmp(value, "off") ? -1 : 0;
    return 2;
}

/* A tick that took more than two vertical blanks: the game itself ran slow there, which in-between frames hide from the
 * shown rate (the counter shows the game's rate beside it, host_counter.cpp). One line a second at most; the ones in
 * between are counted into the next. */
static void tick_gap(u64 ticks)
{
    u64 us = us_of(ticks), t = now();
    if (us * (port_console.tick_hz ? port_console.tick_hz : 60u) <= 2000000u) return;
    if (g_gap_logged && t - g_gap_logged < g_freq) {
        g_gaps_more++;
        return;
    }
    port_log("tick gap: %.1f ms at vertical blank %u, over 2 blanks (%u more since the last line)\n", (double)us / 1000.0,
             port_vblank_count(), g_gaps_more);
    g_gaps_more = 0;
    g_gap_logged = t;
}

/* A tick's work: its sample, and the make time of the first frame that shows it (taken by play_stats_shown). */
static void tick_done(u64 us)
{
    add(TICK, us);
    InterlockedExchange(&g_tick_id, (LONG)g_perf_tick);
    InterlockedExchange(&g_tick_pending, (LONG)(us < 0x7ffffffeu ? us + 1u : 0x7fffffffu));
}

/* A native tick that ran inside its frame's span (play_stats_native; a game that ticks between pg_native_frame and
 * pg_native_present): that span is the frame's make time already, so only the part of the tick before the span opened
 * is the tick's own make time. All inside: the shown frame takes nothing more from it. */
static void tick_in_span(u64 us, u64 in)
{
    add(TICK, us);
    if (us > in) InterlockedExchange(&g_tick_pending, (LONG)(us - in < 0x7ffffffeu ? us - in + 1u : 0x7fffffffu));
}

static void segment_start(void)
{
    play_stats_restart();
    port_log("play: the measured segment starts at vertical blank %u (--perf-from): the summary and verdict count from here\n",
             port_vblank_count());
}

void play_stats_wait_slow(int begin)
{
    u64 t;
    if (!g_play_stats && decide() < 0) return;
    if (begin) perf_plant(); /* inside the tick's work */
    else g_perf_tick++;
    if (g_perf_on | g_perf_frames) perf_thread(PERF_GAME);
    if (perf_segment_due()) segment_start();
    t = now();
    g_game_tid = GetCurrentThreadId(); /* a draw on this thread outside the wait is the tick's own (its render) */
    if (begin) {
        /* the work since the last wait ended, its sleeps left out; the first has no start */
        if (g_wait_end) tick_done(us_of(t - g_wait_end - (g_idle < t - g_wait_end ? g_idle : t - g_wait_end)));
        g_in_wait = 1;
    } else {
        if (g_wait_end) tick_gap(t - g_wait_end); /* from the end of one wait to the end of the next: one tick */
        g_wait_end = t;
        g_in_wait = 0;
    }
    g_idle = g_idle_start = 0;
}

void play_stats_idle_slow(int begin)
{
    if (begin) g_idle_start = now();
    else if (g_idle_start) g_idle += now() - g_idle_start, g_idle_start = 0;
}

void play_stats_tick_slow(double ms)
{
    if (!g_play_stats && decide() < 0) return;
    if (ms < 0.0) return;
    g_perf_tick++;
    if (g_perf_on | g_perf_frames) perf_thread(PERF_GAME);
    if (perf_segment_due()) segment_start();
    g_game_tid = GetCurrentThreadId();
    if (g_span_tid && g_span_tid == GetCurrentThreadId()) {
        u64 us = (u64)(ms * 1000.0), open = us_of(now() - g_draw_start);
        tick_in_span(us, open < us ? open : us);
    } else {
        tick_done((u64)(ms * 1000.0));
    }
    tick_gap((u64)(ms * 1000.0 * (double)g_freq / 1000000.0));
}

/* The draw behind a shown frame is the outermost span only: pvr_render runs inside an in-between frame's draw. A span
 * the game's thread draws outside the wait is inside a tick, whose work already holds it: left out of the make time. */
static void draw_span(int enter, int native)
{
    if (!g_play_stats && decide() < 0) return;
    if (enter) {
        if (g_depth++ == 0) {
            g_draw_start = now();
            g_draw_in_tick = !native && GetCurrentThreadId() == g_game_tid && !g_in_wait;
            g_span_tid = native ? GetCurrentThreadId() : 0;
        }
    } else if (g_depth && --g_depth == 0) {
        u64 d = now() - g_draw_start;
        g_draw_acc += d;
        if (!g_draw_in_tick) g_xdraw_acc += d;
        g_span_tid = 0;
    }
}

void play_stats_draw_slow(int enter) { draw_span(enter, 0); }

/* A native game's frame (native_frame.c: pg_native_frame to pg_native_present, before the present and the cap's wait).
 * It has no wait for a blank, so its draw is never inside a tick: the whole span is make time, and a tick run inside it
 * is counted once, in the span (tick_in_span). */
void play_stats_native_slow(int enter) { draw_span(enter, 1); }

void play_stats_disc_slow(u32 bytes)
{
    u64 t = now();
    if (t - g_disc_start > g_freq / 2u) g_disc_start = t, g_disc_bytes = 0;
    g_disc_bytes += bytes;
    if (g_disc_bytes >= PS_LOADING_BYTES) InterlockedExchange64(&g_loading_until, (LONG64)(t + g_freq / 2u));
}

void play_stats_shown_slow(void)
{
    u64 t, gap = 0, slots, make;
    LONG tick;
    int loading;
    if (!g_play_stats && decide() < 0) return;
    t = now();
    tick = InterlockedExchange(&g_tick_pending, 0);
    loading = g_frames && (u64)InterlockedCompareExchange64(&g_loading_until, 0, 0) >= g_last;
    u32 base = (u32)(uintptr_t)GetModuleHandleW(NULL), by[2]; /* 0: not through gfx_show or gfx_present */
    for (int i = 0; i < 2; i++) by[i] = g_play_by[i] ? (u32)(uintptr_t)g_play_by[i] - base : 0u;
    g_play_by[0] = g_play_by[1] = NULL;
    if (g_frames) {
        gap = us_of(t - g_last);
        add(FRAME, gap);
        slots = (gap * 144u + 500000u) / 1000000u; /* the 144 slots the gap spans, to the nearest: one is on time */
        if (slots > 1u) *(loading ? &g_late_loading : &g_late) += slots - 1u;
        u64 cap = extra_fps() ? extra_fps() : 144u, at_cap = (gap * cap + 500000u) / 1000000u;
        if (at_cap > 1u && !loading) g_repeat += at_cap - 1u;
        if (slots > 1u && !loading) { /* which gaps the late slots come from, and whether the tick's work took half of it */
            int k = slots == 2u ? 0 : slots == 3u ? 1 : slots <= 8u ? 2 : 3;
            g_by_gaps[k]++, g_by_late[k] += slots - 1u;
            int lt = tick > 0 && ((u64)tick - 1u) * 2u > gap, j = 0;
            g_by_tick[k] += lt;
            while (j < 7 && g_paths[j].gaps && (g_paths[j].by != by[0] || g_paths[j].prev != g_by_prev[0])) j++;
            if (!g_paths[j].gaps) g_paths[j].by = j < 7 ? by[0] : ~0u, g_paths[j].prev = j < 7 ? g_by_prev[0] : ~0u;
            g_paths[j].gaps++, g_paths[j].late += (u32)(slots - 1u), g_paths[j].long_tick += lt;
            if (slots >= 4u && port_vblank_count() >= g_late_from && g_gaps_named++ < 40u) /* which path showed it (symbolize.py) */
                port_log("play late gap: %.1f ms at vertical blank %u, tick %.1f ms; shown from port.exe+%x via +%x, the one "
                         "before from +%x via +%x\n", (double)gap / 1000.0, port_vblank_count(),
                         tick > 0 ? (double)(tick - 1) / 1000.0 : 0.0, by[0], by[1], g_by_prev[0], g_by_prev[1]);
        }
    } else {
        g_first = t;
    }
    add(DRAW, us_of(g_draw_acc));
    make = us_of(g_xdraw_acc) + (tick > 0 ? (u64)tick - 1u : 0u);
    if (loading) g_shown_loading++;
    else add(MAKE, make);
    if ((g_perf_on | g_perf_frames) && GetCurrentThreadId() != g_game_tid) perf_thread(PERF_SHOW);
    if (g_perf_frames) /* flags: 1 a loading screen's, 2 the first frame of its tick */
        perf_frame((u32)gap, (u32)make, tick > 0 ? (u32)tick - 1u : 0u, (u32)g_tick_id, (loading ? 1u : 0u) | (tick > 0 ? 2u : 0u),
                   (u32)us_of(g_draw_acc));
    g_draw_acc = g_xdraw_acc = 0;
    g_last = t;
    g_frames++;
    g_by_prev[0] = by[0], g_by_prev[1] = by[1];
}

double play_stats_percentile(const u32 *hist, u64 count, double p)
{
    u64 rank = (u64)(p * (double)count + 0.999999999), seen = 0;
    if (!count) return 0.0;
    if (rank < 1) rank = 1;
    for (u32 i = 0; i < PS_BUCKETS; i++) {
        seen += hist[i];
        if (seen >= rank) return (double)(i + 1u) * PS_BUCKET_US / 1000.0;
    }
    return (double)PS_BUCKETS * PS_BUCKET_US / 1000.0;
}

void play_stats_restart(void)
{
    if (g_play_stats <= 0) return;
    memset(g_s, 0, sizeof g_s);
    g_frames = g_first = g_last = g_wait_end = g_draw_acc = g_xdraw_acc = g_idle = g_idle_start = 0;
    g_draw_start = g_depth ? now() : 0; /* a draw open across the restart counts from here, not from 0 (a 3008 s "make") */
    g_late = g_late_loading = g_shown_loading = g_repeat = 0;
    memset(g_by_gaps, 0, sizeof g_by_gaps), memset(g_by_late, 0, sizeof g_by_late), memset(g_by_tick, 0, sizeof g_by_tick);
    memset(g_paths, 0, sizeof g_paths);
    InterlockedExchange(&g_tick_pending, 0);
}

static u64 total(const u32 *h)
{
    u64 n = 0;
    for (u32 i = 0; i < PS_BUCKETS; i++) n += h[i];
    return n;
}

void play_stats_report(void)
{
    static const char *const names[SERIES] = {"tick_ms", "draw_ms", "frame_ms", "make_ms"};
    char line[400];
    int n;
    PortView view;
    char host[MAX_COMPUTERNAME_LENGTH + 1];
    DWORD hn = sizeof host;
    u64 judged = g_frames - g_shown_loading;
    double make99, span = g_frames > 1 ? (double)(g_last - g_first) / (double)g_freq : 0.0;
    perf_report();
    if (g_play_stats <= 0 || g_frames < 2 || span <= 0.0) return;
    console_view(&view);
    n = sprintf_s(line, sizeof line, "play summary: shown_fps %.1f output %ux%u frames %llu", (double)(g_frames - 1u) / span,
                  view.out_w, view.out_h, g_frames);
    for (int s = 0; s < SERIES; s++) {
        u64 c = total(g_s[s].hist);
        n += sprintf_s(line + n, sizeof line - (size_t)n, " %s %.2f/%.2f/%.2f", names[s],
                       play_stats_percentile(g_s[s].hist, c, 0.5), play_stats_percentile(g_s[s].hist, c, 0.99),
                       (double)g_s[s].worst_us / 1000.0);
    }
    port_log("%s\n", line);
    make99 = play_stats_percentile(g_s[MAKE].hist, total(g_s[MAKE].hist), 0.99);
    if (!GetComputerNameA(host, &hn) || !host[0]) strcpy_s(host, sizeof host, "unknown"); /* only the user's laptop's counts */
    port_log("play verdict: %s make_ms p99 %.2f (bar %.2f) late %llu of %llu shown (%.2f per 1000, bar 1) loading %llu shown "
             "%llu late machine %s\n", !judged ? "none" : make99 <= PS_MAKE_BAR_MS + 1e-9 && g_late * 1000u <= judged ? "pass" : "fail",
             make99, PS_MAKE_BAR_MS, g_late, judged, judged ? (double)g_late * 1000.0 / (double)judged : 0.0,
             g_shown_loading, g_late_loading, host);
    /* at any cap, not only 144: a slot of the cap with no new frame is a repeated one (the user, 2026-11-09: "It should
     * be compatible at any fps") */
    port_log("play smoothness: cap %u, %llu repeated slot(s) in %llu shown (%.2f per 1000, bar 1)\n", extra_fps(), g_repeat,
             judged, judged ? (double)g_repeat * 1000.0 / (double)judged : 0.0);
    if (g_late) /* where the late slots come from: many short gaps (pacing) or a few long ones (a slow tick) */
        port_log("play late: gaps missing 1 slot %llu, 2 slots %llu (%llu late), 3 to 7 %llu (%llu late), 8 or more %llu "
                 "(%llu late); of them with the tick over half the gap %llu, %llu, %llu, %llu\n", g_by_gaps[0], g_by_gaps[1],
                 g_by_late[1], g_by_gaps[2], g_by_late[2], g_by_gaps[3], g_by_late[3], g_by_tick[0], g_by_tick[1],
                 g_by_tick[2], g_by_tick[3]);
    for (int j = 0; j < 8 && g_paths[j].gaps; j++) /* ffffffff: the paths past the first seven */
        port_log("play late path: shown from port.exe+%x after port.exe+%x: %u gaps, %u late, %u with the tick over half\n",
                 g_paths[j].by, g_paths[j].prev, g_paths[j].gaps, g_paths[j].late, g_paths[j].long_tick);
}

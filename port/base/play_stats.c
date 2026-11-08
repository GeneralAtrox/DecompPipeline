/* The play session's summary and verdict: see play_stats.h. Four histograms of 10 us buckets (tick, draw, shown frame,
 * make), the worst of each, the first and last shown frame's time and the missed 144 slots; the percentile is read from the
 * histogram, so a session of any length costs the same few kilobytes. The clock is the QueryPerformanceCounter, not
 * prof_now(): that one can be the thread's cycle count (--prof-clock cycles), and this must be wall time. */
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "base.h"
#include "console.h"
#include "play_stats.h"
#include "session.h"

int g_play_stats;

enum { TICK, DRAW, FRAME, MAKE, SERIES };
static struct {
    u32 hist[PS_BUCKETS];
    u64 worst_us;
} g_s[SERIES];
static u64 g_freq, g_frames, g_first, g_last, g_wait_end, g_draw_start, g_draw_acc, g_xdraw_acc;
static u64 g_late, g_late_loading, g_shown_loading, g_disc_start, g_disc_bytes;
static volatile LONG g_tick_pending;    /* the last tick's work in us + 1, until a shown frame takes it; 0 none */
static volatile LONG64 g_loading_until; /* clock ticks: a gap that starts before this is a loading screen's */
static DWORD g_game_tid;                /* the thread of the wait and tick hooks */
static u32 g_depth, g_gaps_more;
static int g_force, g_in_wait, g_draw_in_tick; /* g_force: --play-summary 1 on, -1 off, 0 auto */
static u64 g_gap_logged;

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
    InterlockedExchange(&g_tick_pending, (LONG)(us < 0x7ffffffeu ? us + 1u : 0x7fffffffu));
}

void play_stats_wait_slow(int begin)
{
    u64 t;
    if (!g_play_stats && decide() < 0) return;
    t = now();
    g_game_tid = GetCurrentThreadId(); /* a draw on this thread outside the wait is the tick's own (its render) */
    if (begin) {
        if (g_wait_end) tick_done(us_of(t - g_wait_end)); /* the work since the last wait ended; the first has no start */
        g_in_wait = 1;
    } else {
        if (g_wait_end) tick_gap(t - g_wait_end); /* from the end of one wait to the end of the next: one tick */
        g_wait_end = t;
        g_in_wait = 0;
    }
}

void play_stats_tick_slow(double ms)
{
    if (!g_play_stats && decide() < 0) return;
    if (ms < 0.0) return;
    g_game_tid = GetCurrentThreadId();
    tick_done((u64)(ms * 1000.0));
    tick_gap((u64)(ms * 1000.0 * (double)g_freq / 1000000.0));
}

/* The draw behind a shown frame is the outermost span only: pvr_render runs inside an in-between frame's draw. A span
 * the game's thread draws outside the wait is inside a tick, whose work already holds it: left out of the make time. */
void play_stats_draw_slow(int enter)
{
    if (!g_play_stats && decide() < 0) return;
    if (enter) {
        if (g_depth++ == 0) {
            g_draw_start = now();
            g_draw_in_tick = GetCurrentThreadId() == g_game_tid && !g_in_wait;
        }
    } else if (g_depth && --g_depth == 0) {
        u64 d = now() - g_draw_start;
        g_draw_acc += d;
        if (!g_draw_in_tick) g_xdraw_acc += d;
    }
}

void play_stats_disc_slow(u32 bytes)
{
    u64 t = now();
    if (t - g_disc_start > g_freq / 2u) g_disc_start = t, g_disc_bytes = 0;
    g_disc_bytes += bytes;
    if (g_disc_bytes >= PS_LOADING_BYTES) InterlockedExchange64(&g_loading_until, (LONG64)(t + g_freq / 2u));
}

void play_stats_shown_slow(void)
{
    u64 t, gap, slots;
    LONG tick;
    int loading;
    if (!g_play_stats && decide() < 0) return;
    t = now();
    tick = InterlockedExchange(&g_tick_pending, 0);
    loading = g_frames && (u64)InterlockedCompareExchange64(&g_loading_until, 0, 0) >= g_last;
    if (g_frames) {
        gap = us_of(t - g_last);
        add(FRAME, gap);
        slots = (gap * 144u + 500000u) / 1000000u; /* the 144 slots the gap spans, to the nearest: one is on time */
        if (slots > 1u) *(loading ? &g_late_loading : &g_late) += slots - 1u;
    } else {
        g_first = t;
    }
    add(DRAW, us_of(g_draw_acc));
    if (loading) g_shown_loading++;
    else add(MAKE, us_of(g_xdraw_acc) + (tick > 0 ? (u64)tick - 1u : 0u));
    g_draw_acc = g_xdraw_acc = 0;
    g_last = t;
    g_frames++;
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
    g_frames = g_first = g_last = g_wait_end = g_draw_start = g_draw_acc = g_xdraw_acc = 0;
    g_late = g_late_loading = g_shown_loading = 0;
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
}

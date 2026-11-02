/* The play session's summary: see play_stats.h. Three histograms of 10 us buckets (tick, draw, shown frame), the worst of
 * each, and the first and last shown frame's time; the percentile is read from the histogram, so a session of any length
 * costs the same few kilobytes. The clock is the QueryPerformanceCounter, not prof_now(): that one can be the thread's
 * cycle count (--prof-clock cycles), and this must be wall time. */
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "base.h"
#include "console.h"
#include "play_stats.h"
#include "session.h"

int g_play_stats;

enum { TICK, DRAW, FRAME, SERIES };
static struct {
    u32 hist[PS_BUCKETS];
    u64 worst_us;
} g_s[SERIES];
static u64 g_freq, g_frames, g_first, g_last, g_wait_end, g_draw_start, g_draw_acc;
static u32 g_depth;
static int g_force; /* --play-summary: 1 on, -1 off, 0 auto */

static u64 now(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (u64)t.QuadPart;
}

static void add(int s, u64 ticks)
{
    u64 us = ticks * 1000000u / g_freq, b = us / PS_BUCKET_US;
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

void play_stats_wait_slow(int begin)
{
    u64 t;
    if (!g_play_stats && decide() < 0) return;
    t = now();
    if (begin) {
        if (g_wait_end) add(TICK, t - g_wait_end); /* the work since the last wait ended; the first has no start */
    } else {
        g_wait_end = t;
    }
}

void play_stats_tick_slow(double ms)
{
    if (!g_play_stats && decide() < 0) return;
    if (ms >= 0.0) add(TICK, (u64)(ms * 1000.0 * (double)g_freq / 1000000.0));
}

/* The draw behind a shown frame is the outermost span only: pvr_render runs inside an in-between frame's draw. */
void play_stats_draw_slow(int enter)
{
    if (!g_play_stats && decide() < 0) return;
    if (enter) {
        if (g_depth++ == 0) g_draw_start = now();
    } else if (g_depth && --g_depth == 0) {
        g_draw_acc += now() - g_draw_start;
    }
}

void play_stats_shown_slow(void)
{
    u64 t;
    if (!g_play_stats && decide() < 0) return;
    t = now();
    if (g_frames) add(FRAME, t - g_last);
    else g_first = t;
    add(DRAW, g_draw_acc);
    g_draw_acc = 0;
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
    g_frames = g_first = g_last = g_wait_end = g_draw_start = g_draw_acc = 0;
}

static u64 total(const u32 *h)
{
    u64 n = 0;
    for (u32 i = 0; i < PS_BUCKETS; i++) n += h[i];
    return n;
}

void play_stats_report(void)
{
    static const char *const names[SERIES] = {"tick_ms", "draw_ms", "frame_ms"};
    char line[400];
    int n;
    PortView view;
    double span = g_frames > 1 ? (double)(g_last - g_first) / (double)g_freq : 0.0;
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
}

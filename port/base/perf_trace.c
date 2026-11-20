/* The per-frame and per-tick trace behind port/tools/perf.py segment (perf.h): two rings, filled only with --play-frames.
 *   frame rows (FILE): t_us, tick, vblank, gap_us, make_us, tick_us, flags, draw_us, present_us, show_wait_us; one per
 *                      shown frame, from play_stats.c, on the thread that shows it
 *   tick rows (FILE's .ticks.csv): t_us, tick, vblank, then the kit's parts in microseconds (perf_parts: Saturn work,
 *                      game, its waits and chips; Dreamcast the tick cost line's sections), one per tick on the game's thread
 * t_us is the sampler's clock (perf_now_us), so a late frame, its tick and the samples taken in it line up. A ring keeps
 * the last rows of a long run; nothing is allocated after the option is read. Also the segment: --perf-from load+N starts
 * the frame rows, the samples and the play summary N blanks after a save state loads.
 *
 * Nearest existing: perf.c's --play-frames rows (a flat array of 7 columns, no tick rows, no parts): extended here, split
 * out of perf.c for the 400-line cap. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "base.h"
#include "console.h"
#include "perf.h"

#define ROWS_MAX 262144u  /* 30 minutes of frames at 144 a second */
#define TICKS_MAX 131072u /* 36 minutes of ticks at 60 a second */

typedef struct Row {
    u32 t_us, tick, vblank, gap_us, make_us, tick_us, flags, draw_us, present_us, show_wait_us;
} Row;
typedef struct TickRow {
    u32 t_us, tick, vblank, part[PERF_PARTS];
} TickRow;

u32 g_perf_restarts;
static Row *g_rows;
static TickRow *g_ticks;
static u32 g_nrows, g_nticks, g_nparts, g_from_load, g_seg_pending; /* g_from_load: --perf-from load+N, N + 1 */
static const char *g_frames_path;
static const char *const *g_part_names;

void perf_parts(const char *const *names, int count)
{
    g_part_names = names;
    g_nparts = count < (int)PERF_PARTS ? (u32)count : PERF_PARTS;
}

void perf_tick(const u32 *us)
{
    TickRow *r;
    if (!g_ticks || port_vblank_count() < g_perf_from) return;
    r = &g_ticks[g_nticks++ % TICKS_MAX];
    r->t_us = perf_now_us();
    r->tick = g_perf_tick;
    r->vblank = port_vblank_count();
    memcpy(r->part, us, g_nparts * sizeof *us);
}

void perf_frame(u32 gap_us, u32 make_us, u32 tick_us, u32 tick, u32 flags, u32 draw_us)
{
    Row *r;
    if (!g_rows || port_vblank_count() < g_perf_from) return;
    r = &g_rows[g_nrows++ % ROWS_MAX];
    r->t_us = perf_now_us();
    r->tick = tick;
    r->vblank = port_vblank_count();
    r->gap_us = gap_us;
    r->make_us = make_us;
    r->tick_us = tick_us;
    r->flags = flags;
    r->draw_us = draw_us;
    perf_frame_timers(make_us, &r->present_us, &r->show_wait_us);
}

void perf_loaded(void)
{
    if (!g_from_load) return;
    g_perf_from = port_vblank_count() + g_from_load - 1u;
    g_seg_pending = 1;
    port_log("perf: a save state loaded at vertical blank %u: the segment starts at %u (--perf-from load+%u)\n",
             port_vblank_count(), g_perf_from, g_from_load - 1u);
}

int perf_segment_due(void)
{
    if (!g_seg_pending || port_vblank_count() < g_perf_from) return 0;
    g_seg_pending = 0;
    g_perf_restarts++;
    return 1;
}

int perf_trace_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--perf-from") && !strncmp(value, "load+", 5)) {
        g_from_load = (u32)strtoul(value + 5, NULL, 10) + 1u;
        g_perf_from = 0xffffffffu; /* nothing until the load */
        return 2;
    }
    if (!strcmp(arg, "--play-frames")) {
        g_frames_path = value;
        if (!g_rows) g_rows = malloc(ROWS_MAX * sizeof *g_rows);
        if (!g_ticks) g_ticks = malloc(TICKS_MAX * sizeof *g_ticks);
        g_perf_frames = g_rows != NULL && g_ticks != NULL;
        return 2;
    }
    return 0;
}

void perf_trace_write(void)
{
    FILE *f;
    char path[MAX_PATH];
    size_t len;
    const char *dot;
    u32 n, first;
    if (!g_rows || !g_frames_path || fopen_s(&f, g_frames_path, "w") || !f) return;
    n = g_nrows < ROWS_MAX ? g_nrows : ROWS_MAX, first = g_nrows - n; /* the ring, oldest first */
    fprintf(f, "t_us,tick,vblank,gap_us,make_us,tick_us,flags,draw_us,present_us,show_wait_us\n");
    for (u32 j = 0; j < n; j++) {
        const Row *r = &g_rows[(first + j) % ROWS_MAX];
        fprintf(f, "%u,%u,%u,%u,%u,%u,%u,%u,%u,%u\n", r->t_us, r->tick, r->vblank, r->gap_us, r->make_us, r->tick_us, r->flags,
                r->draw_us, r->present_us, r->show_wait_us);
    }
    fclose(f);
    if (g_nrows > n) port_log("perf: frame rows: the ring kept the last %u of %u\n", n, g_nrows);
    if (!g_ticks || !g_nparts) return;
    len = strlen(g_frames_path);
    dot = len > 11 && !_stricmp(g_frames_path + len - 11, ".frames.csv") ? g_frames_path + len - 11 : NULL;
    _snprintf_s(path, sizeof path, _TRUNCATE, "%.*s.ticks.csv", (int)(dot ? (size_t)(dot - g_frames_path) : len), g_frames_path);
    if (fopen_s(&f, path, "w") || !f) return;
    n = g_nticks < TICKS_MAX ? g_nticks : TICKS_MAX, first = g_nticks - n;
    fprintf(f, "t_us,tick,vblank");
    for (u32 i = 0; i < g_nparts; i++) fprintf(f, ",%s", g_part_names[i]);
    fprintf(f, "\n");
    for (u32 j = 0; j < n; j++) {
        const TickRow *r = &g_ticks[(first + j) % TICKS_MAX];
        fprintf(f, "%u,%u,%u", r->t_us, r->tick, r->vblank);
        for (u32 i = 0; i < g_nparts; i++) fprintf(f, ",%u", r->part[i]);
        fprintf(f, "\n");
    }
    fclose(f);
    port_log("perf: trace: %u frame rows, %u tick rows of %u parts\n", g_nrows, g_nticks, g_nparts);
}

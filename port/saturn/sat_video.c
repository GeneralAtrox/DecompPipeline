/* Presents a frame: the VDP2 picture (sat_vdp2.c) composed once per vertical blank, then shown through the base's
 * window (base/present_cpu.c). The window, its menu bar, the keyboard and the close button are the base's
 * (base/host_window.cpp); this file keeps what is the Saturn's: when a frame is composed, the PNG shots
 * (`--shot VB FILE`, `--shot-every N`), the picture-pacing report and the hooks the base's per-frame services want
 * (the game log, the scene dump, the play summary). `--headless` shows nothing.
 *
 * Nearest existing: runtime/gfx/gfx_present.c (the Dreamcast's side of the same presenter): the Saturn's frame is a CPU
 * buffer, so it goes through base/present_cpu.c, which any CPU-composed console can use. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "console.h"
#include "game_log.h"
#include "scene_dump.h"
#include "play_stats.h"
#include "work_count.h"
#include "present_cpu.h"

static int g_headless, g_shot_pending;
static u32 g_shot_vb, g_shot_every, g_shots_taken;
static char g_shot_path[260];
u32 sat_shot_max = 60; /* --shot-max: shots of a --shot-every run (a run that hashes and deletes them can take every frame) */

void sat_video_options(int headless, u32 shot_vb, const char *shot_path, u32 every)
{
    g_headless = headless;
    g_shot_every = every;
    if (shot_path) {
        g_shot_vb = shot_vb;
        g_shot_pending = 1;
        strncpy(g_shot_path, shot_path, sizeof g_shot_path - 1);
    }
}

int sat_video_headless(void) { return g_headless; }

/* --dump-frames N,N,.. [--dump-dir DIR]: the picture at the first vertical blank at or after each N, as DIR/frame_NNNNNN.png
 * (what the scene dump's replay command asks for; the Dreamcast kit's names). */
static u32 g_dump_at[16];
static unsigned g_dump_n, g_dump_next;
static char g_dump_dir[260] = ".";

int sat_video_dump_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--dump-dir")) {
        strncpy(g_dump_dir, value, sizeof g_dump_dir - 1);
        return 2;
    }
    if (!strcmp(arg, "--dump-frames")) {
        for (const char *s = value; *s && g_dump_n < 16;) {
            char *end;
            g_dump_at[g_dump_n++] = (u32)strtoul(s, &end, 10);
            s = *end == ',' ? end + 1 : end;
            if (end == s && *s) break;
        }
        return 2;
    }
    return 0;
}

void sat_video_init(void)
{
    if ((!g_headless || g_work_on) && !cpu_present_open())
        port_log("video: no window; the run goes on without a picture\n");
    else if (!g_headless || g_work_on)
        cpu_present_thread_start(!g_headless && !host_fixed_clock()); /* a play run's frames are shown by a thread of their own */
}

/* Picture pacing, for judging stutter on the real clock: the wall time between presents (buckets, ms) and how many
 * presents each distinct picture is held (a 25 fps movie on a 50 Hz game wants steady runs of 2). */
static u32 g_gap[6], g_held[10], g_prev_hash, g_run;
static char g_seq[1500]; /* each picture's hold, 1..9, in order */
static u32 g_nseq;
static LONGLONG g_prev_t;

static void pacing_note(LONGLONG now)
{
    u32 h = 2166136261u;
    LONGLONG freq;
    LARGE_INTEGER f;
    QueryPerformanceFrequency(&f);
    freq = f.QuadPart;
    for (u32 i = 0; i < sat_frame_w * sat_frame_h; i += 7) h = (h ^ sat_frame[i]) * 16777619u;
    if (g_prev_t) {
        u32 ms = (u32)((now - g_prev_t) * 1000 / freq);
        g_gap[ms < 15 ? 0 : ms < 25 ? 1 : ms < 35 ? 2 : ms < 50 ? 3 : ms < 100 ? 4 : 5]++;
    }
    g_prev_t = now;
    if (h == g_prev_hash) {
        g_run++;
    } else {
        g_held[g_run > 9 ? 9 : g_run]++;
        if (g_nseq < sizeof g_seq - 1) g_seq[g_nseq++] = (char)('0' + (g_run > 9 ? 9 : g_run));
        g_run = 1;
        g_prev_hash = h;
    }
}

void sat_video_pacing_report(void)
{
    port_log("pacing: holds in order: %s\n", g_seq);
    port_log("pacing: gap between presents <15 ms %u, <25 %u, <35 %u, <50 %u, <100 %u, more %u\n", g_gap[0], g_gap[1], g_gap[2], g_gap[3], g_gap[4], g_gap[5]);
    port_log("pacing: presents a picture was held for 1..9+: %u %u %u %u %u %u %u %u %u\n", g_held[1], g_held[2], g_held[3], g_held[4], g_held[5], g_held[6], g_held[7], g_held[8], g_held[9]);
    cpu_present_report();
    sat_inb_report();
}

/* With --present-thread the frame is posted to the thread that shows it, which counts the shown frames and their draw for
 * the play summary; the picture's time here is then the tick's. */
/* the composed picture is black (every dot 0); it stops at the first lit dot, so a normal picture costs a few reads */
static int frame_black(void)
{
    const u32 *p = sat_frame, *end = sat_frame + sat_frame_w * sat_frame_h;
    while (p < end && !(*p & 0xffffffu)) p++;
    return sat_frame_w && p == end;
}

void sat_present(void)
{
    LARGE_INTEGER t0, t1, t2;
    int own = !cpu_present_threaded();
    QueryPerformanceCounter(&t0);
    if (own) play_stats_draw(1);
    sat_perf_phase(1);
    sat_wide_frame(); /* the picture's width this blank (sat_wide.c) */
    sat_inb_vblank();
    sat_compose();
    sat_perf_phase(0);
    QueryPerformanceCounter(&t1);
    sat_perf_compose(t1.QuadPart - t0.QuadPart);
    pacing_note(t1.QuadPart);
    {
        int wide = console_widens(), black = sat_black_possible() || wide ? frame_black() : 0;
        if (sat_black_possible()) sat_black_frame(black);
        if (wide) sat_wide_seen(black); /* the log's "widescreen: shown" lines (sat_wide.c) */
    }
    if (g_shot_pending && sat_vblank_count() >= g_shot_vb) {
        char path[300];
        snprintf(path, sizeof path, g_shot_every ? "%s_%05u.png" : "%s", g_shot_path, sat_vblank_count());
        g_shot_pending = g_shot_every != 0 && ++g_shots_taken < sat_shot_max;
        g_shot_vb += g_shot_every;
        sat_vdp_report();
        if (sat_png_write(path, sat_frame, sat_frame_w, sat_frame_h))
            port_log("shot: %s (%ux%u) at vertical blank %u\n", path, sat_frame_w, sat_frame_h, sat_vblank_count());
    }
    if (g_dump_next < g_dump_n && sat_vblank_count() >= g_dump_at[g_dump_next]) {
        char path[300];
        snprintf(path, sizeof path, "%s/frame_%06u.png", g_dump_dir, g_dump_at[g_dump_next++]);
        if (sat_png_write(path, sat_frame, sat_frame_w, sat_frame_h)) port_log("dump: %s (%ux%u) at vertical blank %u\n", path, sat_frame_w, sat_frame_h, sat_vblank_count());
    }
    if (sat_inb_live()) sat_inb_post(); /* shown a blank later, after the in-between frames (sat_inb.c) */
    else if (!g_headless || g_work_on) sat_wide_post(sat_frame, sat_frame_w, sat_frame_h, sat_vblank_count());
    if (own) play_stats_draw(0);
    QueryPerformanceCounter(&t2);
    sat_perf_show(t2.QuadPart - t1.QuadPart);
    game_log_poll(sat_vblank_count());
    scene_dump_poll(sat_vblank_count());
    if (own) play_stats_shown();
}

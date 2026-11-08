/* The Saturn's clock and interrupt delivery. A vertical blank comes on the real clock (50 Hz PAL or 60 Hz NTSC from
 * the game's port_config.h); the machine runs between safe points, tc_call every 4096 calls and tc_spin, so an
 * interrupt is delivered there: the handler address is read from the vector table at VBR (work RAM 0x06000000) as the
 * SH-2's exception entry does, and the handler runs on a fresh frame (sat_call_here) with the mask raised.
 * `--fast` (a test aid, never the launcher's) skips the wait for the real clock.
 * `--fixed-clock 1` (the base's checks and replays) runs the machine on a virtual clock instead: time moves only when
 * the guest waits (sat_spin goes to the next vertical blank) and by a fixed step at each safe point, so a vertical
 * blank, a raster read and so a whole run depend on the guest's own calls and never on this computer's speed.
 * `--det` (earlier, kept): the vertical blank and the timer follow the machine's progress by safe-point counts instead; it
 * wins when both are given.
 * A black loading screen runs without waiting (play runs with Display > Fast disc loads on, sat_black_frame): the game
 * gets the same blanks, sooner. */
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "work_count.h"
#include "sat_cd.h"
#include "sat_scsp.h"
#include "sat_net.h"
#include "sat_vdp.h"

enum { VIRTUAL_STEP_US = 1000, READ_STEP_US = 5 }; /* the virtual clock's step at each safe point (a poll for an interrupt) */
enum { MIN_GAME_US = 5000 }; /* the real clock: the game's least time between one vertical blank's work and the next */
enum { IDLE_ASKS = 32, IDLE_TURNS = 8 };
static LARGE_INTEGER g_freq, g_next;
static u32 g_vblanks, g_period_us = 20000u, g_hz = 50u;
static int g_fast, g_det;
static u32 g_det_polls, g_det_reads;
enum { DET_POLLS = 48 }; /* --det: safe points (4096 calls each) from one vertical blank to the next when the game never waits */
static int g_virtual;
static LONGLONG g_vnow;
static int g_in_service[14]; /* a source whose handler is running is not delivered again until it returns */

u32 sat_vblank_count(void) { return g_vblanks; }

static void next_deadline(void) { g_next.QuadPart += (LONGLONG)g_period_us * g_freq.QuadPart / 1000000; }

/* the machine's time: the virtual clock's, else the computer's */
static LONGLONG clock_now(void)
{
    LARGE_INTEGER now;
    if (g_virtual) return g_vnow;
    QueryPerformanceCounter(&now);
    return now.QuadPart;
}

void sat_sys_init(void)
{
    g_virtual = host_fixed_clock();
    QueryPerformanceFrequency(&g_freq);
    QueryPerformanceCounter(&g_next);
    if (g_virtual) g_next.QuadPart = g_vnow = 0;
    if (g_virtual) next_deadline();
}

int sat_sys_fast(void) { return g_fast; }

/* The user (2026-11-04): cut the black pause after skipping the intro, in play runs only. After START in the intro the
 * game shows 79 blanks of black (1.6 s) while it stops the movie and reads the title's files. While the composed picture
 * is black, the SCSP's sound silent and no disc track playing, a vertical blank comes as soon as the game waits for one
 * instead of on the real clock: the game runs the same blanks, the wall clock is shorter. Under Display > Fast disc
 * loads (sat_cd.c, on in play runs); never in a check (--det, --fixed-clock, --fast) or a headless run. */
static int g_black, g_black_fast;
static u32 g_black_from, g_black_fast_n;
static LARGE_INTEGER g_black_t0;
int sat_black_possible(void) { return sat_cd_fast_loads > 0 && !g_virtual && !g_fast && !g_det && !sat_video_headless(); }
int sat_black_fast(void) { return g_black_fast; }
void sat_black_frame(int black)
{
    LARGE_INTEGER now;
    if (black && !g_black) {
        g_black_from = g_vblanks, g_black_fast_n = 0;
        QueryPerformanceCounter(&g_black_t0);
    }
    if (!black && g_black) {
        QueryPerformanceCounter(&now);
        port_log("black: %u blanks of black screen (vertical blanks %u..%u) in %.0f ms, %u of them without waiting\n", g_vblanks - g_black_from,
                 g_black_from, g_vblanks, (double)(now.QuadPart - g_black_t0.QuadPart) * 1000.0 / (double)g_freq.QuadPart, g_black_fast_n);
    }
    g_black = black;
    g_black_fast = black && sat_audio_silent() && !sat_cdda_playing();
    g_black_fast_n += (u32)g_black_fast;
}
int sat_sys_det(void) { return g_det; }
void sat_sys_set_det(int on) { g_det = on; if (on) g_fast = 1; }

/* --det: the free-running timer from the machine's own progress (the frame, safe points into it, reads so far), so a
 * run with the same input repeats exactly. 3579545 ticks a second at clock / 8, a quarter of that for each step of TCR's
 * divider (`shift` 0, 2 or 4). */
u32 sat_det_frt(u32 shift)
{
    u32 per = (3579545u >> shift) / g_hz, in = g_det_polls * (per / DET_POLLS) + g_det_reads++ * 8u;
    if (in >= per) in = per - 1u; /* never past the next blank's count: a timer read as going back is a wrap (Robo Pit's movie clock) */
    return (g_vblanks * per + in) & 0xffffu;
}

int sat_sys_virtual(void) { return g_virtual; }

/* Time since start-up in units of 1/rate second (the free-running timer reads it). On the virtual clock each read is
 * itself a few microseconds of the machine's time, so a loop that waits on the timer ends. */
u64 sat_clock_ticks(u32 rate)
{
    static LONGLONG t0;
    LONGLONG now;
    if (g_virtual) g_vnow += (LONGLONG)READ_STEP_US * g_freq.QuadPart / 1000000;
    now = clock_now();
    if (!t0) t0 = now;
    return (u64)(now - t0) * rate / (u64)g_freq.QuadPart;
}

void sat_sys_set(u32 hz, int fast)
{
    g_hz = hz ? hz : 50u;
    g_period_us = 1000000u / g_hz;
    g_fast = fast;
}

/* delivers pending SCU interrupts the mask allows; the highest-level one first */
void sat_irq_poll(void)
{
    static const u8 vec[14] = {0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4a, 0x4b, 0x4c, 0x4d};
    static const u8 lvl[14] = {15, 14, 13, 12, 11, 10, 9, 8, 8, 6, 6, 5, 3, 2};
    LONGLONG now;
    if (g_det) {
        if (++g_det_polls >= DET_POLLS) {
            g_det_polls = 0;
            g_det_reads = 0;
            sat_net_vblank(); /* online play: the frame's pads, once both players' are in (sat_net.c) */
            sat_vblank_in();
        } else if (sat_inb_live()) { /* online play, a play run on this timeline: an in-between frame that is due (sat_inb.c) */
            LARGE_INTEGER t;
            QueryPerformanceCounter(&t);
            sat_inb_poll(t.QuadPart, sat_net_next());
        }
    } else {
        if (g_virtual) g_vnow += (LONGLONG)VIRTUAL_STEP_US * g_freq.QuadPart / 1000000;
        now = clock_now();
        if (now >= g_next.QuadPart) {
            while (now >= g_next.QuadPart) next_deadline();
            sat_vblank_in();
            if (!g_virtual) { /* a picture that took the whole frame must not leave the game without time: the frame runs late instead */
                LONGLONG least = (LONGLONG)MIN_GAME_US * g_freq.QuadPart / 1000000;
                now = clock_now();
                if (g_next.QuadPart < now + least) g_next.QuadPart = now + least;
            }
        } else if (!g_virtual) {
            sat_inb_poll(now, g_next.QuadPart); /* an in-between frame that is due (sat_inb.c) */
        }
    }
    for (;;) {
        u32 ready = sat_scu_deliverable(), best = 99, cur = (sat_sr() >> 4) & 15u;
        for (u32 b = 0; b < 14; b++)
            if ((ready >> b) & 1u && !g_in_service[b] && (best == 99 || lvl[b] > lvl[best])) best = b;
        if (best == 99 || lvl[best] <= cur) return;
        {
            u32 handler = sat_peek32(0x06000000u + 4u * vec[best]);
            sat_scu_ack(best);
            if (handler) {
                g_in_service[best] = 1;
                sat_call_here(handler, lvl[best]);
                g_in_service[best] = 0;
            }
        }
    }
}

/* The raster position from the real clock: the line the beam is on (0..total-1) and how far along that line (0..255) */
u32 sat_scanline(u32 total, u32 *along)
{
    LONGLONG start, len = (LONGLONG)g_period_us * g_freq.QuadPart / 1000000, into;
    if (g_det) {
        u32 at = g_det_polls * 256u / DET_POLLS * total + (g_det_reads & 255u);
        if (along) *along = at & 255u;
        return at / 256u;
    }
    start = g_next.QuadPart - len;
    into = clock_now() - start;
    if (into < 0) into = 0;
    if (into >= len) into = len - 1;
    if (along) *along = (u32)((into * total * 256 / len) & 255);
    return (u32)(into * total / len);
}

void sat_spin(void)
{
    /* the game waits for the hardware: advance to the next vertical blank */
    LARGE_INTEGER now;
    if (g_det) {
        g_det_polls = DET_POLLS - 1u;
    } else if (g_virtual) {
        if (g_vnow < g_next.QuadPart) g_vnow = g_next.QuadPart; /* no waiting: the time is simply later */
    } else if (!g_fast && !g_black_fast) {
        LONGLONG until = g_next.QuadPart, due = sat_inb_due(); /* wakes for an in-between frame that is due first */
        if (due && due < until) until = due;
        sat_perf_wait_begin();
        QueryPerformanceCounter(&now);
        if (now.QuadPart < until) pace_wait_us((u64)((until - now.QuadPart) * 1000000 / g_freq.QuadPart));
        sat_perf_wait_end();
    } else {
        QueryPerformanceCounter(&g_next);
    }
    sat_irq_poll();
}

/* a question whose answer changes only with time (the drive's status, which it delivers once a frame): this many in one frame
 * and the game is only waiting, so the rest of the frame need not be burned asking */
void sat_idle_ask(void)
{
    static u32 at, asks;
    if (at != g_vblanks) at = g_vblanks, asks = 0;
    if (++asks >= IDLE_ASKS) sat_vblank_wait();
}

/* the game's main loop (game.json "main_loops") came round again in this frame with nothing to do yet */
void sat_idle_turn(void)
{
    static u32 at, turns;
    if (at != g_vblanks) at = g_vblanks, turns = 0;
    if (++turns >= IDLE_TURNS) sat_vblank_wait();
}

u32 sat_sys_hz(void) { return g_hz; }

void sat_vblank_counted(void)
{
    static u32 s_writes;
    g_vblanks++;
    work_add(WC_GUEST_WRITES, sat_wr_n - s_writes);
    s_writes = sat_wr_n;
    work_count_tick(g_vblanks);
}

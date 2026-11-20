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
 * Fast forward (base/speed.c, a play run on the real clock): the machine's clock runs port_speed() times as fast
 * (port_speed_clock), so the vertical blanks, the free-running timer, the raster and the sound (sat_scsp.c) all keep the
 * time they have at normal speed between two blanks; the game only gets them sooner. As fast as it goes: no wait, the
 * clock jumps to the blank instead.
 * A black loading screen runs without waiting (play runs with Display > Fast disc loads on, sat_black_frame): the game
 * gets the same blanks, sooner. A long still loading picture runs faster (sat_load_frame). */
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "console.h"
#include "work_count.h"
#include "sat_cd.h"
#include "sat_scsp.h"
#include "sat_net.h"
#include "sat_vdp.h"
#include "host.h" /* port_menu_pick_poll: --menu-pick FRAME is the vertical blank here */
#include "save_state.h"

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
static PortSpeedClock g_clk;   /* fast forward: the machine's clock against the real one */
static u32 g_speed = 1u, g_eff = 1u, g_ff_vb; /* g_eff: the clock's speed since its last reading (fast forward's, or a sped loading blank's) */
static LONGLONG g_ff_at;

u32 sat_vblank_count(void) { return g_vblanks; }

static void next_deadline(void) { g_next.QuadPart += (LONGLONG)g_period_us * g_freq.QuadPart / 1000000; }

enum { LOAD_FULL = 20, LOAD_SPEED = 12 }; /* a still loading picture's blanks: the first LOAD_FULL at their pace, then LOAD_SPEED x */
static int g_load_fast; /* this blank is one of a still loading picture's sped blanks (sat_load_frame) */
static int g_kit_work; /* the kit's own work at a blank (sat_vblank_in): never sped, so the game keeps its frame's time */

/* the machine's time: the virtual clock's, else the computer's (fast forward's: port_speed_clock) */
static LONGLONG clock_now(void)
{
    LARGE_INTEGER now;
    LONGLONG m;
    u32 s, e;
    if (g_virtual) return g_vnow;
    QueryPerformanceCounter(&now);
    s = port_speed();
    e = s == 1u && g_load_fast && !g_kit_work ? (u32)LOAD_SPEED : s;
    m = port_speed_clock(&g_clk, now.QuadPart, g_eff); /* the time since the last reading went at the old speed */
    g_eff = e;
    if (s != g_speed) {
        if (g_speed == 1u) g_ff_vb = g_vblanks, g_ff_at = m;
        else if (s == 1u && g_vblanks > g_ff_vb)
            port_log("speed: the machine's clock went %.2f ms a vertical blank while fast (%.2f at normal speed), %u blanks\n",
                     (double)(m - g_ff_at) * 1000.0 / (double)g_freq.QuadPart / (g_vblanks - g_ff_vb), 1000.0 / g_hz, g_vblanks - g_ff_vb);
        g_speed = s;
    }
    if (g_load_fast && m > g_next.QuadPart) { /* a sped loading blank: the clock stops at the blank until it is delivered */
        g_clk.ahead -= m - g_next.QuadPart;
        m = g_next.QuadPart;
    }
    return m;
}

long long sat_sys_now(void) { return clock_now(); }
/* sat_vdp.c sat_vblank_in: the kit's own work at a blank (the picture, the drive's frame, the sound) begins (1) or ends (0).
 * In a still loading picture's sped blanks the machine's clock runs LOAD_SPEED x only outside it: at that speed the kit's
 * couple of milliseconds would take most of the game's frame, and a drive that delivers by time would give it less. */
void sat_sys_kit_work(int on)
{
    clock_now(); /* the time so far at the speed it went */
    g_kit_work = on;
}
u32 sat_sys_speed(void) { return g_speed; }

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

/* The user (2026-11-17), of Dragon Force's NOW LOADING between the map and a battle (257 and 384 blanks, 4-6 s): "it should
 * be about 1 second long", not instant. The game takes one sector of DF.BIN or FIELD0.BIN a frame there, whatever the
 * drive's speed, and spins in its wait between them until the blank. While the game takes sectors of a quick data play
 * (sat_cd_loading) and its picture holds still from one blank to the next, the first LOAD_FULL blanks keep their pace,
 * then the machine's clock runs LOAD_SPEED times as fast as the real one (clock_now, as fast forward's; not in the kit's
 * own work at the blank, sat_sys_kit_work) and stops at each blank until it is delivered: the game runs the same blanks and sees at most a frame's time in each (its timer, the
 * drive), only sooner, and the sound made in them is not queued. A sped blank takes a LOAD_SPEED-th of a frame or its
 * work's real time (about 2.5 ms on build box A): 257 blanks about 0.9 s, 384 about 1.2 s. Only for a game with the option
 * "fast-loading-screens" on (fast_loading_screens), with Display > Fast disc loads as the black screens; never in a check
 * or a headless or fixed-clock run. A picture that moves (a game streaming while it plays) is never sped. */
static int g_loading;
static u32 g_load_from, g_load_fast_n, g_load_last;
static LARGE_INTEGER g_load_t0;
static double g_load_fast_ms; /* real time from the blank before a sped blank to the next blank */
int sat_load_fast(void) { return g_load_fast; }
/* the game's option (console.h PortOption "fast-loading-screens", Dragon Force's df_dialogue.c): 1 on; a game without it
 * never has its loading screens sped */
static int fast_loading_screens(void)
{
    static int at = -2;
    if (at == -2) at = port_option_find("fast-loading-screens");
    return at >= 0 && port_option_at(at) == 1;
}
void sat_load_frame(int loading, int still)
{
    static LARGE_INTEGER prev;
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (g_load_fast) g_load_fast_ms += (double)(now.QuadPart - prev.QuadPart) * 1000.0 / (double)g_freq.QuadPart;
    prev = now;
    if (g_loading && g_vblanks != g_load_last + 1u) g_loading = 0, g_load_fast = 0; /* the blanks jumped (a save state): not one load */
    g_load_last = g_vblanks;
    if (loading && !g_loading) {
        g_load_from = g_vblanks, g_load_fast_n = 0, g_load_fast_ms = 0;
        g_load_t0 = now;
    }
    if (!loading && g_loading)
        port_log("loading: %u blanks while the game took a quick disc read (vertical blanks %u..%u) in %.0f ms; %u of them "
                 "%dx sooner (a still picture), in %.0f ms\n", g_vblanks - g_load_from, g_load_from, g_vblanks,
                 (double)(now.QuadPart - g_load_t0.QuadPart) * 1000.0 / (double)g_freq.QuadPart, g_load_fast_n, LOAD_SPEED,
                 g_load_fast_ms),
        sat_perf_span(g_load_fast_n, g_vblanks - g_load_from - g_load_fast_n, "loading: its blanks at full pace"),
        sat_perf_span(0, g_load_fast_n, "loading: its sped blanks");
    g_loading = loading;
    g_load_fast = fast_loading_screens() && loading && still && g_vblanks - g_load_from >= LOAD_FULL && !g_black_fast && !sat_cdda_playing();
    g_load_fast_n += (u32)g_load_fast;
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
static LONGLONG t0; /* sat_clock_ticks' start */
u64 sat_clock_ticks(u32 rate)
{
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
        now.QuadPart = clock_now(); /* the machine's: fast forward waits a speed's part of it on the real clock */
        if (now.QuadPart < until && g_speed == PORT_SPEED_FREE) g_clk.ahead += until - now.QuadPart; /* as fast as it goes: no wait */
        else if (now.QuadPart < until) pace_wait_us((u64)((until - now.QuadPart) * 1000000 / g_freq.QuadPart / g_eff)); /* g_eff: a sped loading blank's too */
        sat_perf_wait_end();
    } else {
        g_next.QuadPart = clock_now();
    }
    sat_irq_poll();
}

/* the game waits on a flag (sat_call.c's sat_poll_hint and sat_busy_poll): at fast forward's as fast as it goes the blank
 * it waits for is due at once (the clock jumps to it, as sat_spin's wait does), else it would wait at 1x; at any other
 * speed this is sat_irq_poll */
void sat_wait_poll(void)
{
    if (!g_det && !g_virtual && port_speed() == PORT_SPEED_FREE) {
        LONGLONG now = clock_now();
        if (now < g_next.QuadPart) g_clk.ahead += g_next.QuadPart - now;
    }
    sat_irq_poll();
}

/* a question whose answer changes only with time (the drive's status, which it delivers once a frame): this many in one frame
 * and the game is only waiting, so the rest of the frame need not be burned asking */
static u32 g_ask_at, g_asks, g_turn_at, g_turns;
void sat_idle_ask(void)
{
    if (g_ask_at != g_vblanks) g_ask_at = g_vblanks, g_asks = 0;
    if (++g_asks >= IDLE_ASKS) sat_vblank_wait();
}

/* the game's main loop (game.json "main_loops") came round again in this frame with nothing to do yet */
void sat_idle_turn(void)
{
    if (g_turn_at != g_vblanks) g_turn_at = g_vblanks, g_turns = 0;
    if (++g_turns >= IDLE_TURNS) sat_vblank_wait();
}

u32 sat_sys_hz(void) { return g_hz; }

void sat_vblank_counted(void)
{
    static u32 s_writes;
    g_vblanks++;
    port_menu_pick_poll(g_vblanks);
    work_add(WC_GUEST_WRITES, sat_wr_n - s_writes);
    s_writes = sat_wr_n;
    work_count_tick(g_vblanks);
    save_state_tick(g_vblanks); /* --state-save / --state-load come due (base/save_state_slots.c) */
}

/* A save state's clock: the vertical blank count and the safe points' counts, and the machine's time as it stands
 * against the next blank and the timer's start, so a load on the real clock carries on from now and on the virtual
 * clock repeats exactly (the virtual time itself is kept, in this computer's ticks; another tick rate is scaled). */
void sat_sys_state(SatSt *io)
{
    LONGLONG now = clock_now(), next_in = g_next.QuadPart - now, t0_ago = t0 ? now - t0 : 0, vnow = g_vnow, freq = g_freq.QuadPart;
    sat_st(io, &g_vblanks, 4), sat_st(io, &g_det_polls, 4), sat_st(io, &g_det_reads, 4);
    sat_st(io, &g_ask_at, 4), sat_st(io, &g_asks, 4), sat_st(io, &g_turn_at, 4), sat_st(io, &g_turns, 4);
    sat_st(io, &vnow, 8), sat_st(io, &next_in, 8), sat_st(io, &t0_ago, 8), sat_st(io, &freq, 8);
    if (!sat_st_loading(io)) return;
    if (freq > 0 && freq != g_freq.QuadPart) {
        vnow = (LONGLONG)((double)vnow * (double)g_freq.QuadPart / (double)freq);
        next_in = (LONGLONG)((double)next_in * (double)g_freq.QuadPart / (double)freq);
        t0_ago = (LONGLONG)((double)t0_ago * (double)g_freq.QuadPart / (double)freq);
    }
    if (g_virtual) g_vnow = vnow;
    now = clock_now();
    g_next.QuadPart = now + next_in;
    t0 = t0_ago ? now - t0_ago : 0;
    memset(g_in_service, 0, sizeof g_in_service);
}

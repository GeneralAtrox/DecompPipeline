/* In-between frames (--present interp): the simulation keeps the original tick, and every frame the game renders is
 * still counted and dumped, but it is not shown at once. While it waits for its vertical blank (host.c), the game's
 * draw function (port_set_extra_draw) draws more frames, which are shown and then undone: guest RAM and the host
 * state the kit registers (port_extra_state: the register file, rand, the virtual clock, Kamui's banks) go back to
 * what the tick left, so the next tick runs exactly as it would have. A frame with no in-between frame (no time, or
 * none ready) is shown after the wait, as --present tick does.
 *
 *   --present tick|interp|auto  one shown frame per rendered one (the kit's default; the checks use it), or in-between
 *                           frames too, or (a play run's default, base/host_main.cpp) in-between frames for the slots
 *                           the frame-rate cap gives above each rendered frame's rate (auto_decide)
 *   --interp-frames N       --fixed-clock runs: in-between frames per vertical blank waited for (default 1)
 *   --interp-verify 1       logs what in-between frames wrote in guest RAM, and checks that it all went back
 *   --fps N                 real time: in-between frames shown one per 1/N s slot (frame_thread.c; a play run's default
 *                           144, base/host_main.cpp; 0: as many as the display takes and the time allows)
 *   --interp-copy-threads N threads that copy guest RAM (default 4; 1: this thread alone)
 *   --interp-scene on|only|off  a game with no draw function gets in-between frames anyway: the renderer blends the
 *                           scene it drew with the one before (pvr/pvr_blend.c). No game code runs, so there is no
 *                           copy of guest RAM and nothing to undo. With on a game's own draw function takes
 *                           precedence; with only it is not used; off uses the draw function alone. Default off,
 *                           or only for a game with game.json interp_scene
 *   --present-thread on|off|fixed|hold  on: a scene-blended game's in-between frames are drawn and shown by a thread of
 *                           their own on the real clock, so a tick of any length costs only the scene it has not
 *                           delivered; a frame of its own is shown by the tick only when that thread showed none. The
 *                           thread goes on from the end of a render, so a game that renders early in its tick shows no
 *                           long gap, and while nothing can be blended (a movie) it shows the last picture again, the
 *                           same pixels (with a fixed clock or frame dumps it works as hold). hold: the thread stops
 *                           from a render until the vertical blank's wait, and a movie's pictures are shown once each.
 *                           fixed: hold with a fixed clock too, for the proof that ticks do not change. Default off, or
 *                           on for a game with game.json present_thread
 *
 * Guest RAM is copied when the first in-between frame after a vertical blank is drawn (the vertical blank's hook
 * may write it) and copied back after each one. */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"
#include "host.h"
#include "prof.h"
#include "play_stats.h"
#include "frame_cost.h"

#define EXTRA_STATES 16
#define PAGE 4096u
#define VERIFY_RANGES 6
#define RESHOW_US 1500u /* --present-thread on: a movie's picture is shown again this often */

static int g_scene_mode = PORT_INTERP_SCENE ? 2 : 0;
static int g_interp, g_auto, g_verify, g_inited, g_held, g_noted, g_drawing; /* g_held: the frame of a flip waits for its in-between frames */
static u32 g_fixed_frames = 1u;
static PortExtraDrawFn g_draw;
static PortExtraReadyFn g_ready;
static struct {
    void *p;
    u32 size;
    u8 *saved;
} g_states[EXTRA_STATES];
static u32 g_nstates;
/* The undo log: what an in-between frame overwrote outside guest RAM (port_extra_undo), put back in reverse. */
typedef struct Undo {
    struct Undo *prev;
    void *p;
    u32 size;
    PortUndoFn after;
} Undo;
static Undo *g_undo;
static u8 *g_ram;                 /* guest RAM as the tick left it */
static u32 g_ram_vblank = ~0u;    /* the vertical blank g_ram was copied at */
static u32 g_wait;                /* vertical blanks the rendered frame waits for */
static u32 g_count, g_fixed_done; /* in-between frames since port_extra_begin; --fixed-clock: drawn so far */
static u64 g_start_us, g_cost_us; /* when the frame was rendered; what an in-between frame costs, recently */
static u32 g_fps;                 /* --fps N: in-between frames at most N per second (0: as many as fit) */
static u64 g_shown_us;            /* when the last in-between frame started */
static u32 g_total, g_reshown, g_begun, g_no_time, g_not_shown; /* begun: renders that allowed them; skipped, why */
static int g_starved;                       /* this rendered frame's wait skipped one for want of time */
static int g_thread_mode = PORT_PRESENT_THREAD ? 3 : 0; /* --present-thread: 0 off, 1 hold, 2 fixed, 3 on */

int extra_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--present")) {
        g_auto = !strcmp(value, "auto");
        g_interp = g_auto || !strcmp(value, "interp");
    }
    else if (!strcmp(arg, "--interp-frames")) g_fixed_frames = (u32)atoi(value);
    else if (!strcmp(arg, "--interp-verify")) g_verify = atoi(value) != 0;
    else if (!strcmp(arg, "--fps")) g_fps = (u32)atoi(value);
    else if (!strcmp(arg, "--interp-copy-threads")) extra_copy_threads((u32)atoi(value));
    else if (!strcmp(arg, "--interp-scene")) g_scene_mode = !strcmp(value, "only") ? 2 : !strcmp(value, "on");
    else if (!strcmp(arg, "--present-thread")) g_thread_mode = !strcmp(value, "fixed") ? 2 : !strcmp(value, "hold") ? 1 : !strcmp(value, "on") ? 3 : 0;
    else return 0;
    return 2;
}

/* The host's menu (base/host_menu.cpp): the same as the options, at any time */
int extra_interp(void) { return g_interp; }
void extra_set_fps(unsigned fps) { g_fps = fps; }
unsigned extra_fps(void) { return g_fps; }

void port_set_extra_draw(PortExtraDrawFn draw, PortExtraReadyFn ready)
{
    if (g_scene_mode == 2) return; /* --interp-scene only */
    g_draw = draw;
    g_ready = ready;
}

void port_extra_state(void *p, u32 size)
{
    for (u32 i = 0; i < g_nstates; i++)
        if (g_states[i].p == p) return;
    if (g_nstates < EXTRA_STATES) g_states[g_nstates].saved = malloc(size);
    if (g_nstates == EXTRA_STATES || !g_states[g_nstates].saved)
        port_exit("frame_extra: too much state registered for in-between frames", 1);
    g_states[g_nstates].p = p;
    g_states[g_nstates++].size = size;
}

void port_extra_undo(void *p, u32 size, PortUndoFn after)
{
    Undo *u;
    if (!g_drawing || !size) return;
    u = malloc(sizeof *u + size);
    if (!u) port_exit("frame_extra: no memory for the undo log", 1);
    memcpy(u + 1, p, size);
    u->prev = g_undo;
    u->p = p;
    u->size = size;
    u->after = after;
    g_undo = u;
}

static void undo_all(void)
{
    while (g_undo) {
        Undo *u = g_undo;
        memcpy(u->p, u + 1, u->size);
        if (u->after) u->after(u->p, u->size);
        g_undo = u->prev;
        free(u);
    }
}

int port_extra_drawing(void) { return g_drawing; }
/* --present auto keeps the scene before while blending is off, so that turning it on does not blend with an old one */
int extra_scene(void) { return (g_interp || g_auto) && g_scene_mode && !g_draw; }
int extra_scene_mode(void) { return g_draw ? -1 : g_scene_mode; }
u32 port_extra_count(void) { return g_count; }

static int init(void)
{
    g_ram = VirtualAlloc(NULL, GUEST_RAM_SIZE, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!g_ram) {
        port_log("frame_extra: no copy of guest RAM; in-between frames off\n");
        g_interp = 0;
        return 0;
    }
    extra_copy_init();
    sh4_rt_extra_state();
    port_libm_extra_state();
    host_extra_state();
    port_log("frame_extra: in-between frames on%s%s; guest RAM copied by %u thread(s)\n",
             host_fixed_clock() ? " (a fixed number per vertical blank)" : "", g_verify ? ", checked" : "", extra_copy_thread_count());
    return g_inited = 1;
}

/* --present auto, for each rendered frame: in-between frames fill the slots the cap gives above the frame's own rate (60
 * a second over the blanks its flip waits for: under a cap of 60 none for a frame of every blank, the middle slot for one
 * of every other; 144 or none, all), and those a game short of that rate leaves (a load), measured over half a second
 * from the last change of rate. A cap of 60 on a game at 56 left 4 slots a second repeated (the user's log, 11-08). */
static void auto_decide(u32 vblanks)
{
    static u32 from, frames, logged, own, rate;
    u32 now = port_vblank_count(), want = PORT_VBLANK_HZ / (vblanks ? vblanks : 1u);
    if (want != own) from = now, frames = 0, rate = own = want; /* a new scene's rate: measured afresh */
    if (++frames, now - from >= PORT_VBLANK_HZ / 2u) rate = frames * PORT_VBLANK_HZ / (now - from), from = now, frames = 0;
    if (!g_auto) return;
    int on = !g_fps || g_fps > own || rate + rate / 16u < own;
    if (on != g_interp && logged++ < 32u)
        port_log("frame_extra: a frame every %u blank(s), %u a second, cap %u: in-between frames %s\n", PORT_VBLANK_HZ / own,
                 rate, g_fps, on ? "on" : "off");
    g_interp = on;
}

int port_extra_begin(u32 vblanks)
{
    auto_decide(vblanks);
    if (!g_interp || g_drawing || (!g_draw && !(g_scene_mode && pvr_blend_ready()))) return 0;
    if (host_fixed_clock() && host_fast_until()) return 0; /* --fast-until: ticks only until real time starts */
    if (g_draw && g_ready && !g_ready()) return 0;
    if (g_draw && !g_inited && !init()) return 0;
    if (!g_draw && !g_noted) { /* scene blending: no game code runs, so no copy of guest RAM and nothing to set up */
        port_log("frame_extra: in-between frames on, from the scene before (--interp-scene)%s\n",
                 host_fixed_clock() ? " (a fixed number per vertical blank)" : "");
        g_noted = 1;
    }
    /* the cost is only measured when one is drawn: one that was slow once (a load) would keep them all out, so a
     * rendered frame that got none for want of time lowers it a little, and the next one that fits measures again */
    if (g_starved && !g_count) g_cost_us -= g_cost_us / 16u;
    g_starved = 0;
    g_wait = vblanks ? vblanks : 1u;
    g_count = g_fixed_done = 0;
    g_begun++;
    if (!host_fixed_clock()) g_start_us = host_ticks(1000000u);
    if (g_draw) port_extra_thread_own(g_thread_mode == 3 && !host_fixed_clock() && !gfx_capture_asked());
    else {
        port_extra_thread_early(g_thread_mode == 3 && !host_fixed_clock() && !gfx_capture_asked());
        port_extra_thread_begin(g_thread_mode == 2 || (g_thread_mode && g_thread_mode != 2 && !host_fixed_clock()));
    }
    return 1;
}

/* --interp-verify: the pages the frame wrote, as ranges, now and then; then that the copy back is whole. */
static void verify(int before_restore)
{
    const u8 *ram = (const u8 *)(uintptr_t)GUEST_RAM_BASE;
    if (!before_restore) {
        if (memcmp(ram, g_ram, GUEST_RAM_SIZE)) port_exit("frame_extra: guest RAM differs after the copy back", 1);
        return;
    }
    if (g_total > 3u && g_total % 1000u) return;
    u32 pages = 0, n = 0, from = 0, to = 0, open = 0;
    char text[VERIFY_RANGES * 20 + 8] = "", *at = text;
    for (u32 off = 0; off <= GUEST_RAM_SIZE; off += PAGE) {
        int dirty = off < GUEST_RAM_SIZE && memcmp(ram + off, g_ram + off, PAGE);
        if (dirty) {
            pages++;
            if (!open) from = off;
            to = off + PAGE;
            open = 1;
        } else if (open) {
            open = 0;
            if (n++ < VERIFY_RANGES)
                at += sprintf_s(at, sizeof text - (size_t)(at - text), " %08x-%08x", GUEST_RAM_BASE + from,
                                GUEST_RAM_BASE + to - 1u);
        }
    }
    port_log("frame_extra: in-between frame %u wrote %u page(s) in %u range(s):%s%s\n", g_total, pages, n, text,
             n > VERIFY_RANGES ? " ..." : "");
}

/* The performance counter, read directly: host_ticks would move the virtual clock. */
static u64 qpc(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (u64)t.QuadPart;
}

/* Draws one in-between frame and puts everything back; returns what that cost, in microseconds (not --interp-verify's
 * checks, so they do not starve the real-time pacing). */
static u64 draw_scene_one(f32 alpha)
{
    LARGE_INTEGER f;
    u64 t0 = qpc(), pt = prof_begin(PROF_X_DRAW);
    play_stats_draw(1);
    g_drawing = !port_extra_thread_on(); /* the game's thread asks port_extra_drawing about its own render */
    g_count += pvr_blend_draw(alpha) != 0;
    g_drawing = 0;
    play_stats_draw(0);
    prof_end(pt);
    g_total++;
    QueryPerformanceFrequency(&f);
    return (qpc() - t0) * 1000000u / (u64)f.QuadPart;
}

static u64 draw_one(f32 alpha)
{
    static u64 spent[3];
    if (!g_draw) return draw_scene_one(alpha);
    u8 *ram = (u8 *)(uintptr_t)GUEST_RAM_BASE;
    u32 i;
    LARGE_INTEGER f;
    u64 t0 = qpc(), t1, t2, t3, t4, pt = prof_begin(PROF_X_COPY);
    if (g_ram_vblank != port_vblank_count()) {
        extra_copy_ram(g_ram, ram);
        g_ram_vblank = port_vblank_count();
    }
    for (i = 0; i < g_nstates; i++) memcpy(g_states[i].saved, g_states[i].p, g_states[i].size);
    t1 = qpc();
    prof_end(pt);
    pt = prof_begin(PROF_X_DRAW);
    play_stats_draw(1);
    g_drawing = 1;
    int shown = g_draw(alpha);
    g_drawing = 0;
    play_stats_draw(0);
    prof_end(pt);
    t2 = qpc();
    if (g_verify) verify(1);
    t3 = qpc();
    pt = prof_begin(PROF_X_COPY);
    extra_copy_ram(ram, g_ram);
    for (i = 0; i < g_nstates; i++) memcpy(g_states[i].p, g_states[i].saved, g_states[i].size);
    undo_all();
    prof_end(pt);
    t4 = qpc();
    spent[0] += t1 - t0;
    spent[1] += t2 - t1;
    spent[2] += t4 - t3;
    if (g_verify) verify(0);
    g_count += shown != 0;
    QueryPerformanceFrequency(&f);
    if (++g_total % 1000u == 0) {
        port_log("frame_extra: %u in-between frames; each, on average: copy %llu us, draw %llu us, copy back %llu us\n",
                 g_total, spent[0] * 1000u / (u64)f.QuadPart, spent[1] * 1000u / (u64)f.QuadPart,
                 spent[2] * 1000u / (u64)f.QuadPart);
        spent[0] = spent[1] = spent[2] = 0;
    }
    return (t4 - t0 - (t3 - t2)) * 1000000u / (u64)f.QuadPart;
}

/* --present-thread (frame_thread.c), holding gfx_lock: one in-between frame, if one is due. 1 drawn, 2 the display would not
 * take it, 3 not its --fps slot yet, 0 none. */
int port_extra_thread_show(u64 now_us, u64 start_us, int *finished)
{
    u64 tick_us = (u64)g_wait * 1000000u / PORT_VBLANK_HZ, period = port_extra_thread_period(), cost;
    f32 alpha;
    u32 drawn = g_count;
    /* A game can render every other blank while its flip waits for one (a movie that waits on its sound clock for
     * the other): blended over one blank, the in-between frames were done half way and nothing was shown for
     * the rest, a 12-18 ms gap every render. They span the time between the last two renders instead, unless that was a
     * pause (a load), when the frame waited for stands. */
    if (period > tick_us && period < 4u * tick_us) tick_us = period;
    if (g_draw) return port_extra_thread_reshow(now_us, g_fps, &g_reshown); /* the tick draws them; the last frame again */
    if (g_interp && !gfx_held() && !pvr_blend_ready() && port_extra_thread_is_early()) { /* the same picture again */
        if (now_us - g_shown_us < RESHOW_US) return 0;
        if (port_extra_cap_wait(now_us, g_fps, 0u)) return 3; /* in an --fps slot too: shown frames stay under the cap */
        if (!gfx_can_show()) return g_not_shown++, 2;
        gfx_show();
        port_extra_cap_shown(g_shown_us = now_us, g_fps);
        return g_reshown++, 1;
    }
    if (!g_interp || gfx_held() || !pvr_blend_ready()) return 0;
    if (port_extra_cap_wait(now_us, g_fps, g_cost_us)) return 3; /* --fps: not yet; the thread waits for its slot */
    if (!gfx_can_show()) return g_not_shown++, 2;
    alpha = now_us - start_us >= tick_us ? 1.0f : (f32)(now_us - start_us) / (f32)tick_us;
    cost = draw_scene_one(alpha);
    if (g_count != drawn) xcost_frame(cost);
    if (cost >= 100000u) port_log("frame_extra: an in-between frame took %llu ms\n", cost / 1000u);
    g_cost_us = g_cost_us ? (g_cost_us * 7u + cost) / 8u : cost;
    port_extra_cap_shown((g_shown_us = now_us) + cost, g_fps);
    *finished = alpha >= 1.0f;
    return 1;
}

/* gfx_present, holding gfx_lock: a frame with no in-between frames was shown. Under --present-thread on the thread may
 * show it again until the next one. */
void port_extra_shown(void)
{
    int early = g_thread_mode == 3 && g_interp && g_scene_mode && !g_draw && !host_fixed_clock() && !gfx_capture_asked();
    if (!early) return;
    port_extra_thread_early(1);
    port_extra_thread_begin(1);
    port_extra_thread_go();
}

int port_extra_flip(u32 vblanks)
{
    if (gfx_held() || !port_extra_begin(vblanks)) return 0; /* a held frame is not counted: gfx_present drops it */
    gfx_frame_done();
    return g_held = 1;
}
int port_extra_pending(void) { return g_held; }

void port_extra_land(void)
{
    if (!g_held) return;
    g_held = 0;
    if (!g_count) port_extra_thread_shows() ? (void)port_extra_thread_go() : gfx_show(); /* that thread shows it: the tick never Presents */
}

int port_extra_idle(u64 deadline_us)
{
    if (host_fixed_clock()) { /* a fixed number, evenly through the tick */
        u32 all = g_fixed_frames * g_wait;
        for (u32 k = 0; k < g_fixed_frames; k++, g_fixed_done++)
            draw_one((f32)(g_fixed_done + 1u) / (f32)(all + 1u));
        return 1;
    }
    u64 now = host_ticks(1000000u), tick_us = (u64)g_wait * 1000000u / PORT_VBLANK_HZ;
    if (now + g_cost_us + g_cost_us / 4u >= deadline_us) return g_no_time++, g_starved = 1, 0;
    if (port_extra_cap_wait(now, g_fps, g_cost_us)) return 0; /* --fps: not yet */
    if (!gfx_can_show()) return g_not_shown++, 0;               /* the display would not take it */
    f32 alpha = now - g_start_us >= tick_us ? 1.0f : (f32)(now - g_start_us) / (f32)tick_us;
    u64 cost = draw_one(alpha);
    /* the first one is slow (the copy of guest RAM is new memory) and would keep the next ones from being tried; a
     * sample counts at most twice the estimate, so one hitch moves it little and a lasting change still comes through */
    if (g_cost_us && cost > 2u * g_cost_us) cost = 2u * g_cost_us;
    if (g_total > 1u) g_cost_us = g_cost_us ? (g_cost_us * 7u + cost) / 8u : cost;
    port_extra_cap_shown((g_shown_us = now) + cost, g_fps);
    return 1;
}

void port_extra_report(void)
{
    if (g_inited || g_noted)
        port_log("frame_extra: %u in-between frames after %u rendered frames; %u time(s) too little time left, %u time(s) "
                 "the display not taking one; one costs about %llu us; %u shown again\n", g_total, g_begun, g_no_time, g_not_shown,
                 g_cost_us, g_reshown);
}

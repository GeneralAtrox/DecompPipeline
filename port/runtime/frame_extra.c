/* In-between frames (--present interp): the simulation keeps the original tick, and every frame the game renders is
 * still counted and dumped, but it is not shown at once. While it waits for its vertical blank (host.c), the game's
 * draw function (port_set_extra_draw) draws more frames, which are shown and then undone: guest RAM and the host
 * state the kit registers (port_extra_state: the register file, rand, the virtual clock, Kamui's banks) go back to
 * what the tick left, so the next tick runs exactly as it would have. A frame with no in-between frame (no time, or
 * none ready) is shown after the wait, as --present tick does.
 *
 *   --present tick|interp   one shown frame per rendered one (the default), or in-between frames too
 *   --interp-frames N       --fixed-clock runs: in-between frames per vertical blank waited for (default 1)
 *   --interp-verify 1       logs what in-between frames wrote in guest RAM, and checks that it all went back
 *   --fps N                 real time: at most N in-between frames a second (default 0: as many as the display takes
 *                           and the time allows; with VSync on, the display's refresh is the limit)
 *   --interp-copy-threads N threads that copy guest RAM (default 4; 1: this thread alone)
 *
 * Guest RAM is copied when the first in-between frame after a vertical blank is drawn (the vertical blank's hook
 * may write it) and copied back after each one. */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"
#include "host.h"

#define EXTRA_STATES 16
#define PAGE 4096u
#define VERIFY_RANGES 6

static int g_interp, g_verify, g_inited;
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
static int g_drawing;
static u32 g_wait;                /* vertical blanks the rendered frame waits for */
static u32 g_count, g_fixed_done; /* in-between frames since port_extra_begin; --fixed-clock: drawn so far */
static u64 g_start_us, g_cost_us; /* when the frame was rendered; what an in-between frame costs, recently */
static u32 g_fps;                 /* --fps N: in-between frames at most N per second (0: as many as fit) */
static u64 g_shown_us;            /* when the last in-between frame started */
static u32 g_total;
static u32 g_begun, g_no_time, g_not_shown; /* rendered frames that allowed in-between ones; waits skipped, why */

/* Guest RAM copies are memory-bound, and one core does not saturate the memory: a copy is split into slices, one
 * for this thread and one for each helper thread (--interp-copy-threads N: N - 1 helpers). Four threads copy 16 MB
 * in about 0.4 ms where one takes 1.5 to 2. */
#define COPY_HELPERS_MAX 7
static u32 g_copy_threads = 4;
static struct {
    HANDLE go;
    u8 *dst;
    const u8 *src;
    u32 size;
} g_slice[COPY_HELPERS_MAX];
static HANDLE g_copied;
static volatile LONG g_copy_left;

static DWORD WINAPI copy_helper(void *arg)
{
    u32 i = (u32)(uintptr_t)arg;
    for (;;) {
        WaitForSingleObject(g_slice[i].go, INFINITE); /* idle until the next copy */
        memcpy(g_slice[i].dst, g_slice[i].src, g_slice[i].size);
        if (InterlockedDecrement(&g_copy_left) == 0) SetEvent(g_copied);
    }
}

static void copy_init(void)
{
    if (g_copy_threads < 1u) g_copy_threads = 1u;
    if (g_copy_threads > COPY_HELPERS_MAX + 1u) g_copy_threads = COPY_HELPERS_MAX + 1u;
    g_copied = CreateEventW(NULL, FALSE, FALSE, NULL);
    for (u32 i = 0; i + 1u < g_copy_threads; i++) {
        g_slice[i].go = CreateEventW(NULL, FALSE, FALSE, NULL);
        HANDLE t = g_copied && g_slice[i].go ? CreateThread(NULL, 0, copy_helper, (void *)(uintptr_t)i, 0, NULL) : NULL;
        if (!t) {
            g_copy_threads = i + 1u;
            break;
        }
        SetThreadPriority(t, THREAD_PRIORITY_ABOVE_NORMAL);
        CloseHandle(t);
    }
}

static void copy_ram(u8 *dst, const u8 *src)
{
    u32 helpers = g_copy_threads - 1u, slice = GUEST_RAM_SIZE / g_copy_threads & ~(PAGE - 1u);
    g_copy_left = (LONG)helpers;
    for (u32 i = 0; i < helpers; i++) {
        u32 at = slice * (i + 1u);
        g_slice[i].dst = dst + at;
        g_slice[i].src = src + at;
        g_slice[i].size = i + 1u == helpers ? GUEST_RAM_SIZE - at : slice;
        SetEvent(g_slice[i].go);
    }
    memcpy(dst, src, helpers ? slice : GUEST_RAM_SIZE);
    if (helpers && WaitForSingleObject(g_copied, 5000) != WAIT_OBJECT_0)
        port_exit("frame_extra: a guest RAM copy helper did not finish", 1);
}

int extra_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--present")) g_interp = !strcmp(value, "interp");
    else if (!strcmp(arg, "--interp-frames")) g_fixed_frames = (u32)atoi(value);
    else if (!strcmp(arg, "--interp-verify")) g_verify = atoi(value) != 0;
    else if (!strcmp(arg, "--fps")) g_fps = (u32)atoi(value);
    else if (!strcmp(arg, "--interp-copy-threads")) g_copy_threads = (u32)atoi(value);
    else return 0;
    return 2;
}

/* The host's menu (host/host_menu.cpp): the same as the options, at any time */
void extra_set_interp(int on) { g_interp = on != 0; }
int extra_interp(void) { return g_interp; }
void extra_set_fps(unsigned fps) { g_fps = fps; }
unsigned extra_fps(void) { return g_fps; }

void port_set_extra_draw(PortExtraDrawFn draw, PortExtraReadyFn ready)
{
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
u32 port_extra_count(void) { return g_count; }

static int init(void)
{
    g_ram = VirtualAlloc(NULL, GUEST_RAM_SIZE, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!g_ram) {
        port_log("frame_extra: no copy of guest RAM; in-between frames off\n");
        g_interp = 0;
        return 0;
    }
    copy_init();
    sh4_rt_extra_state();
    port_libm_extra_state();
    host_extra_state();
    port_log("frame_extra: in-between frames on%s%s; guest RAM copied by %u thread(s)\n",
             host_fixed_clock() ? " (a fixed number per vertical blank)" : "", g_verify ? ", checked" : "", g_copy_threads);
    return g_inited = 1;
}

int port_extra_begin(u32 vblanks)
{
    if (!g_interp || !g_draw || g_drawing) return 0;
    if (host_fixed_clock() && host_fast_until()) return 0; /* --fast-until: ticks only until real time starts */
    if (g_ready && !g_ready()) return 0;
    if (!g_inited && !init()) return 0;
    g_wait = vblanks ? vblanks : 1u;
    g_count = g_fixed_done = 0;
    g_begun++;
    if (!host_fixed_clock()) g_start_us = host_ticks(1000000u);
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
static u64 draw_one(f32 alpha)
{
    static u64 spent[3];
    u8 *ram = (u8 *)(uintptr_t)GUEST_RAM_BASE;
    u32 i;
    LARGE_INTEGER f;
    u64 t0 = qpc(), t1, t2, t3, t4;
    if (g_ram_vblank != port_vblank_count()) {
        copy_ram(g_ram, ram);
        g_ram_vblank = port_vblank_count();
    }
    for (i = 0; i < g_nstates; i++) memcpy(g_states[i].saved, g_states[i].p, g_states[i].size);
    t1 = qpc();
    g_drawing = 1;
    int shown = g_draw(alpha);
    g_drawing = 0;
    t2 = qpc();
    if (g_verify) verify(1);
    t3 = qpc();
    copy_ram(ram, g_ram);
    for (i = 0; i < g_nstates; i++) memcpy(g_states[i].p, g_states[i].saved, g_states[i].size);
    undo_all();
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

int port_extra_idle(u64 deadline_us)
{
    if (host_fixed_clock()) { /* a fixed number, evenly through the tick */
        u32 all = g_fixed_frames * g_wait;
        for (u32 k = 0; k < g_fixed_frames; k++, g_fixed_done++)
            draw_one((f32)(g_fixed_done + 1u) / (f32)(all + 1u));
        return 1;
    }
    u64 now = host_ticks(1000000u), tick_us = (u64)g_wait * 1000000u / PORT_VBLANK_HZ;
    if (now + g_cost_us + g_cost_us / 4u >= deadline_us) return g_no_time++, 0;
    if (g_fps && now - g_shown_us < 1000000u / g_fps) return 0; /* --fps: not yet */
    if (!gfx_can_show()) return g_not_shown++, 0;               /* the display would not take it */
    f32 alpha = now - g_start_us >= tick_us ? 1.0f : (f32)(now - g_start_us) / (f32)tick_us;
    u64 cost = draw_one(alpha);
    /* the first one is slow (the copy of guest RAM is new memory) and would keep the next ones from being tried */
    if (g_total > 1u) g_cost_us = g_cost_us ? (g_cost_us * 7u + cost) / 8u : cost;
    g_shown_us = now;
    return 1;
}

void port_extra_report(void)
{
    if (g_inited)
        port_log("frame_extra: %u in-between frames after %u rendered frames; %u time(s) too little time left, %u time(s) "
                 "the display not taking one; one costs about %llu us\n", g_total, g_begun, g_no_time, g_not_shown,
                 g_cost_us);
}

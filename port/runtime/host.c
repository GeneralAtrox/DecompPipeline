/* The Dreamcast kit's host services (platform.h): the vertical blank wait, fixed-address memory and the flash
 * image. The clock, the command line and the save files are the base's (base/clock.c). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"
#include "gfx/gfx.h"
#include "session.h"
#include "lockstep.h"
#include "prof.h"
#include "play_stats.h"
#include "work_count.h"

#define VBLANK_TASKS 8
static u32 g_vblanks;
static PortVblankFn g_vblank_fn;
static void *g_vblank_arg;
static struct {
    PortVblankFn fn;
    void *arg;
} g_vblank_tasks[VBLANK_TASKS];

void port_add_vblank_task(PortVblankFn fn, void *arg)
{
    for (u32 i = 0; i < VBLANK_TASKS; i++)
        if (!g_vblank_tasks[i].fn || g_vblank_tasks[i].fn == fn) {
            g_vblank_tasks[i].fn = fn;
            g_vblank_tasks[i].arg = arg;
            return;
        }
    port_exit("host: more than 8 vertical blank tasks", 1);
}

void port_set_vblank_hook(PortVblankFn fn, void *arg)
{
    g_vblank_fn = fn;
    g_vblank_arg = arg;
}

/* Waits for the next 1/60 s boundary of the host clock (a 60 Hz display's vertical blank) and counts it; with
 * --fixed-clock it moves the virtual clock there instead. Window events are handled here too, so the window stays
 * responsive in loops that wait without rendering. extra: in-between frames are drawn while it waits. */
static void wait_vblank(int extra)
{
    u64 pt;
    extra |= port_extra_pending(); /* a frame held at a flip: its in-between frames are drawn in this wait */
    if (extra && port_extra_thread_go()) extra = 0; /* or by their own thread, from now on (--present-thread) */
    gfx_pump();
    play_stats_wait(1);
    pt = prof_begin(PROF_WAIT);
    if (host_fixed_clock()) {
        if (extra) port_extra_idle(0);
        host_virt_to_tick();
    } else {
        pace_next_tick(PORT_VBLANK_HZ, extra ? port_extra_idle : NULL);
    }
    prof_end(pt);
    play_stats_wait(0);
    port_extra_land();
    g_vblanks++;
    work_count_tick(g_vblanks);
    if (host_fixed_clock() && host_fast_until() && g_vblanks >= host_fast_until()) {
        host_clear_fixed();
        port_log("host: real time from vertical blank %u (--fast-until)\n", g_vblanks);
        play_stats_restart();
    }
    lockstep_vblank(); /* the state as the emulator samples it, before the game's handler */
    if (g_vblank_fn) g_vblank_fn(g_vblank_arg);
    for (u32 i = 0; i < VBLANK_TASKS && g_vblank_tasks[i].fn; i++) g_vblank_tasks[i].fn(g_vblank_tasks[i].arg);
    prof_frame();
}

void port_wait_vblank(void) { wait_vblank(0); }
void port_wait_vblank_extra(void) { wait_vblank(1); }

u32 port_vblank_count(void) { return g_vblanks; }

u32 port_vblank_phase(int look) { return host_tick_phase(look); }

/* The virtual clock moves when an in-between frame looks at it: put back afterwards (frame_extra.c). */
void host_extra_state(void) { port_extra_state(host_virt_ptr(), sizeof(u64)); }

void *host_map_fixed(u32 addr, u32 size)
{
    void *at = (void *)(uintptr_t)addr;
    void *got = VirtualAlloc(at, size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (got != at) port_log("host: cannot map %u KB at %08x (%lu)\n", size >> 10, addr, GetLastError());
    return got == at ? got : NULL;
}

/* A game spinning on a clock inside a vertical blank (a sound voice's position) sleeps a little after each burst of
 * polls, booked as a wait: at most CLOCK_NAP_US (a nap of 0.5 ms or less only yields: pace_nap_until), and never in
 * the blank's last millisecond, so what it waits for is still seen in the same blank. A burst is CLOCK_NAP_POLLS polls
 * (--clock-nap N): the loop's other work runs once a turn, and a game's sound update that steps once a call must
 * still settle as it does when the loop spins. Real clock only; returns whether it slept. --clock-nap off keeps the
 * spin, for the side-by-side. */
#define CLOCK_NAP_US 2000u
#define CLOCK_NAP_POLLS 1024u

int port_clock_nap(u32 polls)
{
    static long burst = -1;
    const char *v;
    u32 phase;
    u64 left, pt;
    if (burst < 0) burst = !(v = host_arg("--clock-nap")) ? (long)CLOCK_NAP_POLLS : !strcmp(v, "off") ? 0 : atol(v);
    if (!burst || polls < (u32)burst || host_fixed_clock()) return 0;
    phase = host_tick_phase(1);
    left = (u64)(65536u - phase) * 1000000u / (65536u * PORT_VBLANK_HZ);
    if (phase >= 65536u || left < 1100u) return 0;
    left -= 1000u;
    pt = prof_begin(PROF_WAIT);
    play_stats_idle(1); /* the play summary leaves the sleep out of the tick's work too */
    pace_nap_until(host_ticks(1000000u) + (left < CLOCK_NAP_US ? left : CLOCK_NAP_US), 0);
    play_stats_idle(0);
    prof_end(pt);
    return 1;
}

/* --flash FILE, read like a save file (recorded, and a replay gets the recorded image) under a name no save file
 * can have. */
int host_flash_read(void *buf, u32 size)
{
    const char *path = host_arg("--flash");
    HANDLE f;
    DWORD got = 0;
    LARGE_INTEGER len;
    int ok = session_save_replayed("*flash", buf, size);
    if (ok == -2) {
        ok = 0;
        if (path) {
            f = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
            ok = -1;
            if (f != INVALID_HANDLE_VALUE) {
                if (GetFileSizeEx(f, &len) && len.QuadPart == size && ReadFile(f, buf, size, &got, NULL) &&
                    got == size)
                    ok = 1;
                CloseHandle(f);
            }
        }
    }
    session_save_read("*flash", buf, size, ok);
    return ok;
}

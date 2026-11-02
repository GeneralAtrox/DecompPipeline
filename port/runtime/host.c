/* The Dreamcast kit's host services (platform.h): the vertical blank wait, fixed-address memory and the flash
 * image. The clock, the command line and the save files are the base's (base/clock.c). */
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"
#include "gfx/gfx.h"
#include "session.h"
#include "lockstep.h"
#include "prof.h"
#include "play_stats.h"

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

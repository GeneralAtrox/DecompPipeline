/* Waiting for the real clock (base.h pace_*). A kit with one tick per blank waits in pace_next_tick (--catch-up); the
 * Saturn kit keeps its own grid of deadlines (saturn/sat_sys.c: a late blank keeps the grid) and waits in pace_wait_us.
 *
 * Sleep(1) wakes on the system timer's tick (15.6 ms unless something raised its resolution), so a vertical blank would
 * come 0 to 16 ms late. A game that times its step from a timer then moves unevenly from tick to tick (a judder that
 * in-between frames show). A high-resolution waitable timer wakes within about half a millisecond, and the rest spins.
 * With in-between frames it naps 1 ms at most, so they are still tried through the wait. */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "base.h"
#include "pace.h"

#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002
#endif
#define NAP_MAX_US 1000u
#define SPIN_US 500u
/* About us on the real clock on a high-resolution timer, the calling thread's own (the tick's thread and the
 * present thread, runtime/frame_thread.c, each have one); before Windows 10 1803, Sleep(1). */
void pace_sleep_us(u64 us)
{
    static __declspec(thread) HANDLE timer;
    LARGE_INTEGER due;
    if (!timer) timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    due.QuadPart = -(LONGLONG)(us * 10u); /* relative, in 100 ns */
    if (!timer || !SetWaitableTimer(timer, &due, 0, NULL, NULL, FALSE)) {
        Sleep(1);
        return;
    }
    WaitForSingleObject(timer, (DWORD)(us / 1000u) + 2u);
}

void pace_nap_until(u64 deadline_us, int extra)
{
    u64 now = host_ticks(1000000u), until = deadline_us;
    if (extra && until > now + NAP_MAX_US) until = now + NAP_MAX_US;
    if (now + SPIN_US >= until) {
        SwitchToThread();
        return;
    }
    pace_sleep_us(until - now - SPIN_US);
}

/* A tick that overruns must not cost a whole slot: waiting for the boundary after "now" turned a 17 ms tick into 33 ms,
 * so the guest clock lost time for good and ran at 36-49 blanks a second in a fight whose ticks averaged under 16.7 ms
 * (and starved the audio mixed per blank). --catch-up off: the old wait, for comparison. */
static int g_catch_up = 1;
static u64 g_due;

int pace_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--catch-up") || !value) return 0;
    g_catch_up = strcmp(value, "off") != 0;
    return 2;
}

/* PACE_STALL=MS:EVERY (developer env only, off when unset; port/README.md): every EVERY-th tick of the real clock overruns by
 * MS ms plus (k * 7) % 17 ms at its k-th time, so the late tick ends at a different point of a blank each time: a hitch to
 * measure the pacing after it. Each one logs how late it was and the wait from its end until the next tick goes. */
static u32 g_stall_ms, g_stall_every, g_stall_ticks, g_stall_k;
static void stall_read(void)
{
    const char *e = getenv("PACE_STALL");
    char *end = NULL;
    if (e) g_stall_ms = (u32)strtoul(e, &end, 10);
    if (end && *end == ':') g_stall_every = (u32)strtoul(end + 1, NULL, 10);
    if (g_stall_every)
        port_log("pace: PACE_STALL %u ms every %u ticks (developer test switch)\n", g_stall_ms, g_stall_every);
    else g_stall_every = 0;
}

void pace_next_tick(u32 hz, PaceIdle idle)
{
    static int drop_old = -1;   /* PACE_DROP_NOW=0: a dropped blank waits for the boundary after now, as before 2026-11-14 (pace.h) */
    u64 next, due_us, stalled_at = 0, late = 0;
    u32 ms = 0;
    if (drop_old < 0) { const char *e = getenv("PACE_DROP_NOW"); drop_old = e && e[0] == '0'; stall_read(); }
    if (g_stall_every && ++g_stall_ticks % g_stall_every == 0) {
        ms = g_stall_ms + (++g_stall_k * 7u) % 17u;
        pace_wait_us((u64)ms * 1000u);
        stalled_at = host_ticks(1000000u), late = host_ticks(hz) - g_due;
    }
    next = pace_next(&g_due, host_ticks(hz), g_catch_up && drop_old ? 2 : g_catch_up), due_us = next * 1000000u / hz;
    while (host_ticks(hz) < next)
        if (!idle || !idle(due_us)) pace_nap_until(due_us, idle != NULL);
    if (stalled_at)
        port_log("pace: stall %u: %u ms, %llu blanks late, rule %s: the next tick went %.3f ms after it\n", g_stall_k, ms, late,
                 drop_old ? "old" : "now", (double)(host_ticks(1000000u) - stalled_at) / 1000.0);
    host_tick_real();
}

void pace_wait_us(u64 us)
{
    u64 due = host_ticks(1000000u) + us;
    while (host_ticks(1000000u) < due) pace_nap_until(due, 0);
}

/* Waiting for the real clock (base.h pace_*). A kit with one tick per blank waits in pace_next_tick (--catch-up); the
 * Saturn kit keeps its own grid of deadlines (saturn/sat_sys.c: a late blank keeps the grid) and waits in pace_wait_us.
 *
 * Sleep(1) wakes on the system timer's tick (15.6 ms unless something raised its resolution), so a vertical blank would
 * come 0 to 16 ms late. A game that times its step from a timer then moves unevenly from tick to tick (a judder that
 * in-between frames show). A high-resolution waitable timer wakes within about half a millisecond, and the rest spins.
 * With in-between frames it naps 1 ms at most, so they are still tried through the wait. */
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

void pace_next_tick(u32 hz, PaceIdle idle)
{
    u64 next = pace_next(&g_due, host_ticks(hz), g_catch_up), due_us = next * 1000000u / hz;
    while (host_ticks(hz) < next)
        if (!idle || !idle(due_us)) pace_nap_until(due_us, idle != NULL);
    host_tick_real();
}

void pace_wait_us(u64 us)
{
    u64 due = host_ticks(1000000u) + us;
    while (host_ticks(1000000u) < due) pace_nap_until(due, 0);
}

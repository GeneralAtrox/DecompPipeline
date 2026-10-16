/* Host services for the platform layer (platform.h): time, clock, the vertical blank and fixed-address memory,
 * through the Windows API. */
#include <windows.h>
#include "port.h"
#include "platform.h"

u64 host_ticks(u32 hz)
{
    static LARGE_INTEGER base, freq;
    LARGE_INTEGER now;
    if (!freq.QuadPart) {
        QueryPerformanceFrequency(&freq);
        QueryPerformanceCounter(&base);
    }
    QueryPerformanceCounter(&now);
    return (u64)(now.QuadPart - base.QuadPart) * hz / (u64)freq.QuadPart;
}

void host_local_time(HostTime *t)
{
    SYSTEMTIME s;
    GetLocalTime(&s);
    t->year = s.wYear;
    t->month = s.wMonth;
    t->day = s.wDay;
    t->hour = s.wHour;
    t->minute = s.wMinute;
    t->second = s.wSecond;
    t->day_of_week = s.wDayOfWeek;
}

void host_sleep_ms(u32 ms) { Sleep(ms); }

static u32 g_vblanks;

/* Waits for the next 1/60 s boundary of the host clock (a 60 Hz display's vertical blank) and counts it. */
void port_wait_vblank(void)
{
    u64 next = host_ticks(PORT_VBLANK_HZ) + 1u;
    while (host_ticks(PORT_VBLANK_HZ) < next) Sleep(1);
    g_vblanks++;
}

u32 port_vblank_count(void) { return g_vblanks; }

void *host_map_fixed(u32 addr, u32 size)
{
    void *at = (void *)(uintptr_t)addr;
    void *got = VirtualAlloc(at, size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (got != at) port_log("host: cannot map %u KB at %08x (%lu)\n", size >> 10, addr, GetLastError());
    return got == at ? got : NULL;
}

/* Host services for the platform layer (platform.h): time, clock, the vertical blank, fixed-address memory, the
 * command line and save files, through the Windows API. */
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"
#include "gfx/gfx.h"
#include "session.h"
#include "lockstep.h"

/* --fixed-clock: a virtual clock in units of 1/46,875,000 s (both 60 Hz and the 781,250 Hz TMU divide it). It only
 * moves when the game looks at it (a microsecond per look, so polling loops end) and at each vertical blank (to the
 * next 1/60 s boundary, without sleeping), so a run is the same every time and as fast as the host allows.
 * --fast-until N: the fixed clock until vertical blank N, then real time from the virtual time reached (the RTC keeps
 * the fixed date), to watch a scripted run from a late point without waiting through what comes before. */
#define VIRT_HZ 46875000u
#define VIRT_PER_VBLANK (VIRT_HZ / PORT_VBLANK_HZ)
#define VIRT_PER_LOOK (VIRT_HZ / 1000000u)
static int g_fixed_clock;
static u64 g_virt;
static u32 g_fast_until;
static LARGE_INTEGER g_base, g_freq;

void host_set_fixed_clock(int on) { g_fixed_clock = on; }
int host_fixed_clock(void) { return g_fixed_clock; }
void host_set_fast_until(u32 vblank) { g_fast_until = vblank; }
u32 host_fast_until(void) { return g_fast_until; }

u64 host_ticks(u32 hz)
{
    LARGE_INTEGER now;
    if (g_fixed_clock) {
        g_virt += VIRT_PER_LOOK;
        return g_virt * hz / VIRT_HZ;
    }
    if (!g_freq.QuadPart) {
        QueryPerformanceFrequency(&g_freq);
        QueryPerformanceCounter(&g_base);
    }
    QueryPerformanceCounter(&now);
    return g_virt * hz / VIRT_HZ + (u64)(now.QuadPart - g_base.QuadPart) * hz / (u64)g_freq.QuadPart;
}

static void clock_time(HostTime *t)
{
    SYSTEMTIME s;
    if (g_fixed_clock || g_fast_until) { /* a fixed date, Saturday 2000-01-01 12:00:00, plus the virtual time */
        u32 secs = g_fixed_clock ? (u32)(g_virt / VIRT_HZ) : (u32)host_ticks(1u);
        t->year = 2000;
        t->month = 1;
        t->day = 1;
        t->hour = (u16)(12u + secs / 3600u % 12u);
        t->minute = (u16)(secs / 60u % 60u);
        t->second = (u16)(secs % 60u);
        t->day_of_week = 6;
        return;
    }
    GetLocalTime(&s);
    t->year = s.wYear;
    t->month = s.wMonth;
    t->day = s.wDay;
    t->hour = s.wHour;
    t->minute = s.wMinute;
    t->second = s.wSecond;
    t->day_of_week = s.wDayOfWeek;
}

void host_local_time(HostTime *t)
{
    clock_time(t);
    session_time(t); /* recorded, or the replay's */
}

void host_sleep_ms(u32 ms) { Sleep(ms); }

#define VBLANK_TASKS 4
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
    port_exit("host: more than 4 vertical blank tasks", 1);
}

void port_set_vblank_hook(PortVblankFn fn, void *arg)
{
    g_vblank_fn = fn;
    g_vblank_arg = arg;
}

/* Sleep(1) wakes on the system timer's tick (15.6 ms unless something raised its resolution), so the vertical blank
 * came 0 to 16 ms late. The game times its step from the TMU, so its moves were uneven from tick to tick (a judder that
 * in-between frames show). A high-resolution waitable timer wakes within about half a millisecond, and the rest spins.
 * With in-between frames it naps 1 ms at most, so they are still tried through the wait. */
#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002
#endif
#define NAP_MAX_US 1000u
#define SPIN_US 500u
static HANDLE g_nap_timer;

static void nap_until(u64 deadline_us, int extra)
{
    u64 now = host_ticks(1000000u), until = deadline_us;
    LARGE_INTEGER due;
    if (extra && until > now + NAP_MAX_US) until = now + NAP_MAX_US;
    if (now + SPIN_US >= until) {
        SwitchToThread();
        return;
    }
    if (!g_nap_timer)
        g_nap_timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    due.QuadPart = -(LONGLONG)((until - now - SPIN_US) * 10u); /* relative, in 100 ns */
    if (!g_nap_timer || !SetWaitableTimer(g_nap_timer, &due, 0, NULL, NULL, FALSE)) {
        Sleep(1); /* before Windows 10 1803: no high-resolution timer */
        return;
    }
    WaitForSingleObject(g_nap_timer, (DWORD)((until - now) / 1000u) + 2u);
}

/* Waits for the next 1/60 s boundary of the host clock (a 60 Hz display's vertical blank) and counts it; with
 * --fixed-clock it moves the virtual clock there instead. Window events are handled here too, so the window stays
 * responsive in loops that wait without rendering. extra: in-between frames are drawn while it waits. */
static void wait_vblank(int extra)
{
    gfx_pump();
    if (g_fixed_clock) {
        if (extra) port_extra_idle(0);
        g_virt = (g_virt / VIRT_PER_VBLANK + 1u) * VIRT_PER_VBLANK;
    } else {
        u64 next = host_ticks(PORT_VBLANK_HZ) + 1u, due_us = next * 1000000u / PORT_VBLANK_HZ;
        while (host_ticks(PORT_VBLANK_HZ) < next)
            if (!extra || !port_extra_idle(due_us)) nap_until(due_us, extra);
    }
    g_vblanks++;
    if (g_fixed_clock && g_fast_until && g_vblanks >= g_fast_until) {
        g_fixed_clock = 0;
        port_log("host: real time from vertical blank %u (--fast-until)\n", g_vblanks);
    }
    lockstep_vblank(); /* the state as the emulator samples it, before the game's handler */
    if (g_vblank_fn) g_vblank_fn(g_vblank_arg);
    for (u32 i = 0; i < VBLANK_TASKS && g_vblank_tasks[i].fn; i++) g_vblank_tasks[i].fn(g_vblank_tasks[i].arg);
}

void port_wait_vblank(void) { wait_vblank(0); }
void port_wait_vblank_extra(void) { wait_vblank(1); }

u32 port_vblank_count(void) { return g_vblanks; }

/* The virtual clock moves when an in-between frame looks at it: put back afterwards (frame_extra.c). */
void host_extra_state(void) { port_extra_state(&g_virt, sizeof g_virt); }

void *host_map_fixed(u32 addr, u32 size)
{
    void *at = (void *)(uintptr_t)addr;
    void *got = VirtualAlloc(at, size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (got != at) port_log("host: cannot map %u KB at %08x (%lu)\n", size >> 10, addr, GetLastError());
    return got == at ? got : NULL;
}

static int g_argc;
static char **g_argv;

void host_set_args(int argc, char **argv)
{
    g_argc = argc;
    g_argv = argv;
}

char *const *host_args(int *argc)
{
    *argc = g_argc;
    return g_argv;
}

/* The last one wins, as in main.c's parse: the host puts the saved settings before the command line. */
const char *host_arg(const char *name)
{
    const char *found = NULL;
    for (int i = 1; i + 1 < g_argc; i++)
        if (!strcmp(g_argv[i], name)) found = g_argv[i + 1];
    return found;
}

static char g_save_dir[MAX_PATH];

void host_set_save_dir(const char *dir) { strcpy_s(g_save_dir, sizeof g_save_dir, dir); }

/* The save file's path; 0 when saves are not kept (a --fixed-clock run without --save-dir). */
static int save_path(const char *name, char *path, u32 size)
{
    if (!g_save_dir[0]) {
        char exe[MAX_PATH], *slash;
        if (g_fixed_clock || !GetModuleFileNameA(NULL, exe, sizeof exe)) return 0;
        slash = strrchr(exe, '\\');
        if (slash) *slash = '\0';
        sprintf_s(g_save_dir, sizeof g_save_dir, "%s\\data\\saves", slash ? exe : "."); /* host/host_data.cpp */
    }
    return sprintf_s(path, size, "%s\\%s", g_save_dir, name) > 0;
}

static int save_read(const char *name, void *buf, u32 size)
{
    char path[MAX_PATH];
    HANDLE f;
    DWORD got = 0;
    LARGE_INTEGER len;
    int ok;
    if (!save_path(name, path, sizeof path)) return -1;
    f = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;
    ok = GetFileSizeEx(f, &len) && len.QuadPart == size && ReadFile(f, buf, size, &got, NULL) && got == size;
    CloseHandle(f);
    return ok;
}

/* A replay gets the save files the recorded run read; every read is recorded (session.c). */
int host_save_read(const char *name, void *buf, u32 size)
{
    int got = session_save_replayed(name, buf, size);
    if (got == -2) got = save_read(name, buf, size);
    session_save_read(name, buf, size, got);
    return got;
}

/* Written to NAME.new, then moved over NAME, so a crash mid-write leaves the old file whole. */
int host_save_write(const char *name, const void *buf, u32 size)
{
    char path[MAX_PATH], tmp[MAX_PATH + 4];
    HANDLE f;
    DWORD put = 0;
    int ok;
    if (!save_path(name, path, sizeof path)) return -1;
    CreateDirectoryA(g_save_dir, NULL);
    sprintf_s(tmp, sizeof tmp, "%s.new", path);
    f = CreateFileA(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;
    ok = WriteFile(f, buf, size, &put, NULL) && put == size && FlushFileBuffers(f);
    CloseHandle(f);
    return ok && MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}

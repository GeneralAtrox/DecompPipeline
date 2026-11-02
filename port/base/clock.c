/* The base's clock, command line and save files (Windows API): the same for every console. --fixed-clock is the
 * deterministic clock every headless replay runs on; a console's pacing loop (its wait for the next tick) moves it
 * through host_virt* and reads the phase through host_tick_phase. Moved whole from runtime/host.c. */
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "base.h"
#include "console.h"
#include "session.h"

/* --fixed-clock: a virtual clock in units of 1/46,875,000 s (both 60 Hz and the 781,250 Hz TMU divide it). It only
 * moves when the game looks at it (a microsecond per look, so polling loops end) and at each vertical blank (to the
 * next 1/60 s boundary, without sleeping), so a run is the same every time and as fast as the host allows.
 * --fast-until N: the fixed clock until vertical blank N, then real time from the virtual time reached (the RTC keeps
 * the fixed date), to watch a scripted run from a late point without waiting through what comes before. */
#define VIRT_PER_TICK (VIRT_HZ / port_console.tick_hz) /* 60 Hz and 50 Hz both divide VIRT_HZ */
#define VIRT_PER_LOOK (VIRT_HZ / 1000000u)
static int g_fixed_clock;
static u64 g_virt, g_vblank_virt; /* the virtual time, and the last vertical blank's in its units */
static u32 g_fast_until;
static LARGE_INTEGER g_base, g_freq;

void host_set_fixed_clock(int on) { g_fixed_clock = on; }
int host_fixed_clock(void) { return g_fixed_clock; }
void host_set_fast_until(u32 vblank) { g_fast_until = vblank; }
u32 host_fast_until(void) { return g_fast_until; }

/* A stretch the player does not see (a game's hidden screens): the virtual clock from the time reached, so its frames
 * run without waiting, then real time again from where the virtual time got to. Nothing changes under
 * --fixed-clock. */
static int g_fast_stretch;
void host_fast_begin(void)
{
    if (g_fixed_clock) return;
    g_virt = host_ticks(VIRT_HZ);
    g_fixed_clock = g_fast_stretch = 1;
}

void host_fast_end(void)
{
    if (!g_fast_stretch) return;
    g_fixed_clock = g_fast_stretch = 0;
    QueryPerformanceCounter(&g_base);
}

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
    /* A fixed date, Saturday 2000-01-01 12:00:00, plus the virtual time. A hidden fast stretch keeps the real date:
     * a file the game saves in it is stamped as on the console. */
    if ((g_fixed_clock && !g_fast_stretch) || g_fast_until) {
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

/* The pacing loop (the console's wait for its next tick) moves the virtual clock through these. */
u64 *host_virt_ptr(void) { return &g_virt; } /* port_extra_state saves it around an in-between frame */
void host_virt_to_tick(void) { g_virt = g_vblank_virt = (g_virt / VIRT_PER_TICK + 1u) * VIRT_PER_TICK; }
void host_tick_real(void) { g_vblank_virt = host_ticks(VIRT_HZ); }
void host_clear_fixed(void) { g_fixed_clock = 0; }

u32 host_tick_phase(int look)
{
    u64 now = look || !g_fixed_clock ? host_ticks(VIRT_HZ) : g_virt;
    u64 d = now > g_vblank_virt ? now - g_vblank_virt : 0u;
    return d >= VIRT_PER_TICK ? 65536u : (u32)(d * 65536u / VIRT_PER_TICK);
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


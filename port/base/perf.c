/* The Core's sampler and per-frame record: see perf.h. A thread of its own wakes every --perf-sample microseconds,
 * holds each registered thread for the moment it takes to read its instruction pointer, and writes one record:
 *   t_us, tick, vblank, meta (thread | busy << 7 | section << 8 | depth << 16), where, then depth words of the game's call stack
 * busy: the thread used the processor for most of the time since its last sample. Not busy and not inside a system DLL
 * means Windows had not run it (it was ready but descheduled): the report names that an OS stall.
 * `where` is an offset in the exe; bit 31 set means the thread was inside a system DLL (a wait, a file read) and the
 * offset is the nearest caller in the exe found on its stack. The file starts with "PRF1", the exe's size, the period,
 * the number of sections and their names (32 bytes each). The time the threads were held is logged: the sampler's own
 * cost. This replaces runtime/prof_sample.c (counts by address only, Dreamcast kit only, 1 ms at best). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <intrin.h>
#include <timeapi.h>
#include "base.h"
#include "console.h"
#include "perf.h"

#pragma comment(lib, "winmm.lib")
#define WORDS_MAX (12u << 20) /* 48 MB: about five minutes at 2,000 samples a second on two threads */
#define HEAD 5u

int g_perf_on, g_perf_frames;
u32 g_perf_tick, g_perf_depth, g_perf_stack[PERF_DEPTH], g_perf_from;
static u32 (*g_stack_copy)(u32 *out, u32 max);
#define g_from g_perf_from

static u32 g_us, g_plant_us, g_plant_sys_us, *g_w, g_nw, g_taken[PERF_THREADS], g_dropped, g_nsections;
static const char *g_path;
static const char *const *g_section_names;
static int (*g_section_now)(void);
static HANDLE g_thread[PERF_THREADS];
static volatile LONG g_stop, g_started;
static u64 g_freq, g_t0, g_held;
static __declspec(thread) int t_kind = PERF_THREADS; /* which thread this is: the game's, the one showing, or another */
static uintptr_t g_base, g_code;
static u64 g_cycles[PERF_THREADS], g_tsc[PERF_THREADS]; /* a thread's processor time and the clock at its last sample */
static u32 g_size, g_code_size;

static u64 now(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (u64)t.QuadPart;
}

static u32 us_since_start(void) { return (u32)((now() - g_t0) * 1000000u / g_freq); }

static void start_clock(void)
{
    LARGE_INTEGER f;
    const IMAGE_DOS_HEADER *dos;
    if (g_freq) return;
    QueryPerformanceFrequency(&f);
    g_freq = (u64)f.QuadPart;
    g_t0 = now();
    g_base = (uintptr_t)GetModuleHandleW(NULL);
    dos = (const IMAGE_DOS_HEADER *)g_base;
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)(g_base + (u32)dos->e_lfanew);
    g_size = nt->OptionalHeader.SizeOfImage;
    g_code = g_base + nt->OptionalHeader.BaseOfCode;
    g_code_size = nt->OptionalHeader.SizeOfCode;
}

/* Whether a stack word is a return address into the exe's code: in the code section and just after a call whose
 * target makes sense (a direct call into the code, a call through a pointer in the image such as an import, or a call
 * through a register). A saved register that points at the exe's data, or at guest memory linked into the code
 * section, is not a caller: the planted function caught both being named as one (Spawn, 2026-11-12 and -13). */
static int returns_to_code(uintptr_t v)
{
    const u8 *p = (const u8 *)v;
    int rel;
    if (v - g_code >= g_code_size || v - g_code < 7u) return 0;
    memcpy(&rel, p - 4, sizeof rel);
    if (p[-5] == 0xe8u && v + (uintptr_t)(intptr_t)rel - g_code < g_code_size) return 1;
#ifdef _M_IX86
    if (p[-6] == 0xffu && p[-5] == 0x15u && (uintptr_t)(u32)rel - g_base < g_size) return 1; /* call [pointer] */
#else
    if (p[-6] == 0xffu && p[-5] == 0x15u && v + (uintptr_t)(intptr_t)rel - g_base < g_size) return 1;
#endif
    return p[-2] == 0xffu && (p[-1] & 0xf8u) == 0xd0u; /* call register */
}

/* Whether a 32-bit thread's context sits at a system call's return (ntdll and win32u: call edx, then ret). */
static int at_system_call(uintptr_t ip)
{
    __try {
        const u8 *p = (const u8 *)ip;
        return p[-2] == 0xffu && p[-1] == 0xd2u && (p[0] == 0xc2u || p[0] == 0xc3u);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return 0;
}

/* Where a held thread is, as an offset in the exe. In a system DLL the stack is scanned for the nearest return address
 * into the exe's code (or a stale one: close enough to name the caller).
 * busy: the thread used the processor for most of the time since its last sample. A 32-bit thread that is busy inside
 * the kernel has no context to trust: Windows keeps the one of its last blocking call, so its stack would name that
 * call's caller (Spawn, 2026-11-13: a spin on the clock read was given to the game's wait stub). Such a sample says
 * so (0xfffffffe) and the report places it by section and game call stack instead. A blocked thread's context is good. */
static u32 where(const CONTEXT *c, int busy)
{
#ifdef _M_IX86
    uintptr_t ip = c->Eip, sp = c->Esp;
    if (busy && ip - g_base >= g_size && at_system_call(ip)) return 0xfffffffeu;
#else
    uintptr_t ip = c->Rip, sp = c->Rsp;
    (void)busy;
#endif
    if (ip - g_base < g_size) return (u32)(ip - g_base);
    __try {
        const uintptr_t *s = (const uintptr_t *)sp;
        for (int i = 0; i < 512; i++)
            if (returns_to_code(s[i])) return (u32)(s[i] - g_base) | 0x80000000u;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return 0xffffffffu; /* nothing of the exe on its stack */
}

static void take(u32 which)
{
    CONTEXT c;
    u64 a = now();
    if (SuspendThread(g_thread[which]) == (DWORD)-1) return;
    c.ContextFlags = CONTEXT_CONTROL;
    if (GetThreadContext(g_thread[which], &c) && g_nw + HEAD + PERF_DEPTH <= WORDS_MAX) {
        u32 depth = which != PERF_GAME ? 0u : g_stack_copy ? g_stack_copy(g_w + g_nw + HEAD, PERF_DEPTH)
                                                       : g_perf_depth < PERF_DEPTH ? g_perf_depth : PERF_DEPTH;
        u32 section = which == PERF_GAME && g_section_now ? (u32)g_section_now() & 0xffu : 0xffu;
        u32 *w = g_w + g_nw;
        ULONG64 cycles = 0;
        u64 tsc = __rdtsc();
        int busy;
        QueryThreadCycleTime(g_thread[which], &cycles);
        busy = g_tsc[which] && (cycles - g_cycles[which]) * 4u > (tsc - g_tsc[which]) * 3u; /* held ~10% of it */
        g_cycles[which] = cycles;
        g_tsc[which] = tsc;
        w[0] = (u32)((a - g_t0) * 1000000u / g_freq);
        w[1] = g_perf_tick;
        w[2] = port_vblank_count();
        w[3] = which | (busy ? 0x80u : 0u) | section << 8 | depth << 16;
        w[4] = where(&c, busy);
        if (!g_stack_copy) memcpy(w + HEAD, g_perf_stack, depth * sizeof *w);
        g_nw += HEAD + depth;
        g_taken[which]++;
    } else {
        g_dropped++;
    }
    ResumeThread(g_thread[which]);
    g_held += now() - a;
}

static DWORD WINAPI sampler(void *arg)
{
    /* a high-resolution timer where Windows has one (10 1803 on); else Sleep(1) at a 1 ms scheduler tick */
    HANDLE timer = CreateWaitableTimerExW(NULL, NULL, 0x00000002u /* CREATE_WAITABLE_TIMER_HIGH_RESOLUTION */, TIMER_ALL_ACCESS);
    (void)arg;
    timeBeginPeriod(1);
    while (!g_stop) {
        LARGE_INTEGER due;
        due.QuadPart = -(LONGLONG)g_us * 10;
        if (timer && SetWaitableTimer(timer, &due, 0, NULL, NULL, FALSE)) WaitForSingleObject(timer, 100);
        else Sleep(1); /* not the tick: the sampler's own thread */
        if (port_vblank_count() < g_from) continue;
        for (u32 i = 0; i < PERF_THREADS; i++)
            if (g_thread[i]) take(i);
    }
    timeEndPeriod(1);
    if (timer) CloseHandle(timer);
    return 0;
}

void perf_thread(int which)
{
    t_kind = which;
    HANDLE h;
    if (!g_perf_on || g_thread[which] || !g_w) return;
    start_clock();
    if (!DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &h, 0, FALSE, DUPLICATE_SAME_ACCESS))
        return;
    g_thread[which] = h;
    if (!InterlockedExchange(&g_started, 1)) CloseHandle(CreateThread(NULL, 0, sampler, NULL, 0, NULL));
}

void perf_stack_source(u32 (*copy)(u32 *out, u32 max)) { g_stack_copy = copy; }

void perf_sections(int (*section_now)(void), const char *const *names, int count)
{
    g_section_names = names;
    g_nsections = (u32)count;
    g_section_now = section_now;
}


static const char *const g_timer_names[PERF_TIMERS] = {"pacing sleep", "present", "wait to show", "save file read",
                                                       "audio put", "planted slow function", "planted system-call spin"};
#define TIMERS_ALL (PERF_TIMERS + PERF_KIT_TIMERS)
#define BAR_US 6670u /* port/tools/perf_report.py BAR_US */
static const char *const *g_kit_names;
static int g_kit_count;
static volatile LONG64 g_timer_sum[PERF_THREADS + 1][TIMERS_ALL], g_timer_frame[PERF_THREADS + 1][TIMERS_ALL];
static volatile LONG g_timer_calls[PERF_THREADS + 1][TIMERS_ALL], g_timer_longest[PERF_THREADS + 1][TIMERS_ALL];
static LONG64 g_timer_split[2][PERF_THREADS + 1][TIMERS_ALL]; /* by the shown frame each fell in: within the bar, over it */
static u32 g_split_frames[2];

void perf_kit_timers(const char *const *names, int count)
{
    g_kit_names = names;
    g_kit_count = count < PERF_KIT_TIMERS ? count : PERF_KIT_TIMERS;
}

static void split_timers(u32 make_us);
static u32 frame_timer_us(int which);
/* perf_trace.c, at each shown frame: its present and wait-to-show time, then the timers' split by the bar */
void perf_frame_timers(u32 make_us, u32 *present_us, u32 *show_wait_us)
{
    *present_us = frame_timer_us(PERF_T_PRESENT); /* every thread's, since the last shown frame */
    *show_wait_us = frame_timer_us(PERF_T_SHOW_WAIT);
    split_timers(make_us);
}

u32 perf_now_us(void)
{
    start_clock();
    return us_since_start();
}

static u32 frame_timer_us(int which)
{
    LONG64 t = 0;
    for (int k = 0; k <= PERF_THREADS; k++) t += g_timer_frame[k][which];
    return (u32)((u64)t * 1000000u / g_freq);
}

/* At each shown frame (from --perf-from on): the timers' time since the last one goes to that frame's side of the bar. */
static void split_timers(u32 make_us)
{
    int over = make_us > BAR_US;
    g_split_frames[over]++;
    for (int k = 0; k <= PERF_THREADS; k++)
        for (int i = 0; i < TIMERS_ALL; i++) g_timer_split[over][k][i] += InterlockedExchange64(&g_timer_frame[k][i], 0);
}

u64 perf_timer_clock(void)
{
    start_clock();
    return port_vblank_count() < g_from ? 0 : now();
}

void perf_timer_add(int which, u64 began)
{
    u64 d = now() - began;
    if ((unsigned)which >= (unsigned)TIMERS_ALL) return;
    InterlockedExchangeAdd64(&g_timer_sum[t_kind][which], (LONG64)d);
    InterlockedExchangeAdd64(&g_timer_frame[t_kind][which], (LONG64)d);
    InterlockedIncrement(&g_timer_calls[t_kind][which]);
    if ((LONG)d > g_timer_longest[t_kind][which]) g_timer_longest[t_kind][which] = (LONG)d;
}

/* The planted slow function: named in port.map like any other, so the report must rank it first. */
__declspec(noinline) void perf_planted_slow_function(u32 us)
{
    static u64 per_us; /* processor clock counts in a microsecond, measured once over 2 ms */
    u64 from;
    start_clock();
    if (!per_us) {
        u64 t = now(), c = __rdtsc();
        while (now() - t < g_freq / 500u) {
        }
        per_us = (__rdtsc() - c) / 2000u + 1u;
    }
    from = __rdtsc();
    while (__rdtsc() - from < (u64)us * per_us) { /* in its own code: a spin on a clock read sits in the system */
    }
}

/* The second plant: the same time spent inside system calls (the clock read). The report must not give it to another
 * function: it shows under this name, or as time in the kernel whose caller is not known. */
__declspec(noinline) void perf_planted_system_call(u32 us)
{
    u64 end;
    start_clock();
    end = now() + (u64)us * g_freq / 1000000u;
    while (now() < end) {
    }
}

void perf_plant(void)
{
    if (port_vblank_count() < g_from) return;
    if (g_plant_us) {
        u64 t = perf_timer_begin();
        perf_planted_slow_function(g_plant_us);
        perf_timer_end(PERF_T_PLANT, t);
    }
    if (g_plant_sys_us) {
        u64 t = perf_timer_begin();
        perf_planted_system_call(g_plant_sys_us);
        perf_timer_end(PERF_T_PLANT_SYS, t);
    }
}

static void write_samples(void)
{
    FILE *f;
    u32 head[5];
    char name[32];
    if (!g_path || !g_w || fopen_s(&f, g_path, "wb") || !f) return;
    head[0] = 0x31465250u; /* "PRF1" */
    head[1] = g_size;
    head[2] = g_us;
    head[3] = g_nsections;
    head[4] = g_nw;
    fwrite(head, sizeof head, 1, f);
    for (u32 i = 0; i < g_nsections; i++) {
        memset(name, 0, sizeof name);
        strncpy_s(name, sizeof name, g_section_names[i], _TRUNCATE);
        fwrite(name, sizeof name, 1, f);
    }
    fwrite(g_w, sizeof *g_w, g_nw, f);
    fclose(f);
}

void perf_report(void)
{
    if (g_perf_on && g_started) {
        u32 n = g_taken[PERF_GAME] + g_taken[PERF_SHOW];
        g_stop = 1;
        Sleep(20); /* the sampler's last pass */
        write_samples();
        port_log("perf: %u samples of the game thread and %u of the thread showing frames, one every %u us from vertical "
                 "blank %u; %u dropped; the threads were held %.1f ms in all (%.2f us a sample)\n", g_taken[PERF_GAME],
                 g_taken[PERF_SHOW], g_us, g_from, g_dropped, (double)g_held * 1000.0 / (double)g_freq,
                 n ? (double)g_held * 1000000.0 / (double)g_freq / (double)n : 0.0);
    }
    if (g_perf_on && g_split_frames[0] + g_split_frames[1])
        port_log("perf: timer frames: %u within the bar, %u over it (made in over %u us)\n", g_split_frames[0], g_split_frames[1], BAR_US);
    for (int k = 0; g_perf_on && k <= PERF_THREADS; k++)
        for (int i = 0; i < TIMERS_ALL; i++)
            if (g_timer_calls[k][i])
                port_log("perf: timer %s %s: %.1f ms in %u calls, longest %.2f ms; %.1f ms in frames within the bar, %.1f ms in "
                         "frames over it\n", k == PERF_GAME ? "game" : k == PERF_SHOW ? "show" : "other",
                         i < PERF_TIMERS ? g_timer_names[i] : i - PERF_TIMERS < g_kit_count ? g_kit_names[i - PERF_TIMERS] : "kit",
                         (double)g_timer_sum[k][i] * 1000.0 / (double)g_freq, (u32)g_timer_calls[k][i],
                         (double)g_timer_longest[k][i] * 1000.0 / (double)g_freq, (double)g_timer_split[0][k][i] * 1000.0 / (double)g_freq,
                         (double)g_timer_split[1][k][i] * 1000.0 / (double)g_freq);
    if (g_plant_sys_us) port_log("perf: planted system-call spin, %u us in every tick\n", g_plant_sys_us);
    if (g_plant_us) port_log("perf: planted slow function, %u us in every tick\n", g_plant_us);
    perf_trace_write();
}

int perf_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--perf-sample") || !strcmp(arg, "--prof-sample")) { /* the second: prof_sample.c's old name */
        g_us = (u32)strtoul(value, NULL, 10);
        if (g_us && g_us < 100u) g_us = 100u;
        if (g_us && !g_w) g_w = malloc(WORDS_MAX * sizeof *g_w);
        g_perf_on = g_us && g_w;
        return 2;
    }
    if (!strcmp(arg, "--perf-samples") || !strcmp(arg, "--prof-samples")) {
        g_path = value;
        return 2;
    }
    if (perf_trace_option(arg, value)) return 2; /* --play-frames, --perf-from load+N */
    if (!strcmp(arg, "--perf-from")) {
        g_from = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    if (!strcmp(arg, "--perf-plant-sys")) return g_plant_sys_us = (u32)strtoul(value, NULL, 10), 2;
    if (!strcmp(arg, "--perf-plant")) {
        g_plant_us = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    return 0;
}

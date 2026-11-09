/* The play log's tick cost line (prof.c's family, split off for the file-size cap): --tick-cost on|off, auto (the
 * default) on in a real-time play run (play_stats.c on). prof.c's sections, without the table, the CSV, the GPU queries
 * or the call counts: every 10 s of real time one log line with the mean work a blank by section, the blanks over
 * 16.7 ms and the worst one's sections, the game thread's CPU time against its work and the clock it ran at (its cycles
 * over its CPU time), the in-between thread's CPU time, and the processor's clock as Windows reports it. So a slowdown in
 * a session the user played says whether the game's work grew or the machine slowed (the user's game of 2026-11-08
 * 13:46: fights at 49-55 blanks a second that a replay could not repeat). g_prof_on is 2 while it runs. */
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include <powerbase.h>
#include "port.h"
#include "platform.h"
#include "prof.h"

#pragma comment(lib, "powrprof.lib")
#define TC_REPORT_S 10
#define ROWS (PROF_N + 1u) /* prof.c's rows: the sections, then the game's own code */
#define GAME PROF_N
extern int g_play_stats; /* play_stats.c: 1 in a real-time play run */
u64 g_prof_sections;
static int g_tc_mode = -1; /* --tick-cost: -1 auto, 0 off, 1 on */
static u64 g_tc_sum[ROWS], g_tc_worst[ROWS], g_tc_work, g_tc_worst_work, g_tc_game_cpu, g_tc_present_cpu;
static ULONG64 g_tc_cycles;
static LONGLONG g_tc_start, g_tc_qpf;
static u32 g_tc_n, g_tc_over, g_tc_worst_vb, g_tc_due;
static HANDLE g_tc_game, g_tc_present;
static volatile LONG g_tc_present_set;

int prof_tc_wanted(void) { return g_tc_mode == 1 || (g_tc_mode < 0 && g_play_stats > 0); }

int prof_tc_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--tick-cost")) return 0;
    g_tc_mode = !strcmp(value, "on") ? 1 : !strcmp(value, "off") ? 0 : -1;
    return 2;
}

void prof_tc_note_present(void)
{
    if (g_tc_present_set) return;
    if (InterlockedCompareExchange(&g_tc_present_set, 1, 0) == 0 &&
        !DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &g_tc_present, 0, FALSE,
                         DUPLICATE_SAME_ACCESS))
        g_tc_present = NULL;
}

static u64 thread_cpu_us(HANDLE h)
{
    FILETIME c, e, k, u;
    if (!h || !GetThreadTimes(h, &c, &e, &k, &u)) return 0;
    return ((((u64)k.dwHighDateTime << 32) | k.dwLowDateTime) + (((u64)u.dwHighDateTime << 32) | u.dwLowDateTime)) / 10u;
}

static ULONG64 thread_cycles(HANDLE h)
{
    ULONG64 c = 0;
    if (h) QueryThreadCycleTime(h, &c);
    return c;
}

static LONGLONG qpc(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return t.QuadPart;
}

static void tc_restart(void)
{
    memset(g_tc_sum, 0, sizeof g_tc_sum);
    g_tc_work = g_tc_worst_work = g_prof_sections = 0;
    g_tc_n = g_tc_over = g_tc_due = 0;
    g_tc_game_cpu = thread_cpu_us(g_tc_game);
    g_tc_cycles = thread_cycles(g_tc_game);
    g_tc_present_cpu = thread_cpu_us(g_tc_present);
    g_tc_start = qpc();
}

/* On the game thread at the end of a blank (prof.c's tc_start), from the next blank on. */
void prof_tc_begin(void)
{
    LARGE_INTEGER f;
    if (!DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &g_tc_game, 0, FALSE,
                         DUPLICATE_SAME_ACCESS))
        g_tc_game = NULL;
    QueryPerformanceFrequency(&f);
    g_tc_qpf = f.QuadPart;
    tc_restart();
}

/* A blank's microseconds by row (us[ROWS]) and its work (all but the wait). */
void prof_tc_blank(const u64 *us, u64 work)
{
    for (u32 i = 0; i < ROWS; i++) g_tc_sum[i] += us[i];
    g_tc_work += work;
    g_tc_n++;
    if (work > 16667u) g_tc_over++;
    if (work > g_tc_worst_work) {
        g_tc_worst_work = work;
        memcpy(g_tc_worst, us, sizeof g_tc_worst);
        g_tc_worst_vb = port_vblank_count();
    }
    if (qpc() - g_tc_start >= (LONGLONG)TC_REPORT_S * g_tc_qpf) g_tc_due = 1;
}

typedef struct { /* PROCESSOR_POWER_INFORMATION, which the SDK headers leave out */
    ULONG Number, MaxMhz, CurrentMhz, MhzLimit, MaxIdleState, CurrentIdleState;
} TcPpi;

static void cpu_clock(char *out, size_t cap)
{
    TcPpi p[64];
    SYSTEM_INFO si;
    u64 cur = 0;
    ULONG lim = ~0ul, max = 0;
    u32 n;
    GetSystemInfo(&si);
    n = si.dwNumberOfProcessors < 64u ? si.dwNumberOfProcessors : 64u;
    out[0] = 0;
    if (!n || CallNtPowerInformation(ProcessorInformation, NULL, 0, p, (ULONG)(n * sizeof *p)) != 0) return;
    for (u32 i = 0; i < n; i++) {
        cur += p[i].CurrentMhz;
        if (p[i].MhzLimit < lim) lim = p[i].MhzLimit;
        if (p[i].MaxMhz > max) max = p[i].MaxMhz;
    }
    sprintf_s(out, cap, " | processor clock %lu MHz on average over %u, rated %lu, limit %lu", (ULONG)(cur / n), n, max, lim);
}

/* Off the lock: a line for the blanks since the last. */
static void tc_report(void)
{
    double n = g_tc_n ? (double)g_tc_n : 1.0, secs = (double)(qpc() - g_tc_start) / (double)g_tc_qpf;
    u64 gcpu = thread_cpu_us(g_tc_game) - g_tc_game_cpu, pcpu = thread_cpu_us(g_tc_present) - g_tc_present_cpu;
    ULONG64 cyc = thread_cycles(g_tc_game) - g_tc_cycles;
    char clock[128];
    const u64 *w = g_tc_worst;
    cpu_clock(clock, sizeof clock);
#define TC_MS(i) ((double)g_tc_sum[i] / n / 1000.0)
    port_log("tick cost: %.1f s, %u blanks, work %.2f ms a blank: game code %.2f, ARM7 sound driver %.2f, sound tick %.2f, "
             "media %.2f, TA parse %.2f, Direct3D %.2f, frame done %.2f, present %.2f, in-between copies %.2f and draw %.2f, "
             "read-back %.2f | %u over 16.7 ms, the worst %.2f ms at vertical blank %u (game code %.2f, ARM7 %.2f, sound %.2f, "
             "TA %.2f, Direct3D %.2f, the rest %.2f) | game thread: CPU time %.0f%% of its work, at %.2f GHz | in-between "
             "thread: %s%.2f ms of CPU a blank%s | %.0f timed sections a blank\n",
             secs, g_tc_n, (double)g_tc_work / n / 1000.0, TC_MS(GAME), TC_MS(PROF_ARM), TC_MS(PROF_AUDIO), TC_MS(PROF_MEDIA),
             TC_MS(PROF_TA), TC_MS(PROF_D3D), TC_MS(PROF_DONE), TC_MS(PROF_PRESENT), TC_MS(PROF_X_COPY), TC_MS(PROF_X_DRAW),
             TC_MS(PROF_READ), g_tc_over, g_tc_worst_work / 1000.0, g_tc_worst_vb, w[GAME] / 1000.0, w[PROF_ARM] / 1000.0,
             w[PROF_AUDIO] / 1000.0, w[PROF_TA] / 1000.0, w[PROF_D3D] / 1000.0,
             (double)(g_tc_worst_work - w[GAME] - w[PROF_ARM] - w[PROF_AUDIO] - w[PROF_TA] - w[PROF_D3D]) / 1000.0,
             g_tc_work ? 100.0 * (double)gcpu / (double)g_tc_work : 0.0, gcpu ? (double)cyc / (double)gcpu / 1000.0 : 0.0,
             g_tc_present ? "" : "not seen, ", (double)pcpu / n / 1000.0, clock, (double)g_prof_sections / n);
#undef TC_MS
    tc_restart();
}

void prof_tc_report_due(void)
{
    if (g_tc_due) tc_report();
}

void prof_tc_flush(void)
{
    if (g_tc_n) tc_report();
}

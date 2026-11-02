/* Frame cost, for the runtime target (docs/RUNTIME_TARGET.md): each vertical blank records what the game's thread spent
 * since the last one, apart from the time it waited for the real clock, and how much of that was the software picture
 * (sat_compose) and showing it. The game log ends with the median, 99th percentile and worst of each, and how many
 * frames cost more than the 144 fps budget of 6.94 ms.
 *
 * Nearest existing: sat_where.c (--profile samples where the master is, not how long a frame took). */
#include <windows.h>
#include <mmsystem.h>
#include <stdlib.h>
#include "sat.h"
#include "sat_cpu.h"
#include "sat_sys.h"
#include "play_stats.h"

enum { MAX_FRAMES = 1 << 16 };
static LARGE_INTEGER g_freq, g_last, g_wait_from;
u32 sat_wr_n;
static LONGLONG g_waited, g_poll_t;
static u32 g_poll_wr;
static u32 g_n;
static float g_work[MAX_FRAMES], g_draw[MAX_FRAMES], g_show[MAX_FRAMES], g_tick[MAX_FRAMES];
static float g_cur_draw, g_cur_show, g_cur_v1, g_v1[MAX_FRAMES], g_cur_audio, g_audio[MAX_FRAMES];

static float ms(LONGLONG t) { return (float)((double)t * 1000.0 / (double)g_freq.QuadPart); }

void sat_perf_wait_begin(void) { QueryPerformanceCounter(&g_wait_from); }
void sat_perf_wait_end(void)
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    g_waited += now.QuadPart - g_wait_from.QuadPart;
}

/* The game waits for the vertical blank by reading a flag in a loop, which burns the thread without doing work: a poll
 * with no guest write since the last poll, soon after it, counts the time between them as waiting. An estimate: a long
 * read-only scan would count too. */
void sat_perf_poll(void)
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (!g_freq.QuadPart) QueryPerformanceFrequency(&g_freq);
    if (g_poll_t && sat_wr_n == g_poll_wr && now.QuadPart - g_poll_t < g_freq.QuadPart / 100) g_waited += now.QuadPart - g_poll_t;
    g_poll_t = now.QuadPart;
    g_poll_wr = sat_wr_n;
}

void sat_perf_compose(LONGLONG ticks) { g_cur_draw += ms(ticks); }
void sat_perf_show(LONGLONG ticks) { g_cur_show += ms(ticks); }
void sat_perf_v1(long long ticks) { g_cur_v1 += ms(ticks); }
void sat_perf_audio(long long ticks) { g_cur_audio += ms(ticks); }

/* a vertical blank begins: closes the frame that ran since the last one */
void sat_perf_vblank(void)
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (!g_freq.QuadPart) QueryPerformanceFrequency(&g_freq);
    if (g_last.QuadPart && g_n < MAX_FRAMES) {
        float work = ms(now.QuadPart - g_last.QuadPart - g_waited);
        play_stats_tick((double)work);
        g_work[g_n] = work;
        g_draw[g_n] = g_cur_draw;
        g_show[g_n] = g_cur_show;
        g_v1[g_n] = g_cur_v1;
        g_audio[g_n] = g_cur_audio;
        g_tick[g_n++] = work - g_cur_draw - g_cur_show - g_cur_v1 - g_cur_audio;
    }
    g_last = now;
    g_waited = 0;
    g_cur_draw = g_cur_show = g_cur_v1 = g_cur_audio = 0.0f;
}

static int cmp(const void *a, const void *b)
{
    float x = *(const float *)a, y = *(const float *)b;
    return (x > y) - (x < y);
}

/* the median and worst of each 300-frame stretch (6 s at 50 Hz): which part of the run costs what, in the order played */
static void sections(void)
{
    for (u32 a = 0; a + 100 <= g_n; a += 300) {
        u32 n = g_n - a < 300 ? g_n - a : 300;
        float w[300], t[300], d[300], v[300], au[300];
        for (u32 i = 0; i < n; i++) w[i] = g_work[a + i], t[i] = g_tick[a + i], d[i] = g_draw[a + i], v[i] = g_v1[a + i], au[i] = g_audio[a + i];
        qsort(v, n, sizeof v[0], cmp);
        qsort(w, n, sizeof w[0], cmp);
        qsort(t, n, sizeof t[0], cmp);
        qsort(d, n, sizeof d[0], cmp);
        qsort(au, n, sizeof au[0], cmp);
        port_log("perf: frames %5u-%5u  work median %6.2f worst %7.2f   tick median %6.2f   VDP2 %6.2f   VDP1 %6.2f   audio %6.2f ms\n", a, a + n - 1, w[n / 2], w[n - 1], t[n / 2], d[n / 2], v[n / 2], au[n / 2]);
    }
}

static void line(const char *name, float *v, u32 n)
{
    qsort(v, n, sizeof v[0], cmp);
    port_log("perf: %-22s median %6.2f ms   p99 %6.2f ms   worst %6.2f ms\n", name, v[n / 2], v[(u32)((n - 1) * 0.99)], v[n - 1]);
}

void sat_perf_report(void)
{
    u32 over = 0;
    if (g_n < 10) return;
    for (u32 i = 0; i < g_n; i++) over += g_work[i] > 6.94f;
    {
        FILETIME c, e, k, u;
        GetProcessTimes(GetCurrentProcess(), &c, &e, &k, &u);
        port_log("perf: process CPU time %.1f s (user %.1f, kernel %.1f) over %.1f s of game time at %u Hz\n",
                 (double)(((ULONGLONG)k.dwHighDateTime << 32 | k.dwLowDateTime) + ((ULONGLONG)u.dwHighDateTime << 32 | u.dwLowDateTime)) / 1e7,
                 (double)((ULONGLONG)u.dwHighDateTime << 32 | u.dwLowDateTime) / 1e7, (double)((ULONGLONG)k.dwHighDateTime << 32 | k.dwLowDateTime) / 1e7,
                 (double)g_n / sat_sys_hz(), sat_sys_hz());
    }
    sections();
    port_log("perf: %u frames, %u over the 6.94 ms budget (144 fps); the thread's work per frame, without the wait for the clock\n", g_n, over);
    line("frame (all work)", g_work, g_n);
    line("game tick", g_tick, g_n);
    line("VDP2 + compose", g_draw, g_n);
    line("VDP1 software draw", g_v1, g_n);
    line("audio (68000, SCSP)", g_audio, g_n);
    line("show (copy, present)", g_show, g_n);
}

/* --sample: a thread looks at the running guest function every millisecond (what the game spends its tick in), and at
 * whether the thread is inside the software picture. Racy by design: a statistic, not a trace. */
static volatile int g_phase, g_sampling;
static struct { u32 cpu, fn, n; } g_hot[512];
static u32 g_nhot, g_picture_n, g_samples, g_none_n;
static u32 g_snap[16][12], g_snap_n[16], g_snap_at; /* the last 16 samples' innermost 12 guest functions, to show what a stall is inside */

void sat_perf_phase(int phase) { g_phase = phase; }

static DWORD WINAPI sampler(LPVOID unused)
{
    (void)unused;
    for (;;) {
        u32 id, fn;
        const SatCpu *c;
        Sleep(1); /* not the tick: a watchdog or sampler thread */
        g_samples++;
        if (g_phase) {
            g_picture_n++;
            continue;
        }
        id = (u32)sat_cpu_id();
        c = &g_cpu[id];
        if (c->depth <= 0) {
            g_none_n++;
            continue;
        }
        fn = c->stack[c->depth - 1];
        {
            u32 k = g_snap_at++ & 15u, d = (u32)c->depth < 12u ? (u32)c->depth : 12u;
            g_snap_n[k] = d;
            for (u32 j = 0; j < d; j++) g_snap[k][j] = c->stack[(u32)c->depth - 1u - j];
        }
        for (u32 i = 0; i < g_nhot; i++)
            if (g_hot[i].fn == fn && g_hot[i].cpu == id) {
                g_hot[i].n++;
                goto next;
            }
        if (g_nhot < 512) {
            g_hot[g_nhot].cpu = id;
            g_hot[g_nhot].fn = fn;
            g_hot[g_nhot++].n = 1;
        }
    next:;
    }
}

void sat_perf_sample(int on)
{
    if (on && !g_sampling) {
        g_sampling = 1;
        timeBeginPeriod(1);
        CreateThread(NULL, 0, sampler, NULL, 0, NULL);
    }
}

void sat_perf_sample_report(void)
{
    if (!g_sampling || !g_samples) return;
    port_log("sample: %u samples; software picture %.1f%%, no guest function %.1f%%\n", g_samples, 100.0 * g_picture_n / g_samples, 100.0 * g_none_n / g_samples);
    for (u32 k = 0; k < 16 && k < g_snap_at; k++) {
        port_log("sample: stack %u:", k);
        for (u32 j = 0; j < g_snap_n[k]; j++) port_log(" %08x", g_snap[k][j]);
        port_log("\n");
    }
    for (int k = 0; k < 24; k++) {
        u32 best = 0;
        for (u32 i = 1; i < g_nhot; i++)
            if (g_hot[i].n > g_hot[best].n) best = i;
        if (!g_nhot || !g_hot[best].n) break;
        port_log("sample: %5.1f%%  %s SH-2 in %08x\n", 100.0 * g_hot[best].n / g_samples, g_hot[best].cpu ? "slave " : "master", g_hot[best].fn);
        g_hot[best].n = 0;
    }
}

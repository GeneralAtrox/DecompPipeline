/* Frame cost, for the runtime target (docs/RUNTIME_TARGET.md): each vertical blank records what the game's thread spent
 * since the last one, apart from the time it waited for the real clock, and how much of that was the software picture
 * (sat_compose) and showing it. The game log ends with the median, 99th percentile and worst of each, and how many
 * frames cost more than the 6.67 ms make budget (150 fps of headroom under the 144 cap).
 *
 * Nearest existing: sat_where.c (--profile samples where the master is, not how long a frame took). */
#include <windows.h>
#include <mmsystem.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "sat.h"
#include "sat_cpu.h"
#include "sat_sys.h"
#include "play_stats.h"
#include "perf.h"

enum { MAX_FRAMES = 1 << 16 };
static LARGE_INTEGER g_freq, g_last, g_wait_from;
u32 sat_wr_n;
volatile int sat_sec;
u32 sat_interp_depth[2];
/* g_acct: the clock's ticks so far that are already counted (the timed parts and the waits): a poll's gap leaves out what
 * ran inside it, so a part is never also a wait. Before this the gap between two polls was all waiting even when the
 * vertical blank's compose, VDP1 draw or sound ran in it, and those were then taken away again as parts: "game tick
 * median -1.00 ms" on Dragon Force's map (2026-11-19, the main laptop). */
static LONGLONG g_waited, g_poll_t, g_acct, g_poll_acct, g_wait_pace, g_wait_poll, g_tot[4];
static u32 g_poll_wr, g_poll_streak, g_seen_restarts;
static int g_wait_sec; /* the part sat_perf_wait_begin left */
/* The master's own writes: a poll hands the slave a turn, and the slave's writes must not end the master's wait (Dragon
 * Force's master spins on FTCSR for the slave's signal; counted as the game's work, 2026-11-19). */
static u32 g_wr_other, g_wr_mark;
static int g_wr_cpu;
static LONGLONG g_slave_from, g_flag_t, g_flag_acct, g_slave_cur, g_flag_cur, g_slave_all, g_flag_all;
static u32 g_flag_n;
void sat_perf_cpu(int id) /* the slave's turns are work (one host thread runs both CPUs), never a gap's waiting */
{
    LARGE_INTEGER now;
    if (g_wr_cpu) g_wr_other += sat_wr_n - g_wr_mark;
    if (id != g_wr_cpu) {
        QueryPerformanceCounter(&now);
        if (id) g_slave_from = now.QuadPart;
        else if (g_slave_from) g_acct += now.QuadPart - g_slave_from, g_slave_cur += now.QuadPart - g_slave_from, g_slave_from = 0;
    }
    g_wr_cpu = id, g_wr_mark = sat_wr_n;
}

/* The master read FTCSR with ICF clear: it waits for the slave's signal, whatever it writes meanwhile (its loop calls a
 * function, which pushes a return address, so the write test above never held). The time from one such read to the next,
 * less what was counted inside it (parts, the slave's turns), is waiting; the read that finds ICF set ends it. */
static LONGLONG flag_gap(LONGLONG now)
{
    LONGLONG gap;
    if (!g_flag_t || now - g_flag_t >= g_freq.QuadPart / 100) return 0;
    gap = now - g_flag_t - (g_acct - g_flag_acct);
    return gap > 0 ? gap : 0;
}
void sat_perf_flag(int waiting)
{
    LARGE_INTEGER now;
    LONGLONG gap;
    if (g_wr_cpu) return; /* the slave's own wait is in its turn, already work */
    QueryPerformanceCounter(&now);
    if (!g_freq.QuadPart) QueryPerformanceFrequency(&g_freq);
    gap = flag_gap(now.QuadPart);
    g_waited += gap, g_wait_poll += gap, g_acct += gap, g_flag_cur += gap;
    g_flag_n += waiting && !g_flag_t;
    g_flag_t = waiting ? now.QuadPart : 0, g_flag_acct = g_acct;
}
static u32 own_writes(void) { return sat_wr_n - g_wr_other - (g_wr_cpu ? sat_wr_n - g_wr_mark : 0u); }
static u32 g_n;
static float g_work[MAX_FRAMES], g_draw[MAX_FRAMES], g_show[MAX_FRAMES], g_tick[MAX_FRAMES];
static float g_cur_draw, g_cur_show, g_cur_v1, g_v1[MAX_FRAMES], g_cur_audio, g_audio[MAX_FRAMES];

static float ms(LONGLONG t) { return (float)((double)t * 1000.0 / (double)g_freq.QuadPart); }

void sat_perf_wait_begin(void)
{
    QueryPerformanceCounter(&g_wait_from);
    g_wait_sec = sat_sec;
    sat_sec = SAT_SEC_PACE;
}
void sat_perf_wait_end(void)
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    g_waited += now.QuadPart - g_wait_from.QuadPart;
    g_wait_pace += now.QuadPart - g_wait_from.QuadPart;
    g_acct += now.QuadPart - g_wait_from.QuadPart;
    sat_sec = g_wait_sec;
}

/* A poll's gap: the time since the last poll with no guest write in it and soon after it, less what was already counted
 * inside it (parts, waits). The game waits for the vertical blank by reading a flag in a loop, which burns the thread
 * without doing work. An estimate: a long read-only scan would count too. */
static LONGLONG poll_gap(LONGLONG now)
{
    LONGLONG gap;
    if (!g_poll_t || own_writes() != g_poll_wr || now - g_poll_t >= g_freq.QuadPart / 100) return 0;
    gap = now - g_poll_t - (g_acct - g_poll_acct);
    return gap > 0 ? gap : 0;
}

void sat_perf_poll(void)
{
    LARGE_INTEGER now;
    LONGLONG gap;
    QueryPerformanceCounter(&now);
    if (!g_freq.QuadPart) QueryPerformanceFrequency(&g_freq);
    gap = poll_gap(now.QuadPart);
    g_poll_streak = own_writes() == g_poll_wr ? g_poll_streak + 1u : 0u;
    g_waited += gap, g_wait_poll += gap, g_acct += gap;
    g_poll_t = now.QuadPart;
    g_poll_wr = own_writes();
    g_poll_acct = g_acct;
}

void sat_perf_compose(LONGLONG ticks) { g_cur_draw += ms(ticks), g_acct += ticks; }
void sat_perf_show(LONGLONG ticks) { g_cur_show += ms(ticks), g_acct += ticks; }
void sat_perf_v1(long long ticks) { g_cur_v1 += ms(ticks), g_acct += ticks; }
void sat_perf_audio(long long ticks) { g_cur_audio += ms(ticks), g_acct += ticks; }

u32 sat_interp_marked(u32 cpu, int depth, u32 entry, u32 top, const TcArgs *a, int stack_args)
{
    u32 was = sat_interp_depth[cpu & 1u], r;
    sat_interp_depth[cpu & 1u] = (u32)depth;
    r = sat_interp(entry, top, a, stack_args);
    sat_interp_depth[cpu & 1u] = was;
    return r;
}

/* --- the sampler's view (base/perf.c): which part the game's thread is in, and the game's call stack --- */
static const char *const k_sections[] = {"game: translated C", "game: SH-2 interpreter", "wait: game polls a flag (translated)",
                                         "wait: game polls a flag (interpreter)", "wait: pacing to the blank",
                                         "kit: VDP2 + compose", "kit: VDP1 draw", "kit: audio (68000, SCSP)",
                                         "kit: show (copy, post)", "kit: CD drive, disc read", "kit: in-between frame"};
/* slave: the slave SH-2's turns, inside game; flag: the master's wait for its signal, inside wait_poll */
static const char *const k_parts[] = {"work", "game", "wait_pace", "wait_poll", "vdp2", "vdp1", "audio", "show", "slave", "flag"};

/* On the sampler's thread, while the game's is held: racy reads of words the game's thread writes, a statistic. */
static int section_now(void)
{
    int sec = sat_sec, id, interp, poll;
    LARGE_INTEGER now;
    if (sec) return sec;
    id = sat_cpu_id();
    interp = sat_interp_depth[id] && sat_interp_depth[id] == (u32)g_cpu[id].depth;
    QueryPerformanceCounter(&now);
    poll = (g_poll_streak && g_poll_t && own_writes() == g_poll_wr && now.QuadPart - g_poll_t < g_freq.QuadPart / 100) ||
           (g_flag_t && now.QuadPart - g_flag_t < g_freq.QuadPart / 100);
    return poll ? 2 + interp : interp;
}

static u32 stack_now(u32 *out, u32 max)
{
    const SatCpu *c = &g_cpu[sat_cpu_id()];
    int d = c->depth;
    u32 n;
    if (d <= 0) return 0;
    if (d > 512) d = 512;
    n = (u32)d < max ? (u32)d : max; /* the innermost, outermost of them first */
    memcpy(out, c->stack + d - (int)n, n * sizeof *out);
    return n;
}

/* a vertical blank begins: closes the frame that ran since the last one */
void sat_perf_vblank(void)
{
    static int told;
    LARGE_INTEGER now;
    LONGLONG gap;
    QueryPerformanceCounter(&now);
    if (!g_freq.QuadPart) QueryPerformanceFrequency(&g_freq);
    if (!told) {
        told = 1;
        perf_sections(section_now, k_sections, (int)(sizeof k_sections / sizeof *k_sections));
        perf_stack_source(stack_now);
        perf_parts(k_parts, (int)(sizeof k_parts / sizeof *k_parts));
    }
    gap = poll_gap(now.QuadPart); /* a poll still going at the blank: its time to here is this frame's waiting */
    g_waited += gap, g_wait_poll += gap, g_acct += gap;
    g_poll_t = now.QuadPart, g_poll_acct = g_acct; /* the next poll's gap starts here */
    gap = flag_gap(now.QuadPart); /* and a wait on the slave's signal */
    g_waited += gap, g_wait_poll += gap, g_acct += gap;
    if (g_flag_t) g_flag_t = now.QuadPart, g_flag_acct = g_acct;
    if (g_last.QuadPart && g_n < MAX_FRAMES) {
        LONGLONG total = now.QuadPart - g_last.QuadPart;
        float work = total > g_waited ? ms(total - g_waited) : 0.0f, parts = g_cur_draw + g_cur_show + g_cur_v1 + g_cur_audio;
        float tick = work - parts > 0.0f ? work - parts : 0.0f; /* a part timed on its own clock pair can round over */
        if (g_perf_frames) {
            u32 us[10];
            us[0] = (u32)(work * 1000.0f), us[1] = (u32)(tick * 1000.0f), us[2] = (u32)(ms(g_wait_pace) * 1000.0f);
            us[3] = (u32)(ms(g_wait_poll) * 1000.0f), us[4] = (u32)(g_cur_draw * 1000.0f), us[5] = (u32)(g_cur_v1 * 1000.0f);
            us[6] = (u32)(g_cur_audio * 1000.0f), us[7] = (u32)(g_cur_show * 1000.0f);
            us[8] = (u32)(ms(g_slave_cur) * 1000.0f), us[9] = (u32)(ms(g_flag_cur) * 1000.0f);
            perf_tick(us);
        }
        play_stats_tick((double)work);
        if (g_seen_restarts != g_perf_restarts) { /* --perf-from load+N: the measured segment starts here */
            g_seen_restarts = g_perf_restarts;
            g_slave_all = g_flag_all = 0, g_flag_n = 0;
            g_n = 0;
            memset(g_tot, 0, sizeof g_tot);
        }
        g_tot[0] += total, g_tot[1] += total - g_waited, g_tot[2] += g_wait_pace, g_tot[3] += g_wait_poll;
        g_work[g_n] = work;
        g_draw[g_n] = g_cur_draw;
        g_show[g_n] = g_cur_show;
        g_v1[g_n] = g_cur_v1;
        g_audio[g_n] = g_cur_audio;
        g_tick[g_n++] = tick;
    }
    g_last = now;
    g_slave_all += g_slave_cur, g_flag_all += g_flag_cur;
    g_waited = g_wait_pace = g_wait_poll = g_slave_cur = g_flag_cur = 0;
    g_cur_draw = g_cur_show = g_cur_v1 = g_cur_audio = 0.0f;
}

static double ms_all(LONGLONG t) { return (double)t * 1000.0 / (double)g_freq.QuadPart; }

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

/* n frames ending `back` frames before the last: the median and worst of each part (sat_sys.c: a load's blanks) */
void sat_perf_span(u32 back, u32 n, const char *what)
{
    static float v[6][4096];
    float *src[6] = {g_work, g_tick, g_draw, g_v1, g_audio, g_show};
    static const char *const name[6] = {"work", "tick", "VDP2", "VDP1", "audio", "show"};
    char buf[400];
    int at = 0;
    if (back >= g_n) return;
    if (n > g_n - back) n = g_n - back;
    if (n > 4096u) n = 4096u;
    if (!n) return;
    at += snprintf(buf + at, sizeof buf - (size_t)at, "%s: %u frames, median/worst ms:", what, n);
    for (int k = 0; k < 6; k++) {
        for (u32 i = 0; i < n; i++) v[k][i] = src[k][g_n - back - n + i];
        qsort(v[k], n, sizeof v[k][0], cmp);
        at += snprintf(buf + at, sizeof buf - (size_t)at, " %s %.2f/%.2f", name[k], v[k][n / 2], v[k][n - 1]);
    }
    port_log("%s\n", buf);
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
    for (u32 i = 0; i < g_n; i++) over += g_work[i] > (float)PS_MAKE_BAR_MS;
    {
        FILETIME c, e, k, u;
        GetProcessTimes(GetCurrentProcess(), &c, &e, &k, &u);
        port_log("perf: process CPU time %.1f s (user %.1f, kernel %.1f) over %.1f s of game time at %u Hz\n",
                 (double)(((ULONGLONG)k.dwHighDateTime << 32 | k.dwLowDateTime) + ((ULONGLONG)u.dwHighDateTime << 32 | u.dwLowDateTime)) / 1e7,
                 (double)((ULONGLONG)u.dwHighDateTime << 32 | u.dwLowDateTime) / 1e7, (double)((ULONGLONG)k.dwHighDateTime << 32 | k.dwLowDateTime) / 1e7,
                 (double)g_n / sat_sys_hz(), sat_sys_hz());
    }
    sections();
    port_log("perf: %u frames, %u over the 6.67 ms make budget (150 fps headroom); the thread's work per frame, without the wait for the clock\n", g_n, over);
    line("frame (all work)", g_work, g_n);
    line("game tick", g_tick, g_n);
    line("VDP2 + compose", g_draw, g_n);
    line("VDP1 software draw", g_v1, g_n);
    line("audio (68000, SCSP)", g_audio, g_n);
    line("show (copy, present)", g_show, g_n);
    if (g_tot[0]) { /* the share of the game's thread that was work, and what the rest waited on */
        double all = (double)g_tot[0], parts = 0.0;
        for (u32 i = 0; i < g_n; i++) parts += (double)g_draw[i] + g_show[i] + g_v1[i] + g_audio[i]; /* ms; order does not matter */
        port_log("perf: the game thread's time: work %.1f%% (the kit's parts %.1f%%, the game's code %.1f%%), waiting %.1f%% (pacing "
                 "to the blank %.1f%%, the game polling a flag %.1f%%)\n", 100.0 * (double)g_tot[1] / all,
                 100.0 * parts / ms_all(g_tot[0]), 100.0 * (double)g_tot[1] / all - 100.0 * parts / ms_all(g_tot[0]),
                 100.0 * (all - (double)g_tot[1]) / all, 100.0 * (double)g_tot[2] / all, 100.0 * (double)g_tot[3] / all);
        port_log("perf: the slave SH-2's turns %.1f%% of the game thread (inside the game's code); the master waited on its "
                 "signal %u times, %.1f%%\n", 100.0 * (double)g_slave_all / all, g_flag_n, 100.0 * (double)g_flag_all / all);
    }
}

/* --sample: a thread looks at the running guest function every millisecond (what the game spends its tick in), and at
 * whether the thread is inside the software picture. Racy by design: a statistic, not a trace. */
static volatile int g_phase, g_sampling;
static struct { u32 cpu, fn, n; } g_hot[512];
static u32 g_nhot, g_picture_n, g_samples, g_none_n;
static u32 g_snap[16][12], g_snap_n[16], g_snap_at; /* the last 16 samples' innermost 12 guest functions, to show what a stall is inside */

void sat_perf_phase(int phase)
{
    g_phase = phase;
    sat_sec = phase == 1 ? SAT_SEC_COMPOSE : phase == 2 ? SAT_SEC_VDP1 : SAT_SEC_GAME; /* the base sampler's part */
}

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

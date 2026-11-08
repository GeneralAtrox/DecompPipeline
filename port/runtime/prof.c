/* The frame-time profiler: see prof.h. --prof on, --prof-from VBLANK (statistics from that blank on), --prof-csv FILE
 * (one row per vertical blank). At exit, and every 600 blanks, the log gets the table: the mean, 95th percentile and
 * worst of each row over the blanks counted, how many took longer than 16.7 ms of work (the wait for the blank left
 * out), and the GPU's own time for the scene (D3D11 timestamps, read a few frames late and never waited for). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <timeapi.h>
#include "port.h"
#include "platform.h"
#include "prof.h"
#include "gfx/gfx.h"

int g_prof_on;

#define MAX_FRAMES 65536u
#define DEPTH 16u
#define ROWS (PROF_N + 1u) /* the sections, then the game's own code */
#define GAME PROF_N
#define GPU_SLOTS 8u
#define SUMMARY_EVERY 600u

static const char *const k_names[ROWS] = {"wait for blank", "sound tick (AICA mix, CD)", "ARM7 sound driver",
                                          "media decoders", "TA list parse", "Direct3D calls (CPU)",
                                          "frame done (capture, log)", "present", "in-between: RAM copies",
                                          "in-between: game draw code", "GPU read-back", "game code (translated C)"};

typedef struct Rec {
    u32 vblank, us[ROWS], gpu_us, headers, triangles, work_us;
} Rec;

static Rec *g_rec;
static u32 g_n, g_from, g_headers, g_triangles, g_over, g_next_summary;
static HANDLE g_csv = INVALID_HANDLE_VALUE;
static LARGE_INTEGER g_freq;
static u64 g_since, g_acc[ROWS];
static int g_stack[DEPTH];
static u32 g_depth;

/* --prof-clock cycles: the game thread's own processor cycles (QueryThreadCycleTime), shown as milliseconds at an
 * assumed 3 GHz. Time other programs take from it (preemption, a busy machine) does not count, so two runs of the same
 * code agree on a loaded machine; the default is the wall clock. */
#define CYCLES_PER_SEC 3000000000ull
static int g_cycles;

u64 prof_now(void)
{
    LARGE_INTEGER t;
    ULONG64 c;
    if (g_cycles) {
        QueryThreadCycleTime(GetCurrentThread(), &c);
        return (u64)c;
    }
    QueryPerformanceCounter(&t);
    return (u64)t.QuadPart;
}

static int top(void) { return g_depth ? g_stack[g_depth - 1u] : GAME; }

u64 prof_enter(int id)
{
    if (gfx_on_present_thread()) return 0u; /* the rows are the game thread's */
    u64 t = prof_now();
    g_acc[top()] += t - g_since;
    g_since = t;
    if (g_depth < DEPTH) g_stack[g_depth++] = id;
    return 1u;
}

void prof_leave(u64 token)
{
    u64 t = prof_now();
    (void)token;
    g_acc[top()] += t - g_since;
    g_since = t;
    if (g_depth) g_depth--;
}

/* ---- GPU time: a disjoint query and two timestamps around the scene, read back later without waiting ---- */
static struct {
    ID3D11Query *disjoint, *t0, *t1;
    int busy;
} g_gpu[GPU_SLOTS];
static u32 g_gpu_at;
static u64 g_gpu_us_sum;
static u32 g_gpu_us_max;

static ID3D11Query *make_query(D3D11_QUERY kind)
{
    D3D11_QUERY_DESC d = {kind, 0};
    ID3D11Query *q = NULL;
    return SUCCEEDED(ID3D11Device_CreateQuery(g_gfx.dev, &d, &q)) ? q : NULL;
}

void prof_gpu_begin(void)
{
    if (!g_prof_on || !g_gfx.dev) return;
    u32 i = g_gpu_at % GPU_SLOTS;
    if (g_gpu[i].busy) return; /* the slot's result was never read: skip this scene */
    if (!g_gpu[i].disjoint) g_gpu[i].disjoint = make_query(D3D11_QUERY_TIMESTAMP_DISJOINT);
    if (!g_gpu[i].disjoint) return;
    if (!g_gpu[i].t0) g_gpu[i].t0 = make_query(D3D11_QUERY_TIMESTAMP);
    if (!g_gpu[i].t1) g_gpu[i].t1 = make_query(D3D11_QUERY_TIMESTAMP);
    if (!g_gpu[i].t0 || !g_gpu[i].t1) return;
    ID3D11DeviceContext_Begin(g_gfx.ctx, (ID3D11Asynchronous *)g_gpu[i].disjoint);
    ID3D11DeviceContext_End(g_gfx.ctx, (ID3D11Asynchronous *)g_gpu[i].t0);
    ID3D11DeviceContext_Flush(g_gfx.ctx); /* submit now: unflushed, the stamps include the wait for the driver to send the commands (15-30 ms measured for a 1.5 ms scene) */
    g_gpu[i].busy = 1;
}

void prof_gpu_end(void)
{
    if (!g_prof_on || !g_gfx.dev) return;
    u32 i = g_gpu_at % GPU_SLOTS;
    if (!g_gpu[i].busy || g_gpu[i].busy == 2) return;
    ID3D11DeviceContext_End(g_gfx.ctx, (ID3D11Asynchronous *)g_gpu[i].t1);
    ID3D11DeviceContext_End(g_gfx.ctx, (ID3D11Asynchronous *)g_gpu[i].disjoint);
    ID3D11DeviceContext_Flush(g_gfx.ctx); /* submit now: unflushed, the stamps include the wait for the driver to send the commands (15-30 ms measured for a 1.5 ms scene) */
    g_gpu[i].busy = 2;
    g_gpu_at++;
}

/* The microseconds of the oldest finished scene (0: none ready). */
static u32 gpu_collect(void)
{
    u32 got = 0;
    for (u32 k = 0; k < GPU_SLOTS; k++) {
        u32 i = (g_gpu_at + k) % GPU_SLOTS; /* oldest first */
        D3D11_QUERY_DATA_TIMESTAMP_DISJOINT dj;
        UINT64 a, b;
        if (g_gpu[i].busy != 2) continue;
        if (ID3D11DeviceContext_GetData(g_gfx.ctx, (ID3D11Asynchronous *)g_gpu[i].disjoint, &dj, sizeof dj,
                                        D3D11_ASYNC_GETDATA_DONOTFLUSH) != S_OK)
            continue;
        if (ID3D11DeviceContext_GetData(g_gfx.ctx, (ID3D11Asynchronous *)g_gpu[i].t0, &a, sizeof a, 0) != S_OK ||
            ID3D11DeviceContext_GetData(g_gfx.ctx, (ID3D11Asynchronous *)g_gpu[i].t1, &b, sizeof b, 0) != S_OK)
            continue;
        g_gpu[i].busy = 0;
        if (!dj.Disjoint && dj.Frequency) got += (u32)((b - a) * 1000000u / dj.Frequency);
    }
    return got;
}


/* ---- --prof-sample US: the game thread's instruction pointer every US microseconds from --prof-from on, as
 * port.exe+OFFSET counts in FILE (--prof-samples FILE), for tools/symbolize.py-style reading against port.map ---- */
#define SAMPLES_MAX 4000000u
static HANDLE g_game_thread;
static u32 g_sample_us, *g_samples, g_nsamples;
static const char *g_samples_path;
static volatile LONG g_sampling_on, g_sampler_stop;

#pragma comment(lib, "winmm.lib")
/* The sample for a thread context: Eip when it is in port.exe. Eip in a system DLL (a wait, a file read) says nothing
 * of who called it, so the stack is scanned for the nearest value that points into port.exe (a return address, or a
 * stale one: close enough to name the caller) and the sample is that address with bit 31 set. */
static u32 sample_of(const CONTEXT *c)
{
    u32 base = (u32)(uintptr_t)GetModuleHandleW(NULL);
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)(uintptr_t)base;
    u32 size = ((const IMAGE_NT_HEADERS32 *)(uintptr_t)(base + (u32)dos->e_lfanew))->OptionalHeader.SizeOfImage;
    if (c->Eip - base < size) return c->Eip;
    __try {
        const u32 *sp = (const u32 *)(uintptr_t)c->Esp;
        for (int i = 0; i < 512; i++)
            if (sp[i] - base < size) return sp[i] | 0x80000000u;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return c->Eip;
}

static DWORD WINAPI sampler(void *arg)
{
    (void)arg;
    timeBeginPeriod(1); /* Sleep(1) otherwise sleeps a scheduler tick (15 ms): too few samples */
    while (!g_sampler_stop) {
        Sleep(1); /* not the tick: a watchdog or sampler thread */
        if (!g_sampling_on || g_nsamples >= SAMPLES_MAX) continue;
        if (SuspendThread(g_game_thread) == (DWORD)-1) continue;
        CONTEXT c;
        c.ContextFlags = CONTEXT_CONTROL;
        if (GetThreadContext(g_game_thread, &c)) g_samples[g_nsamples++] = sample_of(&c);
        ResumeThread(g_game_thread);
    }
    return 0;
}

static void sampler_start(void)
{
    if (!g_sample_us || g_game_thread || !g_samples) return;
    if (!DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &g_game_thread, 0, FALSE,
                         DUPLICATE_SAME_ACCESS))
        return;
    CloseHandle(CreateThread(NULL, 0, sampler, NULL, 0, NULL));
}

static int cmp_addr(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;
    return x < y ? -1 : x > y;
}

static void samples_write(void)
{
    u32 base = (u32)(uintptr_t)GetModuleHandleW(NULL);
    FILE *f;
    if (!g_nsamples || !g_samples_path) return;
    g_sampler_stop = 1;
    qsort(g_samples, g_nsamples, sizeof *g_samples, cmp_addr);
    if (fopen_s(&f, g_samples_path, "w") || !f) return;
    fprintf(f, "# %u samples, one every ~1 ms of the game thread; address count (port.exe+offset)\n", g_nsamples);
    for (u32 i = 0; i < g_nsamples;) {
        u32 j = i;
        while (j < g_nsamples && g_samples[j] == g_samples[i]) j++;
        u32 v = g_samples[i], flag = v & 0x80000000u; /* flag: a system wait, named by its caller in port.exe */
        fprintf(f, "%08x %u\n", ((v & 0x7fffffffu) - base) | flag, j - i);
        i = j;
    }
    fclose(f);
}

/* ---- the table ---- */
static int cmp_u32(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;
    return x < y ? -1 : x > y;
}

static u32 *column(u32 row, u32 first, u32 count)
{
    u32 *v = malloc(count * sizeof *v);
    if (!v) return NULL;
    for (u32 i = 0; i < count; i++) {
        const Rec *r = &g_rec[first + i];
        v[i] = row < ROWS ? r->us[row] : row == ROWS ? r->gpu_us : r->work_us;
    }
    qsort(v, count, sizeof *v, cmp_u32);
    return v;
}

static void table(const char *title, u32 first, u32 count)
{
    if (!count) return;
    port_log("prof: %s, vblanks %u-%u (%u): ms per vertical blank, mean / 95th percentile / worst\n", title,
             g_rec[first].vblank, g_rec[first + count - 1u].vblank, count);
    for (u32 row = 0; row <= ROWS + 1u; row++) {
        u32 *v = column(row, first, count);
        u64 sum = 0;
        if (!v) return;
        for (u32 i = 0; i < count; i++) sum += v[i];
        port_log("prof:   %-30s %7.3f %7.3f %7.3f\n",
                 row < ROWS ? k_names[row] : row == ROWS ? "GPU time of the scene" : "WORK (all but the wait)",
                 (double)sum / count / 1000.0, v[(count - 1u) * 95u / 100u] / 1000.0, v[count - 1u] / 1000.0);
        free(v);
    }
}

static void worst(u32 first, u32 count)
{
    u32 used[5], np = 0;
    for (; np < 5u && np < count; np++) {
        u32 best = ~0u;
        for (u32 i = 0; i < count; i++) {
            int taken = 0;
            for (u32 j = 0; j < np; j++) taken |= used[j] == i;
            if (!taken && (best == ~0u || g_rec[first + i].work_us > g_rec[first + best].work_us)) best = i;
        }
        used[np] = best;
    }
    for (u32 k = 0; k < np; k++) {
        const Rec *r = &g_rec[first + used[k]];
        port_log("prof:   worst %u: vblank %u work %.2f ms (game %.2f, sound %.2f, arm %.2f, ta %.2f, d3d %.2f, done %.2f, "
                 "present %.2f, x-copy %.2f, x-draw %.2f, read %.2f; gpu %.2f; %u headers %u triangles)\n", k + 1u,
                 r->vblank, r->work_us / 1000.0, r->us[GAME] / 1000.0, r->us[PROF_AUDIO] / 1000.0,
                 r->us[PROF_ARM] / 1000.0, r->us[PROF_TA] / 1000.0, r->us[PROF_D3D] / 1000.0,
                 r->us[PROF_DONE] / 1000.0, r->us[PROF_PRESENT] / 1000.0, r->us[PROF_X_COPY] / 1000.0,
                 r->us[PROF_X_DRAW] / 1000.0, r->us[PROF_READ] / 1000.0, r->gpu_us / 1000.0, r->headers, r->triangles);
    }
}

static void csv_row(const Rec *r)
{
    char line[512];
    DWORD put;
    int n = sprintf_s(line, sizeof line, "%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u\n", r->vblank, r->work_us,
                      r->us[GAME], r->us[PROF_AUDIO], r->us[PROF_ARM], r->us[PROF_MEDIA], r->us[PROF_TA],
                      r->us[PROF_D3D], r->us[PROF_DONE], r->us[PROF_PRESENT], r->us[PROF_X_COPY], r->us[PROF_X_DRAW],
                      r->us[PROF_READ], r->us[PROF_WAIT], r->gpu_us, r->headers, r->triangles);
    if (n > 0) WriteFile(g_csv, line, (DWORD)n, &put, NULL);
}

void prof_scene(u32 headers, u32 triangles)
{
    g_headers = headers;
    g_triangles = triangles;
}

static void prof_frame_locked(void)
{
    u64 t;
    Rec r;
    if (!g_prof_on) return;
    t = prof_now();
    g_acc[top()] += t - g_since;
    g_since = t;
    memset(&r, 0, sizeof r);
    r.vblank = port_vblank_count();
    for (u32 i = 0; i < ROWS; i++) {
        r.us[i] = (u32)(g_acc[i] * 1000000u / (u64)g_freq.QuadPart);
        if (i != PROF_WAIT) r.work_us += r.us[i];
    }
    memset(g_acc, 0, sizeof g_acc);
    r.gpu_us = gpu_collect();
    sampler_start();
    g_sampling_on = r.vblank >= g_from;
    prof_calls_on((int)g_sampling_on);
    r.headers = g_headers;
    r.triangles = g_triangles;
    if (r.vblank < g_from || !g_rec) return;
    if (g_csv != INVALID_HANDLE_VALUE) csv_row(&r);
    if (g_n < MAX_FRAMES) g_rec[g_n++] = r;
    if (r.work_us > 16667u) g_over++;
    if (g_n >= g_next_summary) {
        table("last 600", g_n - SUMMARY_EVERY, SUMMARY_EVERY);
        g_next_summary = g_n + SUMMARY_EVERY;
    }
}

void prof_report(void)
{
    if (!g_prof_on || !g_n) return;
    port_log("prof: all counted blanks: %u of %u took more than 16.7 ms of work\n", g_over, g_n);
    table("whole run", 0, g_n);
    prof_calls_report(g_n);
    worst(0, g_n);
    if (g_n > 300u) {
        table("last 300", g_n - 300u, 300u);
        worst(g_n - 300u, 300u);
    }
    if (g_csv != INVALID_HANDLE_VALUE) CloseHandle(g_csv);
    g_csv = INVALID_HANDLE_VALUE;
    g_sampling_on = 0;
    samples_write();
}

int prof_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--prof")) {
        g_prof_on = strcmp(value, "off") != 0;
        if (g_prof_on && !g_rec) {
            g_rec = calloc(MAX_FRAMES, sizeof *g_rec);
            QueryPerformanceFrequency(&g_freq);
            if (g_cycles) g_freq.QuadPart = (LONGLONG)CYCLES_PER_SEC;
            g_since = prof_now();
            g_next_summary = SUMMARY_EVERY;
        }
        return 2;
    }
    if (!strcmp(arg, "--prof-clock")) {
        g_cycles = !strcmp(value, "cycles");
        if (g_rec) {
            if (g_cycles) g_freq.QuadPart = (LONGLONG)CYCLES_PER_SEC;
            g_since = prof_now();
        }
        return 2;
    }
    if (!strcmp(arg, "--prof-sample")) {
        g_sample_us = (u32)strtoul(value, NULL, 10);
        if (!g_samples) g_samples = malloc(SAMPLES_MAX * sizeof *g_samples);
        return 2;
    }
    if (!strcmp(arg, "--prof-samples")) {
        g_samples_path = value;
        return 2;
    }
    if (!strcmp(arg, "--prof-from")) {
        g_from = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    if (!strcmp(arg, "--prof-csv")) {
        g_csv = CreateFileA(value, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (g_csv != INVALID_HANDLE_VALUE) {
            static const char head[] = "vblank,work_us,game,sound,arm,media,ta,d3d,done,present,xcopy,xdraw,read,wait,gpu,headers,triangles\n";
            DWORD put;
            WriteFile(g_csv, head, sizeof head - 1u, &put, NULL);
        }
        return 2;
    }
    return 0;
}

void prof_frame(void)
{
    gfx_lock();
    prof_frame_locked();
    gfx_unlock();
}

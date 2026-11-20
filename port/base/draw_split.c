/* PG_DRAW_SPLIT: see draw_split.h. The clock is the QueryPerformanceCounter (wall time, as play_stats.c); the
 * histograms are play_stats.c's 10 us buckets, read with play_stats_percentile.
 * Nearest existing: play_stats.c - it judges make time against the bar in every play run; this splits a native frame
 * into its parts on request only, with GPU queries play_stats must not cost. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "native_game.h"
#pragma warning(push)
#pragma warning(disable : 4201)
#include <d3d11_1.h>
#pragma warning(pop)
#include "play_stats.h"
#include "draw_split.h"

int g_draw_split = -1;
enum { SP_GAME, SP_TICKS, SP_COPY, SP_PRESENT, SP_GPU, SP_GPU_PRESENT, SP_OUTSIDE, SP_FRAME, SP_N };
static const char *const k_sp_names[SP_N] = {"game", "ticks_in_game", "copy", "present", "gpu_frame", "gpu_present", "outside", "frame"};
#define SP_RING 8u
static u32 (*g_sp_hist)[PS_BUCKETS];
static u64 g_sp_n[SP_N], g_sp_worst[SP_N], g_sp_freq, g_sp_frame_at, g_sp_last_frame, g_sp_present_end, g_sp_ticks_in;
static u64 g_q_dropped, g_q_disjoint, g_q_none;
static int g_sp_in_game, g_sp_open;
static u32 g_q_at;
static struct {
    ID3D11Query *dis, *ts[3];
    int state; /* 0 free, 1 open, 2 ended: waiting for its data */
} g_q[SP_RING];

u64 draw_split_now(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (u64)t.QuadPart;
}

int draw_split_on(void)
{
    if (g_draw_split < 0) {
        char v[16];
        DWORD n = GetEnvironmentVariableA("PG_DRAW_SPLIT", v, sizeof v);
        LARGE_INTEGER f;
        g_draw_split = n && !(n == 1 && v[0] == '0');
        if (g_draw_split && (g_sp_hist = calloc(SP_N, sizeof *g_sp_hist)) == NULL) g_draw_split = 0;
        QueryPerformanceFrequency(&f);
        g_sp_freq = (u64)f.QuadPart;
        if (g_draw_split) port_log("native: PG_DRAW_SPLIT on: the frame's spans are timed (draw split: at exit)\n");
    }
    return g_draw_split > 0;
}

static void sp_add_us(int s, u64 us)
{
    u64 b = us / PS_BUCKET_US;
    g_sp_hist[s][b < PS_BUCKETS ? b : PS_BUCKETS - 1u]++;
    g_sp_n[s]++;
    if (us > g_sp_worst[s]) g_sp_worst[s] = us;
}

static void sp_add(int s, u64 ticks) { sp_add_us(s, ticks * 1000000u / g_sp_freq); }

static void q_collect(void)
{
    for (u32 i = 0; i < SP_RING; i++) {
        D3D11_QUERY_DATA_TIMESTAMP_DISJOINT dj;
        UINT64 t[3];
        int ok = 1;
        if (g_q[i].state != 2) continue;
        /* flags 0: a run that never presents (headless) still gets its queries sent to the GPU */
        if (ID3D11DeviceContext_GetData(g_gfx.ctx, (ID3D11Asynchronous *)g_q[i].dis, &dj, sizeof dj, 0) != S_OK)
            continue;
        for (int k = 0; k < 3 && ok; k++)
            ok = ID3D11DeviceContext_GetData(g_gfx.ctx, (ID3D11Asynchronous *)g_q[i].ts[k], &t[k], sizeof t[k],
                                             D3D11_ASYNC_GETDATA_DONOTFLUSH) == S_OK;
        if (!ok) continue;
        g_q[i].state = 0;
        if (dj.Disjoint || !dj.Frequency || t[1] < t[0] || t[2] < t[1]) {
            g_q_disjoint++;
            continue;
        }
        sp_add_us(SP_GPU, (t[1] - t[0]) * 1000000u / dj.Frequency);
        sp_add_us(SP_GPU_PRESENT, (t[2] - t[1]) * 1000000u / dj.Frequency);
    }
}

static void q_end(int k)
{
    u32 i = g_q_at % SP_RING;
    if (g_q[i].state != 1) return;
    ID3D11DeviceContext_End(g_gfx.ctx, (ID3D11Asynchronous *)g_q[i].ts[k]);
    if (k == 2) {
        ID3D11DeviceContext_End(g_gfx.ctx, (ID3D11Asynchronous *)g_q[i].dis);
        g_q[i].state = 2;
        g_q_at++;
    }
}

static void q_begin(void)
{
    u32 i;
    D3D11_QUERY_DESC d = {D3D11_QUERY_TIMESTAMP_DISJOINT, 0};
    if (!g_gfx.ctx || !g_gfx.dev) return;
    if (g_q[g_q_at % SP_RING].state == 1) q_end(1), q_end(2); /* a frame never presented: closed as it stands */
    q_collect();
    i = g_q_at % SP_RING;
    if (g_q[i].state == 2) g_q_dropped++, g_q[i].state = 0; /* not back from the GPU after a whole ring of frames */
    if (!g_q[i].dis) {
        int ok = SUCCEEDED(ID3D11Device_CreateQuery(g_gfx.dev, &d, &g_q[i].dis));
        d.Query = D3D11_QUERY_TIMESTAMP;
        for (int k = 0; k < 3 && ok; k++) ok = SUCCEEDED(ID3D11Device_CreateQuery(g_gfx.dev, &d, &g_q[i].ts[k]));
        if (!ok) {
            g_q_none++;
            return;
        }
    }
    ID3D11DeviceContext_Begin(g_gfx.ctx, (ID3D11Asynchronous *)g_q[i].dis);
    ID3D11DeviceContext_End(g_gfx.ctx, (ID3D11Asynchronous *)g_q[i].ts[0]);
    g_q[i].state = 1;
}

static void sp_game_end(u64 t)
{
    if (g_sp_in_game) sp_add(SP_GAME, t - g_sp_frame_at), g_sp_in_game = 0;
}

void pg_native_split_report(void)
{
    char line[900];
    int n;
    if (!g_sp_hist || !g_sp_n[SP_FRAME]) return;
    n = sprintf_s(line, sizeof line, "draw split: %llu frames (p50/p99/worst ms)", g_sp_n[SP_FRAME] + 1u);
    for (int s = 0; s < SP_N; s++)
        n += sprintf_s(line + n, sizeof line - (size_t)n, " %s %.2f/%.2f/%.2f", k_sp_names[s],
                       play_stats_percentile(g_sp_hist[s], g_sp_n[s], 0.5), play_stats_percentile(g_sp_hist[s], g_sp_n[s], 0.99),
                       (double)g_sp_worst[s] / 1000.0);
    port_log("%s; gpu samples %llu, dropped %llu, disjoint %llu%s\n", line, g_sp_n[SP_GPU], g_q_dropped, g_q_disjoint,
             g_q_none ? ", no queries" : "");
}

void draw_split_frame(void)
{
    u64 t = draw_split_now();
    if (g_sp_present_end) sp_add(SP_OUTSIDE, t - g_sp_present_end);
    if (g_sp_last_frame) sp_add(SP_FRAME, t - g_sp_last_frame);
    g_sp_last_frame = g_sp_frame_at = t;
    g_sp_in_game = g_sp_open = 1;
    g_sp_ticks_in = 0;
    q_begin();
}

void draw_split_copied(u64 since)
{
    sp_game_end(since);
    sp_add(SP_COPY, draw_split_now() - since);
}

void draw_split_game_done(void)
{
    if (!g_sp_open) return;
    sp_game_end(draw_split_now());
    sp_add(SP_TICKS, g_sp_ticks_in);
    g_sp_open = 0;
    q_end(1);
}

void draw_split_presented(u64 since)
{
    sp_add(SP_PRESENT, draw_split_now() - since);
    q_end(2);
}

void draw_split_waited(void) { g_sp_present_end = draw_split_now(); }

void draw_split_tick(u64 qpc)
{
    if (g_sp_open) g_sp_ticks_in += qpc;
}

void draw_split_release(void)
{
    for (u32 i = 0; i < SP_RING; i++) {
        if (g_q[i].dis) ID3D11Query_Release(g_q[i].dis);
        for (int k = 0; k < 3; k++)
            if (g_q[i].ts[k]) ID3D11Query_Release(g_q[i].ts[k]);
    }
    memset(g_q, 0, sizeof g_q);
    if (g_draw_split > 0) g_draw_split = 0; /* the device is gone: nothing more to time on it (the report still logs) */
}

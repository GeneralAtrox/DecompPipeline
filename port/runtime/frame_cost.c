/* --present-cost on: where a scene-blended game's in-between frames spend their time on the thread that draws them
 * (frame_thread.c), logged every 10 s of real time and at exit. In a frame: pairing the scene's triangles with the
 * scene before (once a render), blending the vertices, the rest of the draw
 * (the Direct3D calls and the frame's resolve, what is left of the frame's cost), and the present. Between two frames:
 * giving way to the game's thread for gfx_lock, waiting for the swap chain to take a frame, and having nothing to draw.
 * Each line gives the mean of every frame, and the mean of the late frames (a slot of 144 missed: 10.42 ms or more
 * after the one before, as the play verdict counts it) with what the time between went on. A measurement only: no frame changes. Off by
 * default: a call is one test of a flag. */
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"
#include "frame_cost.h"

#define REPORT_US 10000000u
#define LATE_US 10416u /* 1.5 slots of 144: the gap spans two to the nearest, so one was missed */

int g_xcost;

typedef struct {
    u64 n, part[XC_N], draw, gap, worst;
} Sum;

static u64 g_part[XC_N]; /* since the last frame */
static u64 g_last, g_from;
static Sum g_all, g_late, g_total_all, g_total_late;

u64 xcost_now(void)
{
    LARGE_INTEGER t, f;
    QueryPerformanceCounter(&t);
    QueryPerformanceFrequency(&f);
    return (u64)t.QuadPart * 1000000u / (u64)f.QuadPart;
}

int xcost_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--present-cost") || !value) return 0;
    g_xcost = !strcmp(value, "on");
    return 2;
}

void xcost_add_slow(int part, u64 us) { g_part[part] += us; }

static void add(Sum *s, u64 draw, u64 gap)
{
    s->n++;
    for (int i = 0; i < XC_N; i++) s->part[i] += g_part[i];
    s->draw += draw;
    s->gap += gap;
    if (gap > s->worst) s->worst = gap;
}

static void merge(Sum *into, const Sum *s)
{
    into->n += s->n;
    for (int i = 0; i < XC_N; i++) into->part[i] += s->part[i];
    into->draw += s->draw;
    into->gap += s->gap;
    if (s->worst > into->worst) into->worst = s->worst;
}

static void line(const char *what, const Sum *s)
{
    double n = s->n ? (double)s->n : 1.0;
    port_log("frame_extra: %s %llu in-between frames, mean ms: gap %.2f (worst %.2f); in the frame pair %.2f, blend %.2f, draw %.2f, "
             "present %.2f; before it lock %.2f, swap chain %.2f, nothing to draw %.2f\n",
             what, s->n, s->gap / n / 1000.0, s->worst / 1000.0, s->part[XC_PAIR] / n / 1000.0, s->part[XC_BLEND] / n / 1000.0, s->draw / n / 1000.0,
             s->part[XC_PRESENT] / n / 1000.0, s->part[XC_LOCK] / n / 1000.0, s->part[XC_SWAP] / n / 1000.0,
             s->part[XC_IDLE] / n / 1000.0);
}

void xcost_frame(u64 total_us)
{
    u64 now, gap, inner;
    if (!g_xcost) return;
    g_part[XC_PRESENT] += gfx_last_present_us();
    now = xcost_now();
    gap = g_last ? now - g_last : 0u;
    inner = g_part[XC_PAIR] + g_part[XC_BLEND] + g_part[XC_PRESENT];
    if (g_last) {
        add(&g_all, total_us > inner ? total_us - inner : 0u, gap);
        if (gap > LATE_US) add(&g_late, total_us > inner ? total_us - inner : 0u, gap);
    }
    memset(g_part, 0, sizeof g_part);
    g_last = now;
    if (!g_from) g_from = now;
    if (now - g_from < REPORT_US) return;
    line("last 10 s:", &g_all);
    if (g_late.n) line("last 10 s, late (a slot missed):", &g_late);
    merge(&g_total_all, &g_all);
    merge(&g_total_late, &g_late);
    memset(&g_all, 0, sizeof g_all);
    memset(&g_late, 0, sizeof g_late);
    g_from = now;
}

void xcost_report(void)
{
    if (!g_xcost) return;
    merge(&g_total_all, &g_all);
    merge(&g_total_late, &g_late);
    line("in all:", &g_total_all);
    line("in all, late (a slot missed):", &g_total_late);
}

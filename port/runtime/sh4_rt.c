/* The SH-4 instructions readable C uses through rd_rt.h, for the port: the same arithmetic as the pipeline's
 * readable-C runtime (single-precision SSE operations in the SH-4's order), with the FPU state as plain globals. */
#include <float.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <xmmintrin.h>
#include "port.h"

#define MODE_FR 0x00200000u
#define MODE_SZ 0x00100000u
#define MODE_PR 0x00080000u

static u32 g_xf[16];
static u32 g_fr[16];
static u32 g_mode;  /* FPSCR.FR | SZ | PR in their FPSCR bit positions */
static u32 g_gs[7]; /* r8..r14 */
static u32 (*g_fsca)[2];

static u32 f2u(f32 f) { u32 u; memcpy(&u, &f, 4); return u; }
static f32 u2f(u32 u) { f32 f; memcpy(&f, &u, 4); return f; }

s32 sh4_ftrc(f32 x)
{
    if (x != x) return 0;
    double d = x;
    if (d >= 9223372036854775807.0) return (s32)0xffffffffu;
    if (d <= -9223372036854775808.0) return 0;
    return (s32)(u32)(unsigned long long)(long long)d;
}

f32 sh4_fsrra(f32 x) { return _mm_cvtss_f32(_mm_div_ss(_mm_set_ss(1.0f), _mm_sqrt_ss(_mm_set_ss(x)))); }

f32 sh4_fsqrt_rm(f32 x) { return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(x))); }

void sh4_fsca(u32 fpul, f32 *s, f32 *c)
{
    u32 i = fpul & 0xffffu;
    *s = u2f(g_fsca[i][0]);
    *c = u2f(g_fsca[i][1]);
}

static __m128 mul(f32 a, f32 b) { return _mm_mul_ss(_mm_set_ss(a), _mm_set_ss(b)); }

f32 sh4_fipr(const f32 a[4], const f32 b[4])
{
    __m128 t = mul(a[0], b[0]);
    t = _mm_add_ss(t, mul(a[1], b[1]));
    t = _mm_add_ss(t, mul(a[2], b[2]));
    t = _mm_add_ss(t, mul(a[3], b[3]));
    return _mm_cvtss_f32(t);
}

void sh4_ftrv(f32 v[4])
{
    f32 out[4];
    for (int row = 0; row < 4; row++) {
        __m128 t = mul(u2f(g_xf[row]), v[0]);
        t = _mm_add_ss(t, mul(u2f(g_xf[row + 4]), v[1]));
        t = _mm_add_ss(t, mul(u2f(g_xf[row + 8]), v[2]));
        t = _mm_add_ss(t, mul(u2f(g_xf[row + 12]), v[3]));
        out[row] = _mm_cvtss_f32(t);
    }
    for (int i = 0; i < 4; i++) v[i] = out[i];
}

f32 sh4_xf(int i) { return (i >= 0 && i < 16) ? u2f(g_xf[i]) : 0.0f; }

void sh4_set_xf(int i, f32 v)
{
    if (i >= 0 && i < 16) g_xf[i] = f2u(v);
}

f32 sh4_fr(int i) { return (i >= 0 && i < 16) ? u2f(g_fr[i]) : 0.0f; }

void sh4_set_fr(int i, f32 v)
{
    if (i >= 0 && i < 16) g_fr[i] = f2u(v);
}

void sh4_frchg(void)
{
    if (g_mode & MODE_PR) return;
    for (int i = 0; i < 16; i++) {
        u32 t = g_fr[i];
        g_fr[i] = g_xf[i];
        g_xf[i] = t;
    }
    g_mode ^= MODE_FR;
}

void sh4_fschg(void)
{
    if (!(g_mode & MODE_PR)) g_mode ^= MODE_SZ;
}

void sh4_fpchg(void) { g_mode ^= MODE_PR; }

void sh4_ftrv_fv(int n)
{
    if (n < 0 || n > 12 || (n & 3)) return;
    f32 v[4];
    for (int i = 0; i < 4; i++) v[i] = u2f(g_fr[n + i]);
    sh4_ftrv(v);
    for (int i = 0; i < 4; i++) g_fr[n + i] = f2u(v[i]);
}

void sh4_fipr_fv(int m, int n)
{
    if (m < 0 || m > 12 || (m & 3) || n < 0 || n > 12 || (n & 3)) return;
    f32 a[4], b[4];
    for (int i = 0; i < 4; i++) {
        a[i] = u2f(g_fr[m + i]);
        b[i] = u2f(g_fr[n + i]);
    }
    g_fr[n + 3] = f2u(sh4_fipr(a, b));
}

u32 sh4_r(int i) { return (i >= 8 && i < 15) ? g_gs[i - 8] : 0; }

void sh4_set_r(int i, u32 v)
{
    if (i >= 8 && i < 15) g_gs[i - 8] = v;
}

static f32 round_f32(double x) { return (f32)x; } /* to nearest: the table is built before the game's mode is set */

/* fsca's table as the patched SLEIGH computes it: angle = f32(f32(2pi_f32 * i) / 65536), then f32 sin and cos. */
int sh4_rt_init(const char *check_table)
{
    g_fsca = (u32(*)[2])malloc(65536 * sizeof *g_fsca);
    if (!g_fsca) return 0;
    f32 two_pi = u2f(0x40C90FDBu);
    for (u32 i = 0; i < 65536; i++) {
        f32 angle = round_f32((double)round_f32((double)two_pi * (double)i) / 65536.0);
        g_fsca[i][0] = f2u(round_f32(sin((double)angle)));
        g_fsca[i][1] = f2u(round_f32(cos((double)angle)));
    }
    if (!check_table) return 1;
    FILE *f = NULL;
    if (fopen_s(&f, check_table, "rb") != 0 || !f) {
        port_log("fsca: cannot open %s\n", check_table);
        return 0;
    }
    u32 ref[2], diff = 0, i = 0;
    for (; i < 65536 && fread(ref, sizeof ref, 1, f) == 1; i++) diff += ref[0] != g_fsca[i][0] || ref[1] != g_fsca[i][1];
    fclose(f);
    port_log("fsca: computed table vs %s: %u of %u entries differ\n", check_table, diff, i);
    return diff == 0 && i == 65536;
}

/* FPSCR 0x00040001 (target.json fpscr_entry): DN=1 (denormals flush to zero: DAZ|FTZ), RM=1 (round toward zero).
 * The x87 too: 32-bit code converts integers to float on it. */
void sh4_rt_set_fpu_mode(void)
{
    unsigned int x87 = 0;
    _mm_setcsr((_mm_getcsr() & ~0x6000u) | 0x8040u | 0x6000u);
    __control87_2(_RC_CHOP, _MCW_RC, &x87, NULL);
}

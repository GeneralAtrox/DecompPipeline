/* SH-4 instruction helpers for the readable-C runtime: identical to runtime/tc_rt.c (keep them in step). */
#include <stdlib.h>
#include <string.h>
#include <xmmintrin.h>
#include "rd_internal.h"

#define MODE_FR 0x00200000u
#define MODE_SZ 0x00100000u
#define MODE_PR 0x00080000u

static u32 g_xf[16];
static u32 g_fr[16]; /* fr0..fr15 (fr12..fr15: line H; all of them: line Q) */
static u32 g_mode;   /* FPSCR.FR | SZ | PR in their FPSCR bit positions */
static int g_fpu;    /* line Q given: the whole FPU is state */
static u32 g_gs[7];  /* r8..r14: runtime state (line G), as in tc_rt.c */
static u32 (*g_fsca)[2]; /* 65536 entries of {sin bits, cos bits} */

static u32 f2u(f32 f) { u32 u; memcpy(&u, &f, 4); return u; }
static f32 u2f(u32 u) { f32 f; memcpy(&f, &u, 4); return f; }

/* The SH-4's ftrc: truncation toward zero, saturating at 0x7fffffff above the int range and 0x80000000 below it;
 * a NaN gives 0x80000000. The oracle's FPUL is the same (TcFpu: Ghidra's p-code trunc wraps a 64-bit cast). */
s32 sh4_ftrc(f32 x)
{
    if (x != x) return (s32)0x80000000u;
    double d = x;
    if (d >= 2147483648.0) return 0x7fffffff;
    if (d <= -2147483648.0) return (s32)0x80000000u;
    return (s32)d;
}

/* The float helpers compute in SSE registers by intrinsics: a 32-bit build would otherwise compute on the x87 stack
 * (an f32 result is returned in st(0)), which ignores MXCSR (FPSCR.DN, FPSCR.RM), and x86 sqrtf is the CRT's double
 * sqrt. The 64-bit build of tc_rt.c / tc_fpu.c does exactly these single-precision SSE operations in this order. */
f32 sh4_fsrra(f32 x)
{
    return _mm_cvtss_f32(_mm_div_ss(_mm_set_ss(1.0f), _mm_sqrt_ss(_mm_set_ss(x))));
}

/* fsqrt: the SSE root (sqrtss), so MXCSR decides it exactly as FPSCR does on the SH-4: RC from FPSCR.RM (TcRound: the
 * correctly rounded root, toward zero when RM=1), DAZ|FTZ from FPSCR.DN. A negative operand gives a NaN (the judge
 * compares NaNs as equal). */
f32 sh4_fsqrt_rm(f32 x)
{
    return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(x)));
}

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

/* r8..r14 as runtime state (index 8..14): entry values from line G, exit values compared (line GH), and a callee
 * reading them is compared on them at the call (call record fields 29..35). As tc_rt.c. */
u32 sh4_r(int i) { return (i >= 8 && i < 15) ? g_gs[i - 8] : 0; }

void sh4_set_r(int i, u32 v)
{
    if (i >= 8 && i < 15) g_gs[i - 8] = v;
}

/* ---------------- runner-only ---------------- */

int rd_sh4_init(const char *fsca_table)
{
    FILE *f = NULL;
    if (fopen_s(&f, fsca_table, "rb") != 0 || !f) return 0;
    g_fsca = (u32(*)[2])malloc(65536 * sizeof *g_fsca);
    size_t n = g_fsca ? fread(g_fsca, sizeof *g_fsca, 65536, f) : 0;
    int extra = fgetc(f) != EOF;
    fclose(f);
    return n == 65536 && !extra;
}

/* TcLive, as tc_rt.c: the callees that change r8..r14 (header lines K) and the vector's draws (line A). */
#define RD_MAX_LIVE 256
static struct { u32 target, reg, n, d[4]; } g_live[RD_MAX_LIVE];
static u32 g_nlive;
static u32 g_draws[16];

void rd_sh4_reset(void)
{
    memset(g_xf, 0, sizeof g_xf);
    memset(g_fr, 0, sizeof g_fr);
    memset(g_gs, 0, sizeof g_gs);
    memset(g_draws, 0, sizeof g_draws);
    g_mode = 0;
    g_fpu = 0;
}

void tcrt_set_xf(const u32 bits[16]) { memcpy(g_xf, bits, sizeof g_xf); }

void tcrt_set_fh(const u32 bits[4]) { memcpy(g_fr + 12, bits, 4 * sizeof(u32)); }

void tcrt_set_gs(const u32 bits[7]) { memcpy(g_gs, bits, sizeof g_gs); }

void tcrt_add_live(u32 target, u32 reg, u32 n, const u32 d[4])
{
    if (g_nlive >= RD_MAX_LIVE || reg < 8 || reg > 14 || n > 4) return;
    g_live[g_nlive].target = tcrt_fold(target);
    g_live[g_nlive].reg = reg;
    g_live[g_nlive].n = n;
    memcpy(g_live[g_nlive].d, d, sizeof g_live[0].d);
    g_nlive++;
}

void tcrt_set_draws(const u32 draws[16]) { memcpy(g_draws, draws, sizeof g_draws); }

void rd_sh4_live(u32 target, u32 k)
{
    u32 draw = g_draws[k % 16];
    for (u32 i = 0; i < g_nlive; i++) {
        if (g_live[i].target != target) continue;
        u32 n = g_live[i].n;
        g_gs[g_live[i].reg - 8] += n == 1 ? g_live[i].d[0] : n ? g_live[i].d[draw % n] : 0x20u * (1u + draw % 3u);
    }
}

void tcrt_report_gs(FILE *out)
{
    fprintf(out, "GH");
    for (int k = 0; k < 7; k++) fprintf(out, " %08x", g_gs[k]);
    fputc('\n', out);
}

/* As tcrt_fpu_log (tc_fpu.c): with line Q the call's fr4..fr11 are the FPU registers, not the C arguments. */
void rd_sh4_log(u32 *rec, const u32 fr_args[8])
{
    for (int i = 0; i < 8; i++) rec[5 + i] = g_fpu ? g_fr[4 + i] : fr_args[i];
    for (int i = 0; i < 4; i++) rec[21 + i] = g_fr[12 + i];
    for (int i = 0; i < 7; i++) rec[29 + i] = g_gs[i];
    if (!g_fpu) return;
    for (int i = 0; i < 4; i++) rec[36 + i] = g_fr[i];
    for (int i = 0; i < 16; i++) rec[40 + i] = g_xf[i];
    rec[56] = g_mode;
}

int rd_sh4_fpu_state(void) { return g_fpu; }

void tcrt_report_fh(FILE *out) { fprintf(out, "FH %08x %08x %08x %08x\n", g_fr[12], g_fr[13], g_fr[14], g_fr[15]); }

void rd_sh4_result(u32 bits) { g_fr[0] = bits; }

void rd_sh4_set_fq(const u32 bits[16])
{
    memcpy(g_fr, bits, sizeof g_fr);
    g_fpu = 1;
}

void rd_sh4_report_fq(FILE *out)
{
    fprintf(out, "QH");
    for (int k = 0; k < 16; k++) fprintf(out, " %08x", g_fr[k]);
    fprintf(out, " %08x\n", g_mode);
}

void rd_sh4_report(FILE *out)
{
    fprintf(out, "XF");
    for (int k = 0; k < 16; k++) fprintf(out, " %08x", g_xf[k]);
    fputc('\n', out);
}

/* DecompPipeline Dreamcast SH-4 runtime: the FPU registers (fr0..fr15, the back bank xf0..xf15 = XMTRX, the FPSCR
 * mode bits FR, SZ and PR) and the helpers that use them. tc_rt.c logs and reports them through tc_rt_internal.h.
 *
 * A function that switches FPU modes (meta fpu_state, vectors.txt line Q) has the whole FPU as state: fr0..fr15 are
 * set at entry, compared at exit with the mode bits (line QH) and at every call (call log fields 36..56). Otherwise
 * only fr12..fr15 (line H) and XMTRX (line X) are state, as before. */
#include <string.h>
#include "tc_rt_internal.h"

#define MODE_FR 0x00200000u
#define MODE_SZ 0x00100000u
#define MODE_PR 0x00080000u

static u32 g_fr[16]; /* the current bank: fr0..fr15 */
static u32 g_xf[16]; /* the back bank: xf0..xf15 (XMTRX) */
static u32 g_mode;   /* FPSCR.FR | FPSCR.SZ | FPSCR.PR, in their FPSCR bit positions (TcOracle.modeWord) */
static int g_fpu;    /* line Q given: the whole FPU is state */

static u32 f2u(f32 f) { u32 u; memcpy(&u, &f, 4); return u; }
static f32 u2f(u32 u) { f32 f; memcpy(&f, &u, 4); return f; }

f32 sh4_fipr(const f32 a[4], const f32 b[4])
{
    f32 t = a[0] * b[0];
    t = t + a[1] * b[1];
    t = t + a[2] * b[2];
    t = t + a[3] * b[3];
    return t;
}

void sh4_ftrv(f32 v[4])
{
    f32 out[4];
    for (int row = 0; row < 4; row++) {
        f32 t = u2f(g_xf[row]) * v[0];
        t = t + u2f(g_xf[row + 4]) * v[1];
        t = t + u2f(g_xf[row + 8]) * v[2];
        t = t + u2f(g_xf[row + 12]) * v[3];
        out[row] = t;
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
    if (g_mode & MODE_PR) return; /* as the hardware: frchg needs FPSCR.PR = 0 */
    for (int i = 0; i < 16; i++) {
        u32 t = g_fr[i];
        g_fr[i] = g_xf[i];
        g_xf[i] = t;
    }
    g_mode ^= MODE_FR;
}

void sh4_fschg(void)
{
    if (!(g_mode & MODE_PR)) g_mode ^= MODE_SZ; /* as the hardware: fschg needs FPSCR.PR = 0 */
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

/* ---------------- runner-only ---------------- */

void tcrt_fpu_reset(void)
{
    memset(g_fr, 0, sizeof g_fr);
    memset(g_xf, 0, sizeof g_xf);
    g_mode = 0;
    g_fpu = 0;
}

void tcrt_set_xf(const u32 bits[16]) { memcpy(g_xf, bits, sizeof g_xf); }

void tcrt_set_fh(const u32 bits[4]) { memcpy(g_fr + 12, bits, 4 * sizeof(u32)); }

void tcrt_report_fh(FILE *out) { fprintf(out, "FH %08x %08x %08x %08x\n", g_fr[12], g_fr[13], g_fr[14], g_fr[15]); }

void tcrt_set_fq(const u32 bits[16])
{
    memcpy(g_fr, bits, sizeof g_fr);
    g_fpu = 1;
}

int tcrt_fpu_state(void) { return g_fpu; }

void tcrt_fpu_result(u32 bits) { g_fr[0] = bits; }

void tcrt_fpu_log(u32 *rec, const TcArgs *args)
{
    for (int i = 0; i < 8; i++) rec[5 + i] = g_fpu ? g_fr[4 + i] : args ? f2u(args->fr[i]) : 0;
    for (int i = 0; i < 4; i++) rec[21 + i] = g_fr[12 + i];
    if (!g_fpu) return;
    for (int i = 0; i < 4; i++) rec[36 + i] = g_fr[i];
    for (int i = 0; i < 16; i++) rec[40 + i] = g_xf[i];
    rec[56] = g_mode;
}

void tcrt_report_xf(FILE *out)
{
    fprintf(out, "XF");
    for (int k = 0; k < 16; k++) fprintf(out, " %08x", g_xf[k]);
    fputc('\n', out);
}

void tcrt_report_fq(FILE *out)
{
    fprintf(out, "QH");
    for (int k = 0; k < 16; k++) fprintf(out, " %08x", g_fr[k]);
    fprintf(out, " %08x\n", g_mode);
}

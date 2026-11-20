/* The exit record of the translator's exit-state form (tc_rt.h tcx_*, xenontranslate/exit_form.py): the final value of
 * every register the C wrote, and the entry value of every other one, printed after each vector when the runner runs
 * with "exit" (x360_compare.py: XS, XF, XC, XV). The entry values are those the oracle starts the original with
 * (TcOracle.setEntry): r1 the stack pointer, LR the return sentinel, r3..r10 and f1..f13 the vector's, r12 and the CR
 * fields a function reads before setting them the vector's entry seeds (vectors.txt A), r14..r31, f14..f31 and CR2..CR4
 * the exit-state run's seeds (vectors.txt Q, G, K; runner.c puts them over A's CR2..CR4 in that run), everything else 0. r13 has the project's fixed value
 * in the oracle; the C sets it at entry, so it is always a written register. */
#include <string.h>
#include "tc_rt_internal.h"

#define TCX_SENTINEL 0x700ff000u /* target.json abi.return_sentinel */

int tcx_on;

static struct {
    u64 r[32], f[32], ctr, lr;
    u32 cr, xer[3], fpscr, v[128][4];
    u64 in_r[32], in_f[32];
    u32 in_cr;
    u8 wr[32], wf[32], wv[128], wcr[32], wxer[3], wctr, wlr, wfpscr;
} x;

void tcx_begin(const u64 r[8], const u64 f[TCRT_CALL_FLOATS], const u64 nv[18], const u64 fnv[18], u32 cr, u64 r12)
{
    memset(&x, 0, sizeof x);
    x.in_r[1] = TCRT_SP;
    x.in_r[12] = r12;
    for (int i = 0; i < 8; i++) x.in_r[3 + i] = r[i];
    for (int i = 0; i < TCRT_CALL_FLOATS; i++) x.in_f[1 + i] = f[i];
    for (int i = 0; i < 18; i++) {
        x.in_r[14 + i] = nv[i];
        x.in_f[14 + i] = fnv[i];
    }
    x.in_cr = cr;
}

u64 tcx_in_r(u32 n) { return n < 32 ? x.in_r[n] : 0; }

f64 tcx_in_f(u32 n)
{
    f64 d = 0.0;
    if (n < 32) memcpy(&d, &x.in_f[n], 8);
    return d;
}

u32 tcx_in_cr(u32 bit) { return bit < 32 ? (x.in_cr >> (31 - bit)) & 1u : 0; }

void tcx_r(u32 n, u64 value)
{
    if (n >= 32) return;
    x.r[n] = value;
    x.wr[n] = 1;
}

void tcx_f(u32 n, f64 value)
{
    if (n >= 32) return;
    memcpy(&x.f[n], &value, 8);
    x.wf[n] = 1;
}

void tcx_cr(u32 bit, u32 value)
{
    if (bit >= 32) return;
    x.cr = (x.cr & ~(1u << (31 - bit))) | ((value & 1u) << (31 - bit));
    x.wcr[bit] = 1;
}

void tcx_xer(u32 k, u32 value)
{
    if (k >= 3) return;
    x.xer[k] = value & 1u;
    x.wxer[k] = 1;
}

void tcx_ctr(u64 value)
{
    x.ctr = value;
    x.wctr = 1;
}

void tcx_lr(u64 value)
{
    x.lr = value;
    x.wlr = 1;
}

void tcx_fpscr(u32 value)
{
    x.fpscr = value;
    x.wfpscr = 1;
}

void tcx_v(u32 n, const u32 *w)
{
    if (n >= 128) return;
    memcpy(x.v[n], w, 16);
    x.wv[n] = 1;
}

void tcx_report(FILE *out)
{
    fputs("XS", out);
    for (int i = 0; i < 32; i++) fprintf(out, " %016llx", (unsigned long long)(x.wr[i] ? x.r[i] : x.in_r[i]));
    fputs("\nXF", out);
    for (int i = 0; i < 32; i++) fprintf(out, " %016llx", (unsigned long long)(x.wf[i] ? x.f[i] : x.in_f[i]));
    u32 cr = 0;
    for (int b = 0; b < 32; b++) {
        u32 m = 1u << (31 - b);
        cr |= (x.wcr[b] ? x.cr : x.in_cr) & m;
    }
    fprintf(out, "\nXC %08x %x %x %x %016llx %016llx %08x\n", cr, x.wxer[0] ? x.xer[0] : 0, x.wxer[1] ? x.xer[1] : 0,
            x.wxer[2] ? x.xer[2] : 0, (unsigned long long)(x.wctr ? x.ctr : 0),
            (unsigned long long)(x.wlr ? x.lr : TCX_SENTINEL), x.wfpscr ? x.fpscr : 0);
    for (int n = 0; n < 128; n++) {
        if (x.wv[n]) fprintf(out, "XV %d %08x %08x %08x %08x\n", n, x.v[n][0], x.v[n][1], x.v[n][2], x.v[n][3]);
    }
}

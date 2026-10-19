/* xemu: the SH-4 FPU instructions, from the SH-4 software manual.
 *
 * Register file as the hardware has it: DRn is FRn (the upper word) and FRn+1; with FPSCR.SZ = 1 an fmov moves a
 * pair, an even register field naming DRn, an odd one XDn-1 (xfn-1, xfn), the even register at the lower address.
 * Arithmetic is the host's IEEE single or double operation; the runtime's MXCSR gives FPSCR.DN (DAZ|FTZ) and the
 * rounding (RC from line P, changed by an lds to FPSCR). ftrc saturates as the hardware does: 0x7fffffff above the
 * range, 0x80000000 below it and for a NaN.
 *
 * Where the hardware's rounding is not IEEE, xemu takes the oracle's documented model, so these are not cross-checked
 * (only the registers and order they use are): fmac rounds the product, then the sum (TcFpu); fipr and ftrv sum
 * in element order 0..3, each step rounded; fsrra is 1/sqrt in single precision; fsca reads the runtime's table. */
#include <math.h>
#include <string.h>
#include <xmmintrin.h>
#include "xe.h"

#define FN ((op >> 8) & 15u)
#define FM ((op >> 4) & 15u)
#define SIGN 0x80000000u

static f32 F(u32 u)
{
    f32 f;
    memcpy(&f, &u, 4);
    return f;
}

static u32 U(f32 f)
{
    u32 u;
    memcpy(&u, &f, 4);
    return u;
}

static double D(const Xe *x, u32 n)
{
    unsigned long long b = (unsigned long long)x->fr[n] << 32 | x->fr[n + 1];
    double d;
    memcpy(&d, &b, 8);
    return d;
}

static void set_d(Xe *x, u32 n, double d)
{
    unsigned long long b;
    memcpy(&b, &d, 8);
    x->fr[n] = (u32)(b >> 32);
    x->fr[n + 1] = (u32)b;
}

static u32 *pair(Xe *x, u32 r) { return (r & 1) ? &x->xf[r - 1] : &x->fr[r]; }

static void load(Xe *x, u32 n, u32 a, int sz)
{
    if (!sz) {
        x->fr[n] = xe_rd(a, 4);
        return;
    }
    u32 *p = pair(x, n);
    p[0] = xe_rd(a, 4);
    p[1] = xe_rd(a + 4, 4);
}

static void store(Xe *x, u32 m, u32 a, int sz)
{
    if (!sz) {
        xe_wr(a, x->fr[m], 4);
        return;
    }
    const u32 *p = pair(x, m);
    xe_wr(a, p[0], 4);
    xe_wr(a + 4, p[1], 4);
}

int xe_model_ftrc;

/* Ghidra's p-code ftrc (trunc): a saturating 64-bit cast, then the low 32 bits. The oracle before r15 did this. */
static u32 ghidra_ftrc(double d)
{
    if (d != d) return 0;
    if (d >= 9223372036854775807.0) return 0xffffffffu;
    if (d <= -9223372036854775808.0) return 0;
    return (u32)(unsigned long long)(long long)d;
}

static u32 ftrc(double d)
{
    if (xe_model_ftrc == 1 || (xe_model_ftrc == 2 && d != d)) return ghidra_ftrc(d);
    if (d != d) return 0x80000000u;
    if (d >= 2147483648.0) return 0x7fffffffu;
    if (d <= -2147483649.0) return 0x80000000u;
    return (u32)(s32)d;
}

static f32 sqrt_rm(f32 v) { return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(v))); }

static void need(const Xe *x, int ok, u16 op)
{
    if (!ok) xe_fail("an FPU instruction in a mode xemu does not run", x->pc, op);
}

static void arith(Xe *x, u16 op, int pr)
{
    u32 n = FN, m = FM;
    if (pr) {
        need(x, !((n | m) & 1), op);
        double a = D(x, n), b = D(x, m), r = 0;
        switch (op & 15) {
        case 0: r = a + b; break;
        case 1: r = a - b; break;
        case 2: r = a * b; break;
        case 3: r = a / b; break;
        case 4: x->t = a == b; return;
        case 5: x->t = a > b; return;
        }
        set_d(x, n, r);
        return;
    }
    f32 a = F(x->fr[n]), b = F(x->fr[m]), r = 0;
    switch (op & 15) {
    case 0: r = a + b; break;
    case 1: r = a - b; break;
    case 2: r = a * b; break;
    case 3: r = a / b; break;
    case 4: x->t = a == b; return;
    case 5: x->t = a > b; return;
    }
    x->fr[n] = U(r);
}

static void fipr(Xe *x, u32 vm, u32 vn)
{
    const u32 *a = x->fr + vm, *b = x->fr + vn;
    f32 t = F(a[0]) * F(b[0]);
    t = t + F(a[1]) * F(b[1]);
    t = t + F(a[2]) * F(b[2]);
    t = t + F(a[3]) * F(b[3]);
    x->fr[vn + 3] = U(t);
}

static void ftrv(Xe *x, u32 vn)
{
    f32 v[4], out[4];
    for (int i = 0; i < 4; i++) v[i] = F(x->fr[vn + (u32)i]);
    for (int row = 0; row < 4; row++) { /* XMTRX is column-major: row r is xf r, r+4, r+8, r+12 */
        f32 t = F(x->xf[row]) * v[0];
        t = t + F(x->xf[row + 4]) * v[1];
        t = t + F(x->xf[row + 8]) * v[2];
        t = t + F(x->xf[row + 12]) * v[3];
        out[row] = t;
    }
    for (int i = 0; i < 4; i++) x->fr[vn + (u32)i] = U(out[i]);
}

static void group_d(Xe *x, u16 op, int pr)
{
    u32 n = FN;
    f32 s, c;
    switch (FM) {
    case 0: x->fr[n] = x->fpul; return; /* fsts */
    case 1: x->fpul = x->fr[n]; return; /* flds */
    case 2:                             /* float */
        if (pr) {
            need(x, !(n & 1), op);
            set_d(x, n, (double)(s32)x->fpul);
        } else {
            x->fr[n] = U((f32)(s32)x->fpul);
        }
        return;
    case 3: /* ftrc */
        if (pr) need(x, !(n & 1), op);
        x->fpul = ftrc(pr ? D(x, n) : (double)F(x->fr[n]));
        return;
    case 4: need(x, !pr || !(n & 1), op); x->fr[n] ^= SIGN; return; /* fneg: the sign bit, also of DRn */
    case 5: need(x, !pr || !(n & 1), op); x->fr[n] &= ~SIGN; return;
    case 6:
        if (pr) {
            need(x, !(n & 1), op);
            set_d(x, n, sqrt(D(x, n)));
        } else {
            x->fr[n] = U(sqrt_rm(F(x->fr[n])));
        }
        return;
    case 7: need(x, !pr, op); x->fr[n] = U(1.0f / sqrtf(F(x->fr[n]))); return; /* fsrra */
    case 8: need(x, !pr, op); x->fr[n] = 0; return;
    case 9: need(x, !pr, op); x->fr[n] = 0x3f800000u; return;
    case 10: need(x, pr && !(n & 1), op); set_d(x, n, (double)F(x->fpul)); return; /* fcnvsd */
    case 11: need(x, pr && !(n & 1), op); x->fpul = U((f32)D(x, n)); return;      /* fcnvds */
    case 14: need(x, !pr, op); fipr(x, (n & 3) * 4, (n >> 2) * 4); return;
    case 15:
        if (!(n & 1)) { /* fsca FPUL,DRn */
            need(x, !pr, op);
            sh4_fsca(x->fpul, &s, &c);
            x->fr[n] = U(s);
            x->fr[n + 1] = U(c);
            return;
        }
        if ((n & 3) == 1) { /* ftrv XMTRX,FVn */
            need(x, !pr, op);
            ftrv(x, (n >> 2) * 4);
            return;
        }
        need(x, !pr && (n == 3 || n == 11), op); /* fschg, frchg; PR = 1 and fpchg (SH-4A) are not run */
        xe_set_fpscr(x, x->fpscr ^ (n == 3 ? XE_FPSCR_SZ : XE_FPSCR_FR));
        return;
    }
    xe_fail("an FPU instruction xemu does not run", x->pc, op);
}

void xe_exec_fpu(Xe *x, u16 op)
{
    u32 n = FN, m = FM, *R = x->r, step;
    int pr = (x->fpscr & XE_FPSCR_PR) != 0, sz = (x->fpscr & XE_FPSCR_SZ) != 0;
    step = sz ? 8u : 4u;
    switch (op & 15) {
    case 0: case 1: case 2: case 3: case 4: case 5: arith(x, op, pr); return;
    case 6: load(x, n, R[0] + R[m], sz); return;
    case 7: store(x, m, R[0] + R[n], sz); return;
    case 8: load(x, n, R[m], sz); return;
    case 9: load(x, n, R[m], sz); R[m] += step; return;
    case 10: store(x, m, R[n], sz); return;
    case 11: store(x, m, R[n] - step, sz); R[n] -= step; return;
    case 12:
        if (sz) {
            u32 *d = pair(x, n), *s = pair(x, m), a = s[0], b = s[1];
            d[0] = a;
            d[1] = b;
        } else {
            x->fr[n] = x->fr[m];
        }
        return;
    case 13: group_d(x, op, pr); return;
    case 14: { /* fmac FR0,FRm,FRn */
        need(x, !pr, op);
        f32 p = F(x->fr[0]) * F(x->fr[m]);
        x->fr[n] = U(p + F(x->fr[n]));
        return;
    }
    }
    xe_fail("an FPU instruction xemu does not run", x->pc, op);
}

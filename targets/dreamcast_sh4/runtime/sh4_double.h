/* Double precision (FPSCR.PR=1) on fr pairs: the one definition of the pair helpers tc_rt.h and rd_rt.h declare
 * (sh4_dadd ... sh4_fcnvds), for every runtime that runs SH-4 C: the judge's (tc_rt.c), the readable judge's
 * (runtime_rd/rd_sh4.c) and the port's (port/runtime/sh4_rt.c). It defines the functions, so exactly one file of each
 * runtime includes it, after defining:
 *   SH4D_PR()         nonzero while FPSCR.PR is 1 (the runtime's mode word, set by sh4_lds_fpscr and sh4_fpchg);
 *   SH4D_UNDEFINED(s) what the runtime does at fcnvsd or fcnvds with PR=0 (undefined on the SH-4; s names it),
 * with <emmintrin.h>, sh4_ftrc and sh4_fsqrt_rm in scope.
 *
 * DRn = fr n (the high word) : fr n+1 (the low word). Each operation is one SSE2 double instruction, so MXCSR rounds
 * it (RC: FPSCR.RM) and flushes it (DAZ|FTZ: FPSCR.DN) as the f32 operations are; TcFpu flushes the 64-bit dr
 * operands the same way. The pairs move as bits only (unions: the port kit's /O1 has no inline memcpy), never as
 * float values, and the doubles stay in SSE registers (intrinsics only: no x87 in the 32-bit builds). Each helper runs as the SH-4 runs the instruction with the PR it finds: with PR=0 it is the single
 * instruction on fr n and fr m, so a C that loses the lds setting PR runs single, as the console would. */
#ifndef SH4_DOUBLE_H
#define SH4_DOUBLE_H

typedef union {
    f32 f;
    u32 u;
} Sh4dWord;

typedef union {
    double d;
    unsigned long long u;
} Sh4dPair;

static __m128d dr_get(f32 h, f32 l)
{
    Sh4dWord a, b;
    Sh4dPair p;
    a.f = h;
    b.f = l;
    p.u = (unsigned long long)a.u << 32 | b.u;
    return _mm_set_sd(p.d);
}

static void dr_set(f32 *h, f32 *l, __m128d x)
{
    Sh4dWord a, b;
    Sh4dPair p;
    p.d = _mm_cvtsd_f64(x);
    a.u = (u32)(p.u >> 32);
    b.u = (u32)p.u;
    *h = a.f;
    *l = b.f;
}

void sh4_dadd(f32 *nh, f32 *nl, f32 mh, f32 ml)
{
    if (!SH4D_PR()) { *nh = *nh + mh; return; }
    dr_set(nh, nl, _mm_add_sd(dr_get(*nh, *nl), dr_get(mh, ml)));
}

void sh4_dsub(f32 *nh, f32 *nl, f32 mh, f32 ml)
{
    if (!SH4D_PR()) { *nh = *nh - mh; return; }
    dr_set(nh, nl, _mm_sub_sd(dr_get(*nh, *nl), dr_get(mh, ml)));
}

void sh4_dmul(f32 *nh, f32 *nl, f32 mh, f32 ml)
{
    if (!SH4D_PR()) { *nh = *nh * mh; return; }
    dr_set(nh, nl, _mm_mul_sd(dr_get(*nh, *nl), dr_get(mh, ml)));
}

void sh4_ddiv(f32 *nh, f32 *nl, f32 mh, f32 ml)
{
    if (!SH4D_PR()) { *nh = *nh / mh; return; }
    dr_set(nh, nl, _mm_div_sd(dr_get(*nh, *nl), dr_get(mh, ml)));
}

void sh4_dsqrt(f32 *nh, f32 *nl)
{
    __m128d x;
    if (!SH4D_PR()) { *nh = sh4_fsqrt_rm(*nh); return; }
    x = dr_get(*nh, *nl);
    dr_set(nh, nl, _mm_sqrt_sd(x, x));
}

/* (comisd: false for a NaN either side, as fcmp; DAZ counts a denormal as zero, as DN) */
u32 sh4_dcmpeq(f32 nh, f32 nl, f32 mh, f32 ml)
{
    __m128d a, b;
    if (!SH4D_PR()) return nh == mh;
    a = dr_get(nh, nl);
    b = dr_get(mh, ml);
    return (u32)_mm_movemask_pd(_mm_cmpeq_sd(a, b)) & 1u;
}

u32 sh4_dcmpgt(f32 nh, f32 nl, f32 mh, f32 ml)
{
    __m128d a, b;
    if (!SH4D_PR()) return nh > mh;
    a = dr_get(nh, nl);
    b = dr_get(mh, ml);
    return (u32)_mm_movemask_pd(_mm_cmpgt_sd(a, b)) & 1u;
}

void sh4_dfloat(f32 *nh, f32 *nl, u32 fpul)
{
    if (!SH4D_PR()) { *nh = (f32)(s32)fpul; return; }
    dr_set(nh, nl, _mm_cvtsi32_sd(_mm_setzero_pd(), (int)fpul));
}

/* ftrc DRm,FPUL: truncation toward zero, saturating (0x7fffffff above the int range, 0x80000000 below it and for a
 * NaN), as sh4_ftrc and TcFpu.ftrc */
s32 sh4_dftrc(f32 h, f32 l)
{
    __m128d x;
    if (!SH4D_PR()) return sh4_ftrc(h);
    x = dr_get(h, l);
    if (_mm_movemask_pd(_mm_cmpunord_sd(x, x)) & 1) return (s32)0x80000000u;
    if (_mm_comige_sd(x, _mm_set_sd(2147483648.0))) return 0x7fffffff;
    if (_mm_comile_sd(x, _mm_set_sd(-2147483648.0))) return (s32)0x80000000u;
    return _mm_cvttsd_si32(x);
}

/* fcnvsd and fcnvds: the oracle converts exactly (TcFpu leaves them alone without fpscr_model: no DN), so they run
 * with DAZ and FTZ off; the conversion rounds by MXCSR.RC (FPSCR.RM). Undefined with PR=0: SH4D_UNDEFINED. */
void sh4_fcnvsd(f32 *nh, f32 *nl, u32 fpul)
{
    unsigned int csr = _mm_getcsr();
    Sh4dWord x;
    if (SH4D_PR()) {
        x.u = fpul;
        _mm_setcsr(csr & ~0x8040u);
        dr_set(nh, nl, _mm_cvtss_sd(_mm_setzero_pd(), _mm_set_ss(x.f)));
        _mm_setcsr(csr);
    } else {
        SH4D_UNDEFINED("fcnvsd-with-PR0"); /* (nothing after it: the readable runtime's does not return) */
    }
}

u32 sh4_fcnvds(f32 h, f32 l)
{
    unsigned int csr = _mm_getcsr();
    Sh4dWord x;
    x.u = 0u;
    if (SH4D_PR()) {
        _mm_setcsr(csr & ~0x8040u);
        x.f = _mm_cvtss_f32(_mm_cvtsd_ss(_mm_setzero_ps(), dr_get(h, l)));
        _mm_setcsr(csr);
    } else {
        SH4D_UNDEFINED("fcnvds-with-PR0");
    }
    return x.u;
}

#endif

/* fipr FVm,FVn and ftrv XMTRX,FVn for the port, in packed SSE2: the same results, bit for bit, as the judged runtimes'
 * scalar fipr (tc_fpu.c, rd_sh4.c): one inner product rounded once, as the reference emulator (Flycast: sh4_fpu.cpp,
 * shil_canonical.h innerProduct) computes the SH-4's. Each product in double (exact), the four summed in element order
 * 0..3 in double, the sum converted once to single, all in MXCSR's mode (RC from FPSCR.RM, DAZ|FTZ from FPSCR.DN). Only
 * the lanes are new: ftrv's rows go two to a register, each lane in its own row's order, with the same operands in the
 * same places (matrix times vector, running sum plus product) so a NaN comes out as the scalar code's. SSE2 operations,
 * never the x87 or a fused multiply-add. tests/test_fipr.py runs these against the exact reference and against the
 * judged scalar text, bits compared.
 *
 * sh4_rt.c's out-of-line helpers and tx/tc_rt_inline.h's inline ones (the translator C's calls) both use these. Vector
 * elements are read one float at a time: a caller's array was just written element by element, and a 16-byte read of
 * four fresh 4-byte writes waits on the store buffer. XMTRX is read a column at a time. */
#ifndef PORT_SH4_VEC_H
#define PORT_SH4_VEC_H

#include <emmintrin.h>

#define SH4V_WC_VECTOR_OPS 9 /* work_count.h's WC_VECTOR_OPS, for the translator C, which has no kit headers (sh4_rt.c checks) */

#pragma warning(push)
#pragma warning(disable : 4505) /* an inline function a file does not use */

/* fipr: the sum in the low lane of the result */
static __forceinline __m128 sh4v_fipr_ss(const float *a, const float *b)
{
    __m128 a01 = _mm_unpacklo_ps(_mm_load_ss(a), _mm_load_ss(a + 1)), a23 = _mm_unpacklo_ps(_mm_load_ss(a + 2), _mm_load_ss(a + 3));
    __m128 b01 = _mm_unpacklo_ps(_mm_load_ss(b), _mm_load_ss(b + 1)), b23 = _mm_unpacklo_ps(_mm_load_ss(b + 2), _mm_load_ss(b + 3));
    __m128d p01 = _mm_mul_pd(_mm_cvtps_pd(a01), _mm_cvtps_pd(b01));
    __m128d p23 = _mm_mul_pd(_mm_cvtps_pd(a23), _mm_cvtps_pd(b23));
    __m128d t = _mm_add_sd(p01, _mm_unpackhi_pd(p01, p01));
    t = _mm_add_sd(t, p23);
    t = _mm_add_sd(t, _mm_unpackhi_pd(p23, p23));
    return _mm_cvtsd_ss(_mm_setzero_ps(), t);
}

static __forceinline float sh4v_fipr(const float *a, const float *b) { return _mm_cvtss_f32(sh4v_fipr_ss(a, b)); }

/* ftrv: out = XMTRX * in (out may be in). xf holds XMTRX column-major as bits: column j is xf 4j..4j+3, row r of it
 * xf[4j + r]; lo holds rows 0 and 1, hi rows 2 and 3. */
static __forceinline void sh4v_ftrv(const unsigned int *xf, const float *in, float *out)
{
    __m128d b = _mm_cvtps_pd(_mm_load1_ps(in));
    __m128 c = _mm_loadu_ps((const float *)xf);
    __m128d lo = _mm_mul_pd(_mm_cvtps_pd(c), b), hi = _mm_mul_pd(_mm_cvtps_pd(_mm_movehl_ps(c, c)), b);
#define SH4V_COLUMN(j)                                                                                                 \
    b = _mm_cvtps_pd(_mm_load1_ps(in + (j)));                                                                          \
    c = _mm_loadu_ps((const float *)(xf + 4 * (j)));                                                                   \
    lo = _mm_add_pd(lo, _mm_mul_pd(_mm_cvtps_pd(c), b));                                                               \
    hi = _mm_add_pd(hi, _mm_mul_pd(_mm_cvtps_pd(_mm_movehl_ps(c, c)), b));
    SH4V_COLUMN(1)
    SH4V_COLUMN(2)
    SH4V_COLUMN(3)
#undef SH4V_COLUMN
    _mm_storeu_ps(out, _mm_movelh_ps(_mm_cvtpd_ps(lo), _mm_cvtpd_ps(hi)));
}

#pragma warning(pop)

#endif

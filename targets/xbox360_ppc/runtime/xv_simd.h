/* SSE2 / SSSE3 / SSE4.1 (and, for the float multiply-adds, FMA3 when the host has it) versions of the vector helpers that the translator writes lane by lane (xenontranslate/vmx.py).
 *
 * Port form only: a translated file includes this header when it uses one of these helpers, and each such helper then calls
 * its xs_<name> here, keeping the scalar body beside it as xv_<name>_s (vmx.SIMD, vmx.simd_text). The judge's form never
 * includes it, so the judge sees the scalar helpers byte for byte as before. xb_vmx_mode picks the route (xb_main.c):
 * 0 SSE (the default), 1 scalar (XB_VMX_SCALAR=1, or a host without SSSE3 / SSE4.1), 2 check (XB_VMX_CHECK=1: both run, the
 * scalar result is used, and xb_vmx_check counts per helper the calls whose 16 bytes differ). tests/test_vmx.py runs every
 * helper here against its scalar one.
 *
 * An xv holds four big-endian words (w[0] = guest bytes 0..3, guest byte 0 its top byte). Loaded as it is, the register
 * holds the guest's bytes reversed within each word; XVR reverses the word order as well, so register byte i is guest
 * byte 15 - i, register halfword i guest halfword 7 - i and register word i guest word 3 - i (each with its value the
 * right way round). Lane-wise operations need neither: the same reordering of both operands gives the same reordering of
 * the result. Saturating operations do not record VSCR[SAT], as the scalar helpers (vmx.py's module notes).
 *
 * Nearest existing: the scalar helpers in vmx.py - they stay as the answer key and the judge's form; this is the port's
 * fast path beside them, selected per call. */
#ifndef XV_SIMD_H
#define XV_SIMD_H
#include <intrin.h>

/* 0 SSE, 1 scalar, 2 check (set once at start, before any guest code runs; xb_main.c). selectany: one definition however
 * many translated files include this, and a link without xb_main.c still resolves it (0: the SSE helpers). */
__declspec(selectany) int xb_vmx_mode = 0;
/* check mode: helper `id` (its index in sorted vmx.SIMD) gave f by SSE and s by the scalar body (xb_main.c counts them) */
#ifdef XV_SIMD_NO_CHECK   /* a build of one translated file with no xb_main.c (a judge run of the fast route): no check mode */
#define xb_vmx_check(id, name, f, s) ((void)(id), (void)(name), (void)(f), (void)(s))
#else
void xb_vmx_check(int id, const char *name, const u32 *f, const u32 *s);
#endif

#define XVL(p) _mm_loadu_si128((const __m128i *)(p))
#define XVS(p, v) _mm_storeu_si128((__m128i *)(p), (v))
#define XVR(v) _mm_shuffle_epi32((v), 0x1b)
#define XB8 _mm_set1_epi8(-128)
#define XB16 _mm_set1_epi16(-32768)
#define XB32 _mm_set1_epi32(-2147483647 - 1)

/* lane-wise: r = e over the registers a, b as loaded */
#define XS2(n, e) \
    static __forceinline void xs_##n(u32 *r, const u32 *pa, const u32 *pb) \
    { __m128i a = XVL(pa), b = XVL(pb); XVS(r, e); }
/* whole-register: r = e over a, b with the word order reversed (guest element k is register lane count - 1 - k) */
#define XSR2(n, e) \
    static __forceinline void xs_##n(u32 *r, const u32 *pa, const u32 *pb) \
    { __m128i a = XVR(XVL(pa)), b = XVR(XVL(pb)); XVS(r, XVR(e)); }
#define XSR1(n, e) \
    static __forceinline void xs_##n(u32 *r, const u32 *pb) \
    { __m128i b = XVR(XVL(pb)); XVS(r, XVR(e)); }

/* per-lane shifts by count y & (bits - 1): one conditional shift per count bit (the mask is that bit moved to the lane's
 * top and spread over the lane) */
#define XSH_STEP(x, y, bits, i, sh)     x = _mm_blendv_epi8(x, sh(x, 1 << (i)), bits == 16 ? _mm_srai_epi16(_mm_slli_epi16(y, 15 - (i)), 15)                                                       : _mm_srai_epi32(_mm_slli_epi32(y, 31 - (i)), 31))
#define XSH16(n, sh)     static __forceinline void xs_##n(u32 *r, const u32 *pa, const u32 *pb)     { __m128i x = XVL(pa), y = XVL(pb);       XSH_STEP(x, y, 16, 0, sh); XSH_STEP(x, y, 16, 1, sh); XSH_STEP(x, y, 16, 2, sh); XSH_STEP(x, y, 16, 3, sh); XVS(r, x); }
#define XSH32(n, sh)     static __forceinline void xs_##n(u32 *r, const u32 *pa, const u32 *pb)     { __m128i x = XVL(pa), y = XVL(pb);       XSH_STEP(x, y, 32, 0, sh); XSH_STEP(x, y, 32, 1, sh); XSH_STEP(x, y, 32, 2, sh); XSH_STEP(x, y, 32, 3, sh);       XSH_STEP(x, y, 32, 4, sh); XVS(r, x); }

/* vperm on reversed registers: result guest byte k = (a, b)[c_k & 31]. Register byte i of the result takes selector
 * s = register byte i of c; guest byte s & 15 of a or b is register byte 15 - (s & 15) = (s ^ 15) & 15, and bit 4 of s
 * (moved to bit 7) picks b. */
static __forceinline __m128i xs_perm_r(__m128i a, __m128i b, __m128i c)
{
    __m128i t = _mm_and_si128(_mm_xor_si128(c, _mm_set1_epi8(15)), _mm_set1_epi8(15));
    return _mm_blendv_epi8(_mm_shuffle_epi8(a, t), _mm_shuffle_epi8(b, t), _mm_slli_epi16(c, 3));
}

/* group 1 begin: the Too Human job thread's six (build box A profile 2026-11-13) */
static __forceinline void xs_vperm(u32 *r, const u32 *pa, const u32 *pb, const u32 *pc)
{
    XVS(r, XVR(xs_perm_r(XVR(XVL(pa)), XVR(XVL(pb)), XVR(XVL(pc)))));
}
XS2(vaddshs, _mm_adds_epi16(a, b))
XS2(vsubuhm, _mm_sub_epi16(a, b))
XSH16(vslh, _mm_slli_epi16)
XSH16(vsrah, _mm_srai_epi16)
XSR2(vpkshus, _mm_packus_epi16(b, a))
/* group 1 end */

/* group 2 begin: the other lane-wise byte, halfword and word helpers */
XS2(vaddubm, _mm_add_epi8(a, b))
XS2(vsububm, _mm_sub_epi8(a, b))
XS2(vaddubs, _mm_adds_epu8(a, b))
XS2(vsububs, _mm_subs_epu8(a, b))
XS2(vaddsbs, _mm_adds_epi8(a, b))
XS2(vsubsbs, _mm_subs_epi8(a, b))
XS2(vmaxub, _mm_max_epu8(a, b))
XS2(vminub, _mm_min_epu8(a, b))
XS2(vmaxsb, _mm_max_epi8(a, b))
XS2(vminsb, _mm_min_epi8(a, b))
XS2(vavgub, _mm_avg_epu8(a, b))
XS2(vavgsb, _mm_xor_si128(_mm_avg_epu8(_mm_xor_si128(a, XB8), _mm_xor_si128(b, XB8)), XB8))
XS2(vcmpequb, _mm_cmpeq_epi8(a, b))
XS2(vcmpgtsb, _mm_cmpgt_epi8(a, b))
XS2(vcmpgtub, _mm_cmpgt_epi8(_mm_xor_si128(a, XB8), _mm_xor_si128(b, XB8)))
XS2(vadduhm, _mm_add_epi16(a, b))
XS2(vadduhs, _mm_adds_epu16(a, b))
XS2(vsubuhs, _mm_subs_epu16(a, b))
XS2(vsubshs, _mm_subs_epi16(a, b))
XS2(vmaxuh, _mm_max_epu16(a, b))
XS2(vminuh, _mm_min_epu16(a, b))
XS2(vmaxsh, _mm_max_epi16(a, b))
XS2(vminsh, _mm_min_epi16(a, b))
XS2(vavguh, _mm_avg_epu16(a, b))
XS2(vavgsh, _mm_xor_si128(_mm_avg_epu16(_mm_xor_si128(a, XB16), _mm_xor_si128(b, XB16)), XB16))
XS2(vcmpequh, _mm_cmpeq_epi16(a, b))
XS2(vcmpgtsh, _mm_cmpgt_epi16(a, b))
XS2(vcmpgtuh, _mm_cmpgt_epi16(_mm_xor_si128(a, XB16), _mm_xor_si128(b, XB16)))
XSH16(vsrh, _mm_srli_epi16)
XS2(vadduwm, _mm_add_epi32(a, b))
XS2(vsubuwm, _mm_sub_epi32(a, b))
XS2(vmaxuw, _mm_max_epu32(a, b))
XS2(vminuw, _mm_min_epu32(a, b))
XS2(vmaxsw, _mm_max_epi32(a, b))
XS2(vminsw, _mm_min_epi32(a, b))
XS2(vcmpequw, _mm_cmpeq_epi32(a, b))
XS2(vcmpgtsw, _mm_cmpgt_epi32(a, b))
XS2(vcmpgtuw, _mm_cmpgt_epi32(_mm_xor_si128(a, XB32), _mm_xor_si128(b, XB32)))
XSH32(vslw, _mm_slli_epi32)
XSH32(vsrw, _mm_srli_epi32)
XSH32(vsraw, _mm_srai_epi32)
/* group 2 end */

/* group 3 begin: packs, merges, unpacks, vsldoi and the byte / halfword splats */
XSR2(vpkshss, _mm_packs_epi16(b, a))
XSR2(vpkuhus, _mm_packus_epi16(_mm_min_epu16(b, _mm_set1_epi16(255)), _mm_min_epu16(a, _mm_set1_epi16(255))))
XSR2(vpkuhum, _mm_packus_epi16(_mm_and_si128(b, _mm_set1_epi16(255)), _mm_and_si128(a, _mm_set1_epi16(255))))
XSR2(vpkswss, _mm_packs_epi32(b, a))
XSR2(vpkswus, _mm_packus_epi32(b, a))
XSR2(vpkuwus, _mm_packus_epi32(_mm_min_epu32(b, _mm_set1_epi32(65535)), _mm_min_epu32(a, _mm_set1_epi32(65535))))
XSR2(vpkuwum, _mm_packus_epi32(_mm_and_si128(b, _mm_set1_epi32(65535)), _mm_and_si128(a, _mm_set1_epi32(65535))))
XSR2(vmrghb, _mm_unpackhi_epi8(b, a))
XSR2(vmrglb, _mm_unpacklo_epi8(b, a))
XSR2(vmrghh, _mm_unpackhi_epi16(b, a))
XSR2(vmrglh, _mm_unpacklo_epi16(b, a))
XSR1(vupkhsb, _mm_cvtepi8_epi16(_mm_srli_si128(b, 8)))
XSR1(vupklsb, _mm_cvtepi8_epi16(b))
XSR1(vupkhsh, _mm_cvtepi16_epi32(_mm_srli_si128(b, 8)))
XSR1(vupklsh, _mm_cvtepi16_epi32(b))
/* vsldoi: result guest byte k = (a, b)[k + sh], so the vperm of the control k + sh; register byte i of it is 15 - i + sh */
static __forceinline void xs_vsldoi(u32 *r, const u32 *pa, const u32 *pb, int sh)
{
    __m128i c = _mm_add_epi8(_mm_setr_epi8(15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0), _mm_set1_epi8((char)sh));
    XVS(r, XVR(xs_perm_r(XVR(XVL(pa)), XVR(XVL(pb)), c)));
}
static __forceinline void xs_vspltb(u32 *r, const u32 *pb, int n)
{
    XVS(r, _mm_set1_epi8((char)(pb[(n & 15) >> 2] >> (24 - 8 * (n & 3)))));
}
static __forceinline void xs_vsplth(u32 *r, const u32 *pb, int n)
{
    XVS(r, _mm_set1_epi16((short)(pb[(n & 7) >> 1] >> (16 - 16 * (n & 1)))));
}
/* group 3 end */

/* group 4 begin: the float multiply-adds and dot products (vmx.SIMD_MASK). Each returns a mask of the words it leaves to the
 * scalar body (bit k = word k), which is then used there; the words it keeps equal the scalar body's bit for bit (the proof
 * below; tests/test_vmx.py TestVmxFloat counts mismatches on directed, random and game-like operands). Lane-wise, so the
 * words stay in their loaded order. The scalar model (vmx.py): denormal inputs flushed to zero of the same sign, a NaN or
 * infinite operand handled by the scalar body, a result below 2^-126 flushed. */
#define XFX _mm_set1_epi32(0x7f800000)
/* FMA3 usable: -1 not asked yet, 0 no, 1 yes (cpuid 1: ecx 12 FMA, 27 OSXSAVE, 28 AVX; XCR0 bits 1, 2: the OS saves the
 * registers). xb_main.c asks at start; a build without it asks on the first call. */
__declspec(selectany) int xb_vmx_fma = -1;
static __forceinline int xs_fma_ok(void)
{
    if (xb_vmx_fma < 0) {
        int c[4];
        __cpuid(c, 1);
        xb_vmx_fma = (c[2] & (1 << 12)) && (c[2] & (1 << 27)) && (c[2] & (1 << 28)) && (_xgetbv(0) & 6) == 6;
    }
    return xb_vmx_fma;
}
/* words with a NaN or infinite operand, and the operands with denormals flushed to zero of the same sign */
static __forceinline __m128i xs_fnan(__m128i x) { return _mm_cmpeq_epi32(_mm_and_si128(x, XFX), XFX); }
static __forceinline __m128i xs_fflush(__m128i x)
{
    return _mm_andnot_si128(_mm_andnot_si128(XB32, _mm_cmpeq_epi32(_mm_and_si128(x, XFX), _mm_setzero_si128())), x);
}
/* a * b + c (neg: -(a * b - c)) rounded once, by FMA3; kept apart (noinline) so its VEX code runs only after xs_fma_ok.
 * Proof: a, b, c are normal or zero here (no NaN, no infinity, denormals flushed as the scalar body does), and FMA3 rounds the
 * exact a * b + c once to single. The scalar body (xv_fma1) forms the exact product in double, the double sum s and its error
 * e (TwoSum), and moves s off an exact single tie by e before rounding to single (xv_rnd1): that is the single rounding of
 * the exact value whenever the result is a normal single, and an overflow to infinity is the same in both. The two can
 * differ only for an exact value just below 2^-126: the scalar body rounds s in the denormal range without the tie
 * correction (its tie bit is not bit 28 there) and can give 2^-126 where one rounding gives a denormal (flushed to 0). So a
 * word whose result is at most 2^-126 in size (zero, denormal, or 2^-126) is left to the scalar body, unless a or b is zero:
 * then the result is c exactly (or a zero of the sign IEEE gives a sum of zeros, which the scalar body's double sum gives
 * too). A NaN result needs a NaN or infinite operand, so none is kept. fmsub is a * b + (-c) with the same zero signs as the
 * scalar body's xv_fma1(a, b, c ^ sign); the negation comes after the rounding in both. */
static __inline __declspec(noinline) int xs_fma4(u32 *r, const u32 *pa, const u32 *pb, const u32 *pc, int neg)
{
    __m128i a = XVL(pa), b = XVL(pb), c = XVL(pc), q, bad, tiny, pz;
    __m128 x;
    bad = _mm_or_si128(_mm_or_si128(xs_fnan(a), xs_fnan(b)), xs_fnan(c));
    a = xs_fflush(a);
    b = xs_fflush(b);
    c = xs_fflush(c);
    x = neg ? _mm_fmsub_ps(_mm_castsi128_ps(a), _mm_castsi128_ps(b), _mm_castsi128_ps(c))
            : _mm_fmadd_ps(_mm_castsi128_ps(a), _mm_castsi128_ps(b), _mm_castsi128_ps(c));
    q = _mm_castps_si128(x);
    tiny = _mm_cmplt_epi32(_mm_andnot_si128(XB32, q), _mm_set1_epi32(0x00800001));
    pz = _mm_or_si128(_mm_cmpeq_epi32(_mm_andnot_si128(XB32, a), _mm_setzero_si128()),
                      _mm_cmpeq_epi32(_mm_andnot_si128(XB32, b), _mm_setzero_si128()));
    bad = _mm_or_si128(bad, _mm_andnot_si128(pz, tiny));
    if (neg) q = _mm_xor_si128(q, XB32);
    XVS(r, q);
    return _mm_movemask_ps(_mm_castsi128_ps(bad));
}
static __forceinline int xs_vmaddfp(u32 *r, const u32 *pa, const u32 *pb, const u32 *pc)
{
    return xs_fma_ok() ? xs_fma4(r, pa, pb, pc, 0) : 15;
}
static __forceinline int xs_vnmsubfp(u32 *r, const u32 *pa, const u32 *pb, const u32 *pc)
{
    return xs_fma_ok() ? xs_fma4(r, pa, pb, pc, 1) : 15;
}
/* the dot product of the first n words (3 or 4), SSE2 doubles. Proof: with no NaN or infinite operand and denormals flushed,
 * each product p_k = a_k * b_k of two 24-bit significands is exact in double (48 bits, size 2^-252 to 2^256: never a double
 * denormal or overflow). s = (p0 + p2) + (p1 + p3) (p3 = -0 when n = 3, which changes no sum and no zero's sign) has two
 * roundings on any path, so |T - s| <= 2u/(1 - 2u) * A for the exact sum T and A = sum |p_k| (u = 2^-53, Higham 4.2).
 * d = A * 2^-49 (computed A is within (1 - u)^2 of the true one): d exceeds that bound and the error of forming s - d and
 * s + d (at most u(|s| + d) each) with room to spare, so fl(s - d) <= T <= fl(s + d). Rounding to single is monotonic, so
 * if fl(s - d), s and fl(s + d) round to the same single r, T rounds to r. When r is a normal single above 2^-126, T is above
 * 2^-126 + 2^-150, where rounding to single equals the scalar body's 24-bit rounding of T (xv_dot1: the limb sum rounded to
 * 24 bits, to nearest even), and a result past FLT_MAX gives infinity in both (the tie 2^128 - 2^103 goes to the even 2^128).
 * When A = 0 every product is zero, s is exact, and its sign is -0 only if every product is -0, as xv_dot1. Anything else (a
 * NaN or infinity in the n words, a result at or below 2^-126, an interval that straddles a rounding point, which
 * includes exact and catastrophic cancellation) goes to the scalar body. */
static __forceinline int xs_dot(u32 *r, const u32 *pa, const u32 *pb, int n)
{
    __m128i a = XVL(pa), b = XVL(pb), use = n == 3 ? _mm_set_epi32(0, -1, -1, -1) : _mm_set1_epi32(-1);
    __m128 fa, fb;
    __m128d pl, ph, t, s, ab, d, lh, sg = _mm_set1_pd(-0.0);
    u32 w0, wl, wh;
    if (_mm_movemask_epi8(_mm_and_si128(_mm_or_si128(xs_fnan(a), xs_fnan(b)), use))) return 15;
    fa = _mm_castsi128_ps(xs_fflush(a));
    fb = _mm_castsi128_ps(xs_fflush(b));
    pl = _mm_mul_pd(_mm_cvtps_pd(fa), _mm_cvtps_pd(fb));
    ph = _mm_mul_pd(_mm_cvtps_pd(_mm_movehl_ps(fa, fa)), _mm_cvtps_pd(_mm_movehl_ps(fb, fb)));
    if (n == 3) ph = _mm_unpacklo_pd(ph, sg);
    t = _mm_add_pd(pl, ph);
    s = _mm_add_sd(t, _mm_unpackhi_pd(t, t));
    t = _mm_add_pd(_mm_andnot_pd(sg, pl), _mm_andnot_pd(sg, ph));
    ab = _mm_add_sd(t, _mm_unpackhi_pd(t, t));
    d = _mm_mul_sd(ab, _mm_set_sd(1.7763568394002505e-15));   /* 2^-49 */
    lh = _mm_unpacklo_pd(_mm_sub_sd(s, d), _mm_add_sd(s, d));
    {
        __m128i f = _mm_castps_si128(_mm_cvtpd_ps(lh));
        wl = (u32)_mm_cvtsi128_si32(f);
        wh = (u32)_mm_cvtsi128_si32(_mm_shuffle_epi32(f, 1));
        w0 = (u32)_mm_cvtsi128_si32(_mm_castps_si128(_mm_cvtsd_ss(_mm_setzero_ps(), s)));
    }
    if (!(w0 == wl && w0 == wh && (w0 & 0x7fffffffu) > 0x00800000u) && _mm_cvtsd_f64(ab) != 0.0) return 15;
    XVS(r, _mm_set1_epi32((int)w0));
    return 0;
}
static __forceinline int xs_vmsum3fp(u32 *r, const u32 *pa, const u32 *pb) { return xs_dot(r, pa, pb, 3); }
static __forceinline int xs_vmsum4fp(u32 *r, const u32 *pa, const u32 *pb) { return xs_dot(r, pa, pb, 4); }
/* group 4 end */

#endif

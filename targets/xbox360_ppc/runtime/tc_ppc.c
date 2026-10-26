/* PowerPC (Xenon) instruction helpers, bit-exact with the oracle: Ghidra's 64-bit PowerPC p-code where it follows
 * the architecture, and the architecture's results the target's TcFixups substitute where it does not (fctiw/fctid
 * saturation, the stfs store conversion). Every double operation here is one IEEE double operation (the runtime
 * builds with /fp:strict: no contraction).
 *
 * Multiply-add rounding (DESIGN.md, open question 1) is chosen at build time: TC_FMA_FUSED=0 (the default) rounds
 * the product, then the sum, as Ghidra's p-code does; TC_FMA_FUSED=1 rounds once, as the PowerPC manuals describe.
 * The judge passes the target.json "fma" setting; decompiled C calls the ppc_fmadd family and never needs to change
 * when the setting does. */
#include <fenv.h>
#include <math.h>
#include <string.h>
#include "tc_rt.h"

#ifndef TC_FMA_FUSED
#define TC_FMA_FUSED 0
#endif

static u64 dbits(f64 x)
{
    u64 u;
    memcpy(&u, &x, 8);
    return u;
}

static f64 bitsd(u64 u)
{
    f64 x;
    memcpy(&x, &u, 8);
    return x;
}

/* -- rotates and masks (bit 0 is the most significant, as in the manuals) ------------------------------------- */

static u64 rotl64(u64 x, int n)
{
    n &= 63;
    return n ? (x << n) | (x >> (64 - n)) : x;
}

static u64 mask64(int mb, int me)
{
    u64 a = ~0ull >> (mb & 63), b = ~0ull << (63 - (me & 63));
    return (mb & 63) <= (me & 63) ? (a & b) : (a | b);
}

/* The 32-bit rotates rotate the low word doubled into both halves (ROTL32 in the manuals). */
static u64 rotl32x2(u64 s, int n)
{
    u64 lo = s & 0xffffffffull;
    return rotl64((lo << 32) | lo, n & 31);
}

u64 ppc_rlwinm(u64 s, int sh, int mb, int me) { return rotl32x2(s, sh) & mask64((mb & 31) + 32, (me & 31) + 32); }

u64 ppc_rlwnm(u64 s, u64 b, int mb, int me) { return ppc_rlwinm(s, (int)(b & 31), mb, me); }

u64 ppc_rlwimi(u64 a, u64 s, int sh, int mb, int me)
{
    u64 m = mask64((mb & 31) + 32, (me & 31) + 32);
    return (rotl32x2(s, sh) & m) | (a & ~m);
}

u64 ppc_rldicl(u64 s, int sh, int mb) { return rotl64(s, sh) & mask64(mb, 63); }

u64 ppc_rldicr(u64 s, int sh, int me) { return rotl64(s, sh) & mask64(0, me); }

u64 ppc_rldic(u64 s, int sh, int mb) { return rotl64(s, sh) & mask64(mb, 63 - (sh & 63)); }

u64 ppc_rldimi(u64 a, u64 s, int sh, int mb)
{
    u64 m = mask64(mb, 63 - (sh & 63));
    return (rotl64(s, sh) & m) | (a & ~m);
}

u64 ppc_rldcl(u64 s, u64 b, int mb) { return rotl64(s, (int)(b & 63)) & mask64(mb, 63); }

u64 ppc_rldcr(u64 s, u64 b, int me) { return rotl64(s, (int)(b & 63)) & mask64(0, me); }

/* -- shifts ---------------------------------------------------------------------------------------------------- */

u64 ppc_slw(u64 s, u64 b)
{
    u32 n = (u32)(b & 63);
    return n > 31 ? 0 : (u64)(u32)((u32)s << n);
}

u64 ppc_srw(u64 s, u64 b)
{
    u32 n = (u32)(b & 63);
    return n > 31 ? 0 : (u64)((u32)s >> n);
}

static u64 sraw_n(u64 s, u32 n, u32 *ca)
{
    s32 v = (s32)(u32)s;
    s32 r;
    u32 lost;
    if (n > 31) {
        r = v < 0 ? -1 : 0;
        lost = (u32)v;
    } else {
        r = n ? (s32)(v >> n) : v; /* MSVC's >> on a negative s32 is arithmetic */
        lost = n ? (u32)v & ((1u << n) - 1) : 0;
    }
    if (ca) *ca = (v < 0 && lost) ? 1u : 0u;
    return (u64)(s64)r;
}

u64 ppc_sraw(u64 s, u64 b, u32 *ca) { return sraw_n(s, (u32)(b & 63), ca); }

u64 ppc_srawi(u64 s, int sh, u32 *ca) { return sraw_n(s, (u32)(sh & 31), ca); }

u64 ppc_sld(u64 s, u64 b)
{
    u32 n = (u32)(b & 127);
    return n > 63 ? 0 : s << n;
}

u64 ppc_srd(u64 s, u64 b)
{
    u32 n = (u32)(b & 127);
    return n > 63 ? 0 : s >> n;
}

static u64 srad_n(u64 s, u32 n, u32 *ca)
{
    s64 v = (s64)s;
    s64 r;
    u64 lost;
    if (n > 63) {
        r = v < 0 ? -1 : 0;
        lost = s;
    } else {
        r = n ? (v >> n) : v;
        lost = n ? s & ((1ull << n) - 1) : 0;
    }
    if (ca) *ca = (v < 0 && lost) ? 1u : 0u;
    return (u64)r;
}

u64 ppc_srad(u64 s, u64 b, u32 *ca) { return srad_n(s, (u32)(b & 127), ca); }

u64 ppc_sradi(u64 s, int sh, u32 *ca) { return srad_n(s, (u32)(sh & 63), ca); }

u64 ppc_cntlzw(u64 s)
{
    u32 x = (u32)s, n = 0;
    if (!x) return 32;
    while (!(x & 0x80000000u)) {
        x <<= 1;
        n++;
    }
    return n;
}

u64 ppc_cntlzd(u64 s)
{
    u64 n = 0;
    if (!s) return 64;
    while (!(s & 0x8000000000000000ull)) {
        s <<= 1;
        n++;
    }
    return n;
}

/* -- multiply and divide ----------------------------------------------------------------------------------------- */

u64 ppc_mullw(u64 a, u64 b) { return (u64)((s64)(s32)(u32)a * (s64)(s32)(u32)b); }

u64 ppc_mulhw(u64 a, u64 b) { return (u64)(u32)((u64)((s64)(s32)(u32)a * (s64)(s32)(u32)b) >> 32); }

u64 ppc_mulhwu(u64 a, u64 b) { return (u64)(u32)(((u64)(u32)a * (u64)(u32)b) >> 32); }

u64 ppc_mulhdu(u64 a, u64 b)
{
    u64 al = a & 0xffffffffull, ah = a >> 32, bl = b & 0xffffffffull, bh = b >> 32;
    u64 ll = al * bl, lh = al * bh, hl = ah * bl, hh = ah * bh;
    u64 mid = (ll >> 32) + (lh & 0xffffffffull) + (hl & 0xffffffffull);
    return hh + (lh >> 32) + (hl >> 32) + (mid >> 32);
}

u64 ppc_mulhd(u64 a, u64 b)
{
    u64 h = ppc_mulhdu(a, b); /* signed high = unsigned high - (a < 0 ? b : 0) - (b < 0 ? a : 0) */
    if ((s64)a < 0) h -= b;
    if ((s64)b < 0) h -= a;
    return h;
}

u64 ppc_divw(u64 a, u64 b)
{
    s32 x = (s32)(u32)a, y = (s32)(u32)b;
    if (y == 0) return 0;
    if (x == (s32)0x80000000u && y == -1) return (u64)(s64)(s32)0x80000000u;
    return (u64)(s64)(x / y);
}

u64 ppc_divwu(u64 a, u64 b)
{
    u32 y = (u32)b;
    return y ? (u64)((u32)a / y) : 0;
}

u64 ppc_divd(u64 a, u64 b)
{
    s64 x = (s64)a, y = (s64)b;
    if (y == 0) return 0;
    if (x == (s64)0x8000000000000000ull && y == -1) return a;
    return (u64)(x / y);
}

u64 ppc_divdu(u64 a, u64 b) { return b ? a / b : 0; }

/* -- carries (XER[CA] from the 64-bit result: titles run in 64-bit mode) --------------------------------------- */

u64 ppc_addc(u64 a, u64 b, u32 *ca)
{
    u64 r = a + b;
    if (ca) *ca = r < a;
    return r;
}

u64 ppc_adde(u64 a, u64 b, u32 ca_in, u32 *ca)
{
    u64 t = a + b, r = t + (ca_in & 1);
    if (ca) *ca = (t < a) | (r < t);
    return r;
}

u64 ppc_subfc(u64 a, u64 b, u32 *ca)
{
    if (ca) *ca = b >= a;
    return b - a;
}

u64 ppc_subfe(u64 a, u64 b, u32 ca_in, u32 *ca) { return ppc_adde(~a, b, ca_in, ca); }

/* -- float conversions --------------------------------------------------------------------------------------- */

static s32 sat32(f64 x)
{
    if (x != x) return (s32)0x80000000u;
    if (x >= 2147483648.0) return 0x7fffffff;
    if (x < -2147483648.0) return (s32)0x80000000u;
    return (s32)x;
}

static s64 sat64(f64 x)
{
    if (x != x) return (s64)0x8000000000000000ull;
    if (x >= 9223372036854775808.0) return 0x7fffffffffffffffll;
    if (x < -9223372036854775808.0) return (s64)0x8000000000000000ull;
    return (s64)x;
}

/* Round to nearest even, for |x| < 2^63 (larger values are integers already). */
static f64 nearest_even(f64 x)
{
    if (!(x > -4503599627370496.0 && x < 4503599627370496.0)) return x; /* |x| >= 2^52 or NaN */
    f64 t = (f64)(s64)x; /* toward zero, exact */
    f64 frac = x - t;    /* exact */
    if (frac > 0.5 || (frac == 0.5 && ((s64)t & 1))) t += 1.0;
    else if (frac < -0.5 || (frac == -0.5 && ((s64)t & 1))) t -= 1.0;
    return t;
}

s32 ppc_fctiwz(f64 x) { return sat32(x); }

s32 ppc_fctiw(f64 x) { return sat32(nearest_even(x)); }

s64 ppc_fctidz(f64 x) { return sat64(x); }

s64 ppc_fctid(f64 x) { return sat64(nearest_even(x)); }

u64 ppc_fctiw_fpr(s32 v) { return (u64)(s64)v; }

f64 ppc_fcfid(u64 bits) { return (f64)(s64)bits; }

/* The architecture's store-single conversion (Book I, floating-point store): bit selection, no rounding; a value
 * below the single normal range is denormalized by shifting. */
u32 ppc_single_bits(f64 v)
{
    u64 x = dbits(v);
    u32 e = (u32)((x >> 52) & 0x7ff);
    u32 sel = (u32)(((x >> 32) & 0xc0000000u) | ((x >> 29) & 0x3fffffffu));
    if (e > 896 || e < 874) return sel;
    u64 frac = (1ull << 52) | (x & 0x000fffffffffffffull);
    return (u32)((x >> 32) & 0x80000000u) | (u32)(frac >> (926 - e));
}

f64 ppc_frsp(f64 x) { return (f64)(f32)x; }

/* -- multiply-add ---------------------------------------------------------------------------------------------- */

#if TC_FMA_FUSED
/* a * c + b rounded once to single: the exact result rounded toward zero to double, with the lowest bit set when
 * that rounding was inexact ("round to odd"), then rounded to single. Double keeps more than 2 * 24 + 2 bits, so
 * the second rounding gives the correctly rounded single. */
static f64 fma_single(f64 a, f64 c, f64 b)
{
    int mode = fegetround();
    fesetround(FE_TOWARDZERO);
    feclearexcept(FE_INEXACT);
    f64 r = fma(a, c, b);
    int inexact = fetestexcept(FE_INEXACT);
    fesetround(mode);
    if (inexact && r == r) r = bitsd(dbits(r) | 1);
    return (f64)(f32)r;
}

f64 ppc_fmadd(f64 a, f64 c, f64 b) { return fma(a, c, b); }
f64 ppc_fmsub(f64 a, f64 c, f64 b) { return fma(a, c, -b); }
f64 ppc_fmadds(f64 a, f64 c, f64 b) { return fma_single(a, c, b); }
f64 ppc_fmsubs(f64 a, f64 c, f64 b) { return fma_single(a, c, -b); }
#else
f64 ppc_fmadd(f64 a, f64 c, f64 b)
{
    f64 p = a * c;
    return p + b;
}

f64 ppc_fmsub(f64 a, f64 c, f64 b)
{
    f64 p = a * c;
    return p - b;
}

f64 ppc_fmadds(f64 a, f64 c, f64 b)
{
    f64 p = a * c;
    f64 s = p + b;
    return (f64)(f32)s;
}

f64 ppc_fmsubs(f64 a, f64 c, f64 b)
{
    f64 p = a * c;
    f64 s = p - b;
    return (f64)(f32)s;
}
#endif

f64 ppc_fnmadd(f64 a, f64 c, f64 b) { return ppc_fneg(ppc_fmadd(a, c, b)); }
f64 ppc_fnmsub(f64 a, f64 c, f64 b) { return ppc_fneg(ppc_fmsub(a, c, b)); }
f64 ppc_fnmadds(f64 a, f64 c, f64 b) { return ppc_fneg(ppc_fmadds(a, c, b)); }
f64 ppc_fnmsubs(f64 a, f64 c, f64 b) { return ppc_fneg(ppc_fmsubs(a, c, b)); }

/* -- other float ----------------------------------------------------------------------------------------------- */

f64 ppc_fsqrt(f64 x) { return sqrt(x); }

f64 ppc_fsqrts(f64 x) { return (f64)(f32)sqrt(x); }

f64 ppc_fsel(f64 a, f64 c, f64 b) { return a >= 0.0 ? c : b; }

f64 ppc_fabs(f64 x) { return bitsd(dbits(x) & 0x7fffffffffffffffull); }

f64 ppc_fnabs(f64 x) { return bitsd(dbits(x) | 0x8000000000000000ull); }

f64 ppc_fneg(f64 x) { return bitsd(dbits(x) ^ 0x8000000000000000ull); }

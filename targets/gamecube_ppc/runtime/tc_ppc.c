/* PowerPC instruction helpers, bit-exact with the oracle: Ghidra's Gekko p-code where it matches the 750CL, and
 * the 750CL results TcFixups substitutes where it does not (fctiwz/fctiw saturation, the stfs store conversion).
 * Every double operation here is one IEEE double operation (the runtime builds with /fp:strict: no contraction). */
#include <string.h>
#include "tc_rt.h"

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

static s32 saturate(f64 x)
{
    if (x != x) return (s32)0x80000000u;
    if (x >= 2147483648.0) return 0x7fffffff;
    if (x < -2147483648.0) return (s32)0x80000000u;
    return (s32)x;
}

s32 ppc_fctiwz(f64 x) { return saturate(x); }

s32 ppc_fctiw(f64 x)
{
    if (x != x || x >= 2147483648.0 || x < -2147483648.0) return saturate(x);
    f64 t = (f64)(s64)x; /* toward zero, exact in this range */
    f64 frac = x - t;    /* exact */
    if (frac > 0.5 || (frac == 0.5 && ((s64)t & 1))) t += 1.0;
    else if (frac < -0.5 || (frac == -0.5 && ((s64)t & 1))) t -= 1.0;
    return saturate(t);
}

/* Dolphin's ConvertToSingle (hardware-tested): the architecture's store-single conversion, by bit selection. */
u32 ppc_single_bits(f64 v)
{
    u64 x = dbits(v);
    u32 exp = (u32)((x >> 52) & 0x7ff);
    if (exp > 896 || (x & 0x7fffffffffffffffull) == 0) return (u32)(((x >> 32) & 0xc0000000u) | ((x >> 29) & 0x3fffffffu));
    if (exp >= 874) {
        u32 t = (u32)(0x80000000u | ((x & 0x000fffffffffffffull) >> 21));
        t = t >> (905 - exp);
        t |= (u32)((x >> 32) & 0x80000000u);
        return t;
    }
    return (u32)(((x >> 32) & 0xc0000000u) | ((x >> 29) & 0x3fffffffu));
}

f64 ppc_frsp(f64 x) { return (f64)(f32)x; }

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

f64 ppc_fnmadds(f64 a, f64 c, f64 b) { return -ppc_fmadds(a, c, b); }

f64 ppc_fnmsubs(f64 a, f64 c, f64 b) { return -ppc_fmsubs(a, c, b); }

f64 ppc_fsel(f64 a, f64 c, f64 b) { return a >= 0.0 ? c : b; }

f64 ppc_fabs(f64 x) { return bitsd(dbits(x) & 0x7fffffffffffffffull); }

f64 ppc_fnabs(f64 x) { return bitsd(dbits(x) | 0x8000000000000000ull); }

u32 ppc_cntlzw(u32 x)
{
    u32 n = 0;
    if (!x) return 32;
    while (!(x & 0x80000000u)) {
        x <<= 1;
        n++;
    }
    return n;
}

static u32 rotl(u32 s, int sh)
{
    sh &= 31;
    return sh ? (s << sh) | (s >> (32 - sh)) : s;
}

static u32 mask(int mb, int me)
{
    u32 a = 0xffffffffu >> (mb & 31), b = 0xffffffffu << (31 - (me & 31));
    return (mb & 31) <= (me & 31) ? (a & b) : (a | b);
}

u32 ppc_rlwinm(u32 s, int sh, int mb, int me) { return rotl(s, sh) & mask(mb, me); }

u32 ppc_rlwimi(u32 a, u32 s, int sh, int mb, int me)
{
    u32 m = mask(mb, me);
    return (rotl(s, sh) & m) | (a & ~m);
}

s32 ppc_divw(s32 a, s32 b)
{
    if (b == 0) return 0;
    if (a == (s32)0x80000000u && b == -1) return (s32)0x80000000u;
    return a / b;
}

u32 ppc_divwu(u32 a, u32 b) { return b ? a / b : 0; }

u32 ppc_mulhw(s32 a, s32 b) { return (u32)((u64)((s64)a * (s64)b) >> 32); }

u32 ppc_mulhwu(u32 a, u32 b) { return (u32)(((u64)a * (u64)b) >> 32); }

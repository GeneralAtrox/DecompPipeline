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
    if (s != s && a == a && b == b && c == c) return bitsd(0x7ff8000000000000ull); /* an invalid op: the 750CL's +NaN */
    return (f64)(f32)s;
}

f64 ppc_fmsubs(f64 a, f64 c, f64 b)
{
    f64 p = a * c;
    f64 s = p - b;
    if (s != s && a == a && b == b && c == c) return bitsd(0x7ff8000000000000ull);
    return (f64)(f32)s;
}

f64 ppc_fnmadds(f64 a, f64 c, f64 b) { f64 r = ppc_fmadds(a, c, b); return r != r ? r : -r; } /* a NaN keeps its sign */

f64 ppc_fnmsubs(f64 a, f64 c, f64 b) { f64 r = ppc_fmsubs(a, c, b); return r != r ? r : -r; }

f64 ppc_fsel(f64 a, f64 c, f64 b) { return a >= 0.0 ? c : b; }

f64 ppc_fabs(f64 x) { return bitsd(dbits(x) & 0x7fffffffffffffffull); }

f64 ppc_fnabs(f64 x) { return bitsd(dbits(x) | 0x8000000000000000ull); }

static const s32 frsqrte_base[32] = {
    0x1a7e800, 0x17cb800, 0x1552800, 0x130c000, 0x10f2000, 0xeff000, 0xd2e000, 0xb7c000,
    0x9e5000, 0x867000, 0x6ff000, 0x5ab800, 0x46a000, 0x339800, 0x218800, 0x105800,
    0x3ffa000, 0x3c29000, 0x38aa000, 0x3572000, 0x3279000, 0x2fb7000, 0x2d26000, 0x2ac0000,
    0x2881000, 0x2665000, 0x2468000, 0x2287000, 0x20c1000, 0x1f12000, 0x1d79000, 0x1bf4000
};

static const s32 frsqrte_dec[32] = {
    -0x568, -0x4f3, -0x48d, -0x435, -0x3e7, -0x3a2, -0x365, -0x32e,
    -0x2fc, -0x2d0, -0x2a8, -0x283, -0x261, -0x243, -0x226, -0x20b,
    -0x7a4, -0x700, -0x670, -0x5f2, -0x584, -0x524, -0x4cc, -0x47e,
    -0x43a, -0x3fa, -0x3c2, -0x38e, -0x35e, -0x332, -0x30a, -0x2e6
};

static const s32 fres_base[32] = {
    0x7ff800, 0x783800, 0x70ea00, 0x6a0800, 0x638800, 0x5d6200, 0x579000, 0x520800,
    0x4cc800, 0x47ca00, 0x430800, 0x3e8000, 0x3a2c00, 0x360800, 0x321400, 0x2e4a00,
    0x2aa800, 0x272c00, 0x23d600, 0x209e00, 0x1d8800, 0x1a9000, 0x17ae00, 0x14f800,
    0x124400, 0xfbe00, 0xd3800, 0xade00, 0x88400, 0x65000, 0x41c00, 0x20c00
};

static const s32 fres_dec[32] = {
    0x3e1, 0x3a7, 0x371, 0x340, 0x313, 0x2ea, 0x2c4, 0x2a0,
    0x27f, 0x261, 0x245, 0x22a, 0x212, 0x1fb, 0x1e5, 0x1d1,
    0x1be, 0x1ac, 0x19b, 0x18b, 0x17c, 0x16e, 0x15b, 0x15b,
    0x143, 0x143, 0x12d, 0x12d, 0x11a, 0x11a, 0x108, 0x106
};

/* fres / frsqrte: the 750CL's table estimates (TcEstimate.java in the oracle has the same tables and algorithm;
 * tests/test_gc_estimate.py compares them). The result is the double with these bits; FPSCR is not modelled. */
f64 ppc_frsqrte(f64 x)
{
    u64 b = dbits(x), mant = b & 0xfffffffffffffull, sign = b & 0x8000000000000000ull, exp = b & 0x7ff0000000000000ull;
    s64 e;
    u32 i;
    if (!mant && !exp) return bitsd(sign | 0x7ff0000000000000ull);
    if (exp == 0x7ff0000000000000ull) return bitsd(!mant ? (sign ? 0x7ff8000000000000ull : 0) : b | 0x8000000000000ull);
    if (sign) return bitsd(0x7ff8000000000000ull);
    if (!exp) { /* a denormal: normalise it */
        do {
            exp -= 1ull << 52;
            mant <<= 1;
        } while (!(mant & (1ull << 52)));
        mant &= 0xfffffffffffffull;
        exp += 1ull << 52;
    }
    e = (s64)(exp & (1ull << 52));
    i = (u32)((((u64)e) | mant) >> 37);
    exp = (u64)(((s64)(0x3ffull << 52) - (((s64)exp - (s64)(0x3feull << 52)) / 2)) & (s64)0x7ff0000000000000ull);
    return bitsd(exp | ((u64)(s64)(frsqrte_base[i / 2048] + frsqrte_dec[i / 2048] * (s32)(i % 2048)) << 26));
}

f64 ppc_fres(f64 x)
{
    u64 b = dbits(x), mant = b & 0xfffffffffffffull, sign = b & 0x8000000000000000ull, exp = b & 0x7ff0000000000000ull;
    u32 i;
    if (!mant && !exp) return bitsd(sign | 0x7ff0000000000000ull);
    if (exp == 0x7ff0000000000000ull) return bitsd(!mant ? sign : b | 0x8000000000000ull);
    if (exp < (895ull << 52)) return bitsd(sign | 0x47efffffe0000000ull);
    if (exp >= (1149ull << 52)) return bitsd(sign);
    exp = (0x7fdull << 52) - exp;
    i = (u32)(mant >> 37);
    return bitsd(sign | exp | ((u64)(s64)(fres_base[i / 1024] - (fres_dec[i / 1024] * (s32)(i % 1024) + 1) / 2) << 29));
}

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

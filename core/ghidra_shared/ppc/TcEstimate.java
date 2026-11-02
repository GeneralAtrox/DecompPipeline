// The 750CL's reciprocal estimates (not a script itself; TcFixups calls it): fres and frsqrte are table lookups with a
// linear interpolation on the hardware, where Ghidra's SLEIGH divides and takes a square root exactly. The tables
// below are the hardware-measured ones Dolphin publishes (Source/Core/Common/FloatUtils.cpp, GPL-2.0-or-later,
// master as read on 2026-10-31; the values are facts about the chip, the code here is written from the instruction's
// definition). They are indexed by the top mantissa bits (and, for frsqrte, the exponent's low bit); the result is
// base + dec * (low mantissa bits) placed under the result exponent. Special values: a zero gives an infinity of
// its sign; an infinity gives a zero (frsqrte of -inf, and of any negative number, is the default quiet NaN); a NaN
// is returned quieted; fres of a value below 2^-128 (or a denormal) gives the largest single with the operand's
// sign, of one at or above 2^126 a zero of its sign. FPSCR flags are not modelled (nothing observes them).
// tests/test_gc_estimate.py checks this class against an independent Python version and the C runtime's, and the
// relative error against the architecture's bounds (1/32 for frsqrte, 1/256 for fres).
public class TcEstimate {
    static final int[] FRSQRTE_BASE = {
        0x1a7e800, 0x17cb800, 0x1552800, 0x130c000, 0x10f2000, 0xeff000, 0xd2e000, 0xb7c000,
        0x9e5000, 0x867000, 0x6ff000, 0x5ab800, 0x46a000, 0x339800, 0x218800, 0x105800,
        0x3ffa000, 0x3c29000, 0x38aa000, 0x3572000, 0x3279000, 0x2fb7000, 0x2d26000, 0x2ac0000,
        0x2881000, 0x2665000, 0x2468000, 0x2287000, 0x20c1000, 0x1f12000, 0x1d79000, 0x1bf4000
    };

    static final int[] FRSQRTE_DEC = {
        -0x568, -0x4f3, -0x48d, -0x435, -0x3e7, -0x3a2, -0x365, -0x32e,
        -0x2fc, -0x2d0, -0x2a8, -0x283, -0x261, -0x243, -0x226, -0x20b,
        -0x7a4, -0x700, -0x670, -0x5f2, -0x584, -0x524, -0x4cc, -0x47e,
        -0x43a, -0x3fa, -0x3c2, -0x38e, -0x35e, -0x332, -0x30a, -0x2e6
    };

    static final int[] FRES_BASE = {
        0x7ff800, 0x783800, 0x70ea00, 0x6a0800, 0x638800, 0x5d6200, 0x579000, 0x520800,
        0x4cc800, 0x47ca00, 0x430800, 0x3e8000, 0x3a2c00, 0x360800, 0x321400, 0x2e4a00,
        0x2aa800, 0x272c00, 0x23d600, 0x209e00, 0x1d8800, 0x1a9000, 0x17ae00, 0x14f800,
        0x124400, 0xfbe00, 0xd3800, 0xade00, 0x88400, 0x65000, 0x41c00, 0x20c00
    };

    static final int[] FRES_DEC = {
        0x3e1, 0x3a7, 0x371, 0x340, 0x313, 0x2ea, 0x2c4, 0x2a0,
        0x27f, 0x261, 0x245, 0x22a, 0x212, 0x1fb, 0x1e5, 0x1d1,
        0x1be, 0x1ac, 0x19b, 0x18b, 0x17c, 0x16e, 0x15b, 0x15b,
        0x143, 0x143, 0x12d, 0x12d, 0x11a, 0x11a, 0x108, 0x106
    };

    static final long QNAN = 0x7ff8000000000000L, QUIET = 0x0008000000000000L, INF = 0x7ff0000000000000L;
    static final long MANT = (1L << 52) - 1, SIGN = 1L << 63, EXP = 0x7ffL << 52;

    // frsqrte fD,fB: the result's bits for the operand with bits b.
    static long frsqrte(long b) {
        long mant = b & MANT, sign = b & SIGN, exp = b & EXP;
        if (mant == 0 && exp == 0) return sign | INF;
        if (exp == EXP) return mant == 0 ? (sign != 0 ? QNAN : 0L) : b | QUIET;
        if (sign != 0) return QNAN;
        if (exp == 0) {  // a denormal: normalise it
            do {
                exp -= 1L << 52;
                mant <<= 1;
            } while ((mant & (1L << 52)) == 0);
            mant &= MANT;
            exp += 1L << 52;
        }
        long lsb = exp & (1L << 52);
        exp = ((0x3ffL << 52) - ((exp - (0x3feL << 52)) / 2)) & EXP;
        int i = (int) ((lsb | mant) >>> 37);
        return exp | ((long) (FRSQRTE_BASE[i / 2048] + FRSQRTE_DEC[i / 2048] * (i % 2048)) << 26);
    }

    // fres fD,fB: the result's bits for the operand with bits b.
    static long fres(long b) {
        long mant = b & MANT, sign = b & SIGN, exp = b & EXP;
        if (mant == 0 && exp == 0) return sign | INF;
        if (exp == EXP) return mant == 0 ? sign : b | QUIET;
        if (exp < (895L << 52)) return sign | 0x47efffffe0000000L;
        if (exp >= (1149L << 52)) return sign;
        exp = (0x7fdL << 52) - exp;
        int i = (int) (mant >>> 37);
        return sign | exp | ((long) (FRES_BASE[i / 1024] - (FRES_DEC[i / 1024] * (i % 1024) + 1) / 2) << 29);
    }
}

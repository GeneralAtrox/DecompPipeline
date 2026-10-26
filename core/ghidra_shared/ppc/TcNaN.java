// NaN results on PowerPC (not a script itself; each PowerPC target's TcFixups calls it, with its own list of the
// instructions it applies to). One implementation for every target whose target.json "shared_scripts" names "ppc".
//
// Ghidra's FloatFormat makes every NaN it computes or converts the canonical NaN 0x7ff8000000000000 with at most the
// sign kept: float2float (lfs's single -> double, a single result's double -> single), f+ f- f* f/ sqrt, and f-
// (fneg) and abs (fabs). An invalid operation with no NaN operand (inf - inf, 0 * inf, 0 / 0, sqrt of a negative)
// gives 0xfff8000000000000. The hardware (PowerPC Book I 4.3.x and 4.6; the 750CL, the Xenon; Dolphin's NI_ helpers):
//  - a load of a single NaN widens its fraction into the double (lfs; not quieted);
//  - an arithmetic result with a NaN operand is the first NaN of frA, frB, frC (the instruction's fields, not the
//    assembly order: fmadd fD,fA,fC,fB), quieted; a single-precision result then keeps the top 23 fraction bits;
//  - an invalid operation with no NaN operand gives the default NaN 0x7ff8000000000000;
//  - the fnm* forms do not negate a NaN result (Book I: "QNaNs propagate with no effect on their sign bit");
//  - fneg, fabs (and fnabs, fmr, fsel) change only the sign bit, or copy, and do not quiet.
// A fixup calls these only for a step whose result Ghidra made a NaN, so no other value changes. Through float
// stores the judge's NaN rule (FN) hid the difference; through integer registers (stfs then lwz, ld) it did not.
public class TcNaN {
    static final long SIGN = 0x8000000000000000L, QUIET = 0x0008000000000000L, DEFAULT = 0x7ff8000000000000L;

    static boolean nan(long d) {
        return (d & 0x7ff0000000000000L) == 0x7ff0000000000000L && (d & 0x000fffffffffffffL) != 0;
    }

    static boolean nan32(long s) {
        return (s & 0x7f800000L) == 0x7f800000L && (s & 0x007fffffL) != 0;
    }

    // The double a load-single of the word s makes when s is a NaN.
    static long load(long s) {
        return (s & 0x80000000L) << 32 | 0x7ff0000000000000L | (s & 0x007fffffL) << 29;
    }

    // The operands of op in the order frA, frB, frC, as indexes into its source operands in assembly order.
    static int[] fields(String op) {
        if (op.matches("f(n?m(add|sub))s?")) return new int[] {0, 2, 1};  // fD,fA,fC,fB
        if (op.matches("fsqrts?|frsp")) return new int[] {0};  // fD,fB
        return new int[] {0, 1};  // fD,fA,fB (fadd fsub fdiv) or fD,fA,fC (fmul)
    }

    // The NaN result of the arithmetic op with source operands src (assembly order) when Ghidra's result is a NaN.
    static long arith(String op, long[] src) {
        long r = DEFAULT;
        for (int i : fields(op)) {
            if (nan(src[i])) {
                r = src[i] | QUIET;
                break;
            }
        }
        return op.endsWith("s") || op.equals("frsp") ? r & 0xffffffffe0000000L : r;
    }

    // fneg / fabs of the NaN with bits b.
    static long sign(String op, long b) {
        return op.equals("fneg") ? b ^ SIGN : b & ~SIGN;
    }
}

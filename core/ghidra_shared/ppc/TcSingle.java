// Single-precision results on PowerPC (not a script itself; each PowerPC target's TcFixups calls it, with its own list
// of the instructions it applies to). One implementation for every target whose target.json "shared_scripts" names
// "ppc" (core/paths.target_scripts): the SLEIGH (ppc_instructions.sinc) is the same for all of them.
//
// fadds fsubs fmuls fdivs fmadds fmsubs fnmadds fnmsubs frsp fsqrts compute a double and round it to single with
// float2float, and Ghidra's FloatFormat.getEncoding returns 0 for every value below the smallest single subnormal
// 2^-149 (its "XXX is it possible to round up" branch). IEEE round-to-nearest (Book I 4.3.6 with FPSCR[RN] = 0 and
// NI = 0; the 750CL, the Xenon, the C's (f32) cast) rounds a value above 2^-150 up to 2^-149, bits 0x00000001.
// singleSource recomputes the step's double with Ghidra's own 8-byte FloatFormat ops, in the SLEIGH's order;
// singleResult gives what Ghidra's rounding of it makes and what IEEE makes. They differ only in that range (the
// targets' tests check it over millions of values), so a fixup that replaces only a result equal to Ghidra's, and
// only where the two differ, changes no other value.
import ghidra.pcode.floatformat.FloatFormat;
import ghidra.pcode.floatformat.FloatFormatFactory;

public class TcSingle {
    static final FloatFormat F8 = FloatFormatFactory.getFloatFormat(8), F4 = FloatFormatFactory.getFloatFormat(4);

    // The double the SLEIGH rounds to single (8-byte FloatFormat ops, as the p-code runs them): src holds the bits
    // of the source operands in the instruction's operand order (fadds fD,fA,fB: a, b; fmadds fD,fA,fC,fB: a, c, b).
    static long singleSource(String op, long[] src) {
        switch (op) {
            case "fadds": return F8.opAdd(src[0], src[1]);
            case "fsubs": return F8.opSub(src[0], src[1]);
            case "fmuls": return F8.opMult(src[0], src[1]);
            case "fdivs": return F8.opDiv(src[0], src[1]);
            case "fmadds": case "fnmadds": return F8.opAdd(F8.opMult(src[0], src[1]), src[2]);
            case "fmsubs": case "fnmsubs": return F8.opSub(F8.opMult(src[0], src[1]), src[2]);
            case "fsqrts": return F8.opSqrt(src[0]);
            default: return src[0];  // frsp
        }
    }

    // {what the step gives (Ghidra's float2float), what IEEE gives} as fD's double bits, for the single result of op
    // from the double with bits x (fnm*: negated after the rounding, as the SLEIGH's f- of the float2float).
    static long[] singleResult(String op, long x) {
        long single = F8.opFloat2Float(x, F4) & 0xffffffffL;  // float2float 8 -> 4, as the step
        long ghidra = F4.opFloat2Float(single, F8);  // and back to fD's double (exact)
        long ieee = ghidra;
        double d = Double.longBitsToDouble(x);
        float f = (float) d;  // IEEE round to nearest even, subnormals included
        if ((single & 0x7fffffffL) == 0 && d != 0 && !Double.isNaN(d) && f != 0) {
            ieee = Double.doubleToRawLongBits((double) f);
        }
        if (op.startsWith("fnm")) {
            ghidra = F8.opNeg(ghidra);
            ieee = F8.opNeg(ieee);
        }
        return new long[] {ghidra, ieee};
    }
}

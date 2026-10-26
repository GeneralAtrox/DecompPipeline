// PowerPC NaN results (tests/test_gc_nan.py): TcNaN (core/ghidra_shared/ppc, GameCube TcFixups' rule) against
// Ghidra's own FloatFormat ops and Java's IEEE double arithmetic.
//
// usage: java TcNaNStub N SEED   prints "key count" lines, up to 5 "example key ..." lines per key, and "case" lines:
//   tried        operand sets tried (every op)
//   when_ghidra  Ghidra's result is a NaN where IEEE's is not, or the reverse (the fixup would fire wrongly or miss)
//   loud         TcNaN.arith gave a value that is not a quiet NaN
//   case NAME BITS  fixed examples the test checks against the PowerPC rules
import ghidra.pcode.floatformat.FloatFormat;
import ghidra.pcode.floatformat.FloatFormatFactory;
import java.util.*;

public class TcNaNStub {
    static final FloatFormat F8 = FloatFormatFactory.getFloatFormat(8), F4 = FloatFormatFactory.getFloatFormat(4);
    static final Map<String, Integer> count = new TreeMap<>();
    static final Map<String, List<String>> ex = new TreeMap<>();
    static final String[] OPS = {"fadd", "fsub", "fmul", "fdiv", "fmadd", "fmsub", "fnmadd", "fnmsub", "fsqrt",
        "fadds", "fsubs", "fmuls", "fdivs", "fmadds", "fmsubs", "fnmadds", "fnmsubs", "fsqrts", "frsp"};

    static void note(String key, String what) {
        count.merge(key, 1, Integer::sum);
        List<String> l = ex.computeIfAbsent(key, k -> new ArrayList<>());
        if (l.size() < 5) l.add(what);
    }

    // The step's result as the SLEIGH computes it (8-byte ops; a single result through float2float and back).
    static long ghidra(String op, long[] s) {
        if (op.endsWith("s") || op.equals("frsp")) {
            long[] r = TcSingle.singleResult(op, TcSingle.singleSource(op, s));
            return r[0];
        }
        switch (op) {
            case "fadd": return F8.opAdd(s[0], s[1]);
            case "fsub": return F8.opSub(s[0], s[1]);
            case "fmul": return F8.opMult(s[0], s[1]);
            case "fdiv": return F8.opDiv(s[0], s[1]);
            case "fmadd": return F8.opAdd(F8.opMult(s[0], s[1]), s[2]);
            case "fmsub": return F8.opSub(F8.opMult(s[0], s[1]), s[2]);
            case "fnmadd": return F8.opNeg(F8.opAdd(F8.opMult(s[0], s[1]), s[2]));
            case "fnmsub": return F8.opNeg(F8.opSub(F8.opMult(s[0], s[1]), s[2]));
            default: return F8.opSqrt(s[0]);
        }
    }

    // IEEE: is the result a NaN (Java's double arithmetic; a single result is NaN exactly when the double is).
    static boolean ieeeNaN(String op, long[] s) {
        double a = Double.longBitsToDouble(s[0]), b = s.length > 1 ? Double.longBitsToDouble(s[1]) : 0;
        double c = s.length > 2 ? Double.longBitsToDouble(s[2]) : 0;
        String o = op.endsWith("s") && !op.equals("fsqrts") ? op.substring(0, op.length() - 1) : op;
        switch (o) {
            case "fadd": return Double.isNaN(a + b);
            case "fsub": return Double.isNaN(a - b);
            case "fmul": return Double.isNaN(a * b);
            case "fdiv": return Double.isNaN(a / b);
            case "fmadd": case "fnmadd": return Double.isNaN(a * b + c);
            case "fmsub": case "fnmsub": return Double.isNaN(a * b - c);
            case "frsp": return Double.isNaN(a);
            default: return Double.isNaN(Math.sqrt(a));
        }
    }

    static int arity(String op) {
        return op.matches("f(n?m(add|sub))s?") ? 3 : op.matches("fsqrts?|frsp") ? 1 : 2;
    }

    static long pick(Random rnd) {
        switch (rnd.nextInt(8)) {
            case 0: return 0x7ff0000000000000L | (rnd.nextLong() & 0x000fffffffffffffL) | 1;  // NaN, any payload
            case 1: return 0xfff8000000000000L | rnd.nextLong();  // negative quiet NaN
            case 2: return rnd.nextBoolean() ? 0x7ff0000000000000L : 0xfff0000000000000L;  // inf
            case 3: return rnd.nextBoolean() ? 0 : 0x8000000000000000L;  // zero
            case 4: return TcNaN.load(rnd.nextInt() & 0xffffffffL);  // what lfs of a single NaN gives
            default: return Double.doubleToRawLongBits(Float.intBitsToFloat(rnd.nextInt()));  // a single's value
        }
    }

    static void c(String name, long v) {
        System.out.printf("case %s %016x%n", name, v);
    }

    public static void main(String[] args) {
        int n = Integer.parseInt(args[0]);
        Random rnd = new Random(Long.parseLong(args[1]));
        for (int i = 0; i < n; i++) {
            for (String op : OPS) {
                long[] s = new long[arity(op)];
                for (int k = 0; k < s.length; k++) s[k] = pick(rnd);
                count.merge("tried", 1, Integer::sum);
                boolean g = TcNaN.nan(ghidra(op, s)), e = ieeeNaN(op, s);
                if (g != e) note("when_ghidra", op + " " + Arrays.toString(s));
                if (g) {
                    long r = TcNaN.arith(op, s);
                    if (!TcNaN.nan(r) || (r & TcNaN.QUIET) == 0) note("loud", op + " " + Long.toHexString(r));
                }
            }
        }
        for (Map.Entry<String, Integer> e : count.entrySet()) System.out.println(e.getKey() + " " + e.getValue());
        for (Map.Entry<String, List<String>> e : ex.entrySet()) {
            for (String s : e.getValue()) System.out.println("example " + e.getKey() + " " + s);
        }
        long tooHuman = TcNaN.load(0xffffffffL), one = Double.doubleToRawLongBits(1.0), zero = 0;
        long snan = 0x7ff0000000000001L, qa = 0x7ff8123456789abcL, qb = 0xfff8000000000042L;
        long inf = 0x7ff0000000000000L;
        c("lfs_ffffffff", tooHuman);
        c("lfs_snan_7f800001", TcNaN.load(0x7f800001L));
        c("fmuls_toohuman_0", TcNaN.arith("fmuls", new long[] {tooHuman, zero}));
        c("stfs_toohuman", TcFixups.toSingle(TcNaN.arith("fadds", new long[] {tooHuman, one})));
        c("fadd_snan_1", TcNaN.arith("fadd", new long[] {snan, one}));
        c("fadd_qa_qb", TcNaN.arith("fadd", new long[] {qa, qb}));
        c("fadd_1_qb", TcNaN.arith("fadd", new long[] {one, qb}));
        c("fadds_qa_1", TcNaN.arith("fadds", new long[] {qa, one}));
        c("fmadd_c_before_b", TcNaN.arith("fmadd", new long[] {one, qa, qb}));  // fD,fA,fC,fB: fB wins over fC
        c("fnmadd_qb", TcNaN.arith("fnmadd", new long[] {one, one, qb}));  // not negated
        c("fsub_inf_inf", TcNaN.arith("fsub", new long[] {inf, inf}));
        c("fdiv_0_0", TcNaN.arith("fdiv", new long[] {zero, zero}));
        c("ghidra_fdiv_0_0", ghidra("fdiv", new long[] {zero, zero}));
        c("ghidra_lfs_ffffffff", F4.opFloat2Float(0xffffffffL, F8));
        c("fneg_qb", TcNaN.sign("fneg", qb));
        c("fabs_qb", TcNaN.sign("fabs", qb));
        c("fneg_snan", TcNaN.sign("fneg", snan));
    }
}

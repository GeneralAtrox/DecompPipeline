// The GameCube oracle's single-precision rounding (tests/test_gc_single.py): Ghidra's float2float 8 -> 4 against
// Java's (float) cast (IEEE round to nearest even), and TcSingle.singleResult / singleSource
// (core/ghidra_shared/ppc, GameCube TcFixups' rule) on the same values.
//
// usage: java TcFixupsStub N SEED   prints lines "key count" and up to 5 "example key bits" lines per key:
//   f2f_flush   Ghidra's float2float differs from the cast, |x| in (2^-150, 2^-149) (the bug TcFixups corrects)
//   f2f_other   Ghidra's float2float differs from the cast anywhere else (NaNs: both NaN counts as equal)
//   fix_wrong   singleResult's IEEE value is not the cast's (non-NaN x)
//   fix_moved   singleResult changes a value outside the flush range
//   fix_ghidra  singleResult's Ghidra value is not Ghidra's float2float
//   src_OP      singleSource for OP differs from Java's double arithmetic (non-NaN results)
//   tried_f2f   values tried
import ghidra.pcode.floatformat.FloatFormat;
import ghidra.pcode.floatformat.FloatFormatFactory;
import java.util.*;

public class TcFixupsStub {
    static final FloatFormat F8 = FloatFormatFactory.getFloatFormat(8), F4 = FloatFormatFactory.getFloatFormat(4);
    static final Map<String, Integer> count = new TreeMap<>();
    static final Map<String, List<String>> ex = new TreeMap<>();

    static void note(String key, String what) {
        count.merge(key, 1, Integer::sum);
        List<String> l = ex.computeIfAbsent(key, k -> new ArrayList<>());
        if (l.size() < 5) l.add(what);
    }

    static boolean inFlush(double d) {
        double a = Math.abs(d);
        return a > 0x1p-150 && a < 0x1p-149;
    }

    static long widen(float f) {
        return Double.doubleToRawLongBits((double) f);
    }

    static void one(long x) {
        count.merge("tried_f2f", 1, Integer::sum);
        double d = Double.longBitsToDouble(x);
        long g = F8.opFloat2Float(x, F4) & 0xffffffffL;
        long j = Float.floatToRawIntBits((float) d) & 0xffffffffL;
        boolean nan = Double.isNaN(d);
        if (!nan && g != j) note(inFlush(d) ? "f2f_flush" : "f2f_other", String.format("%016x %08x %08x", x, g, j));
        for (String op : new String[] {"frsp", "fnmadds"}) {
            long[] r = TcSingle.singleResult(op, x);
            long gw = F4.opFloat2Float(g, F8), jw = widen((float) d);
            if (op.startsWith("fnm")) {
                gw = F8.opNeg(gw);
                jw = F8.opNeg(jw);
            }
            if (r[0] != gw) note("fix_ghidra", String.format("%s %016x", op, x));
            if (!nan && r[1] != jw) note("fix_wrong", String.format("%s %016x %016x %016x", op, x, r[1], jw));
            if (r[0] != r[1] && !inFlush(d)) note("fix_moved", String.format("%s %016x", op, x));
        }
    }

    public static void main(String[] args) {
        int n = Integer.parseInt(args[0]);
        Random rnd = new Random(Long.parseLong(args[1]));
        // the edges: zeros, the flush range and its ends, the subnormal and normal boundaries, overflow, NaN, inf
        double[] edge = {0.0, -0.0, 0x1p-150, -0x1p-150, Math.nextUp(0x1p-150), Math.nextDown(0x1p-149), 0x1p-149,
            -0x1p-149, Math.nextDown(0x1p-150), 0x1.8p-150, -0x1.8p-150, 0x1p-160, 0x1p-126, Math.nextDown(0x1p-126),
            0x1.fffffep-127, 0x1.ffffffp-127, Float.MAX_VALUE, Math.nextUp((double) Float.MAX_VALUE), 0x1.ffffffp127,
            Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY, 1.0, -1.0, 0.1, Double.MIN_VALUE};
        for (double d : edge) one(Double.doubleToRawLongBits(d));
        for (int i = 0; i < n; i++) {
            one(rnd.nextLong());  // any bit pattern
            int e = -170 + rnd.nextInt(60);  // around the single subnormals
            double m = 1 + rnd.nextDouble();
            one(Double.doubleToRawLongBits((rnd.nextBoolean() ? m : -m) * Math.pow(2, e)));
            one(Double.doubleToRawLongBits((rnd.nextBoolean() ? 1 : -1) * Math.scalb(rnd.nextDouble(), -149)));
            double a = Float.intBitsToFloat(rnd.nextInt()), b = Float.intBitsToFloat(rnd.nextInt());
            double c = Math.scalb(1 + rnd.nextDouble(), -80 + rnd.nextInt(10)) * (rnd.nextBoolean() ? 1 : -1);
            long la = Double.doubleToRawLongBits(a), lb = Double.doubleToRawLongBits(b), lc = Double.doubleToRawLongBits(c);
            check("fadds", new long[] {la, lb}, a + b);
            check("fsubs", new long[] {la, lb}, a - b);
            check("fmuls", new long[] {la, lc}, a * c);
            check("fdivs", new long[] {la, lc}, a / c);
            check("fmadds", new long[] {la, lc, lb}, a * c + b);
            check("fmsubs", new long[] {la, lc, lb}, a * c - b);
            check("fnmadds", new long[] {la, lc, lb}, a * c + b);
            check("fsqrts", new long[] {la}, Math.sqrt(a));
            check("frsp", new long[] {lc}, c);
            one(Double.doubleToRawLongBits(a * c));  // products of a single and a tiny double: the flush range
        }
        for (Map.Entry<String, Integer> e : count.entrySet()) System.out.println(e.getKey() + " " + e.getValue());
        for (Map.Entry<String, List<String>> e : ex.entrySet()) {
            for (String s : e.getValue()) System.out.println("example " + e.getKey() + " " + s);
        }
    }

    static void check(String op, long[] src, double want) {
        long got = TcSingle.singleSource(op, src);
        if (!Double.isNaN(want) && got != Double.doubleToRawLongBits(want)) {
            note("src_" + op, String.format("%016x", got));
        }
        one(got);
    }
}

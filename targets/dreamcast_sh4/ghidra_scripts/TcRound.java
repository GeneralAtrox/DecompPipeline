// FPSCR.RM in the oracle: IEEE single and double precision results under round-to-nearest (RM=0, Java's own
// arithmetic) or round-toward-zero (RM=1, Toy Commander's mode: SHC's default -round=zero, FPSCR 0x00040001).
// Ghidra's p-code float ops always round to nearest; TcFpu recomputes the results of a step with these when RM=1.
// Method: the round-to-nearest result r, then an exact comparison of |r| with the exact value (BigDecimal: a float or
// double is an exact binary fraction, so sums and products are exact; a quotient or root is compared through the
// inverse operation); when |r| is above it, the next value toward zero is the toward-zero result. A finite result
// that overflowed to infinity becomes the largest finite value of its sign. Non-finite inputs keep r.
// DN (denormal flush) is TcFpu's business, applied to the operands before and to the results after.
// Not a script itself; used by TcFpu (apply: one SH-4 instruction) and its own self-test (main).
import java.math.BigDecimal;

public class TcRound {
    static BigDecimal bd(double x) {
        return new BigDecimal(x); // exact
    }

    static boolean fin(double... xs) {
        for (double x : xs) if (Double.isNaN(x) || Double.isInfinite(x)) return false;
        return true;
    }

    // r rounded to nearest; cmp = sign of (|r| - |exact|), or null when r is exact or not comparable.
    static float towardZero(float r, int cmp) {
        if (Float.isNaN(r)) return r;
        if (Float.isInfinite(r)) return r > 0 ? Float.MAX_VALUE : -Float.MAX_VALUE;
        return cmp > 0 ? Math.nextAfter(r, 0.0) : r;
    }

    static double towardZero(double r, int cmp) {
        if (Double.isNaN(r)) return r;
        if (Double.isInfinite(r)) return r > 0 ? Double.MAX_VALUE : -Double.MAX_VALUE;
        return cmp > 0 ? Math.nextAfter(r, 0.0) : r;
    }

    static int above(double r, BigDecimal exact) {
        if (Double.isInfinite(r)) return 1;
        return bd(r).abs().compareTo(exact.abs());
    }

    // ---------- single precision ----------
    static float add(float a, float b, int rm) {
        float r = a + b;
        if (rm == 0 || !fin(a, b)) return r;
        return towardZero(r, above(r, bd(a).add(bd(b))));
    }

    static float sub(float a, float b, int rm) {
        return add(a, -b, rm);
    }

    static float mul(float a, float b, int rm) {
        float r = a * b;
        if (rm == 0 || !fin(a, b)) return r;
        return towardZero(r, above(r, bd(a).multiply(bd(b))));
    }

    static float div(float a, float b, int rm) {
        float r = a / b;
        if (rm == 0 || !fin(a, b) || b == 0 || a == 0) return r;
        if (Float.isInfinite(r)) return towardZero(r, 1);
        return towardZero(r, bd(r).abs().multiply(bd(b).abs()).compareTo(bd(a).abs()));
    }

    static float sqrt(float a, int rm) {
        float r = (float) Math.sqrt(a); // the double root rounded once to float: correctly rounded (p >= 2*24+2)
        if (rm == 0 || !fin(a) || a <= 0) return r;
        return towardZero(r, bd(r).multiply(bd(r)).compareTo(bd(a)));
    }

    static float fromInt(int i, int rm) {
        float r = (float) i;
        return rm == 0 ? r : towardZero(r, above(r, BigDecimal.valueOf(i)));
    }

    static float fromDouble(double d, int rm) {
        float r = (float) d;
        if (rm == 0 || !fin(d)) return r;
        return towardZero(r, above(r, bd(d)));
    }

    // ---------- double precision ----------
    static double add(double a, double b, int rm) {
        double r = a + b;
        if (rm == 0 || !fin(a, b)) return r;
        return towardZero(r, above(r, bd(a).add(bd(b))));
    }

    static double sub(double a, double b, int rm) {
        return add(a, -b, rm);
    }

    static double mul(double a, double b, int rm) {
        double r = a * b;
        if (rm == 0 || !fin(a, b)) return r;
        return towardZero(r, above(r, bd(a).multiply(bd(b))));
    }

    static double div(double a, double b, int rm) {
        double r = a / b;
        if (rm == 0 || !fin(a, b) || b == 0 || a == 0) return r;
        if (Double.isInfinite(r)) return towardZero(r, 1);
        return towardZero(r, bd(r).abs().multiply(bd(b).abs()).compareTo(bd(a).abs()));
    }

    static double sqrt(double a, int rm) {
        double r = Math.sqrt(a); // correctly rounded to nearest (IEEE)
        if (rm == 0 || !fin(a) || a <= 0) return r;
        return towardZero(r, bd(r).multiply(bd(r)).compareTo(bd(a)));
    }

    // ---------- SH-4 instructions (TcFpu's RM model) ----------
    // The results of one float instruction, from and into regs {register -> bits} (fr/xf/FPUL 32 bits, dr 64 bits),
    // in the operation order of the patched SLEIGH and tc_rt.c; pr: FPSCR.PR (the paired instructions name dr).
    static void apply(TcFpu.Op op, java.util.Map<String, Long> regs, int rm, boolean pr) {
        String[] a = op.a;
        if (pr && TcFpu.PAIRED.contains(op.kind)) {
            String m = TcFpu.dr(a[0]), n = TcFpu.dr(a[a.length - 1]);
            double x = d(regs, m), y = d(regs, n);
            switch (op.kind) {
                case "fadd": regs.put(n, db(add(y, x, rm))); break;
                case "fsub": regs.put(n, db(sub(y, x, rm))); break;
                case "fmul": regs.put(n, db(mul(y, x, rm))); break;
                case "fdiv": regs.put(n, db(div(y, x, rm))); break;
                case "fsqrt": regs.put(n, db(sqrt(y, rm))); break;
                default: break; // fcmp: no result
            }
            return;
        }
        switch (op.kind) {
            case "fadd": regs.put(a[1], fb(add(f(regs, a[1]), f(regs, a[0]), rm))); break;
            case "fsub": regs.put(a[1], fb(sub(f(regs, a[1]), f(regs, a[0]), rm))); break;
            case "fmul": regs.put(a[1], fb(mul(f(regs, a[1]), f(regs, a[0]), rm))); break;
            case "fdiv": regs.put(a[1], fb(div(f(regs, a[1]), f(regs, a[0]), rm))); break;
            case "fsqrt": regs.put(a[0], fb(sqrt(f(regs, a[0]), rm))); break;
            case "fsrra": if (!pr) regs.put(a[0], fb(div(1f, sqrt(f(regs, a[0]), rm), rm))); break;
            case "fmac": if (!pr) regs.put(a[2], fb(add(mul(f(regs, "fr0"), f(regs, a[1]), rm), f(regs, a[2]), rm))); break;
            case "fipr":
                if (pr) break;
                float t = 0;
                for (int i = 0; i < 4; i++) {
                    float p = mul(f(regs, "fr" + (op.n + i)), f(regs, "fr" + (op.m + i)), rm);
                    t = i == 0 ? p : add(t, p, rm);
                }
                regs.put("fr" + (op.n + 3), fb(t));
                break;
            case "ftrv":
                if (pr) break;
                float[] v = new float[4];
                for (int row = 0; row < 4; row++) {
                    for (int k = 0; k < 4; k++) {
                        float p = mul(f(regs, "xf" + (row + 4 * k)), f(regs, "fr" + (op.n + k)), rm);
                        v[row] = k == 0 ? p : add(v[row], p, rm);
                    }
                }
                for (int row = 0; row < 4; row++) regs.put("fr" + (op.n + row), fb(v[row]));
                break;
            case "float": {
                int i = (int) (long) regs.get("FPUL");
                boolean single = !pr || TcFpu.num(a[1]) % 2 == 1;
                if (single) regs.put(a[1], fb(fromInt(i, rm)));
                else regs.put(TcFpu.dr(a[1]), db((double) i));
                break;
            }
            case "fcnvds": regs.put("FPUL", fb(fromDouble(d(regs, a[0]), rm))); break;
            case "fcnvsd": regs.put(a[1], db((double) f(regs, "FPUL"))); break;
            default: break; // fcmp: no result
        }
    }

    static float f(java.util.Map<String, Long> regs, String r) {
        return Float.intBitsToFloat((int) (long) regs.get(r));
    }

    static double d(java.util.Map<String, Long> regs, String r) {
        return Double.longBitsToDouble(regs.get(r));
    }

    static long fb(float x) {
        return Float.floatToRawIntBits(x) & 0xffffffffL;
    }

    static long db(double x) {
        return Double.doubleToRawLongBits(x);
    }

    static boolean isNaN(String r, long bits) {
        return TcFpu.wide(r) ? Double.isNaN(Double.longBitsToDouble(bits)) : Float.isNaN(Float.intBitsToFloat((int) bits));
    }

    // Self-test: java TcRound.java [n]. Checks RZ results against an independent BigDecimal rounding (exact value
    // truncated onto the float grid), and RN results against Java's own arithmetic, on random and edge operands.
    public static void main(String[] args) {
        java.util.Random rnd = new java.util.Random(5);
        int n = args.length > 0 ? Integer.parseInt(args[0]) : 200000, bad = 0;
        java.math.MathContext mc = new java.math.MathContext(400); // exact for any float-representable value
        for (int i = 0; i < n; i++) {
            float a = pick(rnd), b = pick(rnd);
            if (!fin(a, b)) continue;
            BigDecimal[] ex = {bd(a).add(bd(b)), bd(a).multiply(bd(b)),
                b == 0 ? null : bd(a).divide(bd(b), mc), a < 0 ? null : bd(a).sqrt(mc)};
            float[] got = {add(a, b, 1), mul(a, b, 1), div(a, b, 1), sqrt(a, 1)};
            for (int k = 0; k < 4; k++) {
                if (ex[k] == null || !fin(a, b) || (k == 3 && a == 0) || (k == 2 && a == 0)) continue;
                float want = truncF(ex[k]);
                if (Float.floatToRawIntBits(want) != Float.floatToRawIntBits(got[k]) && !(want == 0 && got[k] == 0)) {
                    if (bad++ < 10) System.out.println("op " + k + " " + a + " " + b + " got " + got[k] + " want " + want);
                }
            }
        }
        System.out.println(n + " cases, " + bad + " mismatches");
    }

    static float pick(java.util.Random r) {
        switch (r.nextInt(4)) {
            case 0: return Float.intBitsToFloat(r.nextInt());
            case 1: return (float) (r.nextGaussian() * 1000);
            case 2: return (r.nextBoolean() ? 1 : -1) * (float) Math.scalb(1.0 + r.nextInt(8) / 8.0, r.nextInt(280) - 140);
            default: return r.nextInt(1 << 26) - (1 << 25);
        }
    }

    // The exact value truncated toward zero onto the float grid (normal and subnormal), saturating at MAX_VALUE.
    static float truncF(BigDecimal x) {
        if (x.signum() == 0) return 0f;
        BigDecimal ax = x.abs();
        float lo = 0f, hi = Float.MAX_VALUE;
        if (ax.compareTo(bd(Float.MAX_VALUE)) >= 0) lo = Float.MAX_VALUE;
        else {
            int ilo = 0, ihi = Float.floatToRawIntBits(Float.MAX_VALUE);
            while (ilo < ihi) { // largest float bits whose value <= ax
                int mid = ilo + (ihi - ilo + 1) / 2;
                if (bd(Float.intBitsToFloat(mid)).compareTo(ax) <= 0) ilo = mid;
                else ihi = mid - 1;
            }
            lo = Float.intBitsToFloat(ilo);
        }
        return x.signum() < 0 ? -lo : lo;
    }
}

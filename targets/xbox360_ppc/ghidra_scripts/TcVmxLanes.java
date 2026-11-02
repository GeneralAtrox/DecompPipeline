import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The vector register file, VSCR, lane access and the single-precision helpers. */
abstract class TcVmxLanes extends TcVmxDecode {
    // ============================================================================================ state
    /** The vector register file: v[n] is int[4], word 0 leftmost. */
    public final int[][] v = new int[128][4];
    /** VSCR: bit 16 NJ (always treated as set), bit 0 SAT. */
    public int vscr = 0x00010000;
    /** CR6 nibble of the last exec(): 8 all true, 2 all false (vcmpbfp: 2 all in bounds); -1 when not an Rc op. */
    public int cr6 = -1;
    boolean satHit;

    public int[] get(int r) {
        return v[r].clone();
    }

    public void set(int r, int w0, int w1, int w2, int w3) {
        v[r] = new int[] {w0, w1, w2, w3};
    }

    // ============================================================================================ lane access
    static int gb(int[] x, int i) {
        return (x[i >> 2] >>> (24 - 8 * (i & 3))) & 0xFF;
    }

    static void pb(int[] x, int i, int val) {
        int s = 24 - 8 * (i & 3);
        x[i >> 2] = (x[i >> 2] & ~(0xFF << s)) | ((val & 0xFF) << s);
    }

    static int gh(int[] x, int i) {
        return (x[i >> 1] >>> (16 - 16 * (i & 1))) & 0xFFFF;
    }

    static void ph(int[] x, int i, int val) {
        int s = 16 - 16 * (i & 1);
        x[i >> 1] = (x[i >> 1] & ~(0xFFFF << s)) | ((val & 0xFFFF) << s);
    }

    static byte[] toB(int[] x) {
        byte[] b = new byte[16];
        for (int i = 0; i < 16; i++) {
            b[i] = (byte) gb(x, i);
        }
        return b;
    }

    static int[] toW(byte[] b) {
        int[] x = new int[4];
        for (int i = 0; i < 16; i++) {
            pb(x, i, b[i]);
        }
        return x;
    }

    /** Element i of the given size (8/16/32 bits), signed or unsigned, as a long. */
    static long el(int[] x, int size, int i, boolean sg) {
        switch (size) {
            case 8: {
                int b = gb(x, i);
                return sg ? (byte) b : b;
            }
            case 16: {
                int h = gh(x, i);
                return sg ? (short) h : h;
            }
            default:
                return sg ? x[i] : x[i] & 0xFFFFFFFFL;
        }
    }

    static void put(int[] x, int size, int i, long val) {
        switch (size) {
            case 8: pb(x, i, (int) val); break;
            case 16: ph(x, i, (int) val); break;
            default: x[i] = (int) val;
        }
    }

    interface L2 {
        long ap(long a, long b);
    }

    static int[] map2(int[] a, int[] b, int size, boolean sg, L2 f) {
        int[] r = new int[4];
        for (int i = 0; i < 128 / size; i++) {
            put(r, size, i, f.ap(el(a, size, i, sg), el(b, size, i, sg)));
        }
        return r;
    }

    long sat(long val, int size, boolean sg) {
        long lo = sg ? -(1L << (size - 1)) : 0, hi = sg ? (1L << (size - 1)) - 1 : (1L << size) - 1;
        if (val < lo) {
            satHit = true;
            return lo;
        }
        if (val > hi) {
            satHit = true;
            return hi;
        }
        return val;
    }

    static int size(char c) {
        return c == 'b' ? 8 : c == 'h' ? 16 : 32;
    }

    // ============================================================================================ float helpers
    static final int QNAN = 0x7FC00000;

    static boolean isNaN(int b) {
        return (b & 0x7FFFFFFF) > 0x7F800000;
    }

    static int quiet(int b) {
        return b | 0x00400000;
    }

    /** Non-Java mode input rule: a denormal reads as zero of the same sign. */
    static int ftz(int b) {
        return (b & 0x7F800000) == 0 ? (b & 0x80000000) : b;
    }

    static float F(int b) {
        return Float.intBitsToFloat(b);
    }

    /** A float result to lane bits: NaN is the default NaN, a denormal flushes to zero of the same sign. */
    static int fb(float f) {
        int b = Float.floatToRawIntBits(f);
        return isNaN(b) ? QNAN : ftz(b);
    }

    static int fadd(int a, int b) {
        if (isNaN(a)) {
            return quiet(a);
        }
        if (isNaN(b)) {
            return quiet(b);
        }
        return fb(F(ftz(a)) + F(ftz(b)));
    }

    static int fsub(int a, int b) {
        if (isNaN(a)) {
            return quiet(a);
        }
        if (isNaN(b)) {
            return quiet(b);
        }
        return fb(F(ftz(a)) - F(ftz(b)));
    }

    static int fmul(int a, int b) {
        if (isNaN(a)) {
            return quiet(a);
        }
        if (isNaN(b)) {
            return quiet(b);
        }
        return fb(F(ftz(a)) * F(ftz(b)));
    }

    /** x * y + z, one rounding. p0..p2 are the three operands in NaN-priority order. neg: negate the result. */
    static int fma3(int x, int y, int z, int p0, int p1, int p2, boolean neg) {
        if (isNaN(p0)) {
            return quiet(p0);
        }
        if (isNaN(p1)) {
            return quiet(p1);
        }
        if (isNaN(p2)) {
            return quiet(p2);
        }
        int r = fb(Math.fma(F(ftz(x)), F(ftz(y)), F(ftz(z))));
        return (neg && !isNaN(r)) ? r ^ 0x80000000 : r;
    }

    static int fmax(int a, int b) {
        if (isNaN(a)) {
            return quiet(a);
        }
        if (isNaN(b)) {
            return quiet(b);
        }
        return fb(Math.max(F(ftz(a)), F(ftz(b))));
    }

    static int fmin(int a, int b) {
        if (isNaN(a)) {
            return quiet(a);
        }
        if (isNaN(b)) {
            return quiet(b);
        }
        return fb(Math.min(F(ftz(a)), F(ftz(b))));
    }

    /** vrfin / vrfiz / vrfip / vrfim. mode: 'n' nearest even, 'z' toward zero, 'p' up, 'm' down. */
    static int frnd(int a, char mode) {
        if (isNaN(a)) {
            return quiet(a);
        }
        double d = F(ftz(a));
        double r;
        switch (mode) {
            case 'n': r = Math.rint(d); break;
            case 'p': r = Math.ceil(d); break;
            case 'm': r = Math.floor(d); break;
            default: r = d < 0 ? Math.ceil(d) : Math.floor(d);
        }
        return fb((float) r);
    }
}

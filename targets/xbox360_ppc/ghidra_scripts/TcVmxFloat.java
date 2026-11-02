import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Float conversions, the exact dot product and the Direct3D pack and unpack. */
abstract class TcVmxFloat extends TcVmxLanes {
    int ctsxs(int a, int u) {
        if (isNaN(a)) {
            satHit = true;
            return 0;
        }
        double d = (double) F(ftz(a)) * (double) (1L << u);
        long t = (long) d;                              // truncates toward zero, saturates at the long range
        if (t > Integer.MAX_VALUE) {
            satHit = true;
            return Integer.MAX_VALUE;
        }
        if (t < Integer.MIN_VALUE) {
            satHit = true;
            return Integer.MIN_VALUE;
        }
        return (int) t;
    }

    int ctuxs(int a, int u) {
        if (isNaN(a)) {
            satHit = true;
            return 0;
        }
        double d = (double) F(ftz(a)) * (double) (1L << u);
        long t = (long) d;
        if (t < 0) {
            satHit = true;
            return 0;
        }
        if (t > 0xFFFFFFFFL) {
            satHit = true;
            return -1;
        }
        return (int) t;
    }

    /** Sum of the exact products of the first n lane pairs, rounded once to single (see the doc on vmsum). */
    static int dot(int[] a, int[] b, int n) {
        for (int i = 0; i < n; i++) {
            if (isNaN(a[i])) {
                return quiet(a[i]);
            }
            if (isNaN(b[i])) {
                return quiet(b[i]);
            }
        }
        boolean posInf = false, negInf = false, invalid = false, allNegZero = true;
        BigInteger[] m = new BigInteger[n];
        int[] e = new int[n];
        int emin = Integer.MAX_VALUE;
        for (int i = 0; i < n; i++) {
            int x = ftz(a[i]), y = ftz(b[i]);
            boolean neg = ((x ^ y) & 0x80000000) != 0;
            boolean xz = (x & 0x7FFFFFFF) == 0, yz = (y & 0x7FFFFFFF) == 0;
            boolean xi = (x & 0x7FFFFFFF) == 0x7F800000, yi = (y & 0x7FFFFFFF) == 0x7F800000;
            m[i] = BigInteger.ZERO;
            if (xi || yi) {
                if (xz || yz) {
                    invalid = true;
                } else if (neg) {
                    negInf = true;
                } else {
                    posInf = true;
                }
                allNegZero = false;
                continue;
            }
            if (xz || yz) {
                if (!neg) {
                    allNegZero = false;
                }
                continue;
            }
            allNegZero = false;
            long mx = (x & 0x7FFFFF) | 0x800000, my = (y & 0x7FFFFF) | 0x800000;
            int ex = ((x >>> 23) & 0xFF) - 150, ey = ((y >>> 23) & 0xFF) - 150;
            BigInteger p = BigInteger.valueOf(mx * my);
            m[i] = neg ? p.negate() : p;
            e[i] = ex + ey;
            emin = Math.min(emin, e[i]);
        }
        if (invalid || (posInf && negInf)) {
            return QNAN;
        }
        if (posInf) {
            return 0x7F800000;
        }
        if (negInf) {
            return 0xFF800000;
        }
        BigInteger s = BigInteger.ZERO;
        for (int i = 0; i < n; i++) {
            if (m[i].signum() != 0) {
                s = s.add(m[i].shiftLeft(e[i] - emin));
            }
        }
        if (s.signum() == 0) {
            return allNegZero ? 0x80000000 : 0;
        }
        int sign = s.signum() < 0 ? 0x80000000 : 0;
        BigInteger mag = s.abs();
        int bl = mag.bitLength();
        int shift = bl - 24;
        BigInteger q;
        int E = bl - 1 + emin;                          // value in [2^E, 2^(E+1))
        if (shift > 0) {
            q = mag.shiftRight(shift);
            BigInteger rem = mag.subtract(q.shiftLeft(shift));
            BigInteger half = BigInteger.ONE.shiftLeft(shift - 1);
            int c = rem.compareTo(half);
            if (c > 0 || (c == 0 && q.testBit(0))) {
                q = q.add(BigInteger.ONE);
                if (q.bitLength() > 24) {
                    q = q.shiftRight(1);
                    E++;
                }
            }
        } else {
            q = mag.shiftLeft(-shift);
        }
        if (E > 127) {
            return sign | 0x7F800000;
        }
        if (E < -126) {
            return sign;                                // a denormal result flushes to zero
        }
        return sign | ((E + 127) << 23) | (q.intValue() & 0x7FFFFF);
    }

    // ============================================================================================ Direct3D pack
    static final double P22 = 4194304.0, P23 = 8388608.0;

    /** The packed field of a float whose value is base + n * 2^-scale; NaN gives the lower bound. */
    static int pkFix(int fbits, double base, double scale, int lo, int hi) {
        float f = F(fbits);
        if (Float.isNaN(f)) {
            return lo;
        }
        double t = ((double) f - base) * scale;
        if (t <= lo) {
            return lo;
        }
        if (t >= hi) {
            return hi;
        }
        return (int) t;
    }

    static int pkHalf(int fbits) {
        float f = F(fbits);
        if (Float.isNaN(f)) {
            return 0xFBFF;                              // UNVERIFIED: NaN takes the lower clamp, as the SSE clamp does
        }
        if (f > 65504.0f) {
            f = 65504.0f;                               // UNVERIFIED: overflow saturates to the largest finite half
        }
        if (f < -65504.0f) {
            f = -65504.0f;
        }
        return Float.floatToFloat16(f) & 0xFFFF;
    }

    static int[] vpkd3d(int[] d, int[] b, int type, int mask, int z) {
        int hi = 0, lo;
        switch (type) {
            case 0:                                     // D3DCOLOR: ARGB bytes from w, x, y, z (each 3.0 + n * 2^-22)
                lo = (pkFix(b[3], 3.0, P22, 0, 255) << 24) | (pkFix(b[0], 3.0, P22, 0, 255) << 16)
                        | (pkFix(b[1], 3.0, P22, 0, 255) << 8) | pkFix(b[2], 3.0, P22, 0, 255);
                break;
            case 1:                                     // SHORT_2: x:y, 16-bit signed, [-32767, 32767]
                lo = (pkFix(b[0], 3.0, P22, -32767, 32767) << 16) | (pkFix(b[1], 3.0, P22, -32767, 32767) & 0xFFFF);
                break;
            case 2:                                     // 2_10_10_10: w:2 z:10 y:10 x:10, 10-bit signed [-511, 511]
                lo = (pkFix(b[3], 1.0, P23, 0, 3) << 30) | ((pkFix(b[2], 3.0, P22, -511, 511) & 0x3FF) << 20)
                        | ((pkFix(b[1], 3.0, P22, -511, 511) & 0x3FF) << 10) | (pkFix(b[0], 3.0, P22, -511, 511) & 0x3FF);
                break;
            case 3:                                     // FLOAT16_2
                lo = (pkHalf(b[0]) << 16) | pkHalf(b[1]);
                break;
            case 4:                                     // SHORT_4: a 64-bit result x:y:z:w
                hi = (pkFix(b[0], 3.0, P22, -32767, 32767) << 16) | (pkFix(b[1], 3.0, P22, -32767, 32767) & 0xFFFF);
                lo = (pkFix(b[2], 3.0, P22, -32767, 32767) << 16) | (pkFix(b[3], 3.0, P22, -32767, 32767) & 0xFFFF);
                break;
            case 5:                                     // FLOAT16_4
                hi = (pkHalf(b[0]) << 16) | pkHalf(b[1]);
                lo = (pkHalf(b[2]) << 16) | pkHalf(b[3]);
                break;
            default:
                throw new UnsupportedOperationException("vpkd3d128 type " + type + " (UNVERIFIED/unimplemented)");
        }
        int[] r = d.clone();
        // Placement (from Xenia's instr_vpkd3d128.s): mask 1 puts the word at lane 3-z; mask 2 puts hi:lo at
        // lanes 2-z, 3-z (a lane that falls outside 0..3 is dropped); mask 3 is the same, except z = 3 which writes
        // only lane 3 (the hi word). Mask 0 is not a valid encoding and changes nothing.
        if (mask == 1) {
            r[3 - z] = lo;
        } else if (mask == 2 || (mask == 3 && z < 3)) {
            int p = 2 - z;
            if (p >= 0) {
                r[p] = hi;
            }
            if (p + 1 >= 0 && p + 1 <= 3) {
                r[p + 1] = lo;
            }
        } else if (mask == 3) {
            r[3] = hi;
        }
        return r;
    }

    static int fix3(int n) {                    // 3.0 + n * 2^-22
        return 0x40400000 + n;
    }

    static int sfix(int n) {                    // a 16-bit signed field; the most negative value is a QNaN
        return n == -32768 ? QNAN : fix3(n);
    }

    static int[] vupkd3d(int[] b, int type) {
        int[] r = new int[4];
        int hi = b[2], lo = b[3];
        switch (type) {
            case 0:
                r[0] = 0x3F800000 + ((lo >>> 16) & 0xFF);
                r[1] = 0x3F800000 + ((lo >>> 8) & 0xFF);
                r[2] = 0x3F800000 + (lo & 0xFF);
                r[3] = 0x3F800000 + (lo >>> 24);
                break;
            case 1:
                r[0] = sfix(lo >> 16);
                r[1] = sfix((short) lo);
                r[2] = 0;
                r[3] = 0x3F800000;
                break;
            case 4:
                r[0] = sfix(hi >> 16);
                r[1] = sfix((short) hi);
                r[2] = sfix(lo >> 16);
                r[3] = sfix((short) lo);
                break;
            case 2: {
                for (int i = 0; i < 3; i++) {
                    int n = sext(lo >>> (10 * i), 10);
                    r[i] = n == -512 ? QNAN : fix3(n);
                }
                r[3] = 0x3F800000 + (lo >>> 30);
                break;
            }
            case 3:
                r[0] = Float.floatToRawIntBits(Float.float16ToFloat((short) (lo >>> 16)));
                r[1] = Float.floatToRawIntBits(Float.float16ToFloat((short) lo));
                r[2] = 0;
                r[3] = 0x3F800000;
                break;
            case 5:
                r[0] = Float.floatToRawIntBits(Float.float16ToFloat((short) (hi >>> 16)));
                r[1] = Float.floatToRawIntBits(Float.float16ToFloat((short) hi));
                r[2] = Float.floatToRawIntBits(Float.float16ToFloat((short) (lo >>> 16)));
                r[3] = Float.floatToRawIntBits(Float.float16ToFloat((short) lo));
                break;
            default:
                throw new UnsupportedOperationException("vupkd3d128 type " + type + " (UNVERIFIED/unimplemented)");
        }
        return r;
    }

    // ============================================================================================ execute
}

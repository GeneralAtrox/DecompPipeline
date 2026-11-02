import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The integer vector ops (add, sub, shifts, compares, multiplies) and the pack with saturation. */
abstract class TcVmxInt extends TcVmxFloat {
    int[] cmpResult(Op op, int[] r) {
        if (op.rc != NONE && op.rc != 0) {
            boolean all1 = true, all0 = true;
            for (int x : r) {
                all1 &= x == -1;
                all0 &= x == 0;
            }
            cr6 = (all1 ? 8 : 0) | (all0 ? 2 : 0);
        }
        return r;
    }

    int[] intAlu(Op op, String b, int[] A, int[] B, int[] C) {
        int[] r = new int[4];
        // ---- add / sub / avg / max / min / compares / shifts / rotates, by name pattern
        if (b.startsWith("vadd") && b.length() == 7 && b.charAt(4) != 'c') {
            int sz = size(b.charAt(5));
            boolean sg = b.charAt(4) == 's', sa = b.charAt(6) == 's';
            return map2(A, B, sz, sg, (x, y) -> sa ? sat(x + y, sz, sg) : x + y);
        }
        if (b.startsWith("vsub") && b.length() == 7 && b.charAt(4) != 'c') {
            int sz = size(b.charAt(5));
            boolean sg = b.charAt(4) == 's', sa = b.charAt(6) == 's';
            return map2(A, B, sz, sg, (x, y) -> sa ? sat(x - y, sz, sg) : x - y);
        }
        if ((b.startsWith("vmax") || b.startsWith("vmin")) && b.length() == 6) {
            boolean mx = b.charAt(2) == 'a';
            return map2(A, B, size(b.charAt(5)), b.charAt(4) == 's', (x, y) -> mx ? Math.max(x, y) : Math.min(x, y));
        }
        if (b.startsWith("vavg")) {
            return map2(A, B, size(b.charAt(5)), b.charAt(4) == 's', (x, y) -> (x + y + 1) >> 1);
        }
        if (b.startsWith("vcmpequ")) {
            return cmpResult(op, map2(A, B, size(b.charAt(7)), false, (x, y) -> x == y ? -1 : 0));
        }
        if (b.startsWith("vcmpgt")) {
            return cmpResult(op, map2(A, B, size(b.charAt(7)), b.charAt(6) == 's', (x, y) -> x > y ? -1 : 0));
        }
        if (b.equals("vrlb") || b.equals("vrlh") || b.equals("vrlw")) {
            int sz = size(b.charAt(3));
            return map2(A, B, sz, false, (x, y) -> {
                int n = (int) (y & (sz - 1));
                long mk = sz == 32 ? 0xFFFFFFFFL : (1L << sz) - 1;
                return ((x << n) | (x >>> (sz - n))) & mk;
            });
        }
        if (b.equals("vslb") || b.equals("vslh") || b.equals("vslw")) {
            int sz = size(b.charAt(3));
            return map2(A, B, sz, false, (x, y) -> x << (y & (sz - 1)));
        }
        if (b.equals("vsrb") || b.equals("vsrh") || b.equals("vsrw")) {
            int sz = size(b.charAt(3));
            return map2(A, B, sz, false, (x, y) -> x >>> (y & (sz - 1)));
        }
        if (b.equals("vsrab") || b.equals("vsrah") || b.equals("vsraw")) {
            int sz = size(b.charAt(4));
            return map2(A, B, sz, true, (x, y) -> x >> (y & (sz - 1)));
        }
        switch (b) {
            case "vaddcuw":
                for (int i = 0; i < 4; i++) {
                    r[i] = ((A[i] & 0xFFFFFFFFL) + (B[i] & 0xFFFFFFFFL)) > 0xFFFFFFFFL ? 1 : 0;
                }
                return r;
            case "vsubcuw":                             // 1 when there is no borrow (A >= B unsigned)
                for (int i = 0; i < 4; i++) {
                    r[i] = Integer.compareUnsigned(A[i], B[i]) >= 0 ? 1 : 0;
                }
                return r;
            // ------------------------------------------------------------------------------------ shifts of the whole register
            case "vsl":
            case "vsr": {
                int n = gb(B, 15) & 7;
                byte[] a = toB(A), o = new byte[16];
                for (int i = 0; i < 16; i++) {
                    int cur = a[i] & 0xFF;
                    if (b.equals("vsl")) {
                        int nxt = i < 15 ? a[i + 1] & 0xFF : 0;
                        o[i] = (byte) ((cur << n) | (n == 0 ? 0 : nxt >>> (8 - n)));
                    } else {
                        int prv = i > 0 ? a[i - 1] & 0xFF : 0;
                        o[i] = (byte) ((cur >>> n) | (n == 0 ? 0 : prv << (8 - n)));
                    }
                }
                return toW(o);
            }
            case "vslo":
            case "vsro": {
                int n = (gb(B, 15) >>> 3) & 0xF;
                for (int i = 0; i < 16; i++) {
                    int k = b.equals("vslo") ? i + n : i - n;
                    pb(r, i, (k >= 0 && k < 16) ? gb(A, k) : 0);
                }
                return r;
            }
            // ------------------------------------------------------------------------------------ merge / splat
            case "vmrghb": case "vmrglb": case "vmrghh": case "vmrglh": case "vmrghw": case "vmrglw": {
                int sz = size(b.charAt(5));
                int n = 128 / sz, base = b.charAt(4) == 'h' ? 0 : n / 2;
                for (int i = 0; i < n / 2; i++) {
                    put(r, sz, 2 * i, el(A, sz, base + i, false));
                    put(r, sz, 2 * i + 1, el(B, sz, base + i, false));
                }
                return r;
            }
            case "vspltb": case "vsplth": case "vspltw": {
                int sz = size(b.charAt(5));
                long e = el(B, sz, op.uimm & (128 / sz - 1), false);
                for (int i = 0; i < 128 / sz; i++) {
                    put(r, sz, i, e);
                }
                return r;
            }
            // ------------------------------------------------------------------------------------ pack
            case "vpkuhum": return pack(A, B, 16, 8, 'm', false);
            case "vpkuwum": return pack(A, B, 32, 16, 'm', false);
            case "vpkuhus": return pack(A, B, 16, 8, 's', false);
            case "vpkuwus": return pack(A, B, 32, 16, 's', false);
            case "vpkshus": return pack(A, B, 16, 8, 'u', true);
            case "vpkswus": return pack(A, B, 32, 16, 'u', true);
            case "vpkshss": return pack(A, B, 16, 8, 's', true);
            case "vpkswss": return pack(A, B, 32, 16, 's', true);
            case "vpkpx": {
                for (int i = 0; i < 8; i++) {
                    int w = i < 4 ? A[i] : B[i - 4];
                    ph(r, i, (((w >>> 24) & 1) << 15) | (((w >>> 19) & 31) << 10) | (((w >>> 11) & 31) << 5)
                            | ((w >>> 3) & 31));
                }
                return r;
            }
            // ------------------------------------------------------------------------------------ unpack
            case "vupkhsb": case "vupklsb": {
                int off = b.charAt(4) == 'h' ? 0 : 8;
                for (int i = 0; i < 8; i++) {
                    ph(r, i, (byte) gb(B, off + i));
                }
                return r;
            }
            case "vupkhsh": case "vupklsh": {
                int off = b.charAt(4) == 'h' ? 0 : 4;
                for (int i = 0; i < 4; i++) {
                    r[i] = (short) gh(B, off + i);
                }
                return r;
            }
            case "vupkhpx": case "vupklpx": {
                int off = b.charAt(4) == 'h' ? 0 : 4;
                for (int i = 0; i < 4; i++) {
                    int h = gh(B, off + i);
                    r[i] = ((h & 0x8000) != 0 ? 0xFF000000 : 0) | (((h >>> 10) & 31) << 16) | (((h >>> 5) & 31) << 8) | (h & 31);
                }
                return r;
            }
            // ------------------------------------------------------------------------------------ multiply
            case "vmuleub": case "vmuloub": case "vmulesb": case "vmulosb": {
                int p = b.charAt(4) == 'e' ? 0 : 1;
                boolean sg = b.charAt(5) == 's';
                for (int i = 0; i < 8; i++) {
                    ph(r, i, (int) (el(A, 8, 2 * i + p, sg) * el(B, 8, 2 * i + p, sg)));
                }
                return r;
            }
            case "vmuleuh": case "vmulouh": case "vmulesh": case "vmulosh": {
                int p = b.charAt(4) == 'e' ? 0 : 1;
                boolean sg = b.charAt(5) == 's';
                for (int i = 0; i < 4; i++) {
                    r[i] = (int) (el(A, 16, 2 * i + p, sg) * el(B, 16, 2 * i + p, sg));
                }
                return r;
            }
            case "vmladduhm":
                for (int i = 0; i < 8; i++) {
                    ph(r, i, (int) (el(A, 16, i, false) * el(B, 16, i, false) + el(C, 16, i, false)));
                }
                return r;
            case "vmhaddshs":
            case "vmhraddshs": {
                long rnd = b.equals("vmhraddshs") ? 0x4000 : 0;
                for (int i = 0; i < 8; i++) {
                    long p = (el(A, 16, i, true) * el(B, 16, i, true) + rnd) >> 15;
                    ph(r, i, (int) sat(p + el(C, 16, i, true), 16, true));
                }
                return r;
            }
            case "vmsumubm": case "vmsummbm": {
                boolean as = b.equals("vmsummbm");        // A signed bytes, B unsigned bytes
                for (int i = 0; i < 4; i++) {
                    long s = C[i] & 0xFFFFFFFFL;
                    for (int k = 0; k < 4; k++) {
                        s += el(A, 8, 4 * i + k, as) * el(B, 8, 4 * i + k, false);
                    }
                    r[i] = (int) s;
                }
                return r;
            }
            case "vmsumuhm": case "vmsumuhs": case "vmsumshm": case "vmsumshs": {
                boolean sg = b.charAt(5) == 's', sa = b.charAt(7) == 's';
                for (int i = 0; i < 4; i++) {
                    long s = sg ? C[i] : C[i] & 0xFFFFFFFFL;
                    for (int k = 0; k < 2; k++) {
                        s += el(A, 16, 2 * i + k, sg) * el(B, 16, 2 * i + k, sg);
                    }
                    r[i] = (int) (sa ? sat(s, 32, sg) : s);
                }
                return r;
            }
            // ------------------------------------------------------------------------------------ sums
            case "vsum4ubs": case "vsum4sbs": {
                boolean sg = b.charAt(5) == 's';
                for (int i = 0; i < 4; i++) {
                    long s = el(B, 32, i, sg);
                    for (int k = 0; k < 4; k++) {
                        s += el(A, 8, 4 * i + k, sg);
                    }
                    r[i] = (int) sat(s, 32, sg);
                }
                return r;
            }
            case "vsum4shs":
                for (int i = 0; i < 4; i++) {
                    long s = el(B, 32, i, true) + el(A, 16, 2 * i, true) + el(A, 16, 2 * i + 1, true);
                    r[i] = (int) sat(s, 32, true);
                }
                return r;
            case "vsum2sws":
                r[1] = (int) sat((long) A[0] + A[1] + B[1], 32, true);
                r[3] = (int) sat((long) A[2] + A[3] + B[3], 32, true);
                return r;
            case "vsumsws":
                r[3] = (int) sat((long) A[0] + A[1] + A[2] + A[3] + B[3], 32, true);
                return r;
            default:
                throw new UnsupportedOperationException(op.mn);
        }
    }

    /**
     * vpk*: the elements of A then B, each of srcSize bits, narrowed to dstSize bits.
     * mode 'm' modulo (low bits), 's' saturate to the dst range of the SAME signedness as src ... see the callers:
     * the src signedness is {@code srcSigned}; 'u' saturates to unsigned dst; 's' saturates to (src signedness) dst.
     */
    int[] pack(int[] A, int[] B, int srcSize, int dstSize, char mode, boolean srcSigned) {
        int n = 128 / srcSize;
        int[] r = new int[4];
        for (int i = 0; i < 2 * n; i++) {
            long x = el(i < n ? A : B, srcSize, i % n, srcSigned);
            long y;
            if (mode == 'm') {
                y = x;
            } else if (mode == 'u') {
                y = sat(x, dstSize, false);
            } else {
                y = sat(x, dstSize, srcSigned);
            }
            put(r, dstSize, i, y);
        }
        return r;
    }
}

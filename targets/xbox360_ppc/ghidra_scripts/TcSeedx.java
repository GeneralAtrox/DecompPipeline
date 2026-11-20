import java.util.*;

/**
 * Op-aware input classes for the Xbox 360 oracle's extra vectors (facts "seed_extra", project.json or target.json
 * "seed_extra": N per set; TcVectors.seedExtras), and the cases a vector op reached (TcVmx.event). Pure Java, no
 * Ghidra: tests/java/TcSeedxStub tests it.
 *
 * Random vectors never reach some cases of the vector ops a function holds (a diagnosis of 7 missed planted faults: no
 * halfword sum ever saturated in 82cbc588, no denormal product in 82de7510, and every vector of 82a1d008 took its
 * alignment early exit). A function whose vector ops pick classes gets N extra vectors per set, appended after the usual
 * ones and drawn from their own generators, so the usual vectors are exactly what they were. Each extra vector draws
 * from one class, the classes in turn:
 *   SAT    saturating integer ops (vadds/vsubs, saturating packs, vmsum*s, vmh*addshs, vsum*s, float-to-int with
 *          saturation): memory words built from each element width's extremes (0x7ff0-0x7fff, 0x8000-0x800f for a
 *          halfword, 0x7f 0x80 0xff for a byte, the word limits, a pack's source values next to its target's limits);
 *   TINY   float multiplies (vmaddfp, vnmsubfp, vmulfp128, vmsum3/4fp128, vmaddcfp128 and the 128 multiply-adds):
 *          floats near 2^-68 (a product of two rounds to a nonzero denormal), denormals and floats next to FLT_MIN,
 *          in memory and in f1..f13;
 *   ALIGN  vector loads and stores: every pointer the vector draws is 16-aligned and every small count a multiple of 4,
 *          so an alignment early exit (82a1d008) is passed; the usual vectors keep the unaligned ones. Memory words
 *          are such pointers, counts, or words of nonzero byte fields (bytes4).
 *   GATE   ALIGN for a function with an alignment gate (gateFields): a fast path entered only when the low bits of
 *          sums of argument-struct fields are zero (82a1d008: ((r9&3)|(G+4A)|(F+4*D*C))&15 == 0 with a nonzero byte
 *          D and r9 = min(E-C,(B-A)/2) > 0). Independent ALIGN words pass all of that in about 1 try in 200, so a
 *          seed's extras could miss the fast path entirely. A GATE vector draws as ALIGN, then sets each field the code
 *          before the gate reads off an argument register (gateFill): words are 16-aligned pointers near one base or
 *          small multiples of 16, bytes and halfwords nonzero multiples of 16. Any sum, difference, product or shift
 *          left of such fields has its low 4 bits zero, so the gate is always passed; a byte multiplier or divisor is
 *          never 0. Such a pointer plus a scaled count lands up to 16 KB past the window, in heap no chunk has
 *          written (zeros, on which 82a1d248's planted vmrghh swap changes nothing), so the first GATE_HEAP bytes of
 *          the heap get ALIGN words too, as a patch before the vector's own chunks (theirs win). A GATE vector also
 *          counts as novel while it reaches a block fewer than GATE_KEEP kept vectors reached (rare): one vector
 *          past the gate is not enough, as a fault there shows only on some data (82a1d4b0's vmrghb swap: 1 of 2).
 *          It replaces ALIGN in those functions only; every other function draws what it did.
 * A valid extra vector is kept when it reaches something new, and at most half of them when it does not (keepExtra):
 * the try budget is spent searching for the rare inputs instead of stopping at the first 16 valid vectors.
 */
public final class TcSeedx {
    static final int SAT = 1, TINY = 2, ALIGN = 4, GATE = 8;
    static final String[] NAMES = {"sat", "tiny", "align", "gate"};
    // element widths of the saturating ops (widths()): the extremes a word is built from
    static final int W_BYTE = 1, W_HALF = 2, W_WORD = 4, W_PK_HB = 8, W_PK_WH = 16, W_F2I = 32;

    static final String FMUL = "vmaddfp|vnmsubfp|vmulfp128|vmsum[34]fp128|vmaddfp128|vmaddcfp128|vnmsubfp128";

    static String base(String mn) {
        return mn.endsWith("128") ? mn.substring(0, mn.length() - 3) : mn;
    }

    /** The element widths whose extremes make a saturating op saturate; 0 for an op that never saturates. */
    static int widths(String mn) {
        String m = base(mn);
        if (m.matches("v(add|sub)[su][bhw]s")) return width(m.charAt(m.length() - 2));
        if (m.matches("vpk[su][hw][su]s")) return m.charAt(4) == 'h' ? W_PK_HB : W_PK_WH;
        if (m.matches("vmsum[su]hs|vmhr?addshs")) return W_HALF;
        if (m.matches("vsum4[su]bs")) return W_BYTE | W_WORD;
        if (m.matches("vsum4shs")) return W_HALF | W_WORD;
        if (m.matches("vsum2sws|vsumsws")) return W_WORD;
        if (m.matches("vct[su]xs|vcfp[su]xws")) return W_F2I;
        return 0;
    }

    static int width(char c) {
        return c == 'b' ? W_BYTE : c == 'h' ? W_HALF : W_WORD;
    }

    static boolean fmul(String mn) {
        return mn.matches(FMUL);
    }

    /** A vector load or store (lvx, lvlx, stvx128, lvewx and the rest; not lvsl/lvsr, which read no memory). */
    static boolean vmem(String mn) {
        return (mn.startsWith("lv") || mn.startsWith("stv")) && !mn.matches("lvs[lr](128)?");
    }

    /** The classes (SAT | TINY | ALIGN) a function holding these vector op mnemonics gets, and its saturating widths. */
    static int[] classes(Collection<String> mns) {
        int cls = 0, w = 0;
        for (String m : mns) {
            int x = widths(m);
            if (x != 0) cls |= SAT;
            w |= x;
            if (fmul(m)) cls |= TINY;
            if (vmem(m)) cls |= ALIGN;
        }
        return new int[] {cls, w};
    }

    /** The classes in turn: extra vector k draws from focus(cls)[k % length]. */
    static int[] focus(int cls) {
        List<Integer> out = new ArrayList<>();
        for (int c : new int[] {SAT, TINY, ALIGN, GATE}) if ((cls & c) != 0) out.add(c);
        int[] a = new int[out.size()];
        for (int i = 0; i < a.length; i++) a[i] = out.get(i);
        return a;
    }

    static String names(int cls) {
        StringBuilder b = new StringBuilder("[");
        for (int i = 0; i < NAMES.length; i++) {
            if ((cls & (1 << i)) != 0) b.append(b.length() > 1 ? "," : "").append('"').append(NAMES[i]).append('"');
        }
        return b.append(']').toString();
    }

    // ---------- values ----------
    static final int[] BYTE_X = {0x7f, 0x80, 0xff, 0x7e, 0x81, 0x01, 0xfe};
    static final int[] PK_HB = {0x007f, 0x0080, 0xff7f, 0xff80, 0x00ff, 0x0100, 0xff00, 0x7fff, 0x8000};
    static final int[] PK_WH = {0x00007fff, 0x00008000, 0xffff7fff, 0xffff8000, 0x0000ffff, 0x00010000, 0x7fffffff,
        0x80000000};
    static final int[] F2I = {0x4effffff, 0x4f000000, 0x4f800000, 0xcf000000, 0xcf000001, 0x4f7fffff, 0x7f800000,
        0xff800000, 0x7fc00000, 0xbf800000};

    static int half(Random r) {
        int k = r.nextInt(10);
        if (k < 4) return 0x7ff0 + r.nextInt(16);
        if (k < 8) return 0x8000 + r.nextInt(16);
        return k == 8 ? 0xffff : r.nextInt(0x10000);
    }

    /** A 32-bit memory word built from the extremes of one of the widths (a random one of those set). */
    static long satWord(Random r, int widths) {
        List<Integer> ws = new ArrayList<>();
        for (int b = 1; b <= W_F2I; b <<= 1) if ((widths & b) != 0) ws.add(b);
        int w = ws.isEmpty() ? W_HALF : ws.get(r.nextInt(ws.size()));
        long x = 0;
        switch (w) {
            case W_BYTE:
                for (int i = 0; i < 4; i++) x = (x << 8) | (r.nextInt(10) < 8 ? BYTE_X[r.nextInt(BYTE_X.length)] : r.nextInt(256));
                return x;
            case W_HALF:
                return ((long) half(r) << 16) | half(r);
            case W_PK_HB:
                return ((long) PK_HB[r.nextInt(PK_HB.length)] << 16) | PK_HB[r.nextInt(PK_HB.length)];
            case W_PK_WH:
                return PK_WH[r.nextInt(PK_WH.length)] & 0xffffffffL;
            case W_F2I:
                return F2I[r.nextInt(F2I.length)] & 0xffffffffL;
            default: {
                int k = r.nextInt(10);
                return (k < 4 ? 0x7ffffff0L + r.nextInt(16) : k < 8 ? 0x80000000L + r.nextInt(16) : k == 8 ? 0xffffffffL
                    : r.nextInt() & 0xffffffffL);
            }
        }
    }

    /**
     * A float's bits: near 2^-68 (2^-74..2^-63, so a product of two lies in 2^-148..2^-125 and nearly always rounds to a
     * nonzero single denormal; the old 2^-70..2^-58 put most products below 2^-149, which round to 0, or above FLT_MIN),
     * a denormal, next to FLT_MIN, or +-1 (a product keeps its partner).
     */
    static int tinyFloat(Random r) {
        int k = r.nextInt(10), sign = r.nextInt(2) << 31;
        if (k < 5) return sign | ((127 - 74 + r.nextInt(12)) << 23) | r.nextInt(0x800000);
        if (k < 7) return sign | (1 + r.nextInt(0x7fffff));
        if (k < 9) return sign | ((1 + r.nextInt(4)) << 23) | r.nextInt(0x800000);
        return sign | 0x3f800000;
    }

    /** The 16-aligned form of a pointer the vector draws. */
    static long align(long p) {
        return p & ~15L;
    }

    /** A small count that is a multiple of 4 (4..32). */
    static long count4(Random r) {
        return 4L * (1 + r.nextInt(8));
    }

    /**
     * A word of four byte fields, each 4, 8, 12 or 16: the word is a multiple of 4 and every byte an lbz reads from it
     * is a nonzero multiple of 4. 82a1d008 multiplies by the byte at +0xd and traps when it is 0, and the 16-aligned
     * pointers and plain counts both hold a 0 there.
     */
    static long bytes4(Random r) {
        long x = 0;
        for (int i = 0; i < 4; i++) x = (x << 8) | (4 * (1 + r.nextInt(4)));
        return x;
    }

    /** An ALIGN memory word: the 16-aligned pointer p (half), a multiple-of-4 count, or byte fields (bytes4). */
    static long alignWord(Random r, long p) {
        int k = r.nextInt(4);
        return k < 2 ? p : k == 2 ? count4(r) : bytes4(r);
    }

    // ---------- GATE: an alignment gate and the argument fields it reads (raw PowerPC words, a body in address order) ----------
    /**
     * Index of the first alignment gate, or -1: a mask of the low 2, 3 or 4 bits (rlwinm[.] rA,rS,0,28..30,31 or
     * andi. rA,rS,3/7/15), compared with 0 (cmpwi/cmplwi rA,0 within 3 words, or the record form's cr0) and branched
     * on by a bc reading that CR field within 3 words.
     */
    static int gateAt(int[] w) {
        for (int i = 0; i < w.length; i++) {
            int x = w[i], op = x >>> 26, ra = (x >>> 16) & 31;
            boolean rot = op == 21 && ((x >>> 11) & 31) == 0 && ((x >>> 1) & 31) == 31 && ((x >>> 6) & 31) >= 28
                && ((x >>> 6) & 31) <= 30;
            int ui = x & 0xffff;
            boolean andi = op == 28 && (ui == 3 || ui == 7 || ui == 15);
            if (!rot && !andi) continue;
            if (andi || (x & 1) != 0) {
                if (bcOn(w, i, 0)) return i;
            }
            for (int j = i + 1; j <= i + 3 && j < w.length; j++) {
                int c = w[j], cop = c >>> 26;
                if ((cop == 10 || cop == 11) && ((c >>> 16) & 31) == ra && (c & 0xffff) == 0 && bcOn(w, j, (c >>> 23) & 7)) return i;
            }
        }
        return -1;
    }

    // A bc in the 3 words after i that reads CR field cr.
    static boolean bcOn(int[] w, int i, int cr) {
        for (int j = i + 1; j <= i + 3 && j < w.length; j++) if (w[j] >>> 26 == 16 && ((w[j] >>> 16) & 31) / 4 == cr) return true;
        return false;
    }

    /** The GPRs a word may write (a superset: an X-form writes both of its register fields). */
    static int written(int x) {
        int op = x >>> 26, d = 1 << ((x >>> 21) & 31), a = 1 << ((x >>> 16) & 31);
        switch (op) {
            case 2: case 3: case 4: case 10: case 11: case 16: case 17: case 18: case 19: case 36: case 38: case 44:
            case 48: case 50: case 52: case 54: case 59: case 63: return 0;
            case 20: case 21: case 23: case 24: case 25: case 26: case 27: case 28: case 29: case 30:
            case 37: case 39: case 45: case 49: case 51: case 53: case 55: return a;
            case 62: return (x & 3) == 1 ? a : 0;
            case 33: case 35: case 41: case 43: case 31: return d | a;
            case 58: return d | ((x & 3) == 1 ? a : 0);
            default: return d;
        }
    }

    /**
     * The argument fields an alignment gate reads: {reg, offset, size} for each lwz, lbz, lhz, lha or ld rD,d(rA)
     * before the gate whose rA is r3..r10 and not written before it. None without a gate: the function keeps ALIGN.
     */
    static int[][] gateFields(int[] w) {
        int g = gateAt(w), dirty = 0;
        List<int[]> out = new ArrayList<>();
        Set<Long> seen = new HashSet<>();
        for (int i = 0; i < g; i++) {
            int x = w[i], op = x >>> 26, ra = (x >>> 16) & 31, d = (short) (x & 0xffff);
            int size = op == 32 ? 4 : op == 34 ? 1 : op == 40 || op == 42 ? 2 : op == 58 && (x & 3) == 0 ? 8 : 0;
            if (size > 0 && ra >= 3 && ra <= 10 && (dirty & (1 << ra)) == 0 && d >= 0 && seen.add(((long) ra << 32) | d)) {
                out.add(new int[] {ra, d, size});
            }
            dirty |= written(x);
        }
        return out.toArray(new int[0][]);
    }

    /** ALIGN becomes GATE in a function with gate fields. */
    static int withGate(int cls, int[][] fields) {
        return fields.length > 0 && (cls & ALIGN) != 0 ? (cls & ~ALIGN) | GATE : cls;
    }

    static final int GATE_HEAP = 0x4000;

    /** A gate field's value: a word a 16-aligned pointer near `near` or 16..64; a byte or halfword 16, 32, 48 or 64. */
    static long gateValue(Random r, int size, long near) {
        if (size <= 2) return 16 * (1 + r.nextInt(4));
        return r.nextInt(2) == 0 ? align(near) + 16 * r.nextInt(16) : 16 * (1 + r.nextInt(4));
    }

    /**
     * Set the gate fields of a GATE vector: per argument register, one 16-aligned struct in the scratch window (scr,
     * at address scr0, patch 0) holding every field, the register pointed at it (a struct past the window's end is
     * skipped); then GATE_HEAP bytes of ALIGN words at heap, inserted as patch 1.
     */
    static void gateFill(int[][] fields, long[] r, List<Long> addrs, List<byte[]> datas, long scr0, long heap, Random rnd,
                         long near) {
        byte[] scr = datas.get(0);
        for (int reg = 3; reg <= 10; reg++) {
            int end = 0;
            for (int[] f : fields) if (f[0] == reg) end = Math.max(end, f[1] + f[2]);
            if (end == 0 || end + 16 > scr.length) continue;
            int s = (rnd.nextInt(scr.length - end) & ~15);
            r[reg - 3] = scr0 + s;
            for (int[] f : fields) {
                if (f[0] != reg) continue;
                long x = gateValue(rnd, f[2], near);
                for (int k = 0; k < f[2]; k++) scr[s + f[1] + k] = (byte) (k < f[2] - 4 ? 0 : x >>> (8 * (f[2] - 1 - k)));
            }
        }
        byte[] h = new byte[GATE_HEAP];
        for (int i = 0; i < h.length; i += 4) {
            long x = alignWord(rnd, near);
            for (int k = 0; k < 4; k++) h[i + k] = (byte) (x >>> (24 - 8 * k));
        }
        addrs.add(1, heap);
        datas.add(1, h);
    }

    static final int GATE_KEEP = 4;

    /** A block of cov that fewer than GATE_KEEP kept vectors reached (hits: kept vectors per block). */
    static boolean rare(Set<Integer> cov, Map<Integer, Integer> hits) {
        for (int c : cov) if (hits.getOrDefault(c, 0) < GATE_KEEP) return true;
        return false;
    }

    // ---------- which extra vectors are kept ----------
    /** A valid extra vector reached a block or a vector-op case that no vector kept so far reached. */
    static boolean novel(Set<Integer> cov, Set<String> cases, Set<Integer> seenCov, Set<String> oldCases,
                         Set<String> seedCases) {
        for (int c : cov) if (!seenCov.contains(c)) return true;
        for (String c : cases) if (!oldCases.contains(c) && !seedCases.contains(c)) return true;
        return false;
    }

    /**
     * Keep a valid extra vector? A novel one always; at most half the quota may reach nothing new. So once those are
     * kept, the rest of the try budget (seed_extra * 8 per set) goes on searching: 82a1d008 passes its alignment exit
     * and 82cbc588 saturates in only a few percent of their ALIGN and SAT vectors, too rare for 16 kept vectors.
     */
    static boolean keepExtra(boolean novel, int plainKept, int quota) {
        return novel || plainKept < quota / 2;
    }

    // ---------- the cases a vector op reached (TcVmx.exec sets TcVmx.event; TcOracle notes "pc:event") ----------
    static boolean watched(String mn) {
        return floatIn(mn);
    }

    /** An op whose inputs are floats: denormal inputs read as zero (NJ). */
    static boolean floatIn(String mn) {
        return mn.contains("fp") && !mn.matches("vc[su]xwfp(128)?|vcfs[xu]|vcfux|vcsxwfp128|vcuxwfp128");
    }

    static boolean denormal(int b) {
        return (b & 0x7f800000) == 0 && (b & 0x7fffff) != 0;
    }

    static double val(int b) {
        return (double) Float.intBitsToFloat(denormal(b) ? b & 0x80000000 : b);
    }

    static final double FLT_MIN = 0x1p-126;

    /**
     * The case an op reached, or null: "sat" (it saturated), "den_in" (a float input lane was denormal), "den_out" (a
     * multiply-add, multiply, add or dot product whose exact result, before the flush, is a nonzero denormal); both
     * denormal cases joined by '+'. in: the A, B, C, D registers before the op (null where the op has none).
     */
    static String event(String mn, int[][] in, boolean sat) {
        if (sat) return "sat";
        if (!floatIn(mn)) return null;
        boolean din = false;
        for (int[] x : in) {
            if (x == null) continue;
            for (int b : x) din |= denormal(b);
        }
        boolean dout = false;
        int[] A = in[0], B = in[1], C = in[2], D = in[3];
        String m = mn;
        for (int i = 0; i < 4 && A != null; i++) {
            double r;
            switch (m) {
                case "vmaddfp": r = val(A[i]) * val(C[i]) + val(B[i]); break;
                case "vnmsubfp": r = val(B[i]) - val(A[i]) * val(C[i]); break;
                case "vmaddfp128": r = val(A[i]) * val(B[i]) + val(D[i]); break;
                case "vmaddcfp128": r = val(A[i]) * val(D[i]) + val(B[i]); break;
                case "vnmsubfp128": r = val(D[i]) - val(A[i]) * val(B[i]); break;
                case "vmulfp128": r = val(A[i]) * val(B[i]); break;
                case "vaddfp": case "vaddfp128": r = val(A[i]) + val(B[i]); break;
                case "vsubfp": case "vsubfp128": r = val(A[i]) - val(B[i]); break;
                case "vmsum3fp128": case "vmsum4fp128": {
                    int n = m.charAt(5) - '0';
                    r = 0;
                    for (int j = 0; j < n; j++) r += val(A[j]) * val(B[j]);
                    i = 4;
                    break;
                }
                default: r = 0; i = 4;
            }
            // a nonzero denormal once rounded to single: below 2^-149 the exact result rounds to a zero, which the flush
            // does not change (82de7510's two "den_out" vectors were such, so its planted fault had no input to show on)
            dout |= r != 0 && Math.abs(r) < FLT_MIN && (float) r != 0f;
        }
        return din && dout ? "den_in+den_out" : din ? "den_in" : dout ? "den_out" : null;
    }
}

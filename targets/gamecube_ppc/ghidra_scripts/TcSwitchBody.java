// The cases of a jump-table switch that lie outside the body Ghidra gave the function (Skies of Arcadia round r11,
// 2026-11-14). Ghidra leaves a switch unresolved when its table load is the plain CodeWarrior run
// `lis rT,hi; addi rT,rT,lo; lwzx rS,rT,rI; mtctr rS; bctr`: the body then stops at the bctr, the cases lie in a gap of
// it that no function owns, the oracle stops there ("executes code outside its body") and the translator's switch
// had no cases there either. TcPrepare adds what extend() returns to the body it runs and writes it to meta.json
// "switch_body", which the translator adds to the body it reads (ppctranslate.workspace.body_of): both see the same body.
//
// The rule, from the image's words only (no Ghidra API, so a stub can test it):
// - a bctr (bcctr, LK 0, branch always) whose table is found: walking back at most 16 instructions without leaving
//   straight-line code (no b, bclr, bcctr, or bc with link), mtctr rS, the lwzx rS that last wrote rS, a base register
//   whose last writer is addi rB,rB,lo after lis rB,hi (addis rB,0,hi), and a cmplwi giving the case count (the first
//   one walking back). An instruction whose written registers are not known stops the walk (nothing is added).
// - every case outside the body must lie inside the body's hull [lowest, highest address): the walk from each such case
//   follows fallthrough, b and bc (no link) inside the hull and outside the body, and stops at the body, a bclr or
//   bcctr that always branches, or a branch out of the hull (a tail call). A walk that reaches an address another
//   function owns or starts adds nothing for that table.
// Not a script itself.
import java.util.*;
import java.util.function.*;

public class TcSwitchBody {
    interface Words {
        Long at(long a);  // the image's big-endian word at a, or null outside it
    }

    static final long UNKNOWN = -1L;  // dest(): the written registers are not known
    static final int WINDOW = 16, MAX_WORDS = 4096;
    static final Set<Integer> LD_X = Set.of(23, 87, 279, 343, 534, 790), LDU_X = Set.of(55, 119, 311, 375),
        ST_X = Set.of(151, 215, 407, 662, 918, 535, 599, 727, 983), STU_X = Set.of(183, 247, 439, 695, 759, 567, 631),
        XO = Set.of(266, 40, 235, 104, 10, 8, 138, 136, 202, 200, 234, 232, 75, 11, 491, 459),
        LOG = Set.of(28, 60, 444, 412, 316, 476, 124, 284, 24, 536, 792, 824, 26, 922, 954),
        NOP_X = Set.of(0, 32, 4, 467, 144, 86, 54, 470, 982, 598, 854, 278, 246, 1014), MF = Set.of(339, 19, 371, 83);

    // The general-purpose registers an instruction word writes, as a bit mask, or UNKNOWN.
    static long dest(long w) {
        int op = (int) (w >>> 26);
        long rd = 1L << ((w >>> 21) & 31), ra = 1L << ((w >>> 16) & 31);
        switch (op) {
            case 7: case 8: case 12: case 13: case 14: case 15: case 32: case 34: case 40: case 42:
                return rd;
            case 33: case 35: case 41: case 43:
                return rd | ra;
            case 10: case 11: case 16: case 18: case 19: case 36: case 38: case 44: case 48: case 50: case 52: case 54:
            case 56: case 59: case 60: case 63:
                return 0;
            case 37: case 39: case 45: case 49: case 51: case 53: case 55: case 57: case 61:
            case 20: case 21: case 23: case 24: case 25: case 26: case 27: case 28: case 29:
                return ra;
            case 31: {
                int x = (int) ((w >>> 1) & 0x3FF);
                if (LD_X.contains(x) || MF.contains(x)) return rd;
                if (LDU_X.contains(x)) return rd | ra;
                if (ST_X.contains(x) || NOP_X.contains(x)) return 0;
                if (STU_X.contains(x)) return ra;
                if (XO.contains(x & 0x1FF)) return rd;
                if (LOG.contains(x)) return ra;
                return UNKNOWN;
            }
            default:
                return UNKNOWN;
        }
    }

    static boolean isBctr(long w) {  // bcctr, LK 0, BO "branch always"
        return (w & 0xFC0007FFL) == 0x4C000420L && ((w >>> 21) & 0x14) == 0x14;
    }

    // The case targets of the bctr at a, in table order, or null when the table is not found.
    static long[] table(Words img, long a) {
        List<Long> win = new ArrayList<>();
        for (int k = 1; k <= WINDOW; k++) {
            Long w = img.at(a - 4L * k);
            if (w == null) break;
            int op = (int) (w >>> 26);
            if (op == 18 || (op == 19 && (((w >>> 1) & 0x3FF) == 16 || ((w >>> 1) & 0x3FF) == 528))
                || (op == 16 && (w & 1) != 0)) break;
            win.add(w);
        }
        int i = -1;
        for (int j = 0; j < win.size() && i < 0; j++) {
            if ((win.get(j) & 0xFC1FFFFFL) == 0x7C0903A6L) i = j;  // mtctr rS
            else if (dest(win.get(j)) == UNKNOWN) return null;
        }
        if (i < 0) return null;
        int rs = (int) ((win.get(i) >>> 21) & 31);
        int li = writer(win, i + 1, rs);
        if (li < 0) return null;
        long lw = win.get(li);
        if ((lw >>> 26) != 31 || ((lw >>> 1) & 0x3FF) != 23) return null;  // lwzx rS,rA,rB
        Long base = null;
        for (int b : new int[] {(int) ((lw >>> 16) & 31), (int) ((lw >>> 11) & 31)}) {
            if (b == 0) continue;
            int j = writer(win, li + 1, b);
            if (j < 0) continue;
            long ad = win.get(j);
            if ((ad >>> 26) != 14 || ((ad >>> 16) & 31) != b) continue;  // addi rB,rB,lo
            int k = writer(win, j + 1, b);
            if (k < 0) continue;
            long hi = win.get(k);
            if ((hi >>> 26) != 15 || ((hi >>> 16) & 31) != 0) continue;  // lis rB,hi
            long lo = (short) (ad & 0xFFFF);
            base = (((hi & 0xFFFF) << 16) + lo) & 0xFFFFFFFFL;
            break;
        }
        if (base == null) return null;
        int bound = -1;
        for (long w : win) {
            if ((w >>> 26) == 10 && ((w >>> 21) & 1) == 0) {  // cmplwi: cases 0..imm
                bound = (int) (w & 0xFFFF) + 1;
                break;
            }
        }
        if (bound < 0) return null;
        long[] t = new long[bound];
        for (int n = 0; n < bound; n++) {
            Long x = img.at(base + 4L * n);
            if (x == null || (x & 3) != 0) return null;
            t[n] = x;
        }
        return t;
    }

    // The index in win (walking back from `from`) of the nearest instruction writing register r; -1 when none is
    // found or one with unknown writes comes first.
    static int writer(List<Long> win, int from, int r) {
        for (int i = from; i < win.size(); i++) {
            long d = dest(win.get(i));
            if (d == UNKNOWN) return -1;
            if ((d >>> r & 1) != 0) return i;
        }
        return -1;
    }

    // The body's extension: sorted, merged ranges [lo, hi) of the jump-table cases outside it (empty: none).
    // body: [lo, hi) ranges; taken: an address another function's body holds or another function starts at.
    static List<long[]> extend(Words img, List<long[]> body, LongPredicate taken) {
        TreeSet<Long> add = new TreeSet<>();
        if (body.isEmpty()) return new ArrayList<>();
        long hlo = Long.MAX_VALUE, hhi = Long.MIN_VALUE;
        for (long[] r : body) {
            hlo = Math.min(hlo, r[0]);
            hhi = Math.max(hhi, r[1]);
        }
        for (long[] r : body) {
            for (long a = r[0]; a + 4 <= r[1]; a += 4) {
                Long w = img.at(a);
                if (w == null || !isBctr(w)) continue;
                long[] t = table(img, a);
                if (t == null) continue;
                Set<Long> got = closure(img, body, hlo, hhi, t, taken);
                if (got != null) add.addAll(got);
            }
        }
        List<long[]> out = new ArrayList<>();
        for (long p : add) {
            if (!out.isEmpty() && out.get(out.size() - 1)[1] == p) out.get(out.size() - 1)[1] = p + 4;
            else out.add(new long[] {p, p + 4});
        }
        return out;
    }

    static boolean in(List<long[]> body, long a) {
        for (long[] r : body) if (r[0] <= a && a < r[1]) return true;
        return false;
    }

    // The addresses the cases outside the body run before they rejoin it, or null (see the rule above).
    static Set<Long> closure(Words img, List<long[]> body, long hlo, long hhi, long[] targets, LongPredicate taken) {
        Deque<Long> work = new ArrayDeque<>();
        for (long t : targets) {
            if (in(body, t)) continue;
            if (t < hlo || t >= hhi) return null;
            work.push(t);
        }
        Set<Long> seen = new TreeSet<>();
        while (!work.isEmpty()) {
            long p = work.pop();
            while (!seen.contains(p)) {
                if (in(body, p)) break;
                if (p < hlo || p >= hhi || taken.test(p)) return null;
                Long w = img.at(p);
                if (w == null) return null;
                seen.add(p);
                if (seen.size() > MAX_WORDS) return null;
                int op = (int) (w >>> 26);
                boolean always = ((w >>> 21) & 0x14) == 0x14;
                if (op == 19 && (((w >>> 1) & 0x3FF) == 16 || ((w >>> 1) & 0x3FF) == 528) && (w & 1) == 0) {
                    if (always) break;
                } else if (op == 18 && (w & 1) == 0) {
                    long d = w & 0x03FFFFFCL;
                    if ((d & 0x02000000L) != 0) d -= 0x04000000L;
                    long t = (d + ((w & 2) != 0 ? 0 : p)) & 0xFFFFFFFFL;
                    if (t >= hlo && t < hhi && !in(body, t)) work.push(t);
                    break;
                } else if (op == 16 && (w & 1) == 0) {
                    long d = w & 0xFFFCL;
                    if ((d & 0x8000L) != 0) d -= 0x10000L;
                    long t = (d + ((w & 2) != 0 ? 0 : p)) & 0xFFFFFFFFL;
                    if (t >= hlo && t < hhi && !in(body, t)) work.push(t);
                    if (always) break;
                }
                p += 4;
            }
        }
        return seen;
    }

    // meta.json's "switch_body" value: [["lo","hi"], ...] (hi exclusive).
    static String json(List<long[]> ext) {
        StringBuilder b = new StringBuilder("[");
        for (long[] r : ext) b.append(b.length() > 1 ? "," : "").append(String.format("[\"%08x\",\"%08x\"]", r[0], r[1]));
        return b.append(']').toString();
    }
}

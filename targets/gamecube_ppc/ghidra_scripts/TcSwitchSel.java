// The slicer of the directed switch pass (TcSwitchPass; Skies of Arcadia, 2026-11-15), from the image's words only (no
// Ghidra API: tests/test_gc_switch_pass.py's stub). of(): a table bctr's selector sliced back from `slwi rI,rS,2; lwzx`
// to its root: a parameter (ARG), a load (LOAD: lbz/lhz/lha/lwz, update and indexed forms; a stack local's store and
// reload followed) or a mocked call's r3 (CALL), through mr/ori 0, addi(s), rlwinm, andi., xori(s), extsb/extsh. An `or`
// of two registers (two halfwords joined) is a JOIN of two roots; plan() splits a value by the bits each operand can
// give, checked by running both chains forward. stmw writes no register, lmw rD..r31. guard(): the register a
// conditional branch's compare tests (cmpwi/cmplwi, or andi., andis., rlwinm., extsb., extsh., mr. on cr0) and values
// sending it the wanted way. Anything else is named (why), never hidden. LOCKED: the watcher agent may not change this
// file. Not a script itself.
import java.util.*;

final class TcSwitchSel {
    static final int ARG = 1, LOAD = 2, CALL = 3, JOIN = 4;
    static final int ADD = 1, RLW = 2, AND = 3, LD = 4, XOR = 5;  // chain steps: {op, a, b}
    static final int BACK = 1024;
    static final long M32 = 0xffffffffL;

    static final class Sel {
        long bctr, at = -1, callee;  // bctr: the switch's (or the guard's branch); at: the root's instruction or -1
        long[] targets;              // the table, one target per index (a switch's)
        int kind, reg = -1, size;
        boolean signed;              // LOAD: lha/lhau/lhax (the loaded value is sign-extended)
        String why;                  // the reason when not directed (kind 0)
        Sel[] parts;                 // JOIN: the or's two operands, each sliced from the or
        final List<long[]> chain = new ArrayList<>();  // walk order: from the sliced register back to the root
    }

    static long rotl(long x, int n) {
        return (n &= 31) == 0 ? x & M32 : ((x << n) | (x >>> (32 - n))) & M32;
    }
    static long mask(int mb, int me) {  // rlwinm's mask, big-endian bit numbers
        long m = 0;
        for (int i = mb; ; i = (i + 1) & 31) {
            m |= 1L << (31 - i);
            if (i == me) break;
        }
        return m;
    }
    static long sext(long x, int bits) {
        long m = (1L << bits) - 1, v = x & m;
        return ((v & (1L << (bits - 1))) != 0 ? v | ~m : v) & M32;
    }

    // The general-purpose registers an instruction writes (TcSwitchBody.dest), with stmw (none) and lmw (rD..r31).
    static long dest(long w) {
        int op = (int) (w >>> 26);
        if (op == 47) return 0;
        if (op == 46) return (-1L << ((w >>> 21) & 31)) & M32;
        return TcSwitchBody.dest(w);
    }

    // The selector register and the pc of its slwi, for the bctr at a, or null.
    static long[] index(TcSwitchBody.Words img, long a) {
        List<Long> win = new ArrayList<>();
        for (int k = 1; k <= TcSwitchBody.WINDOW; k++) {
            Long w = img.at(a - 4L * k);
            if (w == null) break;
            win.add(w);
        }
        int mt = -1;
        for (int j = 0; j < win.size() && mt < 0; j++) if ((win.get(j) & 0xFC1FFFFFL) == 0x7C0903A6L) mt = j;
        if (mt < 0) return null;
        int lw = TcSwitchBody.writer(win, mt + 1, (int) ((win.get(mt) >>> 21) & 31));
        if (lw < 0 || (win.get(lw) >>> 26) != 31 || ((win.get(lw) >>> 1) & 0x3FF) != 23) return null;
        int[] ab = {(int) ((win.get(lw) >>> 16) & 31), (int) ((win.get(lw) >>> 11) & 31)};
        for (int n = 0; n < 2; n++) {
            int b = ab[n], j = n == 0 && b == 0 ? -1 : TcSwitchBody.writer(win, lw + 1, b);  // rA 0 is 0, rB 0 is r0
            long w = j < 0 ? 0 : win.get(j);
            if ((w >>> 26) == 21 && ((w >>> 11) & 31) == 2 && ((w >>> 6) & 31) == 0 && ((w >>> 1) & 31) == 29) {
                return new long[] {(w >>> 21) & 31, a - 4L * (j + 1)};  // slwi rI,rS,2
            }
        }
        return null;
    }

    // The table of the bctr at a (TcSwitchBody.table, or with a cmplwi of the selector further back than its window).
    static long[] table(TcSwitchBody.Words img, long fnStart, long a) {
        long[] t = TcSwitchBody.table(img, a);
        if (t != null) return t;
        long[] ix = index(img, a);
        if (ix == null) return null;
        Long base = null;
        List<Long> win = new ArrayList<>();
        for (int k = 1; k <= TcSwitchBody.WINDOW; k++) win.add(img.at(a - 4L * k));
        long lwb = -1;
        for (Long w : win) if (w != null && (w >>> 26) == 31 && ((w >>> 1) & 0x3FF) == 23) { lwb = (w >>> 16) & 31; break; }
        for (int i = 0; i + 1 < win.size() && base == null; i++) {  // addi rB,rB,lo after lis rB,hi
            Long ad = win.get(i);  // its rD is the lwzx's base (CodeWarrior also writes addi rT,rB,lo after lis rB,hi)
            if (ad == null || (ad >>> 26) != 14 || ((ad >>> 21) & 31) != lwb) continue;
            int rb = (int) ((ad >>> 16) & 31);
            for (int j = i + 1; j < win.size(); j++) {
                Long hi = win.get(j);
                if (hi != null && (hi >>> 26) == 15 && ((hi >>> 16) & 31) == 0 && ((hi >>> 21) & 31) == rb) {
                    base = (((hi & 0xFFFF) << 16) + (short) (ad & 0xFFFF)) & M32;
                    break;
                }
            }
        }
        int bound = -1;
        for (long p = ix[1] - 4; base != null && p >= fnStart && p > ix[1] - 4L * BACK && bound < 0; p -= 4) {
            Long w = img.at(p);
            if (w != null && (w >>> 26) == 10 && ((w >>> 21) & 1) == 0 && ((w >>> 16) & 31) == ix[0]) bound = (int) (w & 0xFFFF) + 1;
        }
        if (bound < 0) return null;
        t = new long[bound];
        for (int n = 0; n < bound; n++) {
            Long x = img.at(base + 4L * n);
            if (x == null || (x & 3) != 0) return null;
            t[n] = x;
        }
        return t;
    }
    static Sel none(Sel s, String why) {
        s.kind = 0;
        s.why = why;
        return s;
    }
    static boolean volatileReg(int r) { return r == 0 || (r >= 3 && r <= 12); }

    static String opName(long w) {
        return "opcode " + (w >>> 26) + ((w >>> 26) == 31 ? "/" + ((w >>> 1) & 0x3FF) : "");
    }

    // The selector of the bctr at a in the function at fnStart: its table, root and chain.
    static Sel of(TcSwitchBody.Words img, long fnStart, long a) {
        Sel s = new Sel();
        s.bctr = a;
        s.targets = table(img, fnStart, a);
        long[] ix = index(img, a);
        if (s.targets == null || ix == null) return none(s, "table not found");
        return slice(img, fnStart, ix[1], (int) ix[0], s);
    }

    // Slice register r back from p0 (exclusive) to its root, into s.
    static Sel slice(TcSwitchBody.Words img, long fnStart, long p0, int r, Sel s) {
        for (long p = p0 - 4; p >= fnStart && p > p0 - 4L * BACK; p -= 4) {
            Long wl = img.at(p);
            if (wl == null) return none(s, "outside the image");
            long w = wl;
            int op = (int) (w >>> 26), xo = (int) ((w >>> 1) & 0x3FF);
            int rd = (int) ((w >>> 21) & 31), ra = (int) ((w >>> 16) & 31), rb = (int) ((w >>> 11) & 31);
            if (op == 18 && (w & 1) != 0) {  // bl: the callee's r3, or a register it clobbers
                if (!volatileReg(r)) continue;
                if (r != 3 || (w & 2) != 0) return none(s, "register a call clobbers");
                long d = w & 0x03FFFFFCL;
                s.callee = (((d & 0x02000000L) != 0 ? d - 0x04000000L : d) + p) & M32;
                s.kind = CALL;
                s.at = p;
                return s;
            }
            if (op == 19 && xo == 528 && (w & 1) != 0) {
                if (volatileReg(r)) return none(s, "indirect call result");
                continue;
            }
            long dest = dest(w);
            if (dest == TcSwitchBody.UNKNOWN) return none(s, "unknown instruction (" + opName(w) + ")");
            if ((dest >>> r & 1) == 0) continue;
            boolean upd = op == 33 || op == 35 || op == 41 || op == 43;  // lwzu, lbzu, lhzu, lhau
            boolean ldx = op == 31 && (xo == 23 || xo == 87 || xo == 279 || xo == 343 || xo == 55 || xo == 119
                                       || xo == 311 || xo == 375);
            if ((op == 14 || op == 15) && ra != 0) {  // addi, addis
                long c = (long) (short) (w & 0xFFFF);
                s.chain.add(new long[] {ADD, (op == 15 ? c << 16 : c) & M32, 0});
                r = ra;
            } else if (op == 14 || op == 15) {
                return none(s, "constant");
            } else if (upd && rd != r) {  // the updated base: rA + d
                s.chain.add(new long[] {ADD, (long) (short) (w & 0xFFFF) & M32, 0});
            } else if (op == 21) {  // rlwinm rA,rS,sh,mb,me
                s.chain.add(new long[] {RLW, (w >>> 11) & 31, mask((int) ((w >>> 6) & 31), (int) ((w >>> 1) & 31))});
                r = rd;
            } else if (op == 28) {  // andi.
                s.chain.add(new long[] {AND, w & 0xFFFF, 0});
                r = rd;
            } else if (op == 26 || op == 27) {  // xori, xoris
                s.chain.add(new long[] {XOR, op == 27 ? (w & 0xFFFF) << 16 : w & 0xFFFF, 0});
                r = rd;
            } else if ((op == 24 && (w & 0xFFFF) == 0) || (op == 31 && xo == 444 && rd == rb)) {  // ori rA,rS,0; mr
                r = rd;
            } else if (op == 31 && xo == 444) {  // or rA,rS,rB: two operands joined
                s.kind = JOIN;
                s.at = p;
                s.parts = new Sel[] {slice(img, fnStart, p, rd, new Sel()), slice(img, fnStart, p, rb, new Sel())};
                for (Sel q : s.parts) if (q.kind == 0) return none(s, "joined operand: " + q.why);
                return s;
            } else if (op == 31 && (xo == 954 || xo == 922)) {  // extsb, extsh
                s.chain.add(new long[] {LD, xo == 954 ? 1 : 2, 1});
                r = rd;
            } else if ((op == 32 || op == 34 || op == 40 || op == 42 || upd || ldx) && rd == r) {
                int n = op == 32 || op == 33 || xo == 23 || xo == 55 ? 4 : op == 34 || op == 35 || xo == 87 || xo == 119 ? 1 : 2;
                s.signed = op == 42 || op == 43 || (op == 31 && (xo == 343 || xo == 375));
                s.chain.add(new long[] {LD, n, s.signed ? 1 : 0});
                long st = op == 31 || upd || ra != 1 ? -1 : store(img, fnStart, p, (short) (w & 0xFFFF), n);
                if (st < 0) {
                    s.kind = LOAD;
                    s.at = p;
                    s.size = n;
                    return s;
                }
                r = (int) ((img.at(st) >>> 21) & 31);  // a stack local: the register its store wrote
                p = st;
            } else {
                return none(s, "computed (" + opName(w) + ")");
            }
        }
        if (r >= 3 && r <= 10) {
            s.kind = ARG;
            s.reg = r - 3;
            return s;
        }
        return none(s, (p0 - fnStart > 4L * BACK ? "the walk window ended at r" : "register r") + r + " at entry");
    }

    // The store of n bytes to disp(r1) nearest before p, or -1.
    static long store(TcSwitchBody.Words img, long fnStart, long p, int disp, int n) {
        int op = n == 4 ? 36 : n == 2 ? 44 : 38;
        for (long q = p - 4; q >= fnStart && q > p - 4L * BACK; q -= 4) {
            Long w = img.at(q);
            if (w != null && (w >>> 26) == op && ((w >>> 16) & 31) == 1 && (short) (w & 0xFFFF) == disp) return q;
        }
        return -1;
    }

    // The root value that makes this chain give w, or null.
    static Long back(List<long[]> chain, long w) {
        w &= M32;
        for (long[] c : chain) {
            switch ((int) c[0]) {
                case ADD: w = (w - c[1]) & M32; break;
                case XOR: w = (w ^ c[1]) & M32; break;
                case RLW:
                    if ((w & ~c[2] & M32) != 0) return null;
                    w = rotl(w, 32 - (int) c[1]);
                    break;
                case AND:
                    if ((w & ~c[1] & M32) != 0) return null;
                    break;
                default: {  // LD: n bytes, sign-extended when c[2]
                    int bits = 8 * (int) c[1];
                    if (bits < 32 && (c[2] != 0 ? sext(w, bits) != w : w >>> bits != 0)) return null;
                    if (bits < 32) w &= (1L << bits) - 1;
                }
            }
        }
        return w;
    }

    // The value s's register gets from the roots' values p (the chains run forward).
    static long fwd(Sel s, Map<Sel, Long> p) {
        long v = s.kind == JOIN ? fwd(s.parts[0], p) | fwd(s.parts[1], p) : p.get(s);
        for (int i = s.chain.size() - 1; i >= 0; i--) {
            long[] c = s.chain.get(i);
            switch ((int) c[0]) {
                case ADD: v = v + c[1]; break;
                case XOR: v = v ^ c[1]; break;
                case RLW: v = rotl(v, (int) c[1]) & c[2]; break;
                case AND: v = v & c[1]; break;
                default: {
                    int bits = 8 * (int) c[1];
                    if (bits < 32) v = c[2] != 0 ? sext(v, bits) : v & ((1L << bits) - 1);
                }
            }
        }
        return v & M32;
    }

    // The bits s's register can hold, from the step nearest it (M32: any).
    static long bits(Sel s) {
        if (s.chain.isEmpty()) return M32;
        long[] c = s.chain.get(0);
        if (c[0] == RLW) return c[2];
        if (c[0] == AND) return c[1];
        if (c[0] == LD && c[2] == 0 && c[1] < 4) return (1L << (8 * c[1])) - 1;
        return M32;
    }

    // {root sel: value} that gives s's register the value x, or null.
    static Map<Sel, Long> plan(Sel s, long x) {
        if (s.kind == 0) return null;
        Long w = back(s.chain, x);
        if (w == null) return null;
        Map<Sel, Long> out = new LinkedHashMap<>();
        if (s.kind != JOIN) {
            out.put(s, w);
            return out;
        }
        for (int k = 0; k < 2; k++) {  // the operand with the narrower bits takes them, the other the rest
            long m = bits(s.parts[k]);
            if (m == M32) continue;
            Map<Sel, Long> pa = plan(s.parts[k], w & m), pb = plan(s.parts[1 - k], w & ~m & M32);
            if (pa == null || pb == null) continue;
            out.putAll(pa);
            out.putAll(pb);
            if ((fwd(s.parts[0], out) | fwd(s.parts[1], out)) == w) return out;
            out.clear();
        }
        return null;
    }
    static Long value(Sel s, long x) {  // a single root's value (the stub's view), or null
        Map<Sel, Long> p = plan(s, x);
        return p == null ? null : p.size() == 1 ? p.values().iterator().next() : null;
    }
    static String kind(Sel s) {
        return s.kind == ARG ? "arg" : s.kind == LOAD ? "load" : s.kind == CALL ? "call" : s.kind == JOIN ? "join" : s.why;
    }

    // ---------- guards ----------
    static final class Guard {
        Sel sel;          // the tested register, sliced (kind 0: why)
        long[] want = {};  // values of it that send the branch the wanted way, best first
        String why;       // not directable: why
    }

    // The guard of the conditional branch at p (bc, or bclr/bcctr with a condition): which register it tests and the
    // values that make it branch (taken) or fall through (!taken).
    static Guard guard(TcSwitchBody.Words img, long fnStart, long p, boolean taken) {
        Guard g = new Guard();
        Long wb = img.at(p);
        int bo = wb == null ? 0 : (int) ((wb >>> 21) & 31), bi = wb == null ? 0 : (int) ((wb >>> 16) & 31);
        if (wb == null) return why(g, "outside the image");
        if ((bo & 0x04) == 0) return why(g, "a loop count (ctr)");
        if ((bo & 0x10) != 0) return why(g, "not a condition");
        boolean set = taken == ((bo & 0x08) != 0);  // the CR bit the wanted way needs
        int crf = bi >> 2, bit = bi & 3;
        if (bit == 3) return why(g, "summary overflow bit");
        for (long q = p - 4; q >= fnStart && q > p - 4L * BACK; q -= 4) {
            Long wl = img.at(q);
            if (wl == null) return why(g, "outside the image");
            long w = wl;
            int op = (int) (w >>> 26), xo = (int) ((w >>> 1) & 0x3FF), rs = (int) ((w >>> 21) & 31);
            int rA = (int) ((w >>> 16) & 31), rB = (int) ((w >>> 11) & 31);
            int fd = (int) ((w >>> 23) & 7);
            boolean rc0 = crf == 0 && (w & 1) != 0;
            List<Long> cand = new ArrayList<>();
            int reg;
            java.util.function.LongUnaryOperator res;  // the value compared with 0 (record forms)
            if ((op == 10 || op == 11) && fd == crf) {  // cmplwi, cmpwi
                long imm = op == 11 ? sext(w & 0xFFFF, 16) : w & 0xFFFF;
                for (long d : new long[] {0, 1, -1, 2, -2}) cand.add((imm + d) & M32);
                for (long c : new long[] {0, 1, M32, 0x7fffffffL, 0x80000000L}) cand.add(c);
                for (long c : cand) if (cmp(c, imm, op == 11, bit) == set) g.want = add(g.want, c);
                return sel(g, img, fnStart, q, rA);
            } else if (op == 31 && (xo == 0 || xo == 32) && fd == crf) {
                return why(g, "a compare of two registers");
            } else if ((op == 63 && (xo == 0 || xo == 32)) && fd == crf) {
                return why(g, "a float compare");
            } else if (op == 19 && (xo == 0 ? fd : rs >> 2) == crf && xo != 16 && xo != 528 && xo != 150 && xo != 50) {
                return why(g, "condition register logic");
            } else if (op == 31 && (xo == 144 || xo == 512) || crf == 1 && (op == 59 || op == 63) && (w & 1) != 0) {
                return why(g, "a float record form, mtcrf or mcrxr");
            } else if (crf == 0 && (op == 28 || op == 29)) {  // andi., andis.
                long m = op == 28 ? w & 0xFFFF : (w & 0xFFFF) << 16;
                cand.addAll(List.of(0L, m, m & -m, M32, 0x80000000L));
                res = v -> v & m;
                reg = rs;
            } else if (rc0 && op == 21) {  // rlwinm.
                long m = mask((int) ((w >>> 6) & 31), (int) ((w >>> 1) & 31));
                int sh = (int) ((w >>> 11) & 31);
                cand.addAll(List.of(0L, rotl(m & -m, 32 - sh), rotl(0x80000000L, 32 - sh), M32));
                res = v -> rotl(v, sh) & m;
                reg = rs;
            } else if (rc0 && op == 31 && (xo == 954 || xo == 922)) {  // extsb., extsh.
                int n = xo == 954 ? 8 : 16;
                cand.addAll(List.of(0L, 1L, 1L << (n - 1), (1L << (n - 1)) - 1));
                res = v -> sext(v, n);
                reg = rs;
            } else if (rc0 && op == 31 && xo == 444 && rs == rB) {  // mr.
                cand.addAll(List.of(0L, 1L, 0x80000000L));
                res = v -> v;
                reg = rs;
            } else if (crf == 0 && (rc0 && (op == 20 || op == 23 || op == 31) || op == 13)) {
                return why(g, "record form (" + opName(w) + ")");
            } else {
                continue;  // does not write the CR field
            }
            for (long c : cand) if (cmp(res.applyAsLong(c), 0, true, bit) == set) g.want = add(g.want, c);
            return sel(g, img, fnStart, q, reg);
        }
        return why(g, "no compare found");
    }
    static Guard why(Guard g, String why) { g.why = why; return g; }
    static Guard sel(Guard g, TcSwitchBody.Words img, long fnStart, long q, int reg) {
        g.sel = slice(img, fnStart, q, reg, new Sel());
        g.sel.bctr = q;
        if (g.sel.kind == 0) g.why = "guard: " + g.sel.why;
        return g;
    }
    static long[] add(long[] a, long c) {
        for (long x : a) if (x == c) return a;
        long[] b = Arrays.copyOf(a, a.length + 1);
        b[a.length] = c;
        return b;
    }

    // The CR bit (0 LT, 1 GT, 2 EQ) a compare of x with imm sets.
    static boolean cmp(long x, long imm, boolean signed, int bit) {
        long a = signed ? (int) x : x & M32, b = signed ? (int) imm : imm & M32;
        return bit == 0 ? a < b : bit == 1 ? a > b : a == b;
    }
}

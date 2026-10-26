// Switch dispatches whose cases Ghidra left outside the function body. 8c0144da (Toy Commander) dispatches through a
// 17-entry table, `mova T,r0; mov.w @(r0,r1),r0; braf r0`, but analysis resolved one case: the other cases were in
// no packet, block list or coverage count, while the emulator still ran them (as if they were calls).
// The targets are computed here from the bytes and the dispatch idiom, never from Ghidra's own references:
//   braf rN    with mov.b/mov.w/mov.l @(r0,rK) entries: offsets from the braf + 4 (8c0144da)
//   jmp @rN    with mov.l @(r0,rK) entries: absolute addresses (a P0 mirror address is folded to 0x8c...)
//   braf r0    after `and #m,r0; shll2 r0`: a jump into a sled of m+1 equal-size slots
//   jmp @rN    after `add rI,rN`, rN a literal-pool constant P and rI a shifted index: a jump into a sled at P, one
//              instruction repeated, that runs on into the function's body; the slots are the repeats up to the body
//              (Toy Commander's memset 8c04edec and bzero 8c04eed8: 31 slots of `mov.b rM,@-r4` before the rts)
// The table base is a mova or a literal-pool constant; the entry count comes from the bound check before it
// (`mov #N,rB; cmp/hs rB,rI; bf dispatch`, or cmp/hi, or `bt default`, on the path the dispatch is reached by).
// A dispatch without both is not a table and is left alone (jmp through a struct field is a virtual call; through a
// pool constant, a plain far jump); one with a constant base but no bound is listed as "unbounded".
//
// Script args: 0 "report" or "fix", 1 output file: one line per resolved table dispatch,
//   function, dispatch, kind, table, entries, then each target with where it lies: in (the body), fn (a function
//   entry: a tail call through a table), other (inside another function's body), free (code or bytes in no function).
// "fix" makes every free case target part of the dispatching function: the target is disassembled, gets a
// COMPUTED_JUMP reference from the dispatch, and the body is recomputed from its flow
// (CreateFunctionCmd.fixupFunctionBody); a case already in the body only gets its missing reference. A function entry
// stays a tail call (a callee). A case inside another function's body is not taken (bodies would overlap): it is
// reported, and the oracle's guard (TcOracle) refuses the function if a vector runs it. Repeats until stable (a case
// may hold another switch), then analyzes the changes (call references of the new code).
//@category DecompPipeline
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class TcSwitch extends GhidraScript {
    static final int BACK = 24;        // instructions searched before a dispatch for its table, index and bound
    static final int MAX_ENTRIES = 1024;

    Memory mem;
    Listing listing;
    FunctionManager fm;
    List<String> unbounded = new ArrayList<>(); // a table load from a constant base without a bound check

    static class Table {
        Function fn;
        Instruction jump;
        String kind;     // "braf.w" (offsets), "jmp.l" (addresses), "sled"
        long base;       // table address (sled: the first slot)
        int size;        // bytes per entry (sled: per slot)
        int count;
        List<Long> targets = new ArrayList<>();
    }

    int u16(long a) throws Exception {
        return mem.getShort(toAddr(a)) & 0xffff;
    }

    boolean initialized(long a, int n) {
        MemoryBlock b = mem.getBlock(toAddr(a));
        return b != null && b.isInitialized() && b.contains(toAddr(a + n - 1));
    }

    static long fold(long v) {
        v &= 0xffffffffL;
        return TcBase.fold(v);
    }

    boolean writes(Instruction in, int reg) {
        Register r = currentProgram.getRegister("r" + reg);
        for (Object o : in.getResultObjects()) if (r.equals(o)) return true;
        return false;
    }

    /** The first index >= from in win (instructions before the dispatch, nearest first) that writes rN, or -1. */
    int def(List<Instruction> win, int from, int reg) {
        for (int i = from; i < win.size(); i++) if (writes(win.get(i), reg)) return i;
        return -1;
    }

    /** The constant rN holds after win[i] executes, following register copies back, or null. */
    Long constAt(List<Instruction> win, int i, int reg) throws Exception {
        int d = def(win, i, reg);
        if (d < 0) return null;
        long pc = win.get(d).getAddress().getOffset();
        int w = u16(pc);
        if ((w & 0xff00) == 0xc700) return (pc & ~3L) + 4 + 4L * (w & 0xff);                    // mova
        if (w >> 12 == 0xd) return mem.getInt(toAddr((pc & ~3L) + 4 + 4L * (w & 0xff))) & 0xffffffffL; // mov.l pool
        if (w >> 12 == 0x9) return (long) mem.getShort(toAddr(pc + 4 + 2L * (w & 0xff)));    // mov.w pool
        if (w >> 12 == 0xe) return (long) (byte) w;                                          // mov #imm
        if ((w & 0xf00f) == 0x6003) return constAt(win, d + 1, (w >> 4) & 0xf);               // mov rM,rN
        return null;
    }

    /** {register, window index, scale}: rN live at win[i] traced back through copies and shifts. The result is the
     *  register holding the unscaled value just before the earliest shift, and the index right after that shift. */
    int[] unscale(List<Instruction> win, int i, int reg) throws Exception {
        int scale = 1;
        int[] at = {reg, i, 1};
        while (true) {
            int d = def(win, i, reg);
            if (d < 0) return at;
            int w = u16(win.get(d).getAddress().getOffset());
            int n = (w >> 8) & 0xf, m = (w >> 4) & 0xf;
            if ((w & 0xf0ff) == 0x4000) scale *= 2;                          // shll
            else if ((w & 0xf0ff) == 0x4008) scale *= 4;                     // shll2
            else if ((w & 0xf00f) == 0x300c && n == m) scale *= 2;           // add rN,rN
            else if ((w & 0xf00f) == 0x6003) reg = m;                        // mov rM,rN
            else return at;
            if ((w & 0xf00f) != 0x6003) at = new int[] {reg, d + 1, scale};
            i = d + 1;
        }
    }

    /** Entries allowed by the bound check on the index (in rI at win[i], copies followed back), or -1. */
    int bound(List<Instruction> win, int i, int reg, long jumpAt) throws Exception {
        for (int q = i; q < win.size(); q++) {
            int w = u16(win.get(q).getAddress().getOffset());
            boolean hs = (w & 0xf00f) == 0x3002, hi = (w & 0xf00f) == 0x3006;  // cmp/hs rB,rI / cmp/hi rB,rI
            if ((hs || hi) && ((w >> 8) & 0xf) == reg) return checked(win, q, hs, jumpAt);
            if (!writes(win.get(q), reg)) continue;
            if ((w & 0xf00f) != 0x6003) return -1;  // the index's own definition: no check after it
            reg = (w >> 4) & 0xf;
        }
        return -1;
    }

    /** The entry count of the compare win[q], when the first conditional branch after it leads to the dispatch on
     *  T == 0 (`bf dispatch` taken, or `bt default` not taken). */
    int checked(List<Instruction> win, int q, boolean hs, long jumpAt) throws Exception {
        int w = u16(win.get(q).getAddress().getOffset());
        Long n = constAt(win, q + 1, (w >> 4) & 0xf);
        if (n == null) return -1;
        for (int b = q - 1; b >= 0; b--) {
            long pc = win.get(b).getAddress().getOffset();
            int bw = u16(pc);
            if ((bw & 0xf900) != 0x8900) continue;                    // bt, bf, bt/s, bf/s
            int next = b - ((bw & 0x0400) != 0 ? 2 : 1);              // after the delay slot, if any
            long nextAt = next >= 0 ? win.get(next).getAddress().getOffset() : jumpAt;
            boolean taken = nextAt == pc + 4 + 2L * (byte) bw;
            if (taken != ((bw & 0x0200) != 0)) return -1;              // the dispatch must be the T == 0 path
            long c = hs ? n : n + 1;
            return c >= 1 && c <= MAX_ENTRIES ? (int) c : -1;
        }
        return -1;
    }

    /** Up to BACK instructions executed before `jump`, nearest first, along one path: the fall-through
     *  predecessor, else (after an unconditional branch) the nearest branch before that targets the instruction. */
    List<Instruction> window(Instruction jump) {
        List<Instruction> win = new ArrayList<>();
        Instruction cur = jump;
        while (win.size() < BACK) {
            Instruction p = listing.getInstructionBefore(cur.getAddress());
            if (p == null) break;
            Instruction owner = p.isInDelaySlot() ? listing.getInstructionBefore(p.getAddress()) : p;
            if (owner != null && owner.getFallThrough() != null) {
                win.add(p);
                cur = p;
                continue;
            }
            Instruction from = null;
            for (Reference r : getReferencesTo(cur.getAddress())) {
                Address a = r.getFromAddress();
                if (!r.getReferenceType().isJump() || a.compareTo(cur.getAddress()) >= 0) continue;
                if (from == null || a.compareTo(from.getAddress()) > 0) from = listing.getInstructionAt(a);
            }
            if (from == null) break;
            if (from.getDelaySlotDepth() > 0) win.add(listing.getInstructionAfter(from.getAddress()));
            win.add(from);
            cur = from;
        }
        return win;
    }

    /** The table dispatch ending at `jump` (braf rN / jmp @rN), or null when it is not one. */
    Table table(Function f, Instruction jump) throws Exception {
        long ja = jump.getAddress().getOffset();
        int jw = u16(ja);
        boolean braf = (jw & 0xf0ff) == 0x0023, jmp = (jw & 0xf0ff) == 0x402b;
        if (!braf && !jmp) return null;
        List<Instruction> win = window(jump);
        // the jump register back to its load, through copies, zero-extension and shifts of the loaded entry
        int reg = (jw >> 8) & 0xf, post = 1, i = 0;
        boolean zext = false;
        while (true) {
            int d = def(win, i, reg);
            if (d < 0) return null;
            int w = u16(win.get(d).getAddress().getOffset());
            int m = (w >> 4) & 0xf;
            if ((w & 0xf00f) == 0x6003) reg = m;                                       // mov rM,rN
            else if ((w & 0xf00e) == 0x600c) { reg = m; zext = true; }                 // extu.b / extu.w
            else if ((w & 0xf0ff) == 0x4000) post *= 2;                                // shll
            else if ((w & 0xf0ff) == 0x4008) post *= 4;                                // shll2
            else if (braf && (w & 0xff00) == 0xc900 && reg == 0) return sled(f, jump, w & 0xff, post);
            else if (jmp && post == 1 && (w & 0xf00f) == 0x300c && ((w >> 8) & 0xf) == reg && m != reg) {
                return poolSled(f, jump, win, d, reg, m);                              // add rI,rN
            }
            else if ((w & 0xf00f) >= 0x000c && (w & 0xf00f) <= 0x000e) {               // mov.b/w/l @(r0,rM),rN
                return load(f, jump, win, d, w, braf, post, zext);
            } else return null;
            i = d + 1;
        }
    }

    Table sled(Function f, Instruction jump, int mask, int scale) {
        if (Integer.bitCount(mask + 1) != 1 || scale < 2) return null;
        Table t = new Table();
        t.fn = f;
        t.jump = jump;
        t.kind = "sled";
        t.base = jump.getAddress().getOffset() + 4;
        t.size = scale;
        t.count = mask + 1;
        for (int k = 0; k < t.count; k++) t.targets.add(t.base + (long) k * scale);
        return t;
    }

    /** `jmp @rN` after `add rI,rN` (win[d]), one of rN / rI a literal-pool constant P and the other a shifted index:
     *  a sled at P, every repeat of one instruction (no branch), whose next instruction is in f's body (the sled runs
     *  on into it); null when it is not one. */
    Table poolSled(Function f, Instruction jump, List<Instruction> win, int d, int n, int i) throws Exception {
        Long pn = constAt(win, d + 1, n), pi = constAt(win, d + 1, i);
        if ((pn == null) == (pi == null)) return null;
        int[] ix = unscale(win, d + 1, pn != null ? i : n);
        long base = fold(pn != null ? pn : pi);
        int size = ix[2];
        if (size < 2 || size > 4 || (base & 1) != 0 || !initialized(base, size)) return null;
        int w0 = u16(base), count = 0;
        if (branch(w0) || (size == 4 && branch(u16(base + 2)))) return null;
        for (long at = base; count < MAX_ENTRIES && initialized(at, size); at += size, count++) {
            if (u16(at) != w0 || (size == 4 && u16(at + 2) != u16(base + 2))) break;  // the last slot
            Function g = getFunctionContaining(toAddr(at));
            if (g != null && g != f) return null;  // inside another function's body
        }
        if (count < 2 || !f.getBody().contains(toAddr(base + (long) count * size))) return null;
        Table t = new Table();
        t.fn = f;
        t.jump = jump;
        t.kind = "sled";
        t.base = base;
        t.size = size;
        t.count = count;
        for (int k = 0; k < count; k++) t.targets.add(base + (long) k * size);
        return t;
    }

    /** A branch, call, jump or return opcode: a sled slot never is one. */
    static boolean branch(int w) {
        int f = w & 0xf0ff;
        return w >> 12 == 0xa || w >> 12 == 0xb || (w & 0xf900) == 0x8900 || f == 0x402b || f == 0x400b
            || f == 0x0023 || f == 0x0003 || w == 0x000b || w == 0x002b;
    }

    Table load(Function f, Instruction jump, List<Instruction> win, int d, int w, boolean braf, int post, boolean zext)
            throws Exception {
        int size = 1 << ((w & 0xf) - 0xc), m = (w >> 4) & 0xf;
        if (!braf && (size != 4 || post != 1)) return null;
        Long b0 = constAt(win, d + 1, 0), bm = m == 0 ? null : constAt(win, d + 1, m);
        if ((b0 == null) == (bm == null)) return null;  // exactly one of r0 / rM is the table address
        long base = fold(b0 != null ? b0 : bm);
        int[] ix = unscale(win, d + 1, b0 != null ? m : 0);
        if (ix[2] != size) return null;
        int count = bound(win, ix[1], ix[0], jump.getAddress().getOffset());
        if (count < 0) unbounded.add(String.format("%s	%s	%08x", f.getEntryPoint(), jump.getAddress(), base));
        if (count < 0 || !initialized(base, count * size)) return null;
        Table t = new Table();
        t.fn = f;
        t.jump = jump;
        t.kind = braf ? "braf." + "bwl".charAt(Integer.numberOfTrailingZeros(size)) : "jmp.l";
        t.base = base;
        t.size = size;
        t.count = count;
        long ja = jump.getAddress().getOffset();
        for (int k = 0; k < count; k++) {
            long at = base + (long) k * size;
            long e = size == 4 ? mem.getInt(toAddr(at)) & 0xffffffffL
                : size == 2 ? (zext ? u16(at) : (long) mem.getShort(toAddr(at)))
                : (zext ? mem.getByte(toAddr(at)) & 0xffL : (long) mem.getByte(toAddr(at)));
            long tg = braf ? (ja + 4 + e * post) & 0xffffffffL : fold(e);
            if ((tg & 1) != 0 || !initialized(tg, 2)) return null;  // not a code address: not a jump table
            if (tg >= base && tg < base + (long) count * size) return null;  // a case inside its own table
            t.targets.add(tg);
        }
        return t;
    }

    String where(Function f, long tg) {
        Address a = toAddr(tg);
        if (f.getBody().contains(a)) return "in";
        if (fm.getFunctionAt(a) != null) return "fn";
        return getFunctionContaining(a) != null ? "other" : "free";
    }

    List<Table> scan() throws Exception {
        List<Table> out = new ArrayList<>();
        unbounded.clear();
        for (Function f : fm.getFunctions(true)) {
            if (monitor.isCancelled()) break;
            for (Instruction in : listing.getInstructions(f.getBody(), true)) {
                FlowType ft = in.getFlowType();
                if (!ft.isJump() || !ft.isComputed() || ft.isCall()) continue;
                Table t = table(f, in);
                if (t != null) out.add(t);
            }
        }
        return out;
    }

    /** Takes the free cases of t into its function. Returns the number of targets taken. */
    int absorb(Table t) throws Exception {
        ReferenceManager rm = currentProgram.getReferenceManager();
        Set<Address> have = new HashSet<>();
        for (Reference r : t.jump.getReferencesFrom()) have.add(r.getToAddress());
        int taken = 0;
        for (long tg : new LinkedHashSet<>(t.targets)) {
            Address a = toAddr(tg);
            String w = where(t.fn, tg);
            if (!w.equals("free") && !(w.equals("in") && !have.contains(a))) continue;
            if (listing.getInstructionAt(a) == null) {
                if (listing.getCodeUnitContaining(a) instanceof Data) clearListing(a);
                disassemble(a);
            }
            if (listing.getInstructionAt(a) == null) {
                println("TcSwitch: " + t.jump.getAddress() + " case " + a + " does not disassemble");
                continue;
            }
            if (!have.contains(a)) {
                rm.addMemoryReference(t.jump.getAddress(), a, RefType.COMPUTED_JUMP, SourceType.ANALYSIS, 0);
                have.add(a);
            }
            if (w.equals("free")) taken++;
        }
        if (taken > 0 && !CreateFunctionCmd.fixupFunctionBody(currentProgram, t.fn, monitor)) {
            println("TcSwitch: body fixup failed for " + t.fn.getEntryPoint());
        }
        return taken;
    }

    String line(Table t) {
        StringBuilder s = new StringBuilder(String.format("%s\t%s\t%s\t%08x\t%d\t", t.fn.getEntryPoint(),
            t.jump.getAddress(), t.kind, t.base, t.count));
        for (int k = 0; k < t.targets.size(); k++) {
            if (k > 0) s.append(' ');
            s.append(String.format("%08x:%s", t.targets.get(k), where(t.fn, t.targets.get(k))));
        }
        return s.toString();
    }

    @Override
    public void run() throws Exception {
        String mode = getScriptArgs()[0];
        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        fm = currentProgram.getFunctionManager();
        if (mode.equals("fix")) {
            int total = 0;
            for (int round = 0; round < 8; round++) {
                int taken = 0;
                for (Table t : scan()) taken += absorb(t);
                println("TcSwitch round " + round + ": " + taken + " case target(s) taken into their function");
                total += taken;
                if (taken == 0) break;
            }
            if (total > 0) analyzeChanges(currentProgram);  // the call references of the new code (constant propagation)
            println("TcSwitch: " + total + " case target(s) taken in total");
        }
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(getScriptArgs()[1]), "UTF-8"))) {
            for (Table t : scan()) pw.println(line(t));
            for (String u : unbounded) pw.println("unbounded	" + u);
        }
    }
}

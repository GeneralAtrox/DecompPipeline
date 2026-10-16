// Dead functions Ghidra never decoded: complete functions nothing calls, points to or loads, left as undefined bytes
// between two functions (tools/completeness.py counts them as "unknown"). TcOrphans only makes functions of code
// that is already disassembled, so these were never catalogued: Toy Commander 8c0522e8 (318 bytes, three functions
// back to back after 8c052218's rts and literal pool), 8c050ab4, 8c066088 (a tail-call thunk), 8c01f89c (a leaf
// between 8c01f846 and the literal pool both share).
// A start S is decoded only when it is very likely code:
//  1. it starts at a clean function boundary: right after the delay slot of an rts / rte / bra / jmp (not braf: a
//     switch table follows it) in a function, past nop or zero padding, words PC-relative loads read (that
//     function's literal pool) and aligned words holding a memory address (pool words nothing decoded loads);
//  2. S is undefined bytes nothing refers to: no reference, no image word pointing to it (any mirror form), not a
//     catalog_overrides "data" / "not_entry" address, and no unconditional jump between the functions around S
//     crosses it to a target Ghidra's Shared Return Calls would then make a function (not an entry, nothing falls
//     into it): Spawn ovl 8c5a2546 lies between 8c5a2526 and its `bra 8c5a2580`, so 8c5a2526 would lose 8c5a2580..;
//     such S stay unknown;
//  3. its straight line (decoded linearly from S through the first rts / rte / bra / braf / jmp and its delay slot,
//     at most MAX_PATH instructions) holds only legal instructions on undefined bytes that no function owns and no
//     load reads; its PC-relative loads read no instruction, no function's body and nothing on the straight line
//     itself; its branches land on no loaded word, on an instruction of no function or a function's entry (a
//     tail call; not its middle: TC 8c06caf8 is a loop inside 8c06cadc that only branches to itself), or on
//     undefined bytes between S and the next instruction (a never-decoded target before S waits for a later round);
//  4. it carries a code signal: a prologue first (sts.l pr,@-r15 or mov.l rN,@-r15, as TcOrphans), or a balanced
//     frame (r15 lowered within the first FRAME_WITHIN instructions, changed only by push, pop and add #imm, and
//     back at its entry value after the delay slot: TC 8c04b162 `add #-4,r15 ... jmp @r3; add #4,r15`), or a leaf
//     (no pointer word skipped before it) that never changes r15 and reads no register before writing it except
//     the argument registers, r15, PR and FPSCR (TC 8c050b40 `tst r4,r4 ... rts`, 8c04cb1c `bra 8c04caa0; nop`).
// Ghidra then disassembles S following its flows; the result is undone (and S never retried) when it covers a
// pool word, does not cover the straight line, or no function can be made. Each accepted S becomes a function
// (reason prologue / frame / leaf). Repeats until nothing changes: a new function's end is the next boundary.
// Dead epilogue fragments (TC 8c015694 `add #16,r15; lds.l @r15+,pr; rts`) raise r15 first: never accepted.
// TcOrphans runs next (callees of the new code, fall-only merges, dead fragments), then TcPrune.
//
// Script args: 0 output file (one line per created function: entry, round, reason, body bytes); 1 (optional) the
// catalog_overrides file. Runs on the master after TcPools, before TcOrphans (core/ghidra.py).
//@category DecompPipeline
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.PseudoDisassembler;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class TcDead extends GhidraScript {
    static final int MAX_ROUNDS = 20, MAX_PATH = 1024, MAX_GAP = 0x800, FRAME_WITHIN = 4;
    static final Set<String> ARGS = new HashSet<>(Arrays.asList("r4", "r5", "r6", "r7", "r15", "PR", "FPSCR",
        "FPSCR_SZ", "FPSCR_PR", "dr4", "dr6", "dr8", "dr10", "xd4", "xd6", "xd8", "xd10"));
    static {
        for (int i = 4; i < 12; i++) ARGS.add("fr" + i);
    }

    final Map<Long, Integer> pool = new HashMap<>();  // address -> bytes a PC-relative load of function code reads
    final Set<Long> pointed = new HashSet<>();  // P1 addresses an aligned image word points to (P0/P1/P2 form)
    final Set<Long> pointerWords = new HashSet<>();  // where those words are
    final Set<Address> excluded = new HashSet<>();  // catalog_overrides "data" / "not_entry", and failed starts
    final List<long[]> jumps = new ArrayList<>();  // [from, to] of the jumps Shared Return Calls may act on
    Memory mem;
    Listing listing;
    FunctionManager fm;
    PseudoDisassembler pd;

    int u16(long a) throws Exception {
        return mem.getShort(toAddr(a)) & 0xffff;
    }

    /** In a block with bytes (an image gap or a RAM block past the image has none). */
    boolean inited(long a) {
        MemoryBlock b = mem.getBlock(toAddr(a));
        return b != null && b.isInitialized();
    }

    /** The bytes a PC-relative load at `a` (opcode w) reads: mov.l / mova 4 at (a & ~3) + 4 + d*4, mov.w 2. */
    static long[] pcLoad(long a, int w) {
        if ((w & 0xf000) == 0xd000 || (w & 0xff00) == 0xc700) return new long[] {(a & ~3L) + 4 + (w & 0xff) * 4L, 4};
        if ((w & 0xf000) == 0x9000) return new long[] {a + 4 + (w & 0xff) * 2L, 2};
        return null;
    }

    /** The pool words of function code, and the spans of jumps as SharedReturnAnalysisCmd selects them. */
    void indexPools() throws Exception {
        pool.clear();
        jumps.clear();
        for (Instruction in : listing.getInstructions(true)) {
            long a = in.getAddress().getOffset();
            for (Reference r : in.getReferencesFrom()) {  // it takes the first flow: any of them, to be safe
                RefType rt = r.getReferenceType();
                Address to = r.getToAddress();
                if (rt.isFlow() && rt.isJump() && rt.isUnConditional() && fm.getFunctionAt(to) == null && !mayFallInto(to))
                    jumps.add(new long[] {a, to.getOffset()});
            }
            if (fm.getFunctionContaining(in.getAddress()) == null) continue;
            long[] t = pcLoad(a, u16(a));
            if (t != null) for (long x = t[0]; x < t[0] + t[1]; x++) pool.merge(x, (int) (t[0] + t[1] - x), Math::max);
        }
    }

    /** SharedReturnAnalysisCmd.checkIfCouldHaveFallThruTo: then it makes no function there. */
    boolean mayFallInto(Address x) {
        Instruction in = listing.getInstructionAt(x);
        if (in == null || in.getFlowType() == RefType.TERMINATOR) return true;
        Address from = in.getFallFrom();
        Instruction f = from == null ? null : listing.getInstructionContaining(from);
        return f != null && x.equals(f.getFallThrough());
    }

    /** SharedReturnAnalysisCmd after a function at s: it scans the jumps from the function before s to the one
     *  after, and makes a function of a forward target past s or a backward target before s. */
    boolean crossed(long s) {
        FunctionIterator up = fm.getFunctions(toAddr(s), false), down = fm.getFunctions(toAddr(s), true);
        long lo = up.hasNext() ? up.next().getEntryPoint().getOffset() : 0;
        long hi = down.hasNext() ? down.next().getEntryPoint().getOffset() : Long.MAX_VALUE;
        for (long[] j : jumps)
            if (j[0] >= lo && j[0] < hi && (j[0] < s && j[1] >= s || j[0] > s && j[1] < s)) return true;
        return false;
    }

    void indexPointers() throws Exception {
        for (MemoryBlock b : mem.getBlocks()) {
            if (!b.isInitialized() || b.getSize() > (64L << 20)) continue;
            byte[] buf = new byte[(int) b.getSize()];
            b.getBytes(b.getStart(), buf);
            long base = b.getStart().getOffset();
            for (int i = (int) ((4 - (base & 3)) & 3); i + 4 <= buf.length; i += 4) {
                long w = (buf[i] & 0xffL) | (buf[i + 1] & 0xffL) << 8 | (buf[i + 2] & 0xffL) << 16 | (buf[i + 3] & 0xffL) << 24;
                long seg = w >>> 29, p = (w & 0x1fffffffL) | 0x80000000L;
                if ((seg == 0 || seg == 4 || seg == 5) && mem.contains(toAddr(p))) {
                    pointed.add(p);
                    pointerWords.add(base + i);
                }
            }
        }
    }

    /** Loaded as data: a READ/WRITE reference or a PC-relative load of function code reads it. */
    boolean loaded(long a) {
        if (pool.containsKey(a)) return true;
        for (Reference r : getReferencesTo(toAddr(a)))
            if (r.getReferenceType().isRead() || r.getReferenceType().isWrite()) return true;
        return false;
    }

    /** No instruction, no function, and no defined data except an untyped "undefinedN" item (TcOrphans). */
    boolean free(long a) {
        Address x = toAddr(a);
        if (!inited(a) || !inited(a + 1) || listing.getInstructionContaining(x) != null || fm.getFunctionContaining(x) != null)
            return false;
        Data d = listing.getDefinedDataContaining(x);
        return d == null || ghidra.program.model.data.Undefined.isUndefined(d.getDataType());
    }

    boolean gapPointer;  // pastGap skipped a pointer word nothing decoded loads

    /** The first address past padding, pool words and pointer words after `a` (the end of a delay slot), or -1. */
    long pastGap(long a) throws Exception {
        long end = a + MAX_GAP;
        gapPointer = false;
        while (a < end && inited(a) && inited(a + 1)) {
            Address x = toAddr(a);
            if (listing.getInstructionContaining(x) != null || fm.getFunctionContaining(x) != null) return -1;
            if (loaded(a)) a += pool.getOrDefault(a, 2);
            else if (pointerWords.contains(a)) {
                a += 4;
                gapPointer = true;
            }
            else if ((u16(a) == 0 || u16(a) == 0x0009) && free(a)) a += 2;
            else return a;
        }
        return -1;
    }

    Instruction decode(long a) {
        try {
            return pd.disassemble(toAddr(a));
        } catch (Exception e) {
            return null;
        }
    }

    static boolean ends(Instruction in) {
        FlowType ft = in.getFlowType();
        return ft.isTerminal() || ft.isJump() && !ft.isConditional();
    }

    static final java.util.regex.Pattern STACK = java.util.regex.Pattern.compile(
        "(?:mov\\.l|sts\\.l|fmov\\.s|fmov) (\\w+),@-r15|(?:mov\\.l|lds\\.l|fmov\\.s|fmov) @r15\\+,(\\w+)|add #(-?0x[0-9a-f]+),r15");

    /** The r15 change of one instruction (push, pop, add #imm), 0 when r15 is untouched, null for any other write. */
    static Integer stackDelta(Instruction in) {
        String s = in.toString().toLowerCase().replaceFirst("^_", "");
        java.util.regex.Matcher m = STACK.matcher(s);
        if (m.matches()) {
            if (m.group(3) != null) return (int) Long.parseLong(m.group(3).replace("0x", ""), 16);
            String r = m.group(1) != null ? m.group(1) : m.group(2);
            int n = r.startsWith("dr") || r.startsWith("xd") ? 8 : 4;
            return m.group(1) != null ? -n : n;
        }
        if (in.getDelaySlotDepth() > 0) return 0;  // its result objects include the delay slot's
        for (Object o : in.getResultObjects())
            if (o instanceof Register && ((Register) o).getName().equals("r15")) return null;
        return 0;
    }

    long pathEnd;  // the end of the straight line signal() decoded last

    /** The code signal of the straight line from s (see the header), or null when it is not very likely code. */
    String signal(long s, boolean leafOk) throws Exception {
        List<Instruction> path = new ArrayList<>();
        for (long a = s; path.size() < MAX_PATH; ) {
            Instruction in = decode(a);
            if (in == null || in.getLength() != 2 || !free(a) || loaded(a) || loaded(a + 1)
                    || excluded.contains(toAddr(a))) return null;
            path.add(in);
            a += 2;
            if (path.size() >= 2 && path.get(path.size() - 2).getDelaySlotDepth() > 0 && ends(path.get(path.size() - 2)))
                break;
            if (path.size() == MAX_PATH) return null;
        }
        long last = s + 2L * path.size();
        pathEnd = last;
        Instruction known = listing.getInstructionAfter(toAddr(s));
        long next = known == null ? Long.MAX_VALUE : known.getAddress().getOffset();
        for (Instruction in : path) {
            long a = in.getAddress().getOffset();
            long[] t = pcLoad(a, u16(a));
            if (t != null && (t[0] + t[1] > s && t[0] < last || listing.getInstructionContaining(toAddr(t[0])) != null
                    || fm.getFunctionContaining(toAddr(t[0])) != null)) return null;
            for (Address f : in.getFlows()) {
                long x = f.getOffset();
                Function g = fm.getFunctionContaining(f);
                if (x % 2 != 0 || !mem.contains(f) || loaded(x) || g != null && !g.getEntryPoint().equals(f)
                        || g == null && listing.getInstructionAt(f) == null && (!free(x) || x < s || x >= next))
                    return null;
            }
        }
        if (TcOrphans.prologue(path.get(0))) return "prologue";
        int depth = 0, lowered = -1;
        boolean other = false;
        for (int i = 0; i < path.size(); i++) {
            Integer d = stackDelta(path.get(i));
            if (d == null) other = true;
            else {
                if (d < 0 && depth == 0 && lowered < 0) lowered = i;
                depth += d;
                if (depth > 0) other = true;
            }
        }
        if (!other && depth == 0 && lowered >= 0 && lowered < FRAME_WITHIN) return "frame";
        if (!leafOk || other || lowered >= 0) return null;
        Set<String> written = new HashSet<>();
        for (int i = 0; i < path.size(); i++) {
            Instruction in = path.get(i);
            if (in.getDelaySlotDepth() > 0 && i + 1 < path.size()) {
                if (!reads(path.get(i + 1), written)) return null;
                if (!reads(in, written)) return null;
                i++;
            } else if (!reads(in, written)) return null;
        }
        return "leaf";
    }

    /** False when `in` reads a register not yet written that is not an argument register; records its writes. */
    static boolean reads(Instruction in, Set<String> written) {
        for (Object o : in.getInputObjects())
            if (o instanceof Register && !written.contains(((Register) o).getName())
                    && !ARGS.contains(((Register) o).getName())) return false;
        for (Object o : in.getResultObjects()) if (o instanceof Register) written.add(((Register) o).getName());
        return true;
    }

    /** Disassemble s with its flows; undone when the code covers loaded bytes or misses the straight line. */
    boolean decodeAt(long s, long last) throws Exception {
        for (long a = s; a < last; a += 2) {
            Data d = listing.getDefinedDataContaining(toAddr(a));
            if (d != null) clearListing(d.getMinAddress(), d.getMaxAddress());
        }
        DisassembleCommand cmd = new DisassembleCommand(toAddr(s), null, true);
        cmd.applyTo(currentProgram, monitor);
        AddressSetView got = cmd.getDisassembledAddressSet();
        boolean ok = got != null && got.contains(toAddr(s), toAddr(last - 1));
        if (ok) for (AddressRange r : got)
            for (long a = r.getMinAddress().getOffset(); ok && a <= r.getMaxAddress().getOffset(); a++) ok = !loaded(a);
        if (!ok && got != null && !got.isEmpty()) {
            for (AddressRange r : got) clearListing(r.getMinAddress(), r.getMaxAddress());
        }
        return ok;
    }

    @Override
    public void run() throws Exception {
        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        fm = currentProgram.getFunctionManager();
        pd = new PseudoDisassembler(currentProgram);
        if (getScriptArgs().length > 1) {
            for (String line : java.nio.file.Files.readAllLines(java.nio.file.Paths.get(getScriptArgs()[1]))) {
                String[] p = line.trim().split("\\s+");
                if (p.length == 2 && (p[0].equals("data") || p[0].equals("not_entry")))
                    excluded.add(toAddr(Long.parseLong(p[1], 16)));
            }
        }
        indexPointers();
        int total = 0;
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(getScriptArgs()[0]), "UTF-8"))) {
            for (int round = 0; round < MAX_ROUNDS; round++) {
                indexPools();
                Map<Long, String> found = new TreeMap<>();
                Map<Long, Long> lines = new HashMap<>();  // start -> end of its straight line
                for (Instruction in : listing.getInstructions(true)) {
                    if (in.getDelaySlotDepth() == 0 || !ends(in) || in.getMnemonicString().toLowerCase().contains("braf")
                            || fm.getFunctionContaining(in.getAddress()) == null) continue;
                    Instruction slot = in.getNext();
                    if (slot == null || !slot.isInDelaySlot()) continue;
                    long after = slot.getMaxAddress().getOffset() + 1, s = pastGap(after);
                    Address sa = toAddr(s);
                    if (s < 0 || found.containsKey(s) || !free(s) || excluded.contains(sa) || pointed.contains(s)
                            || getReferencesTo(sa).length > 0 || crossed(s)) continue;
                    String why = signal(s, !gapPointer);
                    if (why == null) continue;
                    found.put(s, why);
                    lines.put(s, pathEnd);
                }
                int made = 0;
                for (Map.Entry<Long, String> e : found.entrySet()) {
                    long s = e.getKey();
                    Address sa = toAddr(s);
                    if (!free(s)) continue;  // an earlier start this round decoded it
                    long last = lines.get(s);
                    Function f = decodeAt(s, last) ? createFunction(sa, null) : null;
                    if (f == null) {
                        excluded.add(sa);
                        if (listing.getInstructionAt(sa) != null) clearListing(sa, toAddr(last - 1));
                        pw.println("# " + sa + " not decoded (" + e.getValue() + "): its code covers loaded bytes or no function");
                        continue;
                    }
                    pw.println(sa + "\t" + round + "\t" + e.getValue() + "\t" + f.getBody().getNumAddresses());
                    made++;
                }
                total += made;
                if (made == 0) break;
            }
        }
        println("TcDead: " + total + " function(s) created for never-decoded dead code");
    }
}

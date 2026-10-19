// Dead runs of hand-written code Ghidra never decoded and TcDead does not accept: unreferenced routines with no
// standard prologue, packed back to back between two functions. Toy Commander 8c0616b0..8c061a48 (920 bytes) is a
// row of dead matrix routines next to the matched FPU helpers: tail stubs (`mov.l op,r0; bra 8c061698; fmov.s
// fr12,@-r15`), wrappers (`sts PR,r4; bsr; nop; bra 8c06162c; lds r4,PR`) and leaf routines that push fr12..fr15,
// load or add 4x4 matrices with fschg/frchg pairs and end in rts; 8c061f10 reads fr12..fr14 and has two exits.
// TcDead decodes one straight line from a clean boundary and needs a code signal (prologue, balanced frame, or a
// leaf reading only argument registers); these routines read fr12..fr15 first, push without popping (the target of
// the tail jump pops), or start after a pointer word only dead code loads.
//
// The rule, for each span of undefined bytes in the game range that starts at a clean boundary (as TcDead: after the
// delay slot of an rts / rte / bra / jmp of a function, not braf and not a jmp to a computed address, whose jump table
// tail follows; past padding, pool words and pointer words) and ends at the next instruction or function, and that
// is not a gap between two parts of one function (see inside):
//  1. nothing outside the span refers to a piece's code (no reference, no image word holding an address in it, in any
//     mirror form); a piece something reaches is refused (not dead: TC 8c061ed8, see reachedFrom), and so is a piece
//     an image word inside the span points into, unless an accepted piece loads that word (Spawn 8c024cf4, see
//     pointedInside);
//  2. the whole span is code: it is swept piece by piece, and every byte is a piece, padding, or a pool word a piece
//     loads. A piece is the straight line from its start through the first rts / rte / bra / jmp and its delay slot
//     (or up to the function entry it falls into), plus the lines its forward branches reach in the span. All its
//     instructions are legal, on undefined bytes nothing loads, and its PC-relative loads read no code;
//  3. each piece is classified:
//     leaf: every line ends in rts, r15 changes only by push / pop / add #imm, never rises above its entry value and
//           is back there at each rts;
//     tail: a line ends in a bra to a function's entry (or falls into one): a stub whose pushes the target pops;
//     refused: anything else, which stays undecoded: a branch or call into the middle of a function or another piece
//           (an alternate entry of a shared tail: TC 8c061a00, 8c061ed8), a jmp / rte end, any other r15 write, or
//           a pop of what it never pushed (dead epilogue fragments, TC 8c015694: TcOrphans keeps dropping those);
//  4. the span holds at least one leaf (the code signal: a balanced routine ending in rts).
// Calls and tail jumps go only to function entries. A computed-jump tail (TC 8c04ee90 after `add r6,r1; jmp @r1`),
// a switch table after braf, literal pools and padding never start or end a piece. Each leaf, then each tail, becomes
// a function unless a jump crossing it would make Ghidra's Shared Return Calls split a neighbour (TcDead.crossed);
// the decode must cover exactly the piece. Privileged instructions are allowed: TcCatalog's sys_ops then puts the
// function in the excluded tier.
//
// Script args: 0 output file (one line per created function: entry, kind, body bytes; "#" lines for refused pieces
// of an accepted span); 1 the catalog_overrides file ("data" / "not_entry" addresses are never code); 2 the game
// ranges, "lo-hi,lo-hi" in hex. Runs on the master after TcDead, before TcOrphans (core/ghidra.py).
//@category DecompPipeline
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.util.PseudoDisassembler;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import java.io.*;
import java.util.*;

public class TcRuns extends TcDead {
    static final int MAX_SPAN = 0x1000, MAX_LINE = 1024;
    static final Set<String> COMPUTE = new HashSet<>(Arrays.asList(
        "add", "addc", "addv", "sub", "subc", "subv", "shll", "shll2", "shll8", "shll16", "shld", "shad", "shal", "neg"));

    final List<long[]> game = new ArrayList<>();
    final TreeMap<Long, List<Long>> pointerFrom = new TreeMap<>();  // P1 target -> the image words holding it

    static class Piece {
        long start, lineEnd;  // the first straight line is [start, lineEnd)
        final TreeSet<Long> code = new TreeSet<>();  // its instruction addresses
        final Set<Long> pool = new HashSet<>();  // the bytes its PC-relative loads read
        boolean tail;
        String why;  // set when refused
    }

    boolean inGame(long a) {
        for (long[] r : game) if (a >= r[0] && a < r[1]) return true;
        return game.isEmpty();
    }

    static String mnemonic(Instruction in) {
        return in.getMnemonicString().toLowerCase().replaceFirst("^_", "");
    }

    /** A jmp @rN whose rN was last computed by arithmetic: a jump into the table tail that follows it (TC 8c04edec
     *  `shll r6; add r6,r1; jmp @r1`, then 31 `mov.b r5,@-r4`). A jmp to a loaded or saved address is not one. */
    boolean computedJump(Instruction in) {
        if (!mnemonic(in).equals("jmp") || !(in.getOpObjects(0).length > 0 && in.getOpObjects(0)[0] instanceof Register))
            return false;
        Register r = (Register) in.getOpObjects(0)[0];
        Instruction q = in.getPrevious();
        for (int k = 0; k < 8 && q != null; k++, q = q.getPrevious()) {
            for (Object o : q.getResultObjects())
                if (r.equals(o)) return COMPUTE.contains(mnemonic(q));
        }
        return false;
    }

    /** The end of the span of undefined bytes at s: the next instruction, function or byte-less address; -1 when it
     *  is longer than MAX_SPAN or leaves the game range. Defined data (a typed pool word) does not end it. */
    long spanEnd(long s) {
        for (long a = s; a < s + MAX_SPAN; a += 2) {
            Address x = toAddr(a);
            if (!inited(a) || listing.getInstructionContaining(x) != null || fm.getFunctionContaining(x) != null)
                return inGame(a - 1) ? a : -1;
            if (!inGame(a)) return -1;
        }
        return -1;
    }

    /** What reaches the piece's code from outside the span [s, end): a reference, or an image word pointing into it
     *  (TC 8c061ed4, the pool word 8c061ec4 loads into PR before `bra 8c061db0`, is the return address 8c061ee4
     *  inside the piece at 8c061ed8). Null when nothing does. */
    String reachedFrom(Piece pc, long s, long end) {
        for (long a : pc.code)
            for (long x = a; x < a + 2; x++) {
                for (ghidra.program.model.symbol.Reference r : getReferencesTo(toAddr(x))) {
                    long f = r.getFromAddress().getOffset();
                    if (f < s || f >= end) return "reached from " + r.getFromAddress();
                }
                for (long w : pointerFrom.getOrDefault(x, Collections.emptyList()))
                    if (w < s || w >= end) return "its address " + toAddr(x) + " is stored at " + toAddr(w);
            }
        return null;
    }

    /** Refuse every piece an image word inside the span points into, unless an accepted piece loads that word (TC
     *  8c061700, the pool word of the tail 8c0616f8, holds its return address 8c0616ee). A word only refused code
     *  loads may still be live: Spawn 8c024cdc jumps to 0xac024cf8, which runs into 8c024d10 and its `jmp` to the
     *  pool word 8c024d20 = 0x0c024cf4, so the `rts; nop` at 8c024cf4 is reached. Repeats until nothing changes. */
    void pointedInside(List<Piece> pieces, long s, long end) {
        for (boolean changed = true; changed; ) {
            changed = false;
            Set<Long> owned = new HashSet<>();
            for (Piece pc : pieces) if (pc.why == null) owned.addAll(pc.pool);
            for (Piece pc : pieces) {
                if (pc.why != null) continue;
                for (long a : pc.code)
                    for (long x = a; x < a + 2 && pc.why == null; x++)
                        for (long w : pointerFrom.getOrDefault(x, Collections.emptyList()))
                            if (w >= s && w < end && !owned.contains(w)) {
                                pc.why = "its address " + toAddr(x) + " is stored at " + toAddr(w)
                                    + ", which no accepted piece loads";
                                changed = true;
                                break;
                            }
            }
        }
    }

    /** The span at s is a gap between parts of function f: dead bytes inside a routine, not a routine (Spawn GEND
     *  8c56402a, the unreachable `rts; sub r3,r0` between two parts of the library strcmp 8c563fbc). */
    static boolean inside(Function f, long s) {
        return f != null && f.getBody().getMinAddress().getOffset() < s && f.getBody().getMaxAddress().getOffset() > s;
    }

    static void refuse(Piece pc, String why) {
        if (pc.why == null) pc.why = why;
    }

    /** One instruction of a piece (or a delay slot): legal, on free bytes, its loads and flows checked. False when
     *  the bytes are not code (the span is then not all code). Updates depth (r15 change; refuse on other writes). */
    boolean take(Piece pc, Instruction in, long a, long end, Map<Long, Integer> runPool, Set<Long> covered,
                 int[] depth, Deque<long[]> work) throws Exception {
        if (in == null || in.getLength() != 2 || !free(a) || loaded(a) || loaded(a + 1) || runPool.containsKey(a)
                || excluded.contains(toAddr(a))) return false;
        pc.code.add(a);
        long[] t = pcLoad(a, u16(a));
        if (t != null) {
            for (long x = t[0]; x < t[0] + t[1]; x++) {
                Address y = toAddr(x);
                if (listing.getInstructionContaining(y) != null || fm.getFunctionContaining(y) != null
                        || pc.code.contains(x & ~1L) || covered.contains(x & ~1L)) return false;
                runPool.merge(x, (int) (t[0] + t[1] - x), Math::max);
                pc.pool.add(x);
            }
        }
        Integer d = stackDelta(in);
        if (d == null) refuse(pc, "writes r15");
        else {
            depth[0] += d;
            if (depth[0] > 0) refuse(pc, "pops what it never pushed");
        }
        String m = mnemonic(in);
        for (Address f : in.getFlows()) {
            long x = f.getOffset();
            if (x % 2 != 0 || !mem.contains(f) || loaded(x) || runPool.containsKey(x)) return false;
            Function g = fm.getFunctionContaining(f);
            boolean call = m.startsWith("bsr") || m.startsWith("jsr");
            if (g != null) {
                if (!g.getEntryPoint().equals(f)) refuse(pc, "branches into the middle of " + g.getEntryPoint());
                else if (!call) pc.tail = true;
            } else if (call) refuse(pc, "calls " + f + ", no function entry");
            else if (listing.getInstructionAt(f) != null) refuse(pc, "branches into code of no function at " + f);
            else if (pc.code.contains(x)) continue;
            else if (covered.contains(x)) refuse(pc, "branches into another piece at " + f);
            else if (x > a && x < end && free(x)) work.add(new long[] {x, 0});  // depth fixed by the caller
            else refuse(pc, "branches to " + f + " outside the run");
        }
        return true;
    }

    /** The piece at p, or null when its bytes are not code. */
    Piece piece(long p, long end, Map<Long, Integer> runPool, Set<Long> covered) throws Exception {
        Piece pc = new Piece();
        pc.start = p;
        Deque<long[]> work = new ArrayDeque<>();
        work.add(new long[] {p, 0});
        boolean first = true;
        while (!work.isEmpty()) {
            long[] w = work.poll();
            long a = w[0];
            int[] depth = {(int) w[1]};
            for (int n = 0; ; n++) {
                if (n > MAX_LINE) return null;
                if (pc.code.contains(a)) break;  // joins one of its own lines
                if (covered.contains(a)) {
                    refuse(pc, "runs into another piece at " + toAddr(a));
                    break;
                }
                if (a >= end) {
                    Function g = fm.getFunctionAt(toAddr(a));
                    if (g != null) pc.tail = true;
                    else refuse(pc, "runs into " + toAddr(a) + ", no function entry");
                    break;
                }
                Instruction in = decode(a);
                Deque<long[]> found = new ArrayDeque<>();
                if (!take(pc, in, a, end, runPool, covered, depth, found)) return null;
                String m = mnemonic(in);
                if (m.equals("braf")) return null;  // a switch table follows
                if (in.getDelaySlotDepth() > 0) {
                    if (!take(pc, decode(a + 2), a + 2, end, runPool, covered, depth, found)) return null;
                    a += 4;
                } else a += 2;
                for (long[] f : found) work.add(new long[] {f[0], depth[0]});
                if (ends(in)) {
                    if (m.equals("rts")) {
                        if (depth[0] != 0) refuse(pc, "returns with r15 moved");
                    } else if (!m.equals("bra")) refuse(pc, m + " ends it");
                    break;
                }
            }
            if (first) pc.lineEnd = a;
            first = false;
        }
        return pc;
    }

    /** The pieces of the span [s, end), or null when not every byte of it is code, padding or a pool word. */
    List<Piece> sweep(long s, long end) throws Exception {
        Map<Long, Integer> runPool = new HashMap<>();
        Set<Long> covered = new HashSet<>();
        List<Piece> out = new ArrayList<>();
        for (long a = s; a < end; ) {
            if (covered.contains(a)) a += 2;
            else if (runPool.containsKey(a)) a += runPool.get(a);
            else if ((u16(a) == 0 || u16(a) == 0x0009) && free(a)) a += 2;
            else {
                Piece pc = piece(a, end, runPool, covered);
                if (pc == null) return null;
                String r = reachedFrom(pc, s, end);
                if (r != null) pc.why = r + (pc.why == null ? "" : "; " + pc.why);
                covered.addAll(pc.code);
                out.add(pc);
                a = Math.max(pc.lineEnd, a + 2);
            }
        }
        pointedInside(out, s, end);
        return out;
    }

    /** Disassemble the piece; undone unless the new code is exactly the piece. */
    boolean decodePiece(Piece pc) throws Exception {
        AddressSet want = new AddressSet();
        for (long a : pc.code) {
            want.add(toAddr(a), toAddr(a + 1));
            Data d = listing.getDefinedDataContaining(toAddr(a));
            if (d != null) clearListing(d.getMinAddress(), d.getMaxAddress());
        }
        DisassembleCommand cmd = new DisassembleCommand(toAddr(pc.start), null, true);
        cmd.applyTo(currentProgram, monitor);
        AddressSetView got = cmd.getDisassembledAddressSet();
        if (got != null && got.equals(want)) return true;
        if (got != null) for (AddressRange r : got) clearListing(r.getMinAddress(), r.getMaxAddress());
        return false;
    }

    @Override
    public void run() throws Exception {
        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        fm = currentProgram.getFunctionManager();
        pd = new PseudoDisassembler(currentProgram);
        String[] args = getScriptArgs();
        if (args.length > 1 && new File(args[1]).isFile()) {
            for (String line : java.nio.file.Files.readAllLines(java.nio.file.Paths.get(args[1]))) {
                String[] p = line.trim().split("\\s+");
                if (p.length == 2 && (p[0].equals("data") || p[0].equals("not_entry")))
                    excluded.add(toAddr(Long.parseLong(p[1], 16)));
            }
        }
        // the game ranges: cmd.exe (analyzeHeadless.bat) splits the comma-joined list into one argument each
        for (int i = 2; i < args.length; i++)
            for (String r : args[i].split(",")) {
                if (r.isEmpty()) continue;
                String[] p = r.split("-");
                game.add(new long[] {Long.parseLong(p[0], 16), Long.parseLong(p[1], 16)});
            }
        indexPointers();
        for (long w : pointerWords) {
            long v = mem.getInt(toAddr(w)) & 0xffffffffL;
            pointerFrom.computeIfAbsent((v & 0x1fffffffL) | 0x80000000L, k -> new ArrayList<>()).add(w);
        }
        indexPools();
        TreeMap<Long, List<Piece>> spans = new TreeMap<>();
        for (Instruction in : listing.getInstructions(true)) {
            if (in.getDelaySlotDepth() == 0 || !ends(in) || mnemonic(in).equals("braf") || computedJump(in)
                    || fm.getFunctionContaining(in.getAddress()) == null) continue;
            Instruction slot = in.getNext();
            if (slot == null || !slot.isInDelaySlot()) continue;
            long s = pastGap(slot.getMaxAddress().getOffset() + 1);
            if (s < 0 || spans.containsKey(s) || !inGame(s) || !free(s)) continue;
            long end = spanEnd(s);
            if (end < 0 || inside(fm.getFunctionContaining(in.getAddress()), s)
                    || inside(fm.getFunctionContaining(toAddr(end)), s)) continue;
            List<Piece> pieces = sweep(s, end);
            if (pieces != null && pieces.stream().anyMatch(p -> p.why == null && !p.tail)) spans.put(s, pieces);
        }
        int total = 0;
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(args[0]), "UTF-8"))) {
            for (List<Piece> pieces : spans.values()) {
                for (int pass = 0; pass < 2; pass++)  // leaves, then tails
                    for (Piece pc : pieces) {
                        if (pc.why != null || pc.tail != (pass == 1)) continue;
                        Address sa = toAddr(pc.start);
                        String kind = pc.tail ? "tail" : "leaf";
                        if (crossed(pc.start)) {
                            pw.println("# " + sa + " " + kind + " skipped: a jump crosses it (Shared Return Calls)");
                            continue;
                        }
                        Function f = decodePiece(pc) ? createFunction(sa, null) : null;
                        if (f == null) {
                            pw.println("# " + sa + " " + kind + " not decoded: its code is not exactly the piece");
                            continue;
                        }
                        pw.println(sa + "\t" + kind + "\t" + f.getBody().getNumAddresses());
                        total++;
                    }
                for (Piece pc : pieces)
                    if (pc.why != null) pw.println("# " + toAddr(pc.start) + " refused: " + pc.why);
            }
        }
        println("TcRuns: " + total + " function(s) created for dead runs of code in " + game.size() + " game range(s)");
    }
}

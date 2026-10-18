// Functions for disassembled code that belongs to no function ("orphan" instructions; tools/completeness.py counts
// them). Ghidra makes functions at call targets, not at addresses only stored as data, so routines reached through
// function-pointer tables stay orphans: Toy Commander had 23.8 KB of them (8c0103c4, pointed to from 8c010716;
// 8c013850, a 2.5 KB routine with a full prologue).
// For each maximal run of instructions in no function: the run's first instruction becomes a function entry when
// something outside the run refers to it (data pointer, call, computed jump/call, or an aligned image word holding
// its address in P0/P1/P2 form, which Ghidra does not reference for P0) or it starts a prologue
// (sts.l PR,@-r15 or mov.l rN,@-r15). Runs entered only by plain branches from other functions are left alone:
// switch cases and shared tails (TcSwitch). Repeats until nothing changes, since a new function shrinks the runs.
// TcPrune runs afterwards and removes any of them that is data seeded as code.
// Each round also disassembles never-decoded branch/call targets of function code (not bytes an instruction loads
// as data) and never-decoded entries of method/handler tables (decodeTableEntries), and merges a function nothing refers to, which cannot run alone, into the function whose code runs into
// it (a fall-only entry: TC 8c02a814 inside 8c02a810's prologue). Bytes loaded as data are never an entry.
// Before the rounds, flows Ghidra refused for conflicting data that is gone now are retried (retryConflicts);
// after them, dead epilogue fragments (no references, pop what they never pushed, nothing runs into them) are
// dropped (dropDeadFragments).
//
// Not an entry: a data pointer loaded by the function whose span holds the target (a local computed jump), or an
// address catalog_overrides marks "data" / "not_entry".
//
// Script args: 0 output file (one line per created function: entry, round, reason); 1 (optional) the
// catalog_overrides file TcOverride reads. Runs on the master after TcOverride, before TcPrune, TcSwitch and every
// TcCatalog (core/ghidra.py).
//@category DecompPipeline
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class TcOrphans extends GhidraScript {
    static final int MAX_ROUNDS = 20;

    static boolean prologue(Instruction in) {
        String s = in.toString().toLowerCase();
        return s.startsWith("sts.l pr,@-r15") || s.matches("mov\\.l r\\d+,@-r15");
    }

    /** True when the pointer stored at `word` is loaded by a function whose address span holds `target`: a local
     *  computed-jump target (memset 8c04eed8 loads 8c04ef60 from its pool and jumps into its unrolled stores). */
    boolean localTarget(Address word, Address target) {
        for (Reference rd : getReferencesTo(word)) {
            Function f = currentProgram.getFunctionManager().getFunctionContaining(rd.getFromAddress());
            if (f != null && f.getBody().getMinAddress().compareTo(target) <= 0
                    && f.getBody().getMaxAddress().compareTo(target) >= 0) return true;
        }
        return false;
    }

    /** True when an instruction loads or stores at `a` (a READ/WRITE reference, e.g. a PC-relative mov.l from a literal
     *  pool): those bytes are data. Spawn 8c02eab0 is a pool word (read by the mov.l at 8c02ea38) that a `bt` decoded
     *  from another pool word branched to. A stored pointer to code is a plain DATA reference, not READ. */
    boolean loadedAsData(Address a) {
        for (Reference r : getReferencesTo(a))
            if (r.getReferenceType().isRead() || r.getReferenceType().isWrite()) return true;
        return false;
    }

    /** Aligned image words holding a memory address in P0, P1 or P2 form (0x0c.., 0x8c.., 0xac.. for RAM), keyed by
     *  the P1 address. Ghidra makes no reference for a P0-form pointer: Spawn's pool word 0x0c062d6a (at 8c0563b8)
     *  points to the alternate entry 8c062d6a, and the listing shows nothing referring to it. */
    final Map<Long, List<Address>> pointers = new HashMap<>();

    void indexPointers() throws Exception {
        ghidra.program.model.mem.Memory mem = currentProgram.getMemory();
        for (ghidra.program.model.mem.MemoryBlock b : mem.getBlocks()) {
            if (!b.isInitialized() || b.getSize() > (64L << 20)) continue;
            byte[] buf = new byte[(int) b.getSize()];
            b.getBytes(b.getStart(), buf);
            long base = b.getStart().getOffset();
            for (int i = (int) ((4 - (base & 3)) & 3); i + 4 <= buf.length; i += 4) {
                long w = (buf[i] & 0xffL) | (buf[i + 1] & 0xffL) << 8 | (buf[i + 2] & 0xffL) << 16 | (buf[i + 3] & 0xffL) << 24;
                long seg = w >>> 29;
                if (seg != 0 && seg != 4 && seg != 5) continue;
                long p = (w & 0x1fffffffL) | 0x80000000L;
                if (!mem.contains(toAddr(p))) continue;
                pointers.computeIfAbsent(p, k -> new ArrayList<>()).add(toAddr(base + i));
                pointerWords.add(base + i);
                long prev = tables.isEmpty() ? -1 : tableEnd;
                if (prev != base + i || p % 2 != 0) {
                    if (!tables.isEmpty() && tables.get(tables.size() - 1).size() < TABLE_MIN) tables.remove(tables.size() - 1);
                    tables.add(new ArrayList<>());
                }
                if (p % 2 == 0) tables.get(tables.size() - 1).add(toAddr(p));
                tableEnd = base + i + 4;
            }
        }
        if (!tables.isEmpty() && tables.get(tables.size() - 1).size() < TABLE_MIN) tables.remove(tables.size() - 1);
    }

    static final int TABLE_MIN = 6;
    final List<List<Address>> tables = new ArrayList<>();  // runs of >= TABLE_MIN consecutive pointer words
    final Set<Long> pointerWords = new HashSet<>();  // where the pointer words are
    long tableEnd = -1;

    /** Decode the entries of method/handler tables that point at never-decoded bytes. A table is a run of >= 6
     *  consecutive pointer words of which at least two thirds already point at function entries (Spawn's 0c-form
     *  tables at 8c08e2c0-8c08efbc: virtual methods Ghidra never decoded, some 4 bytes apart, `rts; nop`). A switch
     *  table points at case labels inside one function, not at entries, so it never qualifies; a target inside an
     *  existing function is left alone. Data, not an entry: a target outside the span of the table's function
     *  entries, or one whose bytes hold a pointer word (Spawn literal pools mix function pointers with pointers to
     *  the data tables at 8c09dcb4 / 8c09e0a0). The decoded entry becomes a function through reason()
     *  (POINTER@word); TcPrune removes it afterwards if its code cannot run. A candidate right after a block end counts
     *  toward the two thirds (Spawn's 8c09deb8: 11 undecoded `jmp @rN` dispatchers, 8 functions). */
    int decodeTableEntries(Listing listing, FunctionManager fm) throws Exception {
        int n = 0;
        for (List<Address> t : tables) {
            int entries = 0;
            Address lo = null, hi = null;
            for (Address a : t) {
                if (fm.getFunctionAt(a) == null) continue;
                entries++;
                if (lo == null || a.compareTo(lo) < 0) lo = a;
                if (hi == null || a.compareTo(hi) > 0) hi = a;
            }
            if (entries == 0) continue;
            int shaped = 0;
            for (Address a : t)
                if (candidate(listing, fm, a, lo, hi) && followsBlockEnd(a)) shaped++;
            if ((entries + shaped) * 3 < t.size() * 2) continue;
            for (Address a : t) {
                if (!candidate(listing, fm, a, lo, hi)) continue;
                Data d = listing.getDefinedDataContaining(a);
                if (d != null) clearListing(d.getMinAddress(), d.getMaxAddress());
                disassemble(a);
                if (listing.getInstructionAt(a) != null) n++;
                else excluded.add(a);
            }
        }
        return n;
    }

    /** A table target to decode: in the entries' span, not decoded, no pointer word, data or typed data there. */
    boolean candidate(Listing listing, FunctionManager fm, Address a, Address lo, Address hi) {
        return a.compareTo(lo) >= 0 && a.compareTo(hi) <= 0 && !pointerWords.contains(a.getOffset() & ~3L)
                && listing.getInstructionContaining(a) == null && fm.getFunctionContaining(a) == null
                && !excluded.contains(a) && !loadedAsData(a) && untypedOrNone(listing, a);
    }

    /** Back over nop/zero padding, a's first predecessor is the delay slot of rts, rte, jmp @rN, bra or braf (a case
     *  label after a `bra` lies inside its decoded switch function, so it is never a candidate). */
    boolean followsBlockEnd(Address a) {
        ghidra.program.model.mem.Memory mem = currentProgram.getMemory();
        try {
            for (Address d = a.subtract(2); mem.contains(d.subtract(2)); d = d.subtract(2)) {
                int op = mem.getShort(d.subtract(2)) & 0xffff;
                if (op == 0x000b || op == 0x002b || (op & 0xf0ff) == 0x402b || (op & 0xf000) == 0xa000
                        || (op & 0xf0ff) == 0x0023) return true;
                int here = mem.getShort(d) & 0xffff;
                if (here != 0x0009 && here != 0x0000) return false;
            }
        } catch (Exception e) {
            return false;
        }
        return false;
    }

    /** A word pointing to x (any mirror form) that is not x's own function's local computed-jump target, or null. */
    Address pointerTo(Address x) {
        for (Address w : pointers.getOrDefault(x.getOffset(), List.of()))
            if (!localTarget(w, x)) return w;
        return null;
    }

    /** Why `start` (first instruction of the orphan run [start, end]) is a function entry, or null. */
    String reason(Instruction first, Address end) {
        Address start = first.getAddress();
        if (excluded.contains(start) || loadedAsData(start)) return null;
        for (Reference r : getReferencesTo(start)) {
            Address from = r.getFromAddress();
            if (from.compareTo(start) >= 0 && from.compareTo(end) <= 0) continue;
            RefType t = r.getReferenceType();
            if (t.isData() && localTarget(from, start)) continue;
            if (t.isData() || t.isCall() || t.isComputed()) return t.getName() + "@" + from;
        }
        Address w = pointerTo(start);
        if (w != null) return "POINTER@" + w;
        return prologue(first) ? "prologue" : null;
    }

    final Set<Address> excluded = new HashSet<>();  // project.json catalog_overrides "data" / "not_entry"

    /** No defined data at t, or only an untyped "undefinedN" item: Ghidra sized the 4-byte float literal at
     *  8c05f57c as undefined8, which covered the first instruction of 8c05f580. Typed data is never cleared. */
    static boolean untypedOrNone(Listing listing, Address t) {
        Data d = listing.getDefinedDataContaining(t);
        return d == null || ghidra.program.model.data.Undefined.isUndefined(d.getDataType());
    }

    /** Disassemble branch and call targets of function code that are still undefined bytes (TC 8c05f6d6 ends in
     *  `bra 8c05f580`, a 176-byte routine Ghidra never decoded). Only flows out of function bodies count: those
     *  instructions are known code, so their targets are code too. Returns how many targets were disassembled. */
    int disassembleFlowTargets(Listing listing, FunctionManager fm) throws Exception {
        Set<Address> todo = new TreeSet<>();
        for (Instruction in : listing.getInstructions(true)) {
            if (fm.getFunctionContaining(in.getAddress()) == null) continue;
            for (Address t : in.getFlows()) {
                if (t.getOffset() % 2 == 0 && currentProgram.getMemory().getBlock(t) != null
                        && currentProgram.getMemory().getBlock(t).isInitialized()
                        && listing.getInstructionContaining(t) == null && untypedOrNone(listing, t)
                        && !excluded.contains(t) && !loadedAsData(t)) todo.add(t);
            }
        }
        int n = 0;
        for (Address t : todo) {
            if (listing.getInstructionContaining(t) != null) continue;
            Data d = listing.getDefinedDataContaining(t);
            if (d != null) clearListing(d.getMinAddress(), d.getMaxAddress());
            disassemble(t);
            if (listing.getInstructionAt(t) != null) n++;
            else {
                excluded.add(t);  // does not decode: do not retry it every round
                println("TcOrphans: branch target " + t + " does not disassemble");
            }
        }
        return n;
    }

    static final java.util.regex.Pattern CONFLICT = java.util.regex.Pattern.compile(
        "conflicting data at [0-9a-fA-F]+ \\(flow from ([0-9a-fA-F]+)\\)");

    /** Retry flows Ghidra refused only for "conflicting data" that is gone now: Spawn 8c052a40's `jmp @r3` at
     *  8c052a46 met data at 8c052a48 made by a literal pool decoded as `mov.l 0x8c052a48,r15` (TcPools clears such
     *  pools first). The function the flow comes from is re-bodied; the retried error bookmarks are removed. */
    int retryConflicts(Listing listing, FunctionManager fm) throws Exception {
        BookmarkManager bm = currentProgram.getBookmarkManager();
        List<Bookmark> marks = new ArrayList<>();
        bm.getBookmarksIterator(BookmarkType.ERROR).forEachRemaining(marks::add);
        Set<Function> grow = new LinkedHashSet<>();
        int n = 0;
        for (Bookmark b : marks) {
            java.util.regex.Matcher m = CONFLICT.matcher(b.getComment());
            if (!m.find()) continue;
            Address t = b.getAddress();
            Function f = fm.getFunctionContaining(toAddr(Long.parseLong(m.group(1), 16)));
            if (f == null || excluded.contains(t) || loadedAsData(t)) continue;
            if (listing.getInstructionAt(t) == null) {
                if (listing.getInstructionContaining(t) != null || !untypedOrNone(listing, t)) continue;
                Data d = listing.getDefinedDataContaining(t);
                if (d != null) clearListing(d.getMinAddress(), d.getMaxAddress());
                disassemble(t);
                if (listing.getInstructionAt(t) == null) continue;
                n++;
            }
            bm.removeBookmark(b);
            if (fm.getFunctionContaining(t) == null) grow.add(f);
        }
        for (Function f : grow) CreateFunctionCmd.fixupFunctionBody(currentProgram, f, monitor);
        return n;
    }

    static final java.util.regex.Pattern PUSH = java.util.regex.Pattern.compile(
        "(?:mov\\.l|sts\\.l|fmov\\.s|fmov) (r\\d+|pr|fr\\d+|dr\\d+),@-r15");
    static final Set<String> CALLEE_SAVED = Set.of("r8", "r9", "r10", "r11", "r12", "r13", "r14", "pr",
        "fr12", "fr13", "fr14", "fr15", "dr12", "dr14");
    static final java.util.regex.Pattern POP = java.util.regex.Pattern.compile(
        "(?:mov\\.l|lds\\.l|fmov\\.s|fmov) @r15\\+,(r\\d+|pr|fr\\d+|dr\\d+)");

    /** True when g, run from its own entry, restores a register it never saved: its code is the middle of another
     *  function's prologue (TC 8c02a814, Spawn library 8c062d6a). A function that saves what it restores is a real
     *  function the compiler placed after its caller (Spawn 8c020560 restores r13, r14 and runs into 8c0205a4). */
    boolean popsUnpushed(Function g) {
        Set<String> pushed = new HashSet<>(), popped = new HashSet<>();
        for (Instruction in : currentProgram.getListing().getInstructions(g.getBody(), true)) {
            String s = in.toString().toLowerCase().replaceFirst("^_", "");  // Ghidra marks delay slots with "_"
            java.util.regex.Matcher m = PUSH.matcher(s);
            if (m.matches()) pushed.add(m.group(1));
            m = POP.matcher(s);
            if (m.matches()) popped.add(m.group(1));
        }
        popped.removeAll(pushed);
        popped.retainAll(CALLEE_SAVED);  // a float temp pushed as fr0, popped as fr5 is no epilogue (Spawn 8c051fb0)
        return !popped.isEmpty();
    }

    /** Remove functions that nothing refers to, that the previous function's code runs into (fallthrough, not the
     *  delay slot of a terminal branch), and that cannot run alone (popsUnpushed): the code belongs to that
     *  predecessor. TC 8c02a810 (a computed-call entry found above) pushes r13, r12 and runs into 8c02a814, whose
     *  epilogue pops r12, r13; the 8c02a810 packet held only the two pushes. Entries from catalog_overrides are kept. */
    int mergeFallOnlyEntries(Listing listing, FunctionManager fm, PrintWriter pw) throws Exception {
        Map<Address, Function> merge = new LinkedHashMap<>();  // removed entry -> the function that runs into it
        for (Function g : fm.getFunctions(true)) {
            Address x = g.getEntryPoint();
            if (keep.contains(x) || getReferencesTo(x).length > 0 || pointerTo(x) != null) continue;
            Instruction prev = listing.getInstructionBefore(x);
            if (prev == null || !x.equals(prev.getFallThrough())) continue;
            if (prev.isInDelaySlot()) {
                Instruction br = listing.getInstructionBefore(prev.getAddress());
                if (br != null && br.getFallThrough() == null) continue;
            }
            Function f = fm.getFunctionContaining(prev.getAddress());
            if (f == null || f == g || merge.containsKey(f.getEntryPoint())) continue;  // chains: one link per round
            if (!popsUnpushed(g)) continue;
            merge.put(x, f);
        }
        for (Map.Entry<Address, Function> m : merge.entrySet()) {
            fm.removeFunction(m.getKey());
            pw.println("# merged " + m.getKey() + " into " + m.getValue().getEntryPoint() + " (no references; runs into it)");
        }
        for (Function f : new LinkedHashSet<>(merge.values())) CreateFunctionCmd.fixupFunctionBody(currentProgram, f, monitor);
        return merge.size();
    }

    /** Remove dead epilogue fragments: functions nothing refers to or points to, that cannot run alone
     *  (popsUnpushed) and that no code runs into (padding or data before it, or a return or tail jump, or its delay
     *  slot). TC 8c015694 pops PR/r13 it never pushed, right after the previous function's tail jump. Their
     *  instructions stay (dead code, counted as orphans); catalog_overrides "entry" addresses are kept. */
    int dropDeadFragments(Listing listing, FunctionManager fm, PrintWriter pw) throws Exception {
        List<Address> dead = new ArrayList<>();
        for (Function g : fm.getFunctions(true)) {
            Address x = g.getEntryPoint();
            if (keep.contains(x) || getReferencesTo(x).length > 0 || pointerTo(x) != null) continue;
            Instruction prev = listing.getInstructionBefore(x);
            boolean adjacent = prev != null && x.equals(prev.getMaxAddress().next());
            boolean ends = !adjacent || prev.getFallThrough() == null;  // padding or pool before x: Spawn 8c050640
            if (adjacent && prev.isInDelaySlot()) {
                Instruction br = listing.getInstructionBefore(prev.getAddress());
                ends = br != null && br.getFallThrough() == null;
            }
            if (ends && popsUnpushed(g)) dead.add(x);
        }
        for (Address x : dead) {
            fm.removeFunction(x);
            pw.println("# dropped " + x + " (dead epilogue fragment: no references, pops what it never pushed)");
        }
        return dead.size();
    }

    final Set<Address> keep = new HashSet<>();  // catalog_overrides "entry"

    @Override
    public void run() throws Exception {
        Listing listing = currentProgram.getListing();
        FunctionManager fm = currentProgram.getFunctionManager();
        if (getScriptArgs().length > 1) {
            for (String line : java.nio.file.Files.readAllLines(java.nio.file.Paths.get(getScriptArgs()[1]))) {
                String[] p = line.trim().split("\\s+");
                if (p.length == 2 && (p[0].equals("data") || p[0].equals("not_entry")))
                    excluded.add(toAddr(Long.parseLong(p[1], 16)));
                if (p.length == 2 && p[0].equals("entry")) keep.add(toAddr(Long.parseLong(p[1], 16)));
            }
        }
        indexPointers();
        int total = 0, removed = 0, dropped = 0;
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(getScriptArgs()[0]), "UTF-8"))) {
            int retried = retryConflicts(listing, fm);
            if (retried > 0) pw.println("# retried " + retried + " flow(s) refused for conflicting data");
            for (int round = 0; round < MAX_ROUNDS; round++) {
                int dis = disassembleFlowTargets(listing, fm);
                if (dis > 0) pw.println("# round " + round + ": disassembled " + dis + " branch/call target(s)");
                int tab = decodeTableEntries(listing, fm);
                if (tab > 0) pw.println("# round " + round + ": disassembled " + tab + " table entr(ies)");
                dis += tab;
                List<Address> entries = new ArrayList<>();
                List<String> why = new ArrayList<>();
                Instruction first = null, last = null;
                for (Instruction in : listing.getInstructions(true)) {
                    boolean orphan = fm.getFunctionContaining(in.getAddress()) == null;
                    boolean contiguous = last != null && last.getMaxAddress().next() != null
                        && last.getMaxAddress().next().equals(in.getAddress());
                    if (first != null && (!orphan || !contiguous)) {
                        String w = reason(first, last.getMaxAddress());
                        if (w != null) {
                            entries.add(first.getAddress());
                            why.add(w);
                        }
                        first = null;
                    }
                    if (orphan && first == null) first = in;
                    last = in;
                }
                if (first != null) {
                    String w = reason(first, last.getMaxAddress());
                    if (w != null) {
                        entries.add(first.getAddress());
                        why.add(w);
                    }
                }
                int made = 0;
                for (int i = 0; i < entries.size(); i++) {
                    Address e = entries.get(i);
                    if (fm.getFunctionContaining(e) != null) continue;  // an earlier entry this round took it
                    if (createFunction(e, null) != null) {
                        pw.println(e + "\t" + round + "\t" + why.get(i));
                        made++;
                    }
                }
                total += made;
                int merged = mergeFallOnlyEntries(listing, fm, pw);
                removed += merged;
                if (made == 0 && dis == 0 && merged == 0) break;
            }
            dropped = dropDeadFragments(listing, fm, pw);
        }
        println("TcOrphans: " + total + " function(s) created for orphan code, " + removed + " fall-only entr(ies) merged, "
            + dropped + " dead fragment(s) dropped");
    }
}

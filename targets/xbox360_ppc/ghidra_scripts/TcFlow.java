// Register dataflow over p-code (not a script itself): the registers a function reads before it writes them on some
// path from its entry (backward liveness at the entry). A call reads its callee's own inputs (looked through `depth`
// levels: pass-through arguments) and then defines its results r3 and f1 and the volatile registers (r0, r3..r12,
// f0..f13, ctr) the callee may write (clobbers); a call through a register defines every volatile register. A branch
// to another function's entry is a tail call and reads that callee's inputs.
// inputsAt is the function under test's own set for the shared pass-through rule (TcPassThrough): there each call
// reads exactly the registers its call record compares (TcPrepare.calleeRegs).
//
// The register save/restore helpers (TcSetup's __savegprlr, __restfpr, ... families: TcBase.isHelper) read and
// define nothing here: the oracle runs them inline and the C code never calls them. An import thunk (__imp_...)
// reads nothing either: the registers compared at an import call are the ones the caller sets up for it
// (TcPrepare.importRegs), so a register a caller passes through to the kernel untouched is not a parameter.
// A move of a register to itself reads and writes nothing (identityMove).
import ghidra.program.model.address.*;
import ghidra.program.model.block.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.*;
import ghidra.util.task.TaskMonitor;
import java.util.*;

public class TcFlow implements TcPassThrough.Flow {
    final Program prog;
    final TaskMonitor monitor;
    final Map<String, Set<String>> memo = new HashMap<>();
    final Set<Address> busy = new HashSet<>();
    static final Set<String> VOLATILE = new HashSet<>();

    static {
        VOLATILE.add("r0");
        for (int i = 3; i <= 12; i++) VOLATILE.add("r" + i);
        for (int i = 0; i <= 13; i++) VOLATILE.add("f" + i);
        VOLATILE.add("ctr");
    }

    TcFlow(Program prog, TaskMonitor monitor) {
        this.prog = prog;
        this.monitor = monitor;
    }

    // The register name of a register varnode (r0..r31, f0..f31, lr, ctr), or null for anything else. A 64-bit
    // register's low word is a varnode 4 bytes into it (big endian) that no register name may start at, so the
    // lookup is by containment in the base registers' byte ranges.
    final TreeMap<Long, Register> bases = new TreeMap<>();

    String nameOf(Varnode v) {
        if (v == null || !v.isRegister()) return null;
        if (bases.isEmpty()) {
            for (Register r : prog.getLanguage().getRegisters()) {
                // f0..f31 are not base registers in this language: each is the high doubleword of its VSX register
                String n = r.getName().toLowerCase();
                if (r.isBaseRegister() && n.matches("r\\d+|lr|ctr") || n.matches("f\\d+")) {
                    bases.put(r.getAddress().getOffset(), r);
                }
            }
        }
        Map.Entry<Long, Register> e = bases.floorEntry(v.getOffset());
        if (e == null || v.getOffset() + v.getSize() > e.getKey() + e.getValue().getMinimumByteSize()) return null;
        return e.getValue().getName().toLowerCase();
    }

    // A save/restore helper entry: an address inside one of TcSetup's helper families.
    static boolean isSaveHelper(Program prog, Address a) {
        return TcBase.isHelper(prog.getFunctionManager().getFunctionContaining(a));
    }

    // The registers f reads before writing them, callees looked through `depth` levels.
    Set<String> inputs(Function f, int depth) {
        String key = f.getEntryPoint() + "/" + depth;
        Set<String> got = memo.get(key);
        if (got != null) return got;
        if (!busy.add(f.getEntryPoint())) return new TreeSet<>();
        try {
            got = solve(f, depth);
        } catch (Exception e) {
            got = new TreeSet<>();
        } finally {
            busy.remove(f.getEntryPoint());
        }
        memo.put(key, got);
        return got;
    }

    static class Blk {
        Set<String> use = new HashSet<>(), def = new HashSet<>(), in = new HashSet<>();
        List<Blk> succ = new ArrayList<>();
    }

    // TcPassThrough.Flow (not memoised: TcPrepare asks it for the function under test only).
    Map<Address, Set<String>> compared;  // set only while inputsAt solves

    public Set<String> inputsAt(Function f, Map<Address, Set<String>> compared) throws Exception {
        this.compared = compared;
        try {
            return solve(f, 0);
        } finally {
            this.compared = null;
        }
    }

    Set<String> reads(Address target, int depth) {
        return compared != null ? compared.getOrDefault(target, Set.of()) : calleeReads(target, depth);
    }

    Set<String> calleeReads(Address target, int depth) {
        Function g = prog.getFunctionManager().getFunctionAt(target);
        if (g == null || depth <= 0 || TcBase.isHelper(g) || TcBase.isImport(g)) return Set.of();
        return inputs(g, depth - 1);
    }

    Set<String> solve(Function f, int depth) throws Exception {
        AddressSetView body = f.getBody();
        Listing listing = prog.getListing();
        Map<Address, Blk> blocks = new LinkedHashMap<>();
        BasicBlockModel bbm = new BasicBlockModel(prog);
        List<CodeBlock> cbs = new ArrayList<>();
        CodeBlockIterator it = bbm.getCodeBlocksContaining(body, monitor);
        while (it.hasNext()) cbs.add(it.next());
        for (CodeBlock cb : cbs) blocks.put(cb.getMinAddress(), new Blk());
        for (CodeBlock cb : cbs) {
            Blk b = blocks.get(cb.getMinAddress());
            for (Instruction in : listing.getInstructions(cb, true)) {
                long w = word(in);
                if (identityMove(w)) continue;
                Set<String> none = literalZero(w);
                for (PcodeOp op : in.getPcode()) step(b, op, body, depth, none);
            }
            CodeBlockReferenceIterator d = cb.getDestinations(monitor);
            while (d.hasNext()) {
                CodeBlockReference r = d.next();
                if (r.getFlowType().isCall()) continue;
                Blk t = blocks.get(r.getDestinationAddress());
                if (t != null) b.succ.add(t);
            }
        }
        boolean changed = true;
        while (changed) {
            changed = false;
            for (Blk b : blocks.values()) {
                Set<String> in = new HashSet<>(b.use);
                for (Blk s : b.succ) for (String r : s.in) if (!b.def.contains(r)) in.add(r);
                if (!in.equals(b.in)) {
                    b.in = in;
                    changed = true;
                }
            }
        }
        Blk entry = blocks.get(f.getEntryPoint());
        return entry == null ? new TreeSet<>() : new TreeSet<>(entry.in);
    }

    void use(Blk b, String r) {
        if (r != null && !b.def.contains(r)) b.use.add(r);
    }

    // The registers an instruction's p-code reads that the processor does not (none: Set.of()). Ghidra reads r0 for
    // the (RA|0) operand of an AltiVec indexed load or store with RA = 0, where the address is RB alone (lvsl
    // v13,r0,r4 in Too Human 82a455d0), and the registers a trap with TO = 31 compares, though it traps whatever they
    // hold (twi 31,r0,0x16 in 8278fd30). Read, they made r0 an entry input of 82d24cf0 and 8277d310 (refused) once a
    // call before them kept r0.
    static final Set<Integer> VMX_INDEXED = Set.of(6, 7, 38, 39, 71, 103, 135, 167, 199, 231, 359, 487, 519, 551, 647,
        679, 775, 807, 903, 935);  // lvsl lvebx lvsr lvehx lvewx lvx stvebx stvehx stvewx stvx lvxl stvxl lvlx lvrx
                                   // stvlx stvrx lvlxl lvrxl stvlxl stvrxl (opcode 31, X-form)

    // A move of a register to itself, with no record bit: an `or rN,rN,rN` (the Xenon's thread priority and delay hints:
    // 180 in Mega Man 10) or the canonical nop `ori r0,r0,0`. Ghidra's p-code reads and writes the register, but nothing
    // is passed or changed. Counted, they made r0 an entry input (821b9b58, refused), and an `or r8,r8,r8` before an
    // import made r8 that import's argument (822c2cc8), so every caller up the chain passed r8 "through" (821ba7d8
    // compared its entry's junk r8 with the C's 0).
    static boolean identityMove(long word) {
        if (word < 0) return false;
        int x = (int) word, op = x >>> 26, s = x >>> 21 & 31, a = x >>> 16 & 31, b = x >>> 11 & 31;
        if (op >= 24 && op <= 27) return s == a && (x & 0xffff) == 0;  // ori oris xori xoris rA,rA,0
        return op == 31 && (x & 0x7ff) == 444 << 1 && s == a && s == b;  // or rA,rA,rA
    }

    static Set<String> literalZero(long word) {
        if (word < 0) return Set.of();
        int x = (int) word, op = x >>> 26, to = x >>> 21 & 31, ra = x >>> 16 & 31, rb = x >>> 11 & 31;
        int xo = x >>> 1 & 0x3ff;
        if (op == 3 && to == 31) return Set.of("r" + ra);  // twi
        if (op == 31 && xo == 4 && to == 31) return ra == rb ? Set.of("r" + ra) : Set.of("r" + ra, "r" + rb);  // tw
        if (op == 31 && ra == 0 && VMX_INDEXED.contains(xo)) return Set.of("r0");
        return Set.of();
    }

    static long word(Instruction in) {
        try {
            byte[] w = in.getBytes();
            return w.length == 4 ? (w[0] & 0xffL) << 24 | (w[1] & 0xff) << 16 | (w[2] & 0xff) << 8 | w[3] & 0xff : -1;
        } catch (Exception e) {
            return -1;
        }
    }

    void step(Blk b, PcodeOp op, AddressSetView body, int depth, Set<String> none) {
        int code = op.getOpcode();
        if (code == PcodeOp.CALL || code == PcodeOp.CALLIND) {
            if (code == PcodeOp.CALL && isSaveHelper(prog, op.getInput(0).getAddress())) return;
            if (code == PcodeOp.CALL) for (String r : reads(op.getInput(0).getAddress(), depth)) use(b, r);
            else use(b, nameOf(op.getInput(0)));
            b.def.addAll(code == PcodeOp.CALL ? clobbers(op.getInput(0).getAddress()) : VOLATILE);
            b.def.add("r3");  // the results: the oracle's mock sets both (TcOracle)
            b.def.add("f1");
            return;
        }
        if (code == PcodeOp.BRANCH && op.getInput(0).isAddress() && !body.contains(op.getInput(0).getAddress())) {
            Address t = op.getInput(0).getAddress();
            if (isSaveHelper(prog, t)) return;  // the epilogue's b __restgprlr_N
            for (String r : reads(t, depth)) use(b, r);  // a tail call
            return;
        }
        for (int i = 0; i < op.getNumInputs(); i++) {
            String r = nameOf(op.getInput(i));
            if (r != null && !none.contains(r)) use(b, r);
        }
        String out = nameOf(op.getOutput());
        if (out != null) b.def.add(out);
    }

    // ---------- what a call may change ----------
    // The volatile registers a call to `target` may change: the ones its code, and the code it calls or branches to,
    // writes. LTCG keeps a volatile register live across a call whose callee never writes it (Too Human 82976860
    // keeps r9 and f2..f4 across 82817340, which writes only r10, r11, f0 and f13; 824c0a50 keeps r4 across
    // 824c0070), so the oracle's mock zeroes only these (TcOracle.clobber) and context.md lists the ones a callee
    // keeps. Every volatile register where the code cannot tell: an import (the kernel), no function at the target or
    // a branch into the middle of one, a call or jump through a register, an instruction the oracle refuses (VMX, ...)
    // or a word Ghidra did not decode, or a cycle of calls. A save/restore helper family counts as a whole.
    final Map<Address, Set<String>> writes = new HashMap<>();
    final Set<Address> walking = new HashSet<>();

    Set<String> clobbers(Address target) {
        Function g = prog.getFunctionManager().getFunctionContaining(target);
        if (g == null || TcBase.isImport(g) || !g.getEntryPoint().equals(target) && !TcBase.isHelper(g)) return VOLATILE;
        Address key = g.getEntryPoint();
        Set<String> w = writes.get(key);
        if (w != null) return w;
        if (!walking.add(key)) return VOLATILE;
        try {
            w = written(g);
        } catch (Exception e) {
            w = VOLATILE;
        } finally {
            walking.remove(key);
        }
        writes.put(key, w);
        return w;
    }

    Set<String> written(Function g) throws Exception {
        Listing listing = prog.getListing();
        AddressSetView body = g.getBody();
        Set<String> w = new TreeSet<>();
        for (Instruction in : listing.getInstructions(body, true)) {
            if (TcFixups.refuse(in) != null) return VOLATILE;
            List<Address> next = new ArrayList<>(Arrays.asList(in.getFlows()));
            if (in.getFallThrough() != null) next.add(in.getFallThrough());
            for (Address to : next) {
                if (body.contains(to) && listing.getInstructionAt(to) == null) return VOLATILE;  // not decoded
            }
            if (identityMove(word(in))) continue;
            for (PcodeOp op : in.getPcode()) {
                int code = op.getOpcode();
                if (code == PcodeOp.CALLIND || code == PcodeOp.BRANCHIND) return VOLATILE;
                boolean out = (code == PcodeOp.CALL || code == PcodeOp.BRANCH || code == PcodeOp.CBRANCH)
                    && op.getInput(0).isAddress() && !body.contains(op.getInput(0).getAddress());
                if (out) w.addAll(clobbers(op.getInput(0).getAddress()));
                Varnode o = op.getOutput();
                if (o == null || !o.isRegister()) continue;
                nameOf(o);  // fills bases
                long lo = o.getOffset(), hi = lo + o.getSize();  // every base register the output overlaps
                Long from = bases.floorKey(lo);
                for (Map.Entry<Long, Register> e : bases.tailMap(from == null ? lo : from, true).entrySet()) {
                    if (e.getKey() >= hi) break;
                    if (e.getKey() + e.getValue().getMinimumByteSize() > lo) w.add(e.getValue().getName().toLowerCase());
                }
            }
        }
        w.retainAll(VOLATILE);
        return w.size() == VOLATILE.size() ? VOLATILE : Collections.unmodifiableSet(w);
    }
}

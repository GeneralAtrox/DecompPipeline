// Register dataflow over p-code (not a script itself): the registers a function reads before it writes them on some
// path from its entry (backward liveness at the entry). A call reads its callee's own inputs (looked through `depth`
// levels: pass-through arguments) and then defines every volatile register (r0, r3..r12, f0..f13, ctr), as a real
// callee may. A branch to another function's entry is a tail call and reads that callee's inputs.
//
// The register save/restore helpers (TcSetup's __savegprlr, __restfpr, ... families: TcBase.isHelper) read and
// define nothing here: the oracle runs them inline and the C code never calls them. An import thunk (__imp_...)
// reads nothing either: the registers compared at an import call are the ones the caller sets up for it
// (TcPrepare.importRegs), so a register a caller passes through to the kernel untouched is not a parameter.
import ghidra.program.model.address.*;
import ghidra.program.model.block.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.*;
import ghidra.util.task.TaskMonitor;
import java.util.*;

public class TcFlow {
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

    // The base register name of a register varnode (r0..r31, f0..f31, lr, ctr), or null for anything else. A 64-bit
    // register's low word is a varnode 4 bytes into it (big endian) that no register name may start at, so the
    // lookup is by containment in the base registers' byte ranges.
    final TreeMap<Long, Register> bases = new TreeMap<>();

    String nameOf(Varnode v) {
        if (v == null || !v.isRegister()) return null;
        if (bases.isEmpty()) {
            for (Register r : prog.getLanguage().getRegisters()) {
                if (r.isBaseRegister() && r.getName().toLowerCase().matches("r\\d+|f\\d+|lr|ctr")) {
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
                for (PcodeOp op : in.getPcode()) step(b, op, body, depth);
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

    void step(Blk b, PcodeOp op, AddressSetView body, int depth) {
        int code = op.getOpcode();
        if (code == PcodeOp.CALL || code == PcodeOp.CALLIND) {
            if (code == PcodeOp.CALL && isSaveHelper(prog, op.getInput(0).getAddress())) return;
            if (code == PcodeOp.CALL) for (String r : calleeReads(op.getInput(0).getAddress(), depth)) use(b, r);
            else use(b, nameOf(op.getInput(0)));
            b.def.addAll(VOLATILE);
            return;
        }
        if (code == PcodeOp.BRANCH && op.getInput(0).isAddress() && !body.contains(op.getInput(0).getAddress())) {
            Address t = op.getInput(0).getAddress();
            if (isSaveHelper(prog, t)) return;  // the epilogue's b __restgprlr_N
            for (String r : calleeReads(t, depth)) use(b, r);  // a tail call
            return;
        }
        for (int i = 0; i < op.getNumInputs(); i++) use(b, nameOf(op.getInput(i)));
        String out = nameOf(op.getOutput());
        if (out != null) b.def.add(out);
    }
}

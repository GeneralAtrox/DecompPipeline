// Register dataflow over p-code (not a script itself): the registers a function reads before it writes them on some
// path from its entry (backward liveness at the entry). A call reads its callee's own inputs (looked through `depth`
// levels: pass-through arguments) and then defines every volatile register (r0, r3..r12, f0..f13, ctr), as a real
// callee may. A tail branch to another function's entry reads that callee's inputs. Register save/restore helpers
// (TcFlow.isSaveHelper: CodeWarrior's _savegpr/_restfpr family, run inline by the oracle) read and define nothing.
// A variadic function's register save area (varargsSaves: after `stwu r1`, a cr1 branch around stfd f1..f8 and the
// stw r3..r10, all into its own frame) is not a read: a stub like OSReport would otherwise make every caller, and
// every function calling one through pass-through, "read" r3..r10 and f1..f8. The cost: a variadic callee's register
// arguments are compared only where the callee reads them as registers (see README, "Variadic callees").
import ghidra.program.model.address.*;
import ghidra.program.model.block.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.*;
import ghidra.program.model.symbol.*;
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

    // The base register name of a register varnode (r0..r31, f0..f31, lr, ctr), or null for anything else.
    String nameOf(Varnode v) {
        if (v == null || !v.isRegister()) return null;
        Register r = prog.getRegister(v.getAddress(), v.getSize());
        if (r == null) r = prog.getRegister(v.getAddress());
        if (r == null) return null;
        while (r.getBaseRegister() != r && r.getBaseRegister() != null) r = r.getBaseRegister();
        String n = r.getName().toLowerCase();
        return n.matches("r\\d+|f\\d+|lr|ctr") ? n : null;
    }

    // CodeWarrior's register save/restore helpers: from the entry to a blr, only stores and loads of r/f registers
    // based on r11 (and the restgpr_x tails: lwz r0,4(r11); mtlr r0; mr r1,r11). They run inline in the oracle.
    static boolean isSaveHelper(Listing listing, Address a) {
        Instruction in = listing.getInstructionAt(a);
        for (int n = 0; in != null && n < 40; n++, in = in.getNext()) {
            String t = in.toString().toLowerCase();
            String m = in.getMnemonicString().toLowerCase();
            if (m.equals("blr")) return n > 0;
            if (m.matches("stw|stfd|lwz|lfd|psq_st|psq_l") && t.matches(".*\\(r11\\).*")) continue;
            if (t.matches("mtspr lr,r0|mtlr r0|mr r1,r11|or r1,r11,r11")) continue;
            return false;
        }
        return false;
    }

    // The prologue stores of a variadic function's register save area (see the header), or an empty set.
    static Set<Address> varargsSaves(Listing listing, Function f) {
        Set<Address> out = new HashSet<>();
        Instruction in = listing.getInstructionAt(f.getEntryPoint());
        boolean cr1 = false;
        for (int n = 0; in != null && n < 24; n++, in = in.getNext()) {
            String t = in.toString().toLowerCase().replaceAll("\\s+", " ");
            if (n == 0 && t.matches("stwu r1,-?0x[0-9a-f]+\\(r1\\)")) continue;
            if (n == 1 && t.matches("(bne|beq) cr1,.*")) {
                cr1 = true;
                continue;
            }
            if (cr1 && t.matches("(stfd f[1-8]|stw r([3-9]|10)),-?0x[0-9a-f]+\\(r1\\)")) {
                out.add(in.getAddress());
                continue;
            }
            break;
        }
        return cr1 ? out : Set.of();
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
        if (g == null || depth <= 0 || isSaveHelper(prog.getListing(), target)) return Set.of();
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
        Set<Address> vaSave = varargsSaves(listing, f);
        for (CodeBlock cb : cbs) {
            Blk b = blocks.get(cb.getMinAddress());
            for (Instruction in : listing.getInstructions(cb, true)) {
                if (vaSave.contains(in.getAddress())) continue;
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
            if (code == PcodeOp.CALL) for (String r : calleeReads(op.getInput(0).getAddress(), depth)) use(b, r);
            else use(b, nameOf(op.getInput(0)));
            Function g = code == PcodeOp.CALL ? prog.getFunctionManager().getFunctionAt(op.getInput(0).getAddress()) : null;
            if (g == null || !isSaveHelper(prog.getListing(), g.getEntryPoint())) b.def.addAll(VOLATILE);
            return;
        }
        if (code == PcodeOp.BRANCH && op.getInput(0).isAddress() && !body.contains(op.getInput(0).getAddress())) {
            for (String r : calleeReads(op.getInput(0).getAddress(), depth)) use(b, r);  // a tail call
            return;
        }
        for (int i = 0; i < op.getNumInputs(); i++) use(b, nameOf(op.getInput(i)));
        String out = nameOf(op.getOutput());
        if (out != null) b.def.add(out);
    }
}

// The T flag handed back across a call. Not a script itself; TcPrepare calls prepare() and TcLive.of calls outs() per
// callee, TcVectors writes header() and exp(). Nearest existing: TcOut (r4..r7 only; TcStrict.walk, which it uses,
// masks r1..r7 and must stay byte-identical with Saturn's copy, so T gets its own walk here).
//
// On the console T after a call is whatever the callee left: Power Stone 2's drawer Lib_RenderModelA (8c188420) reads
// T right after its jsr to 8c1967c0 and 8c197240. A mocked callee left T as it was, so C that kept the caller's own T
// matched the judge and drew the wrong branch in the port. Now:
//   caller side: a caller that reads T after a call to a callee that may change T (changes(): a T write in its extent
//   or a call to such a function, followed bottom-up; an unresolved call counts), before writing it on some path
//   (walk from the return address), gets T from the mocked callee: (draw >> 8) & 1 of the call's draw (line A), so
//   both values occur. TcLive entry 11, header line "K target 16 0". meta "t_out": the callees.
//   callee side (exported): a function that may change T and that some caller reads T after calling has T compared
//   at exit (vectors.txt header line "t", expected.txt "RT v"), and counts T as read at its returns. meta "t_export".
//   A caller reaching its own return (or a tail call) with T unwritten hands T on: read there only when that caller
//   is itself exported (exportedFn, followed up the callers).
// The C runners hand T over through sh4_t / sh4_set_t (the translator keeps t as state, as r8..r14). Without either
// side every harness byte stays as before.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.*;
import ghidra.program.model.symbol.Reference;
import java.util.*;

final class TcTflag {
    static final int MAX_VISITS = 64, MAX_DEPTH = 32;  // walk: per instruction (then T counts as read); changes()
    static boolean exported, exitLive;  // the function's own T is read by a caller; walk: T read at a return
    static final Set<Long> out = new TreeSet<>();  // callees whose T the function reads after calling (meta "t_out")
    static final Map<Address, Boolean> memo = new HashMap<>(), expMemo = new HashMap<>();

    // TcPrepare, before TcLive.of: whether some caller reads the function's T after calling it (exported).
    static void prepare(TcBase s, boolean on) {
        exported = exitLive = false;
        out.clear();
        memo.clear();
        expMemo.clear();
        if (!on) return;
        Program prog = s.getCurrentProgram();
        if (changes(prog, s.fn, 0)) exported = exitLive = exportedFn(prog, s.fn, 0);  // its own returns read T
    }

    // Whether some caller reads f's T after calling it: on a path from the return address before writing it, or at
    // its own return when that caller is exported; a tail call hands it on as a return does. A cycle or a chain past
    // MAX_DEPTH counts.
    static boolean exportedFn(Program prog, Function f, int depth) {
        Boolean m = expMemo.get(f.getEntryPoint());
        if (m != null) return m;
        if (depth > MAX_DEPTH) return true;
        expMemo.put(f.getEntryPoint(), true);
        boolean e = false;
        FunctionManager fm = prog.getFunctionManager();
        for (Reference r : prog.getReferenceManager().getReferencesTo(f.getEntryPoint())) {
            Function c = r.getReferenceType().isCall() ? fm.getFunctionContaining(r.getFromAddress()) : null;
            Instruction at = c == null ? null : prog.getListing().getInstructionAt(r.getFromAddress());
            if (at == null) continue;
            e = at.getFallThrough() == null ? exportedFn(prog, c, depth + 1)
                : walk(prog, TcTail.extent(prog, c), r.getFromAddress().add(4), c, depth + 1);
            if (e) break;
        }
        expMemo.put(f.getEntryPoint(), e);
        return e;
    }

    // TcLive.of, per callee g of the function: e[11] (UNKNOWN: drawn) when the function reads T after a call to g and
    // g may change T. Whether it was set.
    static boolean outs(TcBase s, Function g, long[][] e) {
        Program prog = s.getCurrentProgram();
        AddressSetView ext = TcTail.extent(prog, s.fn);
        boolean read = false;
        for (Instruction in : prog.getListing().getInstructions(ext, true)) {
            for (Reference r : in.getReferencesFrom()) {
                if (!read && r.getReferenceType().isCall() && r.getToAddress().equals(g.getEntryPoint())) {
                    read = in.getFallThrough() == null ? exitLive : walk(prog, ext, in.getAddress().add(4), null, 0);
                }
            }
        }
        if (!read || !changes(prog, g, 0)) return false;
        e[11] = TcLive.UNKNOWN;
        out.add(g.getEntryPoint().getOffset());
        return true;
    }

    // TcLive.apply at a mocked call to a callee with entry 11: T = bit 8 of the call's draw. Always true.
    static boolean apply(TcOracle o, EmulatorHelper emu, long draw) {
        emu.writeRegister("T", o.u(draw >> 8 & 1));
        return true;
    }

    // Whether f may return with T other than its entry T: a T write in its extent, or a call to such a function (an
    // unresolved one, a cycle or a chain past MAX_DEPTH counts).
    static boolean changes(Program prog, Function f, int depth) {
        Boolean m = memo.get(f.getEntryPoint());
        if (m != null) return m;
        if (depth > MAX_DEPTH) return true;
        memo.put(f.getEntryPoint(), true);  // a cycle: counts
        boolean ch = false;
        FunctionManager fm = prog.getFunctionManager();
        for (Instruction in : prog.getListing().getInstructions(TcTail.extent(prog, f), true)) {
            if ((use(prog, in) & 2) != 0) { ch = true; break; }
            if (!in.getFlowType().isCall()) continue;
            boolean known = false;  // a jsr @rN resolves through its call reference (the pool constant)
            for (Reference r : in.getReferencesFrom()) {
                Function g = r.getReferenceType().isCall() ? fm.getFunctionAt(r.getToAddress()) : null;
                if (g == null) continue;
                known = true;
                ch |= changes(prog, g, depth + 1);
            }
            if (ch |= !known) break;
        }
        memo.put(f.getEntryPoint(), ch);
        return ch;
    }

    // Whether T is read on some path from start inside body before it is written; a return or tail call reads it when
    // owner is exported (exportedFn), or for the function itself (owner null) when exitLive. True when the walk
    // cannot finish. Calls are passed over (a callee that keeps T leaves the one before it).
    static boolean walk(Program prog, AddressSetView body, Address start, Function owner, int depth) {
        Listing listing = prog.getListing();
        Map<Address, Boolean> in = new HashMap<>();  // T written on every path there
        Map<Address, Integer> visits = new HashMap<>();
        in.put(start, false);
        Deque<Address> work = new ArrayDeque<>(List.of(start));
        while (!work.isEmpty()) {
            Address a = work.pop();
            Instruction ins = listing.getInstructionAt(a);
            if (ins == null || visits.merge(a, 1, Integer::sum) > MAX_VISITS) return true;
            boolean w = in.get(a), ends = false;
            for (PcodeOp op : ins.getPcode()) {  // a delayed branch's own p-code holds its slot's
                for (Varnode v : op.getInputs()) if (!w && isT(prog, v)) return true;
                w |= isT(prog, op.getOutput());
                ends |= op.getOpcode() == PcodeOp.RETURN;
            }
            if (ends || ins.getFlowType().isCall() && ins.getFallThrough() == null) {  // a return, or a tail call
                if (!w && (owner == null ? exitLive : exportedFn(prog, owner, depth))) return true;
                continue;
            }
            List<Address> next = new ArrayList<>();
            if (!ins.getFlowType().isCall()) {
                for (Address t : ins.getFlows()) if (body.contains(t)) next.add(t);
            }
            Address ft = ins.getFallThrough();
            if (ft != null && body.contains(ft)) next.add(ft);
            for (Address n : next) {
                Boolean old = in.get(n);
                if (old != null && (!old || w)) continue;  // already walked with T unwritten, or as written
                in.put(n, old == null ? w : old && w);
                work.push(n);
            }
        }
        return false;
    }

    // Bit 1: the instruction (its slot's p-code included) reads T before writing it; bit 2: it writes T.
    static int use(Program prog, Instruction ins) {
        int u = 0;
        for (PcodeOp op : ins.getPcode()) {
            for (Varnode v : op.getInputs()) if ((u & 2) == 0 && isT(prog, v)) u |= 1;
            if (isT(prog, op.getOutput())) u |= 2;
        }
        return u;
    }

    static boolean isT(Program prog, Varnode v) {
        if (v == null || !v.isRegister()) return false;
        Register r = prog.getRegister(v.getAddress(), v.getSize());
        if (r == null) r = prog.getRegister(v.getAddress());
        return r != null && r.getName().equals("T");
    }

    // vectors.txt header line "t" (T compared at exit), only when exported.
    static String header() {
        return exported ? "t\n" : "";
    }

    // expected.txt "RT v" at exit (TcExit.collect: Result.ro[4]), only when exported.
    static String exp(TcOracle.Result r) {
        return !exported || r.halted ? "" : "\nRT " + (r.ro[4] & 1);
    }

    // meta.json: "t_out" ["callee", ...] (T read after calling them), "t_export" (its own T read by a caller).
    static String meta() {
        StringBuilder b = new StringBuilder();
        for (long c : out) b.append(b.length() > 0 ? "," : "").append(String.format("\"%08x\"", c));
        return (out.isEmpty() ? "" : ",\n  \"t_out\": [" + b + "]") + (exported ? ",\n  \"t_export\": true" : "");
    }
}

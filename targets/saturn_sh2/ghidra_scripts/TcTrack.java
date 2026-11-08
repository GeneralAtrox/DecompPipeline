// The argument registers of calls through function pointers. Not a script itself; TcPrepare sets `on` for every
// prepare (null: the body has no computed call or jump, and nothing changes).
// A call to a target the body's references do not name (not in meta callees, whose argument registers come from the
// callee's prototype) is a call through a function pointer, or a direct call or branch to code no listed callee starts,
// or a run on past the body's end (unlisted). The argument registers the run set itself since its
// entry or its last call (its own parameters count as set at entry; a callee leaves them undefined), r4..r7 in order
// up to the first it did not set, are that call's arguments: expected.txt "CA reg..." after its CALL line (call).
// The judge compares them as a listed callee's (core/compare.py). Stack words are not compared: without a prototype
// the words at @(0,r15).. are as often the caller's locals as arguments. (The SH-2 has no FPU: no float registers,
// and every memory word compares bit for bit.)
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.symbol.FlowType;
import java.util.*;

final class TcTrack {
    static TcTrack on;  // the function being prepared has a computed call or jump; or null
    static final Set<String> ARGS = new HashSet<>(Arrays.asList("r4", "r5", "r6", "r7"));

    final Set<Long> callees;   // the listed callees' entries (TcPrepare.calledBy)
    final Set<String> params;  // the function's own argument registers, set at entry
    final Map<Long, Set<String>> written = new HashMap<>();
    Run cur;

    // A run's argument registers set since its entry or last call.
    static final class Run {
        final Set<String> defined = new HashSet<>();
    }

    TcTrack(Set<Long> callees, Set<String> params) {
        this.callees = callees;
        this.params = params;
    }

    // `on` for the body (null when it has no computed call or jump).
    static TcTrack of(Listing listing, AddressSetView body, List<Function> callees, TcBase.Proto proto) {
        Set<Long> c = new HashSet<>();
        for (Function g : callees) c.add(g.getEntryPoint().getOffset());
        boolean calls = false;
        for (Instruction in : listing.getInstructions(body, true)) {
            FlowType f = in.getFlowType();
            calls |= f.isComputed() && (f.isCall() || f.isJump()) || unlisted(in, f, body, c);
        }
        if (!calls) return null;
        Set<String> p = new HashSet<>();
        for (TcBase.Param q : proto.params) p.add(q.storage.toLowerCase());
        p.retainAll(ARGS);
        return new TcTrack(c, p);
    }

    // A flow out of the body to an address no listed callee starts. The oracle mocks it as a call (TcOracle) whose target
    // has no record, so the CA line holds its arguments (call): a direct call or branch to code no prepared function
    // starts, or a run on past the body's end. Before 2026-11-07 only a computed call or jump turned the lines on, and
    // such a call had no argument compared (core/unchecked_args.py; target.json stamp scope unlisted_call_args).
    static boolean unlisted(Instruction in, FlowType f, AddressSetView body, Set<Long> c) {
        boolean out = false;
        if ((f.isJump() || f.isCall()) && !f.isComputed()) {
            for (Address to : in.getFlows()) out |= !body.contains(to) && !c.contains(to.getOffset());
        }
        Address next = in.isInDelaySlot() ? null : in.getFallThrough();
        return out || next != null && !body.contains(next) && !c.contains(next.getOffset());
    }

    // TcOracle.runOracle: a new run's record, or null.
    static Run start() {
        if (on == null) return null;
        on.cur = new Run();
        on.cur.defined.addAll(on.params);
        return on.cur;
    }

    // The argument registers the instructions of the step at pca write (their p-code outputs).
    Set<String> written(TcLoads steps, Address pca) {
        Set<String> w = written.get(pca.getOffset());
        if (w == null) {
            w = new HashSet<>();
            for (Instruction in : steps.stepInstructions(pca)) {
                for (PcodeOp op : in.getPcode()) {
                    Varnode out = op.getOutput();
                    if (out == null || !out.isRegister()) continue;
                    Register reg = in.getProgram().getRegister(out.getAddress(), out.getSize());
                    if (reg != null) w.add(reg.getName().toLowerCase());
                }
            }
            w.retainAll(ARGS);
            written.put(pca.getOffset(), w);
        }
        return w;
    }

    // TcOracle, after the step at pca in the body.
    static void step(TcLoads steps, Address pca) {
        if (on != null) on.cur.defined.addAll(on.written(steps, pca));
    }

    // TcOracle, at a mocked call to target: "\nCA reg..." when target is no listed callee, else "". The registers are
    // undefined after it (the callee's to change: SH-2 calling convention).
    static String call(long target) {
        if (on == null) return "";
        Set<String> d = on.cur.defined;
        StringBuilder s = new StringBuilder();
        if (!on.callees.contains(target)) {
            s.append("\nCA");
            for (int i = 4; i < 8 && d.contains("r" + i); i++) s.append(" r").append(i);
        }
        d.clear();
        return s.toString();
    }
}

// Callee summaries for parameter inference (target.json "param_summaries"; TcSig uses them when TcFacts.paramSummaries).
// Per function, bottom-up over the call graph (calls, tail calls and running on into the next function without a
// branch, SP 8c0587b0: TcFlow's view of them), from TcFlow's dataflow
// under the ABI with the callees' own summaries:
//   reads  - registers some path reads before writing, its callees' reads included where the register is not written
//            on every path to the call (the callee's real inputs, transitively; callee-saved pushes are no reads)
//   defs   - registers written on every path to a return (a call defines its callee's defs, plus r0 and fr0)
//   writes - registers it may leave changed (its callees' writes included), saved-and-restored ones excepted; a pop
//            it did not push itself (an epilogue fragment restoring r8..r14) is a change
// A cycle of mutually recursive functions (a strongly connected component) is solved together, conservatively: a call
// inside it defines only r0 and fr0, and reads and writes grow to a fixpoint from nothing (both only grow).
// A caller's harness then compares at a call to g the registers g reads (TcSig.callMask), not those of g's prototype:
// Ghidra's guesses and a depth limit no longer decide them, so a parameter g never reads does not cascade up the call
// tree, and runtime state (r8..r14, fr12..fr15) read deep below is not lost.
// A mode-switching function (fschg/frchg/fpchg: TcFlow sees one float bank and one transfer size, so its float masks
// are unsafe) passes on no float reads of its own dataflow: its float inputs come from protoMask (TcSig: its
// prototype, as without summaries), applied once its component is solved; it defines no float register and may change
// the caller-saved ones (fr0..fr11, the ABI). An opaque function (it branches or runs on out of its extent into code
// no function starts at, OVL 8c5a00cc `bra 0x8c5a00d0`: what it reads there is unknown) reads at least its prototype's
// registers (protoMask, as without summaries), defines nothing and may change the caller-saved ones. Not a script.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.util.task.TaskMonitor;
import java.util.*;
import java.util.function.ToIntFunction;

public class TcSum {
    static final int MAX_ROUNDS = 32; // fixpoint rounds for one cycle; then its members keep the ABI's view

    static class S {
        int reads, defs, writes;
        boolean modes; // fschg/frchg/fpchg: its float masks are unsafe (TcSig falls back to the prototype's)
        boolean opaque; // leaves its extent into code no function starts at (TcSig falls back to the prototype's)
        boolean abi; // its cycle reached no fixpoint: the ABI's view (TcSig.ownDefs)
    }

    final Program prog;
    final TaskMonitor monitor;
    ToIntFunction<Function> protoMask; // the registers of a function's prototype (null: none), see modes and opaque
    final Map<Address, S> done = new HashMap<>();
    // Tarjan's algorithm (recursive; call chains are shallow) over the functions reached from a query
    final Map<Address, Integer> index = new HashMap<>(), low = new HashMap<>();
    final Deque<Function> stack = new ArrayDeque<>();
    final Set<Address> onStack = new HashSet<>();
    final Map<Address, S> trial = new HashMap<>(); // the current cycle's iterate
    int counter;

    TcSum(Program prog, TaskMonitor monitor) {
        this.prog = prog;
        this.monitor = monitor;
    }

    S of(Function g) {
        S s = done.get(g.getEntryPoint());
        if (s != null) return s;
        visit(g);
        return done.get(g.getEntryPoint());
    }

    List<Function> callees(Function f) {
        List<Function> out = new ArrayList<>();
        Set<Address> seen = new HashSet<>();
        try {
            for (Function g : f.getCalledFunctions(monitor)) if (seen.add(g.getEntryPoint())) out.add(g);
        } catch (Exception e) {
            throw new IllegalStateException("callees of " + f.getEntryPoint(), e);
        }
        AddressSetView body = TcTail.extent(prog, f);
        List<Address> next = new ArrayList<>(TcTail.tailCallees(prog, f, body));
        next.addAll(TcTail.jumpCallees(prog, body)); // `bra` into another function (TcFlow sees it with summaries)
        for (Instruction in : prog.getListing().getInstructions(body, true)) { // running on into the next function
            Address a = TcFlow.fallOut(in, body);
            if (a != null) next.add(a);
        }
        for (Address a : next) {
            Function g = prog.getFunctionManager().getFunctionAt(a);
            if (g != null && seen.add(a)) out.add(g);
        }
        return out;
    }

    void visit(Function v) {
        Address va = v.getEntryPoint();
        index.put(va, counter);
        low.put(va, counter);
        counter++;
        stack.push(v);
        onStack.add(va);
        for (Function w : callees(v)) {
            Address wa = w.getEntryPoint();
            if (done.containsKey(wa)) continue;
            if (!index.containsKey(wa)) {
                visit(w);
                low.put(va, Math.min(low.get(va), low.get(wa)));
            } else if (onStack.contains(wa)) {
                low.put(va, Math.min(low.get(va), index.get(wa)));
            }
        }
        if (!low.get(va).equals(index.get(va))) return;
        List<Function> scc = new ArrayList<>();
        Function w;
        do {
            w = stack.pop();
            onStack.remove(w.getEntryPoint());
            scc.add(w);
        } while (!w.getEntryPoint().equals(va));
        solve(scc);
    }

    // One component: iterate its members' summaries to a fixpoint (a single non-recursive function: one round).
    void solve(List<Function> scc) {
        Set<Address> members = new HashSet<>();
        for (Function f : scc) {
            members.add(f.getEntryPoint());
            trial.put(f.getEntryPoint(), new S());
        }
        boolean recursive = scc.size() > 1;
        if (!recursive) {
            for (Function g : callees(scc.get(0))) recursive |= g.getEntryPoint().equals(scc.get(0).getEntryPoint());
        }
        boolean stable = false;
        for (int round = 0; round < MAX_ROUNDS && !stable; round++) {
            stable = true;
            for (Function f : scc) {
                S next = flow(f, members);
                S cur = trial.get(f.getEntryPoint());
                if (next.reads != cur.reads || next.writes != cur.writes || next.defs != cur.defs || next.modes != cur.modes) {
                    stable = false;
                    trial.put(f.getEntryPoint(), next);
                }
            }
            if (!recursive) { // one round is exact: no callee depends on this function's own iterate
                stable = true;
                break;
            }
        }
        for (Function f : scc) {
            S s = trial.remove(f.getEntryPoint());
            if (!stable) { // no fixpoint: callers keep the ABI's view of these callees (every argument compared)
                s.reads |= TcFlow.INTS | TcFlow.LOW | 0xfff0 | TcFlow.ISTATE;
                s.writes |= TcFlow.CLOBBER;
                s.defs = 0;
                s.abi = true;
            }
            done.put(f.getEntryPoint(), s);
        }
        for (Function f : scc) { // after done: the hook may query f's callees (all solved by now) through TcSig
            S s = done.get(f.getEntryPoint());
            if (protoMask == null || !(s.modes || s.opaque)) continue;
            s.reads |= protoMask.applyAsInt(f) & (s.opaque ? TcFlow.ALL : 0xffff);
        }
    }

    S flow(Function f, Set<Address> members) {
        TcFlow fl = new TcFlow(prog, monitor);
        fl.passAbi = true;
        fl.restoreIsWrite = true;
        fl.calleeDefs = g -> members.contains(g.getEntryPoint()) ? 0 : get(g).defs;
        fl.calleeWrites = g -> get(g).writes;
        int[] r = fl.entryFlow(f, g -> get(g).reads);
        S s = new S();
        s.reads = r[0] | r[1] | r[3];
        s.writes = r[5];
        s.defs = fl.exitDefs;
        s.modes = r[2] != 0;
        for (Map.Entry<Address, Integer> e : fl.fallOuts.entrySet()) { // runs on into g: a tail call to it
            Function g = prog.getFunctionManager().getFunctionAt(e.getKey());
            if (g == null) {
                s.opaque = true;
                continue;
            }
            S t = get(g);
            s.reads |= t.reads & ~e.getValue();
            s.writes |= t.writes;
        }
        s.opaque |= escapes(f);
        if (s.opaque) { // what it reads, defines and changes past its extent is unknown
            s.writes |= TcFlow.CLOBBER;
            s.defs = 0;
        }
        if (s.modes) { // unsafe float masks: no float inputs here (modesFloat adds them), none defined, ABI changes
            s.reads &= ~0xffff;
            s.defs &= ~0xffff;
            s.writes = (s.writes & ~0xffff) | (TcFlow.CLOBBER & 0xffff);
        }
        return s;
    }

    // Whether f branches (no call) out of its extent to an address no function starts at.
    boolean escapes(Function f) {
        AddressSetView body = TcTail.extent(prog, f);
        FunctionManager fm = prog.getFunctionManager();
        for (Instruction in : prog.getListing().getInstructions(body, true)) {
            for (ghidra.program.model.symbol.Reference r : in.getReferencesFrom()) {
                Address to = r.getToAddress();
                if (r.getReferenceType().isFlow() && !r.getReferenceType().isCall() && !body.contains(to)
                        && fm.getFunctionAt(to) == null) return true;
            }
        }
        return false;
    }

    // A callee's summary: final, or the current cycle's iterate (a callee outside both: solved first by visit()).
    S get(Function g) {
        S s = done.get(g.getEntryPoint());
        if (s == null) s = trial.get(g.getEntryPoint());
        if (s == null) { // reached through a call TcFlow sees but callees() did not list: solve it on its own
            if (!index.containsKey(g.getEntryPoint())) visit(g);
            s = done.get(g.getEntryPoint());
        }
        if (s == null) { // still open (a cycle back into the current search): the ABI's view, conservatively
            s = new S();
            s.reads = TcFlow.INTS | TcFlow.LOW | 0xfff0 | TcFlow.ISTATE;
            s.writes = TcFlow.CLOBBER;
            s.abi = true;
        }
        return s;
    }
}

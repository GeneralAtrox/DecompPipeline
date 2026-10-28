// The C signature a function is tested with: Ghidra's prototype, repaired from the register dataflow (TcFlow) under
// the target's calling convention (target.json "abi", TcFacts). Not a script itself; TcBase.prototypeOf uses it.
//
// Inputs: every argument-class register (abi int_args, float_args and extra_inputs_allowed: r4..r7, fr4..fr11 and
// r0..r3 on the SH-4) that some path reads before writing, or passes through to a callee unwritten (also across an
// earlier call: the oracle's mocked callees leave such registers as they were, TcFlow), is a parameter,
// whatever Ghidra's prototype says (it misses float inputs it cannot type, r0..r3 inputs of SHC runtime helpers, and
// arguments only forwarded to a callee). The callee-saved registers are never parameters: fr12..fr15 and r8..r14 read
// at entry or left changed are runtime state (Proto.frIn/frState, giIn/giState), set at entry and compared at exit,
// and compared at calls to a callee that reads them. Any other register read at entry cannot be passed: the function
// under test is refused, except fr0..fr3 alone with TcFacts.fpuState, which makes the whole FPU state (fpuProto).
// Results: Ghidra's return storage is checked against what the code leaves changed: a callee-saved or restored
// register is not a result (Ghidra's "return in r11" of `mov r11,r0; ... mov.l @r15+,r11`): the ABI register r0 is;
// a 64-bit result is r0:r1 only when r1 is left changed; another changed caller-saved register is a nonstandard
// result register (Proto.retReg).
// The game is built single-precision (no doubles), but Ghidra's SH-4 model often types two single floats as one
// 8-byte pair: a parameter in drN, a result in dr0 (or fr1). Then the float parameters are exactly the registers
// the function reads (singlesProto); Ghidra's own float parameters are dropped, as are those the code never reads
// or passes on (readFloatsOnly: a stored signature can name one, 8c021474's integer `param_1` in fr5).
// Mode-switching functions (fschg, frchg, fpchg) get no float repairs and no float state: their float masks are unsafe.
// With TcFacts.fpuState the function under test instead takes the whole FPU as state (fpuProto).
// With callee summaries (TcFacts.paramSummaries, target.json "param_summaries"; TcSum) a callee's compared registers
// are the registers it really reads, transitively (callMask), at any depth, instead of its depth-limited prototype;
// and a pop the function did not push itself is a change (an epilogue fragment's r8..r14 are state).
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.listing.*;
import java.util.*;
import java.util.function.ToIntFunction;

public class TcSig {
    // Callee prototypes are looked through this many levels deep for pass-through arguments. TcPrepare uses depth
    // PASS_DEPTH - 1 for the callees it compares: the same prototypes the caller's analysis used.
    static final int PASS_DEPTH = 3;

    final TcBase s;
    final DecompInterface di;
    final TcFacts facts;
    final Map<String, TcBase.Proto> memo = new HashMap<>();
    TcSum sum; // callee summaries, when facts.paramSummaries

    TcSig(TcBase s, DecompInterface di) {
        this.s = s;
        this.di = di;
        this.facts = s.facts;
    }

    // Is this parameter storage one a caller sets for it (compared at each call)?
    boolean isArg(String storage) {
        int m = TcFlow.maskOf(storage);
        return m != 0 && Integer.bitCount(m) == 1 && (m & facts.args()) != 0;
    }

    // Callers are compared on these registers at the call: the argument registers the prototype takes and the
    // callee-saved state it reads at entry.
    int argMask(TcBase.Proto p) {
        int m = p.frIn | p.giIn;
        for (TcBase.Param q : p.params) if (isArg(q.storage)) m |= TcFlow.maskOf(q.storage);
        return m;
    }

    // With callee summaries: the registers compared at a call to g (TcPrepare.calleeRegs) and passed on by its callers
    // (derive): those g reads (argument registers and runtime state). A mode-switching g's float part comes from its
    // prototype as before (its float masks are unsafe).
    int callMask(Function g) {
        TcSum.S x = summary(g);
        int m = x.reads & (facts.args() | TcFlow.STATE | TcFlow.ISTATE);
        if (x.modes) m = (m & ~0xffff) | (argMask(of(g, PASS_DEPTH - 1)) & 0xffff);
        return m;
    }

    TcSum.S summary(Function g) {
        if (sum == null) {
            sum = new TcSum(s.getCurrentProgram(), s.getMonitor());
            sum.protoMask = h -> argMask(of(h, PASS_DEPTH - 1));
        }
        return sum.of(g);
    }

    // With callee summaries, what a call to g defines for the caller's own reads: the argument registers r4..r7 and
    // fr4..fr11 only where g writes them on every path (hand-written chains read an argument register a callee
    // preserved: Toy Commander 8c061aac passes its entry r6 to 8c061ad4 after two calls, so r6 is a parameter;
    // 8c03a2f6 reads its entry fr8 after calling 8c061394, which writes only fr0..fr3 and fr7: the oracle's mock keeps
    // fr8 too, so fr8 is a parameter, as TcSum's reads already say); the rest as the ABI (every caller-saved
    // register: r0..r3 and fr0..fr3 are no arguments, and a read of fr3 after a call can be Ghidra's PR=0 decode of a
    // double op, GEND 8c565968), the floats too after a mode-switching g (its float masks are unsafe), and all of it
    // for a callee whose summary is unknown past its extent or not a fixpoint.
    int ownDefs(Function g) {
        TcSum.S x = summary(g);
        if (x.opaque || x.abi) return TcFlow.CLOBBER;
        int own = TcFlow.INTS | (x.modes ? 0 : facts.args() & 0xffff); // abi float_args: fr4..fr11
        return (x.defs & own) | (TcFlow.CLOBBER & ~own);
    }

    TcBase.Proto of(Function f, int depth) {
        String key = f.getEntryPoint() + "/" + depth;
        TcBase.Proto p = memo.get(key);
        if (p != null) return p;
        TcBase.Proto base = s.ghidraProto(di, f);
        boolean floats = false;
        for (TcBase.Param q : base.params) floats |= q.storage.matches("fr\\d+");
        boolean plainRet = base.retStorage == null && !base.retWide;
        if (depth < PASS_DEPTH && ((base.unsupported != null && !noDecompile(base))
            || (depth == 0 && !base.pairs && !floats && plainRet && !usesState(f)))) {
            p = base;
        } else {
            p = derive(f, base, depth);
        }
        memo.put(key, p);
        return p;
    }

    TcBase.Proto derive(Function f, TcBase.Proto base, int depth) {
        ToIntFunction<Function> args = facts.paramSummaries ? this::callMask
            : depth == 0 ? g -> 0 : g -> argMask(of(g, depth - 1));
        TcFlow fl = new TcFlow(s.getCurrentProgram(), s.getMonitor());
        fl.restoreIsWrite = facts.paramSummaries;
        if (facts.paramSummaries) fl.calleeDefs = this::ownDefs;
        int[] flow = fl.entryFlow(f, args);
        boolean modes = flow[2] != 0 || TcFpscr.Sz.wide();  // a mode switch, or SZ=1 at entry (TcFpscr.Sz)
        int read = flow[0] | flow[1] | flow[3];
        if (modes) read &= ~0xffff; // float masks are unsafe across mode switches
        int state = TcFlow.STATE | TcFlow.ISTATE;
        int foreign = read & ~facts.args() & ~state;
        int frIn = read & TcFlow.STATE;
        int frState = modes ? 0 : frIn | flow[4];
        int giIn = read & TcFlow.ISTATE;
        int giState = giIn | (flow[5] & TcFlow.ISTATE);
        TcBase.Proto p;
        // Only fr0..fr3 read at entry, with TcFacts.fpuState: the whole FPU is state (fpuProto), as for a mode switch.
        // Spawn 8c021748 jumps into its own loop at 8c0217b6 when the first record is not -1, storing the fr0..fr2 and
        // fr4..fr6 its caller left (the previous transform).
        boolean floatEntry = facts.fpuState && foreign != 0 && (foreign & ~0xffff) == 0;
        if (depth == PASS_DEPTH && foreign != 0 && !floatEntry) {
            p = TcBase.copyOf(base);
            p.unsupported = "reads " + names(foreign) + " at entry (not argument registers)";
            return p;
        }
        int regs = read & facts.args();
        if ((modes || floatEntry) && facts.fpuState && depth == PASS_DEPTH) {
            p = fpuProto(f, base, regs & ~0xffff, flow[5]);
            if (p.unsupported != null) return p;
            p.giIn = giIn;
            p.giState = giState;
            return p;
        }
        if (noDecompile(base)) {
            p = fromFlow(f, regs, flow[5]);
        } else if (base.unsupported != null) {
            return base;
        } else {
            p = base.pairs ? singlesProto(base, flow, regs) : withRegisters(readFloatsOnly(base, flow), regs);
            if (p.unsupported != null) return p;
            resolveReturn(p, base, flow[5]);
        }
        p.frIn = frIn;
        p.frState = frState;
        p.giIn = giIn;
        p.giState = giState;
        return p;
    }

    static boolean noDecompile(TcBase.Proto base) {
        return base.unsupported != null && base.unsupported.startsWith("decompile failed");
    }

    // The decompiler gave up (an asm-only packet): the prototype is the dataflow's alone. Parameters are the argument
    // registers read (u32/f32 by register class) and the stack words read (TcStack); the result is r0 if the code
    // leaves it changed, else fr0 if it leaves that changed, else none.
    TcBase.Proto fromFlow(Function f, int regs, int changed) {
        TcBase.Proto p = new TcBase.Proto();
        withRegisters(p, regs);
        for (int off : TcStack.paramReads(s.listing, f, s::poolValue)) {
            TcBase.Param q = new TcBase.Param();
            q.name = "param_s" + off / 4;
            q.kind = "u32";
            q.storage = "stack";
            q.stackOffset = off;
            p.params.add(q);
            p.paramIsPointer.add(false);
        }
        int r0 = TcFlow.maskOf(facts.retInt), fr0 = TcFlow.maskOf(facts.retFloat);
        p.ret = (changed & r0) != 0 ? "u32" : (changed & fr0) != 0 ? "f32" : "void";
        return p;
    }

    // Ghidra's return storage against the registers the code leaves changed (TcFlow flow[5]; a call or tail call
    // leaves every caller-saved register changed). p is a copy; base is Ghidra's prototype.
    void resolveReturn(TcBase.Proto p, TcBase.Proto base, int changed) {
        int r0 = TcFlow.maskOf(facts.retInt), hi = TcFlow.maskOf(facts.retInt64Hi);
        if (base.retWide) {
            p.ret = (changed & hi) != 0 ? "u64" : (changed & r0) != 0 ? "u32" : "void";
            return;
        }
        if (base.retStorage == null) return;
        int m = TcFlow.maskOf(base.retStorage);
        if (m != 0 && (m & changed) != 0 && (m & facts.calleeSaved) == 0 && (m & facts.args()) != 0) {
            p.retReg = base.retStorage; // a changed caller-saved register: a nonstandard result register
        } else if ((changed & r0) == 0) {
            p.ret = "void"; // a restored register is no result, and the ABI result register is left as it was
        }
    }

    // Cheap pre-check for the depth-0 shortcut: does the code (with its shared tails) name fr12..fr15 (fr12, dr12, fv12)?
    boolean usesState(Function f) {
        Program prog = s.getCurrentProgram();
        for (Instruction in : prog.getListing().getInstructions(TcTail.extent(prog, f), true)) {
            if (in.toString().toLowerCase().matches(".*\\b(fr1[2-5]|dr1[24]|fv12)\\b.*")) return true;
        }
        return false;
    }

    // Ghidra's prototype without the float register parameters the code never reads or passes on, and without
    // parameters in callee-saved registers (state, never parameters). Floats are kept as they are when the function
    // switches FPU modes: its float masks are unsafe.
    static TcBase.Proto readFloatsOnly(TcBase.Proto p, int[] flow) {
        TcBase.Proto q = TcBase.copyOf(p);
        q.params.clear();
        q.paramIsPointer.clear();
        for (int i = 0; i < p.params.size(); i++) {
            TcBase.Param x = p.params.get(i);
            int m = TcFlow.maskOf(x.storage);
            if ((m & (TcFlow.STATE | TcFlow.ISTATE)) != 0) continue;
            if (flow[2] == 0 && x.storage.matches("fr\\d+") && (m & (flow[0] | flow[1])) == 0) continue;
            q.params.add(x);
            q.paramIsPointer.add(p.paramIsPointer.get(i));
        }
        return q;
    }

    // Rebuild a pair-typed prototype: integer and stack parameters as they were, then one f32 per float register read.
    // Register parameters are named after their register (param_r4, param_fr5): Ghidra's numbering does not apply.
    TcBase.Proto singlesProto(TcBase.Proto p, int[] flow, int regs) {
        TcBase.Proto q = new TcBase.Proto();
        q.ret = p.ret;
        if (flow[2] != 0) {
            q.unsupported = "float pair/bank mode switch (fschg, frchg or fpchg) with a pair-typed prototype";
            return q;
        }
        for (int i = 0; i < p.params.size(); i++) {
            TcBase.Param x = p.params.get(i);
            if (x.storage.matches("dr\\d+|fr\\d+")) continue; // pair artifacts: the dataflow has the real reads
            if ((TcFlow.maskOf(x.storage) & TcFlow.ISTATE) != 0) continue; // state, not a parameter
            TcBase.Param y = new TcBase.Param();
            y.name = x.storage.matches("r[0-7]") ? "param_" + x.storage : x.name;
            y.kind = x.kind;
            y.storage = x.storage;
            y.stackOffset = x.stackOffset;
            q.params.add(y);
            q.paramIsPointer.add(p.paramIsPointer.get(i));
        }
        return withRegisters(q, regs);
    }

    // The function under test switches FPU modes, or reads fr0..fr3 at entry (TcFacts.fpuState): every float register,
    // XMTRX and the FPSCR mode bits are state (TcVectors line Q, compared at exit and at every call), so its float
    // parameters and float result are dropped; integer and stack parameters stay as Ghidra's prototype and the dataflow
    // give them (regs has no float bits). Callees keep their own prototypes: only depth PASS_DEPTH gets here.
    TcBase.Proto fpuProto(Function f, TcBase.Proto base, int regs, int changed) {
        TcBase.Proto q;
        if (noDecompile(base)) {
            q = fromFlow(f, regs, changed);
        } else if (base.unsupported != null) {
            return base;
        } else {
            q = new TcBase.Proto();
            q.ret = base.ret;
            for (int i = 0; i < base.params.size(); i++) {
                TcBase.Param x = base.params.get(i);
                if (x.storage.matches("dr\\d+|fr\\d+|xd\\d+|xf\\d+")) continue;
                if ((TcFlow.maskOf(x.storage) & TcFlow.ISTATE) != 0) continue; // state, not a parameter
                TcBase.Param y = new TcBase.Param();
                y.name = base.pairs && x.storage.matches("r[0-7]") ? "param_" + x.storage : x.name;
                y.kind = x.kind;
                y.storage = x.storage;
                y.stackOffset = x.stackOffset;
                q.params.add(y);
                q.paramIsPointer.add(base.paramIsPointer.get(i));
            }
            withRegisters(q, regs);
            if (q.unsupported != null) return q;
            resolveReturn(q, base, changed);
        }
        if (q.ret.equals("f32")) q.ret = "void"; // fr0 is compared as state
        q.fpuState = true;
        return q;
    }

    static String names(int mask) {
        List<String> out = new ArrayList<>();
        for (int b = 0; b < 31; b++) if ((mask & (1 << b)) != 0) out.add(TcFlow.nameOf(b));
        return String.join(", ", out);
    }

    // Append a parameter named after its register for every register in mask (TcFlow bits) that q lacks: float
    // arguments as f32, integer ones as u32. Callee-saved registers are runtime state, never parameters: skipped.
    // Other registers cannot be passed by the C runner: q becomes unsupported.
    TcBase.Proto withRegisters(TcBase.Proto q, int mask) {
        for (TcBase.Param x : q.params) mask &= ~TcFlow.maskOf(x.storage);
        List<String> bad = new ArrayList<>();
        for (int b = 0; b < 31; b++) {
            if ((mask & (1 << b)) == 0 || ((TcFlow.STATE | TcFlow.ISTATE) & (1 << b)) != 0) continue;
            String reg = TcFlow.nameOf(b);
            if ((facts.args() & (1 << b)) == 0) {
                bad.add(reg);
                continue;
            }
            TcBase.Param x = new TcBase.Param();
            x.name = "param_" + reg;
            x.kind = b < 16 ? "f32" : "u32";
            x.storage = reg;
            q.params.add(x);
            q.paramIsPointer.add(false);
        }
        if (!bad.isEmpty()) q.unsupported = "reads " + String.join(", ", bad) + " at entry (not argument registers)";
        return q;
    }
}

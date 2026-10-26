// What a run's own steps write, for two judge rules. Not a script itself; TcPrepare sets `on` for every prepare (null:
// the body has no computed call or jump and no float store, and nothing changes).
// 1. Calls through function pointers (a target the body's references do not name: not in meta callees, whose
//    argument registers come from the callee's prototype). The argument registers the run set itself since its
//    entry or its last call (its own parameters count as set at entry; a callee leaves them undefined), in the
//    convention's order up to the first gap (r4..r7; fr4..fr11 by pairs, which SHC and GCC fill in either order),
//    are that call's arguments: expected.txt "CA reg..." after its CALL line (call). The judge compares them as a
//    listed callee's (core/compare.py). Stack words are not compared: without a prototype the words at @(0,r15)..
//    are as often the caller's locals as arguments.
// 2. NaN words in memory. The judge takes two different NaN words as equal only where the original stored a float:
//    a computed NaN's payload is the FPU's, which C does not reproduce. Every other word, an integer word holding a
//    NaN's pattern (-1 is one), is compared bit for bit. A float store is an fmov to memory or sts.l FPUL, and a
//    mov.l of a register holding float bits: from sts FPUL, a mov of such a register, or a mov.l load of a word a
//    float store of this run wrote (a float moved through integer registers or the stack). expected.txt "FN addr..."
//    lists the words a float store wrote that still hold what it wrote and are a NaN at exit (exit), only when there
//    is one.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.FlowType;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import java.util.*;

final class TcTrack {
    static TcTrack on;  // the function being prepared has a computed call or jump, or a float store; or null
    static final Set<String> ARGS = new HashSet<>(Arrays.asList("r4", "r5", "r6", "r7", "fr4", "fr5", "fr6", "fr7",
        "fr8", "fr9", "fr10", "fr11"));
    static final List<String> CLOBBERED = Arrays.asList("r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7");

    final Set<Long> callees;      // the listed callees' entries (TcPrepare.calledBy)
    final Set<String> params;     // the function's own argument registers, written at entry
    final boolean calls, stores;  // a computed call or jump; a float store (fmov to memory, sts FPUL)
    final Map<Long, Set<String>> outs = new HashMap<>();
    Run cur;

    // A run's argument registers set since its entry or last call; the integer registers holding float bits; the
    // words its float stores wrote (word -> value).
    static final class Run {
        final Set<String> defined = new HashSet<>();
        final Set<String> fbits = new HashSet<>();
        final Map<Long, Long> stored = new TreeMap<>();
        final List<Long> nans = new ArrayList<>();
    }

    TcTrack(Set<Long> callees, Set<String> params, boolean calls, boolean stores) {
        this.callees = callees;
        this.params = params;
        this.calls = calls;
        this.stores = stores;
    }

    // `on` for the body (null when it has neither a computed call or jump nor a float store).
    static TcTrack of(Listing listing, AddressSetView body, List<Function> callees, TcBase.Proto proto) {
        boolean calls = false, stores = false;
        for (Instruction in : listing.getInstructions(body, true)) {
            FlowType f = in.getFlowType();
            calls |= f.isComputed() && (f.isCall() || f.isJump());
            int w = op(in);
            stores |= store(w) >= 0 || (w & 0xf0ff) == 0x4052 || (w & 0xf0ff) == 0x005a;
        }
        if (!calls && !stores) return null;
        Set<Long> c = new HashSet<>();
        for (Function g : callees) c.add(g.getEntryPoint().getOffset());
        Set<String> p = new HashSet<>();
        for (TcBase.Param q : proto.params) p.addAll(split(q.storage));
        p.retainAll(ARGS);
        return new TcTrack(c, p, calls, stores);
    }

    // A register's argument registers: drN is frN and frN+1, fvN frN..frN+3.
    static List<String> split(String reg) {
        String r = reg.toLowerCase();
        int n = r.matches("(dr|fv)\\d+") ? Integer.parseInt(r.substring(2)) : -1;
        if (n < 0) return List.of(r);
        List<String> out = new ArrayList<>();
        for (int i = 0; i < (r.startsWith("dr") ? 2 : 4); i++) out.add("fr" + (n + i));
        return out;
    }

    // TcOracle.runOracle: a new run's record, or null.
    static Run start() {
        if (on == null) return null;
        on.cur = new Run();
        on.cur.defined.addAll(on.params);
        return on.cur;
    }

    // The registers an instruction writes (its p-code outputs; drN and fvN split).
    Set<String> outs(Instruction in) {
        Set<String> w = outs.get(in.getAddress().getOffset());
        if (w == null) {
            w = new HashSet<>();
            for (PcodeOp op : in.getPcode()) {
                Varnode out = op.getOutput();
                if (out == null || !out.isRegister()) continue;
                Register reg = in.getProgram().getRegister(out.getAddress(), out.getSize());
                if (reg != null) w.addAll(split(reg.getName()));
            }
            outs.put(in.getAddress().getOffset(), w);
        }
        return w;
    }

    // The registers the step at in wrote: the p-code outputs on the path it took. The SH-4 SLEIGH holds an FPU
    // instruction's single and double forms in one body (`if (!(FPSCR_SZ == 0)) goto <doubleWidth>`; FPSCR_PR the
    // same, some with decode-time constants), and only the form the mode selects runs: fmov.s @r0,fr6 with SZ 0 writes
    // fr6, not dr6's fr7. The conditions are evaluated on the mode bits (no FPU step changes them); a branch on anything
    // else counts every output (outs).
    Set<String> written(Instruction in, EmulatorHelper emu) {
        Program p = in.getProgram();
        Set<String> w = path(in.getPcode(), v -> {
            Register r = p.getRegister(v.getAddress(), v.getSize());
            return r == null ? null : r.getName();
        }, m -> emu.readRegister(m).longValue());
        return w != null ? w : outs(in);
    }

    // The register outputs (drN and fvN split) on the path the p-code takes when its conditions read only constants
    // and the mode bits FPSCR_SZ and FPSCR_PR (mode: their values); null when a condition reads anything else.
    static Set<String> path(PcodeOp[] ops, java.util.function.Function<Varnode, String> name,
                            java.util.function.ToLongFunction<String> mode) {
        Map<Long, Long> tmp = new HashMap<>();
        Set<String> w = new HashSet<>();
        int i = 0;
        for (int steps = 0; i >= 0 && i < ops.length; steps++) {
            if (steps > 4 * ops.length) return null;
            PcodeOp op = ops[i];
            int code = op.getOpcode();
            if (code == PcodeOp.CBRANCH || code == PcodeOp.BRANCH) {
                Long c = code == PcodeOp.BRANCH ? Long.valueOf(1) : value(op.getInput(1), tmp, name, mode);
                Varnode to = op.getInput(0);
                if (c == null) return null;
                if (c == 0) i++;
                else if (to.isConstant()) i += (int) to.getOffset();
                else break;  // out of the instruction (inst_next)
                continue;
            }
            Varnode out = op.getOutput();
            if (out != null && out.isUnique()) {
                Long v = eval(op, tmp, name, mode);
                if (v != null) tmp.put(out.getOffset(), v);
            } else if (out != null && out.isRegister() && name.apply(out) != null) {
                w.addAll(split(name.apply(out)));
            }
            i++;
        }
        return w;
    }

    static Long eval(PcodeOp op, Map<Long, Long> tmp, java.util.function.Function<Varnode, String> name,
                     java.util.function.ToLongFunction<String> mode) {
        Long a = value(op.getInput(0), tmp, name, mode);
        Long b = op.getNumInputs() > 1 ? value(op.getInput(1), tmp, name, mode) : null;
        if (a == null || op.getNumInputs() > 1 && b == null) return null;
        switch (op.getOpcode()) {
            case PcodeOp.COPY: case PcodeOp.INT_ZEXT: return a;
            case PcodeOp.BOOL_NEGATE: return a == 0 ? 1L : 0L;
            case PcodeOp.INT_EQUAL: return a.equals(b) ? 1L : 0L;
            case PcodeOp.INT_NOTEQUAL: return a.equals(b) ? 0L : 1L;
            case PcodeOp.BOOL_AND: return a != 0 && b != 0 ? 1L : 0L;
            case PcodeOp.BOOL_OR: return a != 0 || b != 0 ? 1L : 0L;
            default: return null;
        }
    }

    // A condition's input: a constant, a value computed before it, or the mode bit FPSCR_SZ or FPSCR_PR; else null.
    static Long value(Varnode v, Map<Long, Long> tmp, java.util.function.Function<Varnode, String> name,
                      java.util.function.ToLongFunction<String> mode) {
        if (v.isConstant()) return v.getOffset();
        if (v.isUnique()) return tmp.get(v.getOffset());
        String r = v.isRegister() ? name.apply(v) : null;
        return r != null && r.matches("FPSCR_(SZ|PR)") ? mode.applyAsLong(r) : null;
    }

    // The instruction's 16-bit opcode, or -1.
    static int op(Instruction in) {
        byte[] b;
        try {
            b = in.getBytes();
        } catch (Exception e) {
            return -1;
        }
        return b.length == 2 ? (b[0] & 0xff) | (b[1] & 0xff) << 8 : -1;
    }

    // A float store fmov FRm,@Rn / @-Rn / @(R0,Rn) (TcPair's encodings): n, plus 16 for @(R0,Rn); else -1.
    static int store(int w) {
        int o = w & 0xf, n = (w >> 8) & 0xf;
        if (w < 0 || (w >> 12) != 0xf || (o != 0xa && o != 0xb && o != 0x7)) return -1;
        return o == 0x7 ? n | 16 : n;
    }

    static long reg(EmulatorHelper emu, int n) {
        return emu.readRegister("r" + n).longValue() & 0xffffffffL;
    }

    // TcOracle, after the step at pca in the body: specs and pend are its mov loads and their addresses (TcLoads,
    // taken before it). Store addresses come from the registers after it (@-Rn: Rn then).
    static void step(EmulatorHelper emu, TcLoads steps, Address pca, List<TcLoads.LoadSpec> specs, long[][] pend) {
        if (on == null) return;
        Run r = on.cur;
        for (Instruction in : steps.stepInstructions(pca)) {
            Set<String> outs = on.outs(in);
            if (on.calls) for (String x : on.written(in, emu)) if (ARGS.contains(x)) r.defined.add(x);
            if (!on.stores) continue;
            int w = op(in), s = store(w), at = flow(r.fbits, w, outs), n = (w >> 8) & 15;
            if (s >= 0) {
                int size = emu.readRegister("FPSCR_SZ").signum() != 0 ? 8 : 4;
                mark(emu, r, reg(emu, s & 15) + (s >= 16 ? reg(emu, 0) : 0), size);
            } else if (at >= 0) {
                mark(emu, r, reg(emu, n) + (at == 1 ? reg(emu, 0) : at >= 2 ? at - 2 : 0), 4);
            }
            for (int i = 0; i < specs.size(); i++) {  // a mov.l load of a word a float store wrote: float bits
                TcLoads.LoadSpec ls = specs.get(i);
                long a = TcBase.fold(pend[i][1]);
                Long v = ls.pc == in.getAddress().getOffset() && ls.size == 4 ? r.stored.get(a) : null;
                if (v != null && v == word(emu, a)) r.fbits.add(ls.dest);
            }
        }
    }

    // Float bits through the integer registers for the instruction w (outs: the registers it writes): fbits after it,
    // and the word it stores float bits to: 0 @Rn (sts.l FPUL,@-Rn; mov.l Rm,@Rn / @-Rn: Rn after it), 1 @(R0,Rn),
    // 2 + 4 * disp @(disp,Rn); or -1. sts FPUL,Rn and mov Rm,Rn of float bits make Rn hold float bits.
    static int flow(Set<String> fbits, int w, Set<String> outs) {
        int n = (w >> 8) & 15, m = (w >> 4) & 15, o = w & 0xf00f;
        boolean src = fbits.contains("r" + m);  // before this instruction's writes
        fbits.removeAll(outs);
        if ((w & 0xf0ff) == 0x4052) return 0;
        if ((w & 0xf0ff) == 0x005a || (o == 0x6003 && src)) fbits.add("r" + n);
        else if (src && (o == 0x2002 || o == 0x2006)) return 0;
        else if (src && o == 0x0006) return 1;
        else if (src && (w & 0xf000) == 0x1000) return 2 + 4 * (w & 0xf);
        return -1;
    }

    static void mark(EmulatorHelper emu, Run r, long a, int size) {
        for (int i = 0; i < size; i += 4) {
            long w = TcBase.fold((a + i) & 0xffffffffL);
            if ((w & 3) == 0) r.stored.put(w, word(emu, w));
        }
    }

    static long word(EmulatorHelper emu, long w) {
        Address at = emu.getProgram().getAddressFactory().getDefaultAddressSpace().getAddress(w);
        byte[] b = emu.readMemory(at, 4);
        return (b[0] & 0xffL) | (b[1] & 0xffL) << 8 | (b[2] & 0xffL) << 16 | (b[3] & 0xffL) << 24;
    }

    static boolean nan(long v) {
        return (v & 0x7f800000L) == 0x7f800000L && (v & 0x007fffffL) != 0;
    }

    // TcOracle, at a mocked call to target: "\nCA reg..." when target is no listed callee, else "". The registers are
    // undefined after it (the callee's to change: SH-4 calling convention), r0..r7 hold no float bits.
    static String call(long target) {
        if (on == null) return "";
        on.cur.fbits.removeAll(CLOBBERED);
        if (!on.calls) return "";
        Set<String> d = on.cur.defined;
        StringBuilder s = new StringBuilder();
        if (!on.callees.contains(target)) {
            s.append("\nCA");
            for (int i = 4; i < 8 && d.contains("r" + i); i++) s.append(" r").append(i);
            for (int i = 4; i < 12 && (d.contains("fr" + i) || d.contains("fr" + (i + 1))); i += 2) {
                for (int j = i; j < i + 2; j++) if (d.contains("fr" + j)) s.append(" fr").append(j);
            }
        }
        d.clear();
        return s.toString();
    }

    // TcExit.collect: the words a float store wrote that still hold its value and are a NaN.
    static void exit(EmulatorHelper emu) {
        if (on == null) return;
        for (Map.Entry<Long, Long> e : on.cur.stored.entrySet()) {
            long v = word(emu, e.getKey());
            if (v == e.getValue() && nan(v)) on.cur.nans.add(e.getKey());
        }
    }

    // expected.txt line FN (TcHw.exit), or "".
    static String exit(Run r) {
        if (r == null || r.nans.isEmpty()) return "";
        StringBuilder s = new StringBuilder("FN");
        for (long w : r.nans) s.append(' ').append(TcBase.hex(w));
        return s.append('\n').toString();
    }
}

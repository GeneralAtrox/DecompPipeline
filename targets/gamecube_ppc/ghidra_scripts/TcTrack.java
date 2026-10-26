// What a run's own steps write, for two judge rules. Not a script itself; TcPrepare sets `on` for every prepare (null:
// the body has no computed call or jump and no float store, and nothing changes).
// 1. Calls through function pointers (bctrl, blrl, bctr: a target not in meta callees, whose argument registers come
//    from the callee's prototype). The argument registers the run set since its entry or its last call (its own
//    parameters count as set at entry; after a call r3 and f1 hold its scripted results, the mock zeroes the rest:
//    TcOracle.clobber), in order up to the first gap (r3..r10; f1..f8), are that call's arguments: expected.txt
//    "CA reg..." after its CALL line (call). The judge compares them as a listed callee's (ppc_compare). Stack words
//    are not compared: without a prototype the words at r1+8.. are as often the caller's locals as arguments.
// 2. NaN words in memory. The judge takes two different NaN words as equal only where the original stored a float
//    (stfs, stfd, psq_st and their u/x forms; not stfiwx, an integer store): a computed NaN's payload is the FPU's.
//    Every other word, an integer word holding a NaN's pattern (-1 is one), is compared bit for bit. A float moved
//    through the integer registers counts too: an lwz of a word a float store of this run wrote makes its register
//    hold float bits, mr copies them, and an stw of such a register is a float store. expected.txt "FN addr..."
//    lists the words a float store wrote that still hold what it wrote and are a NaN at exit (exit).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.symbol.FlowType;
import java.util.*;

final class TcTrack {
    static TcTrack on;  // the function being prepared has a computed call or jump, or a float store; or null
    static final Set<String> ARGS = new HashSet<>(Arrays.asList("r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10",
        "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8"));
    static final List<String> CLOBBERED = Arrays.asList("r0", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11",
        "r12");

    final Set<Long> callees;      // the listed callees' entries (TcPrepare.calledBy)
    final Set<String> params;     // the function's own argument registers, set at entry
    final boolean calls, stores;  // a computed call or jump; a float store
    final Map<Long, Set<String>> outs = new HashMap<>();
    Run cur;

    // A run's registers set since its entry or last call; the integer registers holding float bits; the words its
    // float stores wrote (word -> value).
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
            stores |= store(in);
        }
        if (!calls && !stores) return null;
        Set<Long> c = new HashSet<>();
        for (Function g : callees) c.add(g.getEntryPoint().getOffset());
        Set<String> p = new HashSet<>();
        for (TcBase.Param q : proto.params) p.add(q.storage);
        p.retainAll(ARGS);
        return new TcTrack(c, p, calls, stores);
    }

    static boolean store(Instruction in) {
        return in.getMnemonicString().toLowerCase().matches("stf[sd]u?x?|psq_stu?x?");
    }

    // TcOracle.runOracle: a new run's record, or null.
    static Run start() {
        if (on == null) return null;
        on.cur = new Run();
        on.cur.defined.addAll(on.params);
        return on.cur;
    }

    // The registers an instruction writes (its p-code outputs).
    Set<String> outs(Instruction in) {
        Set<String> w = outs.get(in.getAddress().getOffset());
        if (w == null) {
            w = new HashSet<>();
            for (PcodeOp op : in.getPcode()) {
                Varnode out = op.getOutput();
                if (out == null || !out.isRegister()) continue;
                Register reg = in.getProgram().getRegister(out.getAddress(), out.getSize());
                if (reg != null) w.add(reg.getBaseRegister().getName().toLowerCase());
            }
            outs.put(in.getAddress().getOffset(), w);
        }
        return w;
    }

    static String reg(Instruction in, int i) {
        Register r = in.getNumOperands() > i ? in.getRegister(i) : null;
        return r == null ? "" : r.getName().toLowerCase();
    }

    // TcOracle, after the step of in (bank: its loads and stores).
    static void step(EmulatorHelper emu, Instruction in, TcBank bank) {
        if (on == null || in == null) return;
        Run r = on.cur;
        Set<String> outs = on.outs(in);
        if (on.calls) for (String x : outs) if (ARGS.contains(x)) r.defined.add(x);
        if (!on.stores) return;
        String m = in.getMnemonicString().toLowerCase(), d = reg(in, 0);
        if (flow(r.fbits, m, d, reg(in, 1), outs) || store(in)) {
            for (long[] st : bank.stores) {
                for (long w = (st[0] + 3) & ~3L; w + 4 <= st[0] + st[1]; w += 4) r.stored.put(w, word(bank, w));
            }
        }
        if (!m.matches("lwzu?x?")) return;
        for (long[] rd : bank.reads) {  // an lwz of a word a float store wrote: float bits
            Long v = rd[2] == 4 ? r.stored.get(rd[1]) : null;
            if (v != null && v == word(bank, rd[1])) r.fbits.add(d);
        }
    }

    // Float bits through the integer registers for an instruction (mnemonic m; d and s its first two operands'
    // registers; outs: the registers it writes): fbits after it, and true for an stw of a register holding float
    // bits (a float store). mr of such a register makes d hold float bits (an lwz: step).
    static boolean flow(Set<String> fbits, String m, String d, String s, Set<String> outs) {
        boolean src = fbits.contains(m.equals("mr") ? s : d);  // before this instruction's writes
        fbits.removeAll(outs);
        if (m.matches("stwu?x?")) return src;
        if (m.equals("mr") && src) fbits.add(d);
        return false;
    }

    // A word of RAM as the step left it, read past the bank's log: a read through the emulator would join the
    // step's loads (the load log, rule R8) and change the list step iterates.
    static long word(TcBank bank, long w) {
        byte[] b = new byte[4];
        bank.in.getChunk(TcBase.fold(w), 4, b, false);
        return (b[0] & 0xffL) << 24 | (b[1] & 0xffL) << 16 | (b[2] & 0xffL) << 8 | (b[3] & 0xffL);
    }

    // At exit (the bank's pc is -1 there: no read is logged).
    static long word(EmulatorHelper emu, long w) {
        Address at = emu.getProgram().getAddressFactory().getDefaultAddressSpace().getAddress(w);
        byte[] b = emu.readMemory(at, 4);
        return (b[0] & 0xffL) << 24 | (b[1] & 0xffL) << 16 | (b[2] & 0xffL) << 8 | (b[3] & 0xffL);
    }

    static boolean nan(long v) {
        return (v & 0x7f800000L) == 0x7f800000L && (v & 0x007fffffL) != 0;
    }

    // TcOracle, at a mocked call to target: "\nCA reg..." when target is no listed callee, else "". Afterwards only
    // r3 and f1 are set (the callee's scripted results), and no clobbered register holds float bits.
    static String call(long target) {
        if (on == null) return "";
        on.cur.fbits.removeAll(CLOBBERED);
        if (!on.calls) return "";
        Set<String> d = on.cur.defined;
        StringBuilder s = new StringBuilder();
        if (!on.callees.contains(target)) {
            s.append("\nCA");
            for (int i = 3; i <= 10 && d.contains("r" + i); i++) s.append(" r").append(i);
            for (int i = 1; i <= 8 && d.contains("f" + i); i++) s.append(" f").append(i);
        }
        d.clear();
        d.add("r3");
        d.add("f1");
        return s.toString();
    }

    // TcOracle.collect: the words a float store wrote that still hold its value and are a NaN.
    static void exit(EmulatorHelper emu) {
        if (on == null) return;
        for (Map.Entry<Long, Long> e : on.cur.stored.entrySet()) {
            long v = word(emu, e.getKey());
            if (v == e.getValue() && nan(v)) on.cur.nans.add(e.getKey());
        }
    }

    // expected.txt line FN (TcVectors), or "".
    static String exit(Run r) {
        if (r == null || r.nans.isEmpty()) return "";
        StringBuilder s = new StringBuilder("FN");
        for (long w : r.nans) s.append(' ').append(TcBase.hex(w));
        return s.append('\n').toString();
    }
}

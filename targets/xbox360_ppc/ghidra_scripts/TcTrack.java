// What a run's own steps write, for two judge rules (the Xenon counterpart of the GameCube's TcTrack). Not a script
// itself; TcPrepare sets `on` for every prepare (null: the body has no computed call or jump and no float store, and
// nothing changes).
// 1. Calls through function pointers (bctrl, bctr: a target not in meta callees, whose argument registers come from
//    the callee's prototype), and direct calls and branches to code no listed callee starts (outside the prepared
//    closure, or a run on past the body's end: unlisted, TcRun.tailJump, TcRun.pastEnd). The argument registers the run set since its entry or its last call (its own
//    parameters count as set at entry; after a call r3 and f1 hold its scripted results and the registers the mock
//    zeroes are unset, TcOracle.clobber, while those the callee keeps keep their state), in order up to the first gap
//    (r3..r10; f1..f13), are that call's arguments: expected.txt "CA reg..." after its CALL line (call). The judge
//    compares them as a listed callee's (x360_compare). Stack words are not compared: without a prototype the
//    doublewords at r1+0x50.. are as often the caller's locals as arguments.
// 2. NaN words in memory. The judge takes two different NaN words as equal only where the original stored a float
//    (stfs, stfd and their u/x forms; not stfiwx, an integer store): a computed NaN's payload is the FPU's. Every
//    other word, an integer word holding a NaN's pattern (-1 is one), is compared bit for bit. A float moved through
//    the integer registers counts too: an lwz/lwa of a word (an ld of a doubleword) a float store of this run wrote
//    makes its register hold float bits, mr (Ghidra's `or rA,rS,rS`) copies them, and an stw or std of such a
//    register is a float store. expected.txt "FN addr..." lists the words a float store wrote that still hold what
//    it wrote and are a NaN at exit (exit).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.symbol.FlowType;
import java.util.*;

final class TcTrack {
    static TcTrack on;  // the function being prepared has a computed call or jump, or a float store; or null
    static final Set<String> ARGS = new HashSet<>();

    static {
        for (int i = 3; i <= 10; i++) ARGS.add("r" + i);
        for (int i = 1; i <= 13; i++) ARGS.add("f" + i);
    }

    final Set<Long> callees;      // the listed callees' entries (TcPrepare.calledBy)
    final Set<String> params;     // the function's own argument registers, set at entry
    final boolean calls, stores;  // a computed call or jump; a float store
    final TcFlow flow;            // register names (f0..f31 are not base registers here: TcFlow.nameOf)
    final Map<Long, Op> ops = new HashMap<>();
    Run cur;

    // A run's registers set since its entry or last call; the integer registers holding float bits; the words its
    // float stores wrote (word -> value).
    static final class Run {
        final Set<String> defined = new HashSet<>();
        final Set<String> fbits = new HashSet<>();
        final Map<Long, Long> stored = new TreeMap<>();
        final List<Long> nans = new ArrayList<>();
    }

    TcTrack(Set<Long> callees, Set<String> params, boolean calls, boolean stores, TcFlow flow) {
        this.callees = callees;
        this.params = params;
        this.calls = calls;
        this.stores = stores;
        this.flow = flow;
    }

    // `on` for the body (null when it has neither a computed call or jump, nor a flow to code no listed callee
    // starts, nor a float store).
    static TcTrack of(Listing listing, AddressSetView body, List<Function> callees, TcBase.Proto proto, TcFlow flow) {
        Set<Long> c = new HashSet<>();
        for (Function g : callees) c.add(g.getEntryPoint().getOffset());
        boolean calls = false, stores = false;
        for (Instruction in : listing.getInstructions(body, true)) {
            FlowType f = in.getFlowType();
            calls |= f.isComputed() && (f.isCall() || f.isJump());
            if ((f.isJump() || f.isCall()) && !f.isComputed()) {
                for (Address to : in.getFlows()) calls |= unlisted(in, to, body, c);
            }
            calls |= in.getFallThrough() != null && unlisted(in, in.getFallThrough(), body, c);
            stores |= store(in);
        }
        if (!calls && !stores) return null;
        Set<String> p = new HashSet<>();
        for (TcBase.Param q : proto.params) p.add(q.storage);
        p.retainAll(ARGS);
        return new TcTrack(c, p, calls, stores, flow);
    }

    // A flow out of the body to an address no listed callee starts. The oracle mocks it as a call (TcOracle) whose target
    // has no record, so the CA line holds its arguments (call): a direct call or branch to code no prepared function
    // starts, or a run on past the body's end. Before 2026-11-07 only a computed call or jump turned the lines on, and
    // such a call had no argument compared (core/unchecked_args.py; target.json stamp scope unlisted_call_args).
    // The frame helpers (__savegprlr_N ...) run inline: no call, no record.
    static boolean unlisted(Instruction in, Address to, AddressSetView body, Set<Long> c) {
        return !body.contains(to) && !c.contains(to.getOffset())
            && !TcBase.isHelper(in.getProgram().getFunctionManager().getFunctionAt(to));
    }

    static boolean store(Instruction in) {
        return in.getMnemonicString().toLowerCase().matches("stf[sd]u?x?");
    }

    // TcOracle.runOracle: a new run's record, or null.
    static Run start() {
        if (on == null) return null;
        on.cur = new Run();
        on.cur.defined.addAll(on.params);
        return on.cur;
    }

    // What step needs of an instruction, taken once per address: a regex on every emulated step was a sixth of a
    // prepare (Too Human 82e97a88: TcTrack.step 16% of the samples), and the run steps the same few instructions.
    static final class Op {
        final Set<String> outs = new HashSet<>();  // the registers it writes (its p-code outputs), by TcFlow's names
        final String d, s, b;                       // its first three operands' registers ("" for none)
        final boolean mr, wordStore, floatStore, lw, ld;

        Op(Instruction in, TcFlow flow) {
            if (!TcFlow.identityMove(TcFlow.word(in))) {
                for (PcodeOp op : in.getPcode()) {
                    String n = flow.nameOf(op.getOutput());
                    if (n != null) outs.add(n);
                }
            }
            String m = in.getMnemonicString().toLowerCase();
            d = reg(in, 0);
            s = reg(in, 1);
            b = reg(in, 2);
            mr = mr(m, s, b);
            wordStore = m.matches("st[wd]u?x?");
            floatStore = store(in);
            lw = m.matches("lwzu?x?|lwax?|lwaux");
            // ldarx: Ghidra 12.1.2's name for ldx (XO 21, a plain load); a real ldarx is an atomic, which is refused
            ld = m.matches("ldu?x?|ldarx");
        }
    }

    Op op(Instruction in) {
        Op o = ops.get(in.getAddress().getOffset());
        if (o == null) {
            o = new Op(in, flow);
            ops.put(in.getAddress().getOffset(), o);
        }
        return o;
    }

    static String reg(Instruction in, int i) {
        Register r = in.getNumOperands() > i ? in.getRegister(i) : null;
        return r == null ? "" : r.getName().toLowerCase();
    }

    // TcOracle, after the step of in (bank: its loads and stores).
    static void step(EmulatorHelper emu, Instruction in, TcBank bank) {
        if (on == null || in == null) return;
        Run r = on.cur;
        Op o = on.op(in);
        if (on.calls) for (String x : o.outs) if (ARGS.contains(x)) r.defined.add(x);
        if (!on.stores) return;
        if (flow(r.fbits, o.mr, o.wordStore, o.d, o.s, o.outs) || o.floatStore) {
            for (long[] st : bank.stores) {
                for (long w = (st[0] + 3) & ~3L; w + 4 <= st[0] + st[1]; w += 4) r.stored.put(w, word(bank, w));
            }
        }
        if (!o.lw && !o.ld) return;
        for (long[] rd : bank.reads) {  // an lwz of a word (an ld of two words) a float store wrote: float bits
            if (rd[2] != (o.lw ? 4 : 8)) continue;
            boolean bits = true;
            for (long w = rd[1]; w < rd[1] + rd[2]; w += 4) {
                Long v = r.stored.get(w);
                bits &= v != null && v == word(bank, w);
            }
            if (bits) r.fbits.add(o.d);
        }
    }

    // Float bits through the integer registers for an instruction (mnemonic m; d, s and b its first three operands'
    // registers; outs: the registers it writes): fbits after it, and true for an stw or std of a register holding
    // float bits (a float store). mr (`or d,s,s`) of such a register makes d hold float bits (an lwz or ld: step).
    static boolean flow(Set<String> fbits, String m, String d, String s, String b, Set<String> outs) {
        return flow(fbits, mr(m, s, b), m.matches("st[wd]u?x?"), d, s, outs);
    }

    static boolean mr(String m, String s, String b) {
        return m.equals("or") && s.equals(b) && !s.isEmpty();
    }

    static boolean flow(Set<String> fbits, boolean mr, boolean wordStore, String d, String s, Set<String> outs) {
        boolean src = fbits.contains(mr ? s : d);  // before this instruction's writes
        fbits.removeAll(outs);
        if (wordStore) return src;
        if (mr && src) fbits.add(d);
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

    // TcOracle, at a mocked call to target that sets r3 and f1 and zeroes `zeroed`: "\nCA reg..." when target is no
    // listed callee, else "". Afterwards r3 and f1 are set (the callee's scripted results), a zeroed register is
    // unset, a kept one is as it was; no zeroed register holds float bits.
    static String call(long target, Set<String> zeroed) {
        if (on == null) return "";
        on.cur.fbits.removeAll(zeroed);
        on.cur.fbits.remove("r3");
        if (!on.calls) return "";
        Set<String> d = on.cur.defined;
        StringBuilder s = new StringBuilder();
        if (!on.callees.contains(target)) {
            s.append("\nCA");
            for (int i = 3; i <= 10 && d.contains("r" + i); i++) s.append(" r").append(i);
            for (int i = 1; i <= 13 && d.contains("f" + i); i++) s.append(" f").append(i);
        }
        d.removeAll(zeroed);
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

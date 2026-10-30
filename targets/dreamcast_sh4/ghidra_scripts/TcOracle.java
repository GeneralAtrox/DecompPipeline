// The emulator oracle: runs the ORIGINAL code in Ghidra's p-code emulator (patched SuperH4:LE:32:tcfix
// language) on inputs from TcInputs. LOCKED: the watcher agent may not change this file. Extended by TcVectors.
// Helpers: TcFold (the RAM mirror), TcLoads (the load log and rule R8), TcFpu and TcRound (FPSCR.DN and RM), TcPair
// (64-bit fmov pairs, TcFacts.fpuState), TcLive and TcSq (the callees' changes of r8..r14, the store queues), TcExit
// (a run's results at its end, and the halt of an endless `bra .`), TcSpin (the halt of a wait loop nothing in it
// ends), TcFpscr (FPSCR as state, for a body that reads or writes it), TcTrack (pointer-call arguments, float NaN words).

import ghidra.app.emulator.EmulatorHelper;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.pcode.memstate.DefaultMemoryState;
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryState;
import ghidra.pcode.pcoderaw.PcodeOpRaw;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.math.BigInteger;
import java.util.*;

public abstract class TcOracle extends TcInputs {
    // ---------- oracle ----------
    static class Result {
        boolean ok;
        String reason = "";
        long r0;
        int fr0;
        long retx;                        // the extra result word (entryRet): a nonstandard result register, u64 high
        int[] xf = new int[16];
        int[] fh = new int[4];            // fr12..fr15 at exit
        long[] gi = new long[7], ro = new long[5];  // r8..r14, r4..r7 (TcOut) and T (TcTflag) at exit
        int[] fq = new int[16];           // fr0..fr15 at exit (Proto.fpuState)
        long mode; TcFpscr.Run fs; TcTrack.Run tr;  // FPSCR mode bits at exit (modeWord); FPSCR as state; TcTrack's
        List<String> writes = new ArrayList<>();
        List<String> calls = new ArrayList<>();
        Set<Integer> cov = new TreeSet<>(), dirs = new TreeSet<>();  // blocks; branch directions (TcDirs)
        Set<Long> sites = new TreeSet<>();  // the call sites (call or tail-jump instructions) whose mocked callee ran
        long faultAddr = -1;              // first address outside RAM that was read or written (or code read: R8)
        boolean faultWrite;
        long storeBase = -1;              // storeFaults: the base register's value at the store outside RAM
        List<long[]> loads = new ArrayList<>(); // last LOAD_LOG loads: {pc, effective address, size, value}
        long outside = -1;                // a non-call address outside the body that the run reached
        List<long[]> firstLoads = new ArrayList<>(); // the first LOAD_LOG loads too (TcCount, TcSentinel)
        TcCount count;                    // countRepair: the compares of the steps before the limit (TcCount)
        boolean stepLimited;              // the run hit stepLimit
        boolean callFault;                // faultAddr is a call target from the inputs (TcCalls), not a memory access
        boolean callCapped;               // the run stopped at CALL_LIMIT calls (TcSentinel)
        boolean halted;                   // the run ended in an endless `bra .` (TcExit.halts), or spun (TcSpin)
        boolean spun; TcStrict strict; long misaligned = -1; boolean misWrite;  // TcSpin's wait loop; TcStrict's records; the first SH-4 address error, or -1 (TcAlign)
        int imask = -1; long gbr = -1;    // SR.IMASK at exit for an SR access (TcSr); GBR, listed (TcHw); else -1
        List<long[]> watched; int watchCalls; long[] watchRegs; // at watchPc's first arrival: loads, calls, r0..r15
    }

    static final int LOAD_LOG = 64;
    // A vector making more calls is rejected like one hitting STEP_LIMIT: its loop waits on a callee result the
    // mocks never give, and logging every call ran Ghidra out of heap. tc_rt.c's MAX_CALLS must equal it.
    static final int CALL_LIMIT = 4096;
    // Scripted call results per vector (line C: Vec.rets, then entryLate); a later call returns 0.
    static final int RETS = 64;  // tc_rt_internal.h's TCRT_RETS must equal it

    TreeMap<Long, Integer> blockIndex = new TreeMap<>(); TcDirs dirs;  // null unless branch_coverage (TcDirs)
    // Entry values the next runOracle sets beyond TcInputs' Vec (TcVectors decides them; null: left as the emulator
    // starts them, zero): fr12..fr15 and r8..r14 for runtime state (Proto.frState, giState), r0..r3 for parameters there.
    int[] entryFh = null;
    long[] entryGi = null, entryLow = null;
    long[] entryLate = null; // the results of the calls after Vec.rets' (TcLate), up to RETS in all
    int[] entryFq = null;   // fr0..fr15 at entry for a Proto.fpuState function (then call records carry the FPU too)
    String entryRet = null; // the register read into Result.retx at exit (Proto.retReg, or r1 for a u64), or null
    boolean callWide = false; Set<Long> floatRet = new HashSet<>(); // call records also carry r0..r3, r8..r14; f32 callees (TcFret)
    TcLoads loadView; TcFpu fpu;  // made at the first run
    int stepLimit = STEP_LIMIT; long workSteps, workRuns; // the overtime rescue lowers it; the work so far (TcBudget)
    boolean countRepair = false; // TcVectors' overtime rescue: runs record what TcCount needs
    // TcVectors' rescue pass (batch 29): a store outside RAM stops the run at the store (collect only sees it at the
    // end, after the epilogue's loads have filled the load log), with the store's base register value (TcProvenance).
    boolean storeFaults = false;
    long watchPc = -1;  // TcReach: record the load log at the first arrival there (Result.watched); -1: none

    // Leaving `body` (TcPrepare: TcTail.extent, the function's body and the shared tails it runs) is a call only when a
    // call instruction (jsr, bsr, bsrf) did it, or a jump (a tail call) reached another function's entry: those are the
    // callees, mocked and compared; so is a computed jump Ghidra knows no target of (`jmp @r1` through a pointer the
    // run loaded: Spawn GEND 8c563e0e) to an address outside the program's loaded bytes, where no code of it can run.
    // Anything else (a switch case Ghidra left outside the body, a jump into the image without a function there,
    // bytes that are no instruction) is code no packet, block list or coverage count shows, and that a C version is
    // never tested on: TcPrepare refuses the function. prev: the address last stepped, or -1 right after a mocked call.
    boolean entersCallee(long prev, Address pca) {
        Instruction last = prev < 0 ? null : listing.getInstructionAt(toAddr(prev));
        if ((last != null && last.getFlowType().isCall()) || fm.getFunctionAt(pca) != null) return true;
        if (last == null || !last.getFlowType().isComputed() || !last.getFlowType().isJump()) return false;
        for (Reference r : last.getReferencesFrom()) if (r.getReferenceType().isFlow()) return false;
        return !currentProgram.getMemory().getLoadedAndInitializedAddressSet().contains(pca);
    }

    // A run leaving the body for an address that is no code of the program (no instruction byte in the code ranges, the
    // C runner's R8 map, and no instruction Ghidra knows there), by a computed jump, a return, or the return to PR after
    // a mocked call, to an address that is no constant of the body (a literal-pool word it loads, an address it names):
    // the target came from the inputs (a handler table indexed past its end by an input opcode, a function pointer read
    // from input memory, a saved PR an input-sized loop overwrote). The program crashes the same way on that input, so
    // the vector is invalid, not the function. A target in code, or a constant one, still refuses it (entersCallee).
    boolean inputJump(long prev, long pc) {
        if (!facts.haveCode || facts.isCode(pc) || listing.getInstructionContaining(toAddr(pc)) != null) return false;
        Instruction last = prev < 0 ? null : listing.getInstructionAt(toAddr(prev));
        if (last != null && !last.getFlowType().isComputed() && !last.getFlowType().isTerminal()) return false;
        return !bodyConsts().contains(pc);
    }

    Set<Long> bodyConsts = null;

    // The literal-pool words the body loads and the addresses it names.
    Set<Long> bodyConsts() {
        if (bodyConsts == null) {
            bodyConsts = new HashSet<>();
            for (Instruction in : listing.getInstructions(body, true)) {
                Long c = poolValue(in);
                if (c != null) bodyConsts.add(c & 0xffffffffL);
                for (Reference r : in.getReferencesFrom()) bodyConsts.add(r.getToAddress().getOffset());
            }
        }
        return bodyConsts;
    }

    BigInteger u(long v) {
        return BigInteger.valueOf(v & 0xffffffffL);
    }

    long reg(EmulatorHelper emu, String name) {
        return emu.readRegister(name).longValue() & 0xffffffffL;
    }

    String where(long off) {
        return facts.isMmio(off) ? " (hardware register)" : "";
    }

    // The call record: target, r4..r7, fr4..fr11, the stack words @(0,r15)..@(28,r15), fr12..fr15, and with callWide
    // r0..r3 and r8..r14 (tc_rt.c's log has the same 36 fields). For a Proto.fpuState function (entryFq; callWide is
    // then set) also fr0..fr3, xf0..xf15 and the mode word: 57 fields.
    String callRecord(EmulatorHelper emu, long pc) {
        StringBuilder c = new StringBuilder(hex(pc));
        for (int i = 4; i < 8; i++) c.append(' ').append(hex(reg(emu, "r" + i)));
        for (int i = 4; i < 12; i++) c.append(' ').append(hex(reg(emu, "fr" + i)));
        long sp = fold(reg(emu, "r15"));
        for (int i = 0; i < 8; i++) {
            long w = 0;
            if (sp + 4 * i + 4 <= RAM_HI && sp + 4 * i >= RAM_LO) {
                byte[] b = emu.readMemory(toAddr(sp + 4 * i), 4);
                w = (b[0] & 0xffL) | (b[1] & 0xffL) << 8 | (b[2] & 0xffL) << 16 | (b[3] & 0xffL) << 24;
            }
            c.append(' ').append(hex(w));
        }
        for (int i = 12; i < 16; i++) c.append(' ').append(hex(reg(emu, "fr" + i))); // runtime state
        if (callWide) {
            for (int i = 0; i < 4; i++) c.append(' ').append(hex(reg(emu, "r" + i)));
            for (int i = 8; i < 15; i++) c.append(' ').append(hex(reg(emu, "r" + i)));
        }
        if (entryFq != null) {
            for (int i = 0; i < 4; i++) c.append(' ').append(hex(reg(emu, "fr" + i)));
            for (int i = 0; i < 16; i++) c.append(' ').append(hex(reg(emu, "xf" + i)));
            c.append(' ').append(hex(modeWord(emu)));
        }
        return c.toString();
    }

    // FPSCR.FR, SZ and PR in their FPSCR bit positions (21, 20, 19), from the field registers the SLEIGH toggles.
    long modeWord(EmulatorHelper emu) {
        return (reg(emu, "FPSCR_FR") & 1) << 21 | (reg(emu, "FPSCR_SZ") & 1) << 20 | (reg(emu, "FPSCR_PR") & 1) << 19;
    }

    // fsca's table is the one of the rounding the FPU runs in (TcFpu.fscaTable); reject: an RM other than the entry's.
    void fscaCallbacks(EmulatorHelper emu, MemoryState ms, String[] fault, String[] reject) {
        for (String name : new String[] {"sin", "cos"}) {
            final int[][] tables = name.equals("sin") ? fscaSin : fscaCos;
            emu.registerCallOtherCallback(name, new BreakCallBack() {
                @Override
                public boolean pcodeCallback(PcodeOpRaw op) {
                    long rm = facts.fpscrModel ? emu.readRegister("FPSCR_RM").longValue() : 0;
                    int[] table = tables[TcFpu.fscaTable(facts.fpscrModel, rm, facts.fpscrEntry, reject)];
                    int bits = (int) ms.getValue(op.getInput(1));
                    Integer frac = angleToFraction.get(bits);
                    if (frac == null && fault[0] == null) fault[0] = "fsca angle not from table";
                    ms.setValue(op.getOutput(), frac == null ? 0 : table[frac] & 0xffffffffL);
                    return true;
                }
            });
        }
    }

    // A vector's patch bytes over the function's own text get the image's bytes back (TcText; "text_restored").
    Set<Long> text = null;
    int textRestored = 0;

    void setEntry(EmulatorHelper emu, Vec v) {
        if (text == null) text = TcText.of(listing, body, this::poolValue);
        if (TcText.restore(mem, text, v.patchAddr, v.patchData)) textRestored++;
        for (int i = 0; i < v.patchAddr.size(); i++) emu.writeMemory(toAddr(v.patchAddr.get(i)), v.patchData.get(i).clone()); // the emulator may adopt the array as a page
        for (int i = 0; i < 4; i++) emu.writeRegister("r" + (4 + i), u(TcCalls.entry(i, v.r[i])));  // a settable run flag's base (TcCalls)
        for (int i = 0; i < 8; i++) emu.writeRegister("fr" + (4 + i), u(v.fr[i]));
        for (int i = 0; i < 16; i++) emu.writeRegister("xf" + i, u(v.xf[i]));
        for (int i = 0; entryFh != null && i < 4; i++) emu.writeRegister("fr" + (12 + i), u(entryFh[i]));
        for (int i = 0; entryLow != null && i < 4; i++) emu.writeRegister("r" + i, u(entryLow[i]));
        for (int i = 0; entryGi != null && i < 7; i++) emu.writeRegister("r" + (8 + i), u(entryGi[i]));
        for (int i = 0; entryFq != null && i < 16; i++) emu.writeRegister("fr" + i, u(entryFq[i]));
        emu.writeRegister("r15", u(SP));
        emu.writeRegister("PR", u(SENTINEL));
        long fp = TcFpscr.entry(facts.fpscrEntry, v);  // FLAG and CAUSE per vector when FPSCR is state
        emu.writeRegister("FPSCR", u(fp));
        if (facts.fpscrModel || TcFpscr.on != null) {  // the SLEIGH reads (and sts rebuilds FPSCR from) the fields
            String[] field = {"FPSCR_RM", "FPSCR_FLAG", "FPSCR_ENABLE", "FPSCR_CAUSE", "FPSCR_DN", "FPSCR_PR", "FPSCR_SZ", "FPSCR_FR"};
            int[] shift = {0, 2, 7, 12, 18, 19, 20, 21}, width = {2, 5, 5, 6, 1, 1, 1, 1};
            for (int i = 0; i < field.length; i++) emu.writeRegister(field[i], u((fp >> shift[i]) & ((1L << width[i]) - 1)));
        } else {
            emu.writeRegister("FPSCR_PR", BigInteger.ZERO);
            emu.writeRegister("FPSCR_SZ", BigInteger.ZERO);
            emu.writeRegister("FPSCR_FR", BigInteger.ZERO);
        }
        TcFpscr.Sz.enter(emu); if (sr != null) sr.enter(emu, v); TcLate.enter(emu, v);  // MD=1, RB=0, BL=0, the vector's IMASK (TcSr); GBR (TcLate)
        emu.writeRegister(emu.getPCRegister(), u(fn.getEntryPoint().getOffset()));
    }

    Result runOracle(Vec v) {
        Result res = new Result();
        workRuns++;
        res.count = countRepair ? new TcCount() : null; res.fs = TcFpscr.start(); res.tr = TcTrack.start();
        if (loadView == null) {
            loadView = new TcLoads(listing, facts);
            fpu = new TcFpu(loadView, facts);
        }
        EmulatorHelper emu = new EmulatorHelper(currentProgram);
        final String[] fault = {null}, fscaRm = {null}; final long[] faultAt = {-1}; final boolean[] faultW = {false};
        try {
            emu.setMemoryFaultHandler(new MemoryFaultHandler() {
                @Override
                public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) {
                    long off = address.getOffset();
                    if (address.isMemoryAddress() && (off < RAM_LO || off + size > RAM_HI) && fault[0] == null) {
                        fault[0] = "read " + address + where(off);
                        faultAt[0] = off;
                    }
                    // No zeroing: Ghidra hands over every byte from the first uninitialized one to the end of the
                    // read in its page, initialized ones too (a vector's memory patches), and the page keeps what this
                    // returns. Fresh pages are zero-filled, so the bytes no input set still read 0 (r15: xemu ghmem).
                    return true;
                }

                @Override
                public boolean unknownAddress(Address address, boolean write) {
                    if (fault[0] == null) {
                        fault[0] = (write ? "write " : "read ") + address + where(address.getOffset());
                        faultAt[0] = address.getOffset();
                        faultW[0] = write;
                    }
                    return false;
                }
            });
            final MemoryState ms = emu.getEmulator().getMemState();
            DefaultMemoryState dms = (DefaultMemoryState) ms;
            AddressSpace ramSpace = fn.getEntryPoint().getAddressSpace();
            TcFold bank = new TcFold(dms.getMemoryBank(ramSpace), sq, entryGi);
            dms.setMemoryBank(bank);
            fscaCallbacks(emu, ms, fault, fscaRm); TcHw.callbacks(emu);  // listed cache ops: no-ops (TcHw)
            emu.enableMemoryWriteTracking(true);
            setEntry(emu, v); res.strict = TcStrict.of(this, emu);  // null with every strict flag off (TcStrict)

            int steps = 0, calls = 0;
            TcSpin spin = spinWait ? new TcSpin(this) : null;  // opt-in: project.json "spin_waits"
            long prev = -1;
            while (true) {
                long pc = emu.getExecutionAddress().getOffset();
                if (fold(pc) != pc) {  // a jump or call through a RAM mirror
                    pc = fold(pc);
                    emu.writeRegister(emu.getPCRegister(), u(pc));
                }
                if (dirs != null) dirs.hit(res.dirs, prev, pc);  // the direction the branch at prev took
                if (pc == SENTINEL) break;
                Address pca = toAddr(pc);
                if (!body.contains(pca)) {
                    if (!entersCallee(prev, pca)) {
                        if (inputJump(prev, pc)) {  // an invalid vector, not a refusal
                            res.reason = "jumps outside its body to " + hex(pc) + ", not code (a target from the inputs)";
                            return res;
                        }
                        res.reason = "executes code outside its body at " + hex(pc);
                        res.outside = pc;
                        return res;
                    }
                    if (TcCalls.fromInputs(pc, bodyConsts())) {  // an invalid vector, repaired like a fault (TcCalls)
                        res.reason = "calls " + hex(pc) + " outside RAM, no constant (a target from the inputs)";
                        res.faultAddr = pc;
                        res.callFault = true;
                        return res;
                    }
                    if (calls >= CALL_LIMIT) {
                        res.reason = "more than " + CALL_LIMIT + " calls (a loop the mocked callees never end)";
                        res.callCapped = true;
                        return res;
                    }
                    res.calls.add(callRecord(emu, pc) + TcTrack.call(pc)); TcFpscr.call(emu); TcFpscr.Sz.call(emu, pc); if (sq != null) sq.callee(this, emu, pc);
                    TcStrict.call(res, emu, calls); TcCalls.flagCall(this, res, emu, pc);  // snapshots, junk, writes (after the records; TcSq.callee reads r4..r7 first); a listed run flag (TcCalls)
                    if (prev >= 0) res.sites.add(prev);
                    if (TcExit.halts(listing, pca)) {  // a callee that never returns: the run ends with it
                        res.halted = true;
                        break;
                    }
                    int k = calls - v.rets.length;
                    long rv = k < 0 ? (v.rets[calls] = TcLate.ranged(v, pc, calls, TcFret.at(floatRet, pc, v.rets[calls])))  // genretfn
                        : entryLate != null && k < entryLate.length ? (entryLate[k] = TcLate.ranged(v, pc, calls, TcFret.at(floatRet, pc, TcLate.pooled(v, pc, k, entryLate[k]))))
                        : 0;
                    TcLive.after(live, this, emu, pc, calls++); // the callee's own change of r8..r14 (TcLive)
                    if (spin != null) spin.call();
                    emu.writeRegister("r0", u(rv));
                    emu.writeRegister("fr0", u(rv));
                    emu.writeRegister(emu.getPCRegister(), u(reg(emu, "PR")));
                    prev = -1;
                    continue;
                }
                Map.Entry<Long, Integer> b = blockIndex.floorEntry(pc);
                if (b != null) res.cov.add(b.getValue());
                if (TcExit.halts(listing, pca)) {  // an endless `bra .`: the run ends here (TcExit)
                    res.halted = true;
                    break;
                }
                if (spin != null && spin.spins(emu, prev, pca, steps)) {  // a listed wait loop it never leaves
                    res.halted = res.spun = true;
                    break;
                }
                if (pc == watchPc && res.watched == null) TcReach.snap(res, calls, emu);
                workSteps++;
                if (++steps > stepLimit) {
                    res.reason = "step limit (possible infinite loop)";
                    res.stepLimited = true;
                    return res;
                }
                if (res.count != null && steps > stepLimit - TcCount.WINDOW) res.count.observe(emu, loadView.stepInstructions(pca));
                long code = loadView.codeRead(emu, pca);
                if (code >= 0) {  // rule R8: the C runner fails any such read
                    res.reason = "fault: read machine code at " + hex(code) + " (R8)";
                    res.faultAddr = code;
                    return res;
                }
                if (storeFaults) {
                    long[] base = {-1};
                    long st = loadView.storeOutside(emu, pca, base);
                    if (st >= 0) {
                        res.reason = "write outside RAM";
                        res.faultAddr = st;
                        res.faultWrite = true;
                        res.storeBase = base[0];
                        return res;
                    }
                }
                String[] reject = {null};
                TcFpu.State zeroed = fpu.before(emu, pca, reject); TcFpscr.before(emu, loadView, pca, reject);
                if (reject[0] != null) {
                    res.reason = reject[0];
                    return res;
                }
                List<TcLoads.LoadSpec> specs = loadView.loadsAt(pca);
                long[][] pend = new long[specs.size()][];
                for (int i = 0; i < pend.length; i++) pend[i] = loadView.loadBefore(emu, specs.get(i));
                prev = pc;
                if (sq != null) sq.step(this, emu, pca, fault, faultAt);  // a pref sending a store queue (TcSq)
                List<Integer> drs = facts.fpuState ? TcPair.drOperands(emu, loadView.stepInstructions(pca)) : List.of();
                TcPair.swap(emu, drs);
                bank.stepping = true; boolean stepped = emu.step(monitor); bank.stepping = false;  // its own accesses only (TcFold.align)
                TcPair.swap(emu, drs);
                if (bank.p4Read >= 0 && fault[0] == null) {  // cache state, not the bytes stored (TcFold.p4Read)
                    fault[0] = "read " + toAddr(bank.p4Read) + " (cache address array)";
                    faultAt[0] = bank.p4Read;
                }
                String fpuWhy = fpu.after(emu, pca, zeroed); TcTrack.step(emu, loadView, pca, specs, pend);
                if ((fpuWhy != null || fscaRm[0] != null) && stepped && fault[0] == null) {
                    res.reason = fpuWhy != null ? fpuWhy : fscaRm[0];
                    return res;
                }
                for (int i = 0; i < pend.length; i++) {
                    pend[i][3] = reg(emu, specs.get(i).dest);
                    pend[i][4] = calls;
                    res.loads.add(pend[i]);
                    if (res.firstLoads.size() < LOAD_LOG) res.firstLoads.add(pend[i]);
                    if (res.loads.size() > LOAD_LOG) res.loads.remove(0);
                }
                if (!stepped || fault[0] != null) {
                    res.reason = fault[0] != null ? "fault: " + fault[0] : "emulation error: " + emu.getLastError();
                    res.faultAddr = faultAt[0];
                    res.faultWrite = faultW[0];
                    return res;
                }
                String srWhy = sr == null ? null : sr.after(emu, pc);  // an ldc setting RB or BL (TcSr)
                if (srWhy != null) {
                    res.reason = srWhy;
                    return res;
                }
            }
            TcExit.collect(this, emu, res); TcStrict.exit(res, emu); res.misaligned = bank.misaligned; res.misWrite = bank.misWrite;  // exit_frame (TcStrict); TcAlign
            return res;
        } catch (Exception e) {
            res.reason = "exception: " + e;
            return res;
        } finally {
            emu.dispose();
        }
    }
}

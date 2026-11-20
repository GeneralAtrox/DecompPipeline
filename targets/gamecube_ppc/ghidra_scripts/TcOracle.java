// The emulator oracle: runs the ORIGINAL code in Ghidra's p-code emulator (PowerPC:BE:32:Gekko_Broadway) on inputs
// from TcInputs, with the 750CL corrections of TcFixups. LOCKED: the watcher agent may not change this file.
// Extended by TcVectors.
//
// Entry state: r3..r10 and f1..f8 from the vector (a helper entry also r11, r14..r31, f14..f31), r1 = SP, LR = SENTINEL, the project's fixed registers (r2/r13,
// GQRn: TcFacts.fixed). The run ends when the PC reaches SENTINEL. Leaving the body is a call when a call
// instruction (bl, bctrl, blrl) did it or the PC is another function's entry (a tail branch): the callee is not run;
// its record (target, r3..r10, f1..f8, the stack words at r1+8..r1+36) is logged, r3 and f1 get the vector's
// scripted results (r4 too after a callee returning r3:r4: TcCallFx), and execution continues at LR. CodeWarrior's
// register save/restore helpers (TcFlow.isSaveHelper)
// run inline: they only move registers to and from the frame, and the C code never calls them.
// A function that is itself a helper entry (selfHelper) runs its chain inline to the blr, and enters with a frame
// (frameOf, TcVectors.frame): r11 at a save area, r14..r31 and f14..f31 from the vector (vectors.txt X). Every other
// function enters with them 0, as before.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.pcode.memstate.DefaultMemoryState;
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.FlowType;
import java.math.BigInteger;
import java.util.*;

public abstract class TcOracle extends TcInputs {
    // A vector's scripted hardware words (TcBank.hw). Beside the Vec, not in it: Vec is each workspace's own TcInputs.java.
    static final Map<Vec, long[]> hwOf = new WeakHashMap<>();
    // A helper entry's frame: r11, r14..r31, f14..f31's bits (37 words; TcVectors.frame). Only while selfHelper.
    static final Map<Vec, long[]> frameOf = new WeakHashMap<>();
    // Scripted results for the calls after Vec.rets' (vectors.txt C after its 16 words; tc_rt_internal.h TCRT_RETS):
    // 0 unless TcVectors.repair made one a pointer (a run that faulted through it), so a vector without one runs as
    // before. Beside the Vec, as hwOf: each workspace's TcInputs.java keeps its 16. Calls past RETS return 0. RETS was
    // 64 until Skies' loops of 75 to 400 allocator calls (80197270, 80195f9c: genretptr, TcCallFx.grow) needed more.
    static final Map<Vec, long[]> lateOf = new WeakHashMap<>();
    static final int RETS = 1024;  // tc_rt_internal.h's TCRT_RETS must equal it
    static final int Q_CALLS = 64;  // the calls whose r4 line Q scripts (TcCallFx): RETS's old value, so Q is as before
    // A vector's scripted r4 words (TcCallFx.r4Words), beside the Vec as hwOf: what call k leaves in r4 after a callee
    // that sets r4 on every path. Without them (a vector made elsewhere) 0, clobber's value.
    static final Map<Vec, long[]> r4Of = new WeakHashMap<>();
    // A vector's callee writes {call, addr, size, value} (TcCallFx, vectors.txt K): only TcVectors.lastResort adds one.
    static final Map<Vec, List<long[]>> fxOf = new WeakHashMap<>();
    boolean selfHelper;                    // fn is a save/restore helper entry (TcFlow.isSaveHelper; findInline)
    final Map<Long, long[]> retPtr = new HashMap<>();  // genretptr hints: callee -> {OFF, PTR..} (TcVectors.retPtrHints)
    boolean growRets;                      // runs may set them (TcVectors.runGrow only)

    static class Result {
        boolean ok;
        String reason = "";
        long r3, r4;
        long f1;                          // f1's 64 bits at exit
        long msr = TcSystem.MSR_ENTRY;    // MSR at exit (TcVectors "MSR" when it is not the entry value)
        List<String> writes = new ArrayList<>();
        List<String> io = new ArrayList<>();  // stores into hardware registers, in order: "address size value" (TcBank)
        List<String> calls = new ArrayList<>();
        List<long[]> r4s = new ArrayList<>();  // {call, r4} where r4 was scripted (vectors.txt Q, TcCallFx)
        Map<Long, Integer> storedAt = new HashMap<>();  // word the run stored -> the calls made before its last store
        Set<Integer> cov = new TreeSet<>(), dirs = new TreeSet<>();  // blocks; branch directions (TcDirs)
        Set<Long> sites = new TreeSet<>();
        long faultAddr = -1;
        boolean faultWrite;
        boolean grew;                     // it set a genretptr result (TcCallFx.grow): TcVectors.runGrow runs it again
        List<long[]> loads = new ArrayList<>(); // the last LOAD_LOG data reads {pc, address, size, value}
        long outside = -1;
        boolean stepLimited;
        boolean hwReads;                  // it read a hardware register: the vector needs its H line
        TcTrack.Run tr;                   // pointer-call arguments, float NaN words (TcTrack)
        TcStrict strict;                  // the stricter judge's records (TcStrict), null with its flags off
        List<long[]> bctrs = new ArrayList<>();  // {pc, CTR} per bcctr executed in the body (TcSwitchHits; report only)
        Map<Long, long[]> loadAt = new HashMap<>();  // the first load per pc {pc, address, size, value} (TcSwitchPass)
    }

    static final int LOAD_LOG = 64;
    int stepLimit = STEP_LIMIT;  // lowered after repeated overruns (TcVectors)
    long workSteps, workRuns;    // the work so far: emulated steps and runs (TcBudget; oracle_summary.json "work")
    static final int CALL_LIMIT = 4096;  // tc_rt.c's MAX_CALLS must equal it

    TreeMap<Long, Integer> blockIndex = new TreeMap<>();
    TcDirs dirs;                           // null unless branch_coverage (TcDirs)
    AddressSet inline = new AddressSet();  // the save/restore helpers' bodies (run inline)
    TcFixups fix = new TcFixups();
    String refused = null;                 // TcFixups.refuse of an instruction a run executed

    BigInteger u(long v) {
        return BigInteger.valueOf(v & 0xffffffffL);
    }

    BigInteger u64(long v) {
        return new BigInteger(Long.toUnsignedString(v));
    }

    long reg(EmulatorHelper emu, String name) {
        return emu.readRegister(name).longValue() & 0xffffffffL;
    }

    long reg64(EmulatorHelper emu, String name) {
        return emu.readRegister(name).longValue();
    }

    String where(long off) {
        return facts.isMmio(off) ? " (hardware register)" : "";
    }

    // A helper's body, and its instructions from the entry to the blr (isSaveHelper's walk): Ghidra may split
    // CodeWarrior's chain (one stw/lwz per entry, each falling into the next) into one-instruction functions, and the
    // fall-through into the next entry is still the helper, not a call.
    void findInline() {
        inline = new AddressSet();
        List<Address> walks = new ArrayList<>();
        for (Function c : fn.getCalledFunctions(monitor)) {
            if (!TcFlow.isSaveHelper(listing, c.getEntryPoint())) continue;
            inline.add(c.getBody());
            walks.add(c.getEntryPoint());
        }
        selfHelper = TcFlow.isSaveHelper(listing, fn.getEntryPoint());
        if (selfHelper) walks.add(fn.getEntryPoint());  // its fall-through into the next entry is itself, not a call
        for (Address a : walks) {
            Instruction in = listing.getInstructionAt(a);
            for (int n = 0; in != null && n < 40; n++, in = in.getNext()) {
                inline.add(in.getMinAddress(), in.getMaxAddress());
                if (in.getMnemonicString().equalsIgnoreCase("blr")) break;
            }
        }
    }

    // A non-inline exit from the body is a call when a call instruction did it, or a branch reached a function entry.
    boolean entersCallee(long prev, Address pca) {
        Instruction last = prev < 0 ? null : listing.getInstructionAt(toAddr(prev));
        return (last != null && last.getFlowType().isCall()) || fm.getFunctionAt(pca) != null;
    }

    // CodeWarrior sets cr1[eq] (CR bit 6) before a call to a variadic callee: creqv 6,6,6 when float arguments are
    // in f1..f8, crxor 6,6,6 when there are none. A call with crxor the nearer of the two before it, with no call or
    // branch between (a Ghidra block runs on past a bl), passes no float arguments, so its f1..f8 are not arguments:
    // the CALL line ends with "nofp" (ppc_compare).
    static final int CRXOR_6 = 0x4cc63182, CREQV_6 = 0x4cc63242;

    boolean noFloatArgs(long site) {
        Instruction ins = listing.getInstructionAt(toAddr(site));
        while (ins != null && !blockIndex.containsKey(ins.getAddress().getOffset())) {
            ins = listing.getInstructionBefore(ins.getAddress());
            if (ins == null || !body.contains(ins.getAddress())) return false;
            FlowType flow = ins.getFlowType();
            if (flow.isCall() || flow.isJump() || flow.isTerminal()) return false;  // an earlier call's own setup
            byte[] b;
            try {
                b = ins.getBytes();
            } catch (Exception e) {
                return false;
            }
            int w = (b[0] & 0xff) << 24 | (b[1] & 0xff) << 16 | (b[2] & 0xff) << 8 | (b[3] & 0xff);
            if (w == CRXOR_6) return true;
            if (w == CREQV_6) return false;
        }
        return false;
    }

    // target, r3..r10, f1..f8 (64-bit), s0..s7 (the words at r1+8+4N): tc_rt.c logs the same 25 fields.
    // The callee at target sets r4 on every path (TcFlow.returnsR4): r4 after it is a result, not junk.
    boolean calleeSetsR4(long target) {
        Function g = currentProgram.getFunctionManager().getFunctionAt(toAddr(target));
        if (g == null) return false;
        if (flow == null) flow = new TcFlow(currentProgram, monitor);
        return flow.returnsR4(g);
    }

    String callRecord(EmulatorHelper emu, long pc) {
        StringBuilder c = new StringBuilder(hex(pc));
        for (int i = 3; i <= 10; i++) c.append(' ').append(hex(reg(emu, "r" + i)));
        for (int i = 1; i <= 8; i++) c.append(' ').append(hex64(reg64(emu, "f" + i)));
        long sp = fold(reg(emu, "r1"));
        for (int i = 0; i < 8; i++) {
            long a = sp + 8 + 4 * i, w = 0;
            if (a >= RAM_LO && a + 4 <= RAM_HI) {
                byte[] b = emu.readMemory(toAddr(a), 4);
                w = (b[0] & 0xffL) << 24 | (b[1] & 0xffL) << 16 | (b[2] & 0xffL) << 8 | (b[3] & 0xffL);
            }
            c.append(' ').append(hex(w));
        }
        return c.toString();
    }

    // Mocked call k's r3: Vec.rets[k], then lateOf's, then 0.
    static long ret(Vec v, int k) {
        long[] late = lateOf.get(v);
        int j = k - v.rets.length;
        return j < 0 ? v.rets[k] : late != null && j < late.length ? late[j] : 0;
    }

    // A mocked callee leaves 0 in every volatile register but its results (r3, f1), as TcFlow assumes it may clobber
    // them: a value the caller passes on after a call is then the C's 0, never the caller's own entry value.
    void clobber(EmulatorHelper emu) {
        emu.writeRegister("r0", u(0));
        for (int i = 4; i <= 12; i++) emu.writeRegister("r" + i, u(0));
        emu.writeRegister("f0", u64(0));
        for (int i = 2; i <= 13; i++) emu.writeRegister("f" + i, u64(0));
        emu.writeRegister("CTR", u(0));
    }

    void setEntry(EmulatorHelper emu, Vec v) {
        for (int i = 0; i < v.patchAddr.size(); i++) emu.writeMemory(toAddr(v.patchAddr.get(i)), v.patchData.get(i).clone());
        for (int i = 0; i < 8; i++) emu.writeRegister("r" + (3 + i), u(v.r[i]));
        for (int i = 0; i < 8; i++) emu.writeRegister("f" + (1 + i), u64(v.f[i]));
        for (Map.Entry<String, Long> e : facts.fixed.entrySet()) {
            String name = e.getKey().startsWith("gqr") ? e.getKey().toUpperCase() : e.getKey();
            emu.writeRegister(name, u(e.getValue()));
        }
        long[] fr = frameOf.get(v);
        if (fr != null) {  // a helper entry's frame (TcVectors.frame; vectors.txt X)
            emu.writeRegister("r11", u(fr[0]));
            for (int i = 0; i < 18; i++) emu.writeRegister("r" + (14 + i), u(fr[1 + i]));
            for (int i = 0; i < 18; i++) emu.writeRegister("f" + (14 + i), u64(fr[19 + i]));
        }
        emu.writeRegister(TcSystem.msr(emu), u(TcSystem.MSR_ENTRY));
        emu.writeRegister("r1", u(SP));
        emu.writeRegister("LR", u(SENTINEL));
        emu.writeRegister(emu.getPCRegister(), u(fn.getEntryPoint().getOffset()));
    }

    Result runOracle(Vec v) {
        Result res = new Result();
        workRuns++;
        res.tr = TcTrack.start();
        EmulatorHelper emu = new EmulatorHelper(currentProgram);
        TcCallOther.install(emu, fn.getEntryPoint().getAddressSpace());
        final String[] fault = {null};
        final long[] faultAt = {-1};
        final boolean[] faultW = {false};
        try {
            emu.setMemoryFaultHandler(new MemoryFaultHandler() {
                @Override
                public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) {
                    long off = fold(address.getOffset());
                    if (address.isMemoryAddress() && (off < RAM_LO || off + size > RAM_HI) && fault[0] == null) {
                        fault[0] = "read " + address + where(off);
                        faultAt[0] = off;
                    }
                    for (int i = 0; i < size; i++) buf[bufOffset + i] = 0;
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
            DefaultMemoryState dms = (DefaultMemoryState) emu.getEmulator().getMemState();
            AddressSpace ram = fn.getEntryPoint().getAddressSpace();
            TcBank bank = new TcBank(dms.getMemoryBank(ram), facts);
            dms.setMemoryBank(bank);
            bank.hw = hwOf.get(v);
            setEntry(emu, v);
            fix.invalid = null;
            bank.track = true;
            res.strict = TcStrict.of(this, bank);
            int steps = 0, calls = 0;
            long prev = -1;
            while (true) {
                long pc = emu.getExecutionAddress().getOffset();
                if (fold(pc) != pc) {
                    pc = fold(pc);
                    emu.writeRegister(emu.getPCRegister(), u(pc));
                }
                if (dirs != null) dirs.hit(res.dirs, prev, pc);  // the direction the branch at prev took
                if (pc == SENTINEL) break;
                Address pca = toAddr(pc);
                if (!body.contains(pca) && !inline.contains(pca)) {
                    if (!entersCallee(prev, pca)) {
                        res.reason = "executes code outside its body at " + hex(pc);
                        res.outside = pc;
                        return res;
                    }
                    if (calls >= CALL_LIMIT) {
                        res.reason = "more than " + CALL_LIMIT + " calls (a loop the mocked callees never end)";
                        return res;
                    }
                    res.calls.add(callRecord(emu, pc) + (prev >= 0 && noFloatArgs(prev) ? " nofp" : "") + TcTrack.call(this, pc));
                    if (prev >= 0) res.sites.add(prev);
                    int g = growRets ? TcCallFx.grow(this, v, calls, retPtr.get(pc)) : -1;  // genretptr
                    bank.track = g < 0;  // the new chunk goes in now, untracked; the rerun has it at entry
                    for (int i = Math.max(g, 0); g >= 0 && i < v.patchAddr.size(); i++) emu.writeMemory(toAddr(v.patchAddr.get(i)), v.patchData.get(i).clone());
                    bank.track = true;
                    res.grew |= g >= 0;
                    emu.writeRegister("r3", u(ret(v, calls)));
                    emu.writeRegister("f1", u64(calls < v.frets.length ? v.frets[calls] : 0));
                    if (!TcStrict.call(res, emu, calls)) clobber(emu);  // mock_junk: junk instead (TcStrict)
                    if (calls < Q_CALLS && calleeSetsR4(pc)) {  // r3:r4: the low word is scripted too (TcCallFx)
                        long w = TcCallFx.r4(r4Of.get(v), calls);
                        emu.writeRegister("r4", u(w));
                        res.r4s.add(new long[] {calls, w});
                    }
                    for (long[] e : fxOf.getOrDefault(v, List.of())) {  // callee writes (TcCallFx, vectors.txt K)
                        if (e[0] == calls) emu.writeMemory(toAddr(e[1]), TcCallFx.bytes(e));
                    }
                    calls++;
                    emu.writeRegister(emu.getPCRegister(), u(reg(emu, "LR")));
                    prev = -1;
                    continue;
                }
                Map.Entry<Long, Integer> b = blockIndex.floorEntry(pc);
                if (b != null && body.contains(pca)) res.cov.add(b.getValue());
                workSteps++;
                if (++steps > stepLimit) {
                    res.reason = "step limit (possible infinite loop)";
                    res.stepLimited = true;
                    return res;
                }
                Instruction in = listing.getInstructionAt(pca);
                String why = in == null ? null : TcFixups.refuse(in, facts.fixed);
                if (why != null) {
                    refused = why;
                    res.reason = "unsupported: " + why;
                    return res;
                }
                if (in != null && TcSwitchHits.isBcctr(in.getInt(0) & 0xffffffffL)) res.bctrs.add(new long[] {pc, reg(emu, "CTR")});
                fix.before(emu, in);
                bank.beginStep(pc);
                prev = pc;
                boolean stepped = emu.step(monitor);
                if (stepped && fault[0] == null) {
                    fix.after(emu, ram, bank, bank.stored, bank.reads);
                    TcTrack.step(emu, in, bank);
                }
                if (fix.invalid != null) {  // a dcbi of a block the run wrote (TcFixups)
                    res.reason = "invalid vector: " + fix.invalid;
                    return res;
                }
                for (long[] ld : bank.reads) {
                    res.loads.add(ld);
                    res.loadAt.putIfAbsent(ld[0], ld);
                    if (res.loads.size() > LOAD_LOG) res.loads.remove(0);
                }
                for (long[] st : bank.stores) {  // now, while the load log still holds the pointer's source (repairVec)
                    if ((st[0] < RAM_LO || st[0] + st[1] > RAM_HI) && !facts.isMmio(st[0])) {
                        res.reason = "write outside RAM";
                        res.faultAddr = st[0];
                        res.faultWrite = true;
                        return res;
                    }
                    if (!facts.isMmio(st[0])) res.storedAt.put(st[0] & ~3L, calls);  // TcVectors.lastResort
                }
                if (bank.codeRead >= 0) {  // rule R8: the C runner fails any such read
                    res.reason = "fault: read machine code at " + hex(bank.codeRead) + " (R8)";
                    res.faultAddr = bank.codeRead;
                    return res;
                }
                if (!stepped || fault[0] != null) {
                    res.reason = fault[0] != null ? "fault: " + fault[0] : "emulation error: " + emu.getLastError();
                    res.faultAddr = faultAt[0];
                    res.faultWrite = faultW[0];
                    return res;
                }
            }
            bank.pc = -1;
            if (bank.ioOverflow) {
                res.reason = "more than " + TcBank.IO_LIMIT + " hardware-register writes";
                return res;
            }
            for (long[] e : bank.io) {
                res.io.add(hex(e[0]) + " " + e[1] + " " + String.format("%0" + 2 * e[1] + "x", e[2]) + (e[3] != 0 ? " r" : ""));
                if (e[3] != 0) res.hwReads = true;
            }
            collect(emu, res, bank.written);
            TcStrict.exit(res, emu);
            return res;
        } catch (Exception e) {
            res.reason = "exception: " + e;
            return res;
        } finally {
            emu.dispose();
        }
    }

    // The observable results of a run that reached the sentinel; res.ok unless it wrote outside RAM.
    void collect(EmulatorHelper emu, Result res, AddressSet ws) {
        TcTrack.exit(emu);  // the float NaN words (TcTrack)
        res.r3 = reg(emu, "r3");
        res.r4 = reg(emu, "r4");
        res.f1 = reg64(emu, "f1");
        res.msr = reg(emu, TcSystem.msr(emu)) & 0xffffffffL;
        for (AddressRange rg : ws) {
            long lo = rg.getMinAddress().getOffset(), hi = rg.getMaxAddress().getOffset() + 1;
            if (lo < RAM_LO || hi > RAM_HI) {
                res.reason = "write outside RAM";
                res.faultAddr = lo;
                res.faultWrite = true;
                return;
            }
            long cutHi = Math.min(hi, STACK_LO);
            if (lo < cutHi) res.writes.add(hex(lo) + " " + bytesHex(emu.readMemory(toAddr(lo), (int) (cutHi - lo))));
        }
        res.ok = true;
    }
}

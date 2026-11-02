// The emulator oracle: runs the ORIGINAL code in Ghidra's p-code emulator (PowerPC:BE:64:64-32addr) on inputs from
// TcInputs, with the Xenon corrections of TcFixups. LOCKED: the watcher agent may not change this file. Extended by
// TcVectors.
//
// Entry state: r3..r10 and f1..f13 from the vector (64 bits each), r1 = SP, LR = SENTINEL, the project's fixed
// registers (r13: TcFacts.fixed). The run ends when the PC reaches SENTINEL. Leaving the body is a call when a call
// instruction (bl, bctrl, blrl) did it, the PC is another function's entry (a tail branch) or the jump is a tail call
// by TcRun.tailJump, and so is a call instruction's jump to the function's own entry (a self-call, TcBase.selfCall):
// the callee is not run;
// its record (target, r3..r10, f1..f13, the doublewords at r1+0x50+8N; x360_compare.py) is logged, r3 and f1 get the
// vector's scripted results, the other volatile registers the callee may write are zeroed (clobber), and execution
// continues at LR. The register save/restore helper families (TcBase.isHelper) run inline: they only move registers
// to and from the frame, and the C code never calls them.
//
// Vector instructions (VMX, VMX128): TcSetup replaced each by a stand-in Ghidra decodes (TcBase.VMX_MAP keeps the
// original word); the oracle runs the original in TcVmx instead of stepping the stand-in: the vector registers start
// at zero, a compare with Rc sets cr6, loads and stores go through the same memory bank (faults, R8, the written
// bytes). Mocked calls leave the vector registers alone (the C does too).
//
// Memory: the four regions (TcBase.mapped). Reads of the heap, stack and kernel regions start as zero; any access
// outside the regions faults, and so does a data read of an instruction byte (rule R8), as in the C runner.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.pcode.memstate.DefaultMemoryState;
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.math.BigInteger;
import java.util.*;

public abstract class TcOracle extends TcInputs {
    static class Result {
        boolean ok;
        String reason = "";
        long r3;                          // r3's 64 bits at exit
        long f1;                          // f1's 64 bits at exit
        List<String> writes = new ArrayList<>();
        List<String> calls = new ArrayList<>();
        Set<Integer> cov = new TreeSet<>();
        Set<Long> sites = new TreeSet<>();
        long faultAddr = -1;
        boolean faultWrite;
        List<long[]> loads = new ArrayList<>(); // the last LOAD_LOG data reads {pc, address, size, value}
        long outside = -1;
        boolean stepLimited;
        int steps;                        // of a run that reached the sentinel
        TcTrack.Run tr;                   // pointer-call arguments, float NaN words (TcTrack)

        // What the checker compares: equal results give equal expected.txt lines.
        String observable() {
            return hex64(r3) + " " + hex64(f1) + " " + writes + " " + calls;
        }
    }

    static final int LOAD_LOG = 64;
    static final int CALL_LIMIT = 4096;  // tc_rt.c's MAX_CALLS must equal it
    static final int CALL_FLOATS = 13;

    TreeMap<Long, Integer> blockIndex = new TreeMap<>();
    AddressSet inline = new AddressSet();  // the save/restore helpers' bodies (run inline)
    TcFixups fix = new TcFixups();
    String refused = null;                 // TcFixups.refuse of an instruction a run executed
    TcRun.StepBudget budget = new TcRun.StepBudget(STEP_LIMIT);

    BigInteger u64(long v) {
        return new BigInteger(Long.toUnsignedString(v));
    }

    long reg64(EmulatorHelper emu, String name) {
        return emu.readRegister(name).longValue();
    }

    long reg(EmulatorHelper emu, String name) {
        return reg64(emu, name) & 0xffffffffL;
    }

    String where(long off) {
        return facts.isMmio(off) ? " (hardware register)" : "";
    }

    // The helper families the body calls or branches to (an entry may be any label inside a family).
    void findInline() {
        inline = new AddressSet();
        for (Instruction in : listing.getInstructions(body, true)) {
            for (Address to : in.getFlows()) {
                Function g = fm.getFunctionContaining(to);
                if (isHelper(g)) inline.add(g.getBody());
            }
        }
    }

    // A non-inline exit from the body is a call when a call instruction did it, a branch reached a function entry, or
    // the jump is a tail call by TcRun.tailJump.
    boolean entersCallee(long prev, Address pca) {
        if (callAt(prev) || fm.getFunctionAt(pca) != null) return true;
        Instruction last = prev < 0 ? null : listing.getInstructionAt(toAddr(prev));
        return last != null && tailJump(last, pca);
    }

    boolean tailJump(Instruction in, Address to) {
        boolean into = false;
        for (Address a : in.getFlows()) into |= body.contains(a);
        return TcRun.tailJump(in.getFlowType(), into, to != null && fm.getFunctionContaining(to) != null);
    }

    // Did the instruction at prev (-1: none, the entry) transfer by a call?
    boolean callAt(long prev) {
        Instruction last = prev < 0 ? null : listing.getInstructionAt(toAddr(prev));
        return last != null && last.getFlowType().isCall();
    }

    // One vector instruction through TcVmx (the memory it touches goes through the emulator's bank, so faults, R8 and
    // the written bytes work as for any access). False when it could not run (vecError says why).
    String vecError = null;

    boolean stepVector(EmulatorHelper emu, TcVmx vm, TcVmx.Op op, long pc) {
        try {
            long[] gpr = new long[32];
            for (int g : op.gRead) gpr[g] = reg64(emu, "r" + g);
            TcVmx.Mem m = new TcVmx.Mem() {
                @Override
                public byte[] read(long ea, int n) {
                    return emu.readMemory(toAddr(ea & 0xffffffffL), n);
                }

                @Override
                public void write(long ea, byte[] data) {
                    emu.writeMemory(toAddr(ea & 0xffffffffL), data);
                }
            };
            int cr = vm.exec(op, gpr, m);
            if (cr >= 0) emu.writeRegister("cr6", BigInteger.valueOf(cr));
            emu.writeRegister(emu.getPCRegister(), u64(pc + 4));
            return true;
        } catch (RuntimeException e) {
            vecError = op + ": " + e;
            return false;
        }
    }

    // target, r3..r10, f1..f13, s0..s7 (the doublewords at r1+0x50+8N): tc_rt.c logs the same 30 fields.
    String callRecord(EmulatorHelper emu, long pc) {
        StringBuilder c = new StringBuilder(hex(pc));
        for (int i = 3; i <= 10; i++) c.append(' ').append(hex64(reg64(emu, "r" + i)));
        for (int i = 1; i <= CALL_FLOATS; i++) c.append(' ').append(hex64(reg64(emu, "f" + i)));
        long sp = reg(emu, "r1");
        for (int i = 0; i < 8; i++) {
            long a = sp + STACK_ARGS + 8 * i, w = 0;
            if (mapped(a, 8)) {
                byte[] b = emu.readMemory(toAddr(a), 8);
                for (byte x : b) w = w << 8 | (x & 0xffL);
            }
            c.append(' ').append(hex64(w));
        }
        return c.toString();
    }

    // A mocked callee leaves 0 in the volatile registers but its results (r3, f1) that the real callee may write
    // (TcFlow.clobbers, as TcFlow assumes): a value the caller passes on after a call is then the C's 0, never the
    // caller's own entry value. The ones the callee never writes keep their values, as on the Xenon (LTCG relies on
    // it). A call through a register or a tail branch zeroes them all. Both taken once per call site and target: a
    // loop of mocked calls (up to CALL_LIMIT a run) spent 3% of a prepare here (Too Human 82e97a88).
    final Map<Long, Boolean> directAt = new HashMap<>();
    final Map<Long, Set<String>> zeroedFor = new HashMap<>();  // by target; key -1: a call through a register
    Set<String> clobbered(long prev, Address pca) {
        boolean direct = prev >= 0 && directAt.computeIfAbsent(prev, p -> {
            Instruction last = listing.getInstructionAt(toAddr(p));
            return last != null && last.getFlowType().isCall() && !last.getFlowType().isComputed();
        });
        return zeroedFor.computeIfAbsent(direct ? pca.getOffset() : -1L, k -> {
            if (flow == null) flow = new TcFlow(currentProgram, monitor);
            Set<String> z = new HashSet<>(direct ? flow.clobbers(pca) : TcFlow.VOLATILE);
            z.remove("r3");
            z.remove("f1");
            return Collections.unmodifiableSet(z);
        });
    }

    void clobber(EmulatorHelper emu, Set<String> zeroed) {
        for (String r : zeroed) emu.writeRegister(r.equals("ctr") ? "CTR" : r, u64(0));
    }

    void setEntry(EmulatorHelper emu, Vec v) {
        for (int i = 0; i < v.patchAddr.size(); i++) emu.writeMemory(toAddr(v.patchAddr.get(i)), v.patchData.get(i).clone());
        for (int i = 0; i < 8; i++) emu.writeRegister("r" + (3 + i), u64(v.r[i]));
        for (int i = 0; i < CALL_FLOATS; i++) emu.writeRegister("f" + (1 + i), u64(v.f[i]));
        for (Map.Entry<String, Long> e : facts.fixed.entrySet()) emu.writeRegister(e.getKey(), u64(e.getValue()));
        emu.writeRegister("r1", u64(SP));
        emu.writeRegister("LR", u64(SENTINEL));
        emu.writeRegister(emu.getPCRegister(), u64(fn.getEntryPoint().getOffset()));
    }

    Result runOracle(Vec v) {
        Result res = new Result();
        res.tr = TcTrack.start();
        EmulatorHelper emu = new EmulatorHelper(currentProgram);
        fix.install(emu);
        fix.trapped = false;
        final String[] fault = {null};
        final long[] faultAt = {-1};
        final boolean[] faultW = {false};
        try {
            emu.setMemoryFaultHandler(new MemoryFaultHandler() {
                @Override
                public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) {
                    if (TcBank.probing) return false;
                    long off = fold(address.getOffset());
                    if (address.isMemoryAddress() && !mapped(off, size) && fault[0] == null) {
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
            setEntry(emu, v);
            bank.track = true;
            TcVmx vm = new TcVmx();
            vecError = null;
            int steps = 0, calls = 0;
            long prev = -1;
            while (true) {
                long pc = emu.getExecutionAddress().getOffset() & 0xffffffffL;
                if (pc == SENTINEL) break;
                Address pca = toAddr(pc);
                boolean self = pca.equals(fn.getEntryPoint()) && callAt(prev);
                if (self || !body.contains(pca) && !inline.contains(pca)) {
                    // The body's last instruction is a call that returned here: on the Xenon it never returns (82096680,
                    // main, ends in the call of the game's fatal exit). The C goes on past the body's end by a tail call
                    // of the next address (translate.py, fall), so the run ends with the same call.
                    boolean past = !self && TcRun.pastEnd(prev, calls, body.contains(toAddr(pc - 4)));
                    if (!self && !past && !entersCallee(prev, pca)) {
                        if (prev < 0 && calls > 0) {  // a mocked call "returned" to an address the code put in LR, outside the
                            // body: this input is invalid, but the body is not incomplete (res.outside stays unset: it
                            // would refuse the whole function)
                            res.reason = "returns from a call into code outside its body at " + hex(pc);
                            return res;
                        }
                        res.reason = "executes code outside its body at " + hex(pc);
                        res.outside = pc;
                        return res;
                    }
                    if (calls >= CALL_LIMIT) {
                        res.reason = "more than " + CALL_LIMIT + " calls (a loop the mocked callees never end)";
                        return res;
                    }
                    Set<String> zeroed = clobbered(prev, pca);
                    res.calls.add(callRecord(emu, pc) + TcTrack.call(pc, zeroed));
                    if (prev >= 0) res.sites.add(prev);
                    emu.writeRegister("r3", u64(calls < 16 ? v.rets[calls] : 0));
                    emu.writeRegister("f1", u64(calls < 16 ? v.frets[calls] : 0));
                    clobber(emu, zeroed);
                    calls++;
                    if (past) break;
                    emu.writeRegister(emu.getPCRegister(), u64(reg(emu, "LR")));
                    prev = -1;
                    continue;
                }
                Map.Entry<Long, Integer> b = blockIndex.floorEntry(pc);
                if (b != null && body.contains(pca)) res.cov.add(b.getValue());
                if (++steps > budget.limit) {
                    res.reason = "step limit (possible infinite loop)";
                    res.stepLimited = true;
                    return res;
                }
                Instruction in = listing.getInstructionAt(pca);
                String why = in == null ? "no instruction at " + hex(pc) : TcFixups.refuse(in);
                if (why != null) {
                    refused = why;
                    res.reason = "unsupported: " + why;
                    return res;
                }
                TcVmx.Op vop = vmxOp(pc);
                boolean stepped;
                if (vop != null) {
                    bank.beginStep(pc);
                    prev = pc;
                    stepped = stepVector(emu, vm, vop, pc);
                } else {
                    fix.before(emu, in);
                    bank.beginStep(pc);
                    prev = pc;
                    stepped = emu.step(monitor);
                    if (stepped && fault[0] == null) {
                        fix.after(emu, ram, bank.stored, bank.reads);
                        TcTrack.step(emu, in, bank);
                    }
                }
                // The load log is for pointer repair, and a frame helper's restores are no input's pointer: a write fault
                // known only at exit (collect) spent all its repairs on them, each zero value being "near" address 0
                // (821ca768: 71 of 161 tries).
                if (!TcRun.logsLoads(inline.contains(pca))) bank.reads.clear();
                for (long[] ld : bank.reads) {
                    res.loads.add(ld);
                    if (res.loads.size() > LOAD_LOG) res.loads.remove(0);
                }
                if (fix.trapped) {
                    res.reason = "trap taken at " + hex(pc) + " (the original stops here)";
                    return res;
                }
                if (bank.codeRead >= 0) {  // rule R8: the C runner fails any such read
                    res.reason = "fault: read machine code at " + hex(bank.codeRead) + " (R8)";
                    res.faultAddr = bank.codeRead;
                    return res;
                }
                if (!stepped || fault[0] != null) {
                    res.reason = fault[0] != null ? "fault: " + fault[0]
                        : vecError != null ? "emulation error: " + vecError : "emulation error: " + emu.getLastError();
                    res.faultAddr = faultAt[0];
                    res.faultWrite = faultW[0];
                    return res;
                }
            }
            bank.pc = -1;
            res.steps = steps;
            collect(emu, res, bank.written);
            return res;
        } catch (Exception e) {
            res.reason = "exception: " + e;
            return res;
        } finally {
            emu.dispose();
        }
    }

    // The observable results of a run that reached the sentinel; res.ok unless it wrote outside the regions. Writes
    // to the stack region are not compared (the frame is the C compiler's own), as in the runner.
    void collect(EmulatorHelper emu, Result res, AddressSet ws) {
        TcTrack.exit(emu);  // the float NaN words (TcTrack)
        res.r3 = reg64(emu, "r3");
        res.f1 = reg64(emu, "f1");
        for (AddressRange rg : ws) {
            long lo = rg.getMinAddress().getOffset(), hi = rg.getMaxAddress().getOffset() + 1;
            if (!mapped(lo, hi - lo)) {
                res.reason = "write outside RAM";
                res.faultAddr = lo;
                res.faultWrite = true;
                return;
            }
            long[][] parts = {{lo, Math.min(hi, STACK_LO)}, {Math.max(lo, STACK_HI), hi}};
            for (long[] p : parts) {
                if (p[0] < p[1]) res.writes.add(hex(p[0]) + " " + bytesHex(emu.readMemory(toAddr(p[0]), (int) (p[1] - p[0]))));
            }
        }
        res.ok = true;
    }
}

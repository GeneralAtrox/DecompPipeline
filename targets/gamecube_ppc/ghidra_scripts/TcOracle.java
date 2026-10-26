// The emulator oracle: runs the ORIGINAL code in Ghidra's p-code emulator (PowerPC:BE:32:Gekko_Broadway) on inputs
// from TcInputs, with the 750CL corrections of TcFixups. LOCKED: the watcher agent may not change this file.
// Extended by TcVectors.
//
// Entry state: r3..r10 and f1..f8 from the vector, r1 = SP, LR = SENTINEL, the project's fixed registers (r2/r13,
// GQRn: TcFacts.fixed). The run ends when the PC reaches SENTINEL. Leaving the body is a call when a call
// instruction (bl, bctrl, blrl) did it or the PC is another function's entry (a tail branch): the callee is not run;
// its record (target, r3..r10, f1..f8, the stack words at r1+8..r1+36) is logged, r3 and f1 get the vector's
// scripted results, and execution continues at LR. CodeWarrior's register save/restore helpers (TcFlow.isSaveHelper)
// run inline: they only move registers to and from the frame, and the C code never calls them.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.pcode.memstate.DefaultMemoryState;
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.FlowType;
import java.math.BigInteger;
import java.util.*;

public abstract class TcOracle extends TcInputs {
    static class Result {
        boolean ok;
        String reason = "";
        long r3, r4;
        long f1;                          // f1's 64 bits at exit
        List<String> writes = new ArrayList<>();
        List<String> calls = new ArrayList<>();
        Set<Integer> cov = new TreeSet<>(), dirs = new TreeSet<>();  // blocks; branch directions (TcDirs)
        Set<Long> sites = new TreeSet<>();
        long faultAddr = -1;
        boolean faultWrite;
        List<long[]> loads = new ArrayList<>(); // the last LOAD_LOG data reads {pc, address, size, value}
        long outside = -1;
        boolean stepLimited;
        TcTrack.Run tr;                   // pointer-call arguments, float NaN words (TcTrack)
        TcStrict strict;                  // the stricter judge's records (TcStrict), null with its flags off
    }

    static final int LOAD_LOG = 64;
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
        for (Function c : fn.getCalledFunctions(monitor)) {
            if (!TcFlow.isSaveHelper(listing, c.getEntryPoint())) continue;
            inline.add(c.getBody());
            Instruction in = listing.getInstructionAt(c.getEntryPoint());
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
        emu.writeRegister("r1", u(SP));
        emu.writeRegister("LR", u(SENTINEL));
        emu.writeRegister(emu.getPCRegister(), u(fn.getEntryPoint().getOffset()));
    }

    Result runOracle(Vec v) {
        Result res = new Result();
        res.tr = TcTrack.start();
        EmulatorHelper emu = new EmulatorHelper(currentProgram);
        TcFixups.install(emu);
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
            setEntry(emu, v);
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
                    res.calls.add(callRecord(emu, pc) + (prev >= 0 && noFloatArgs(prev) ? " nofp" : "") + TcTrack.call(pc));
                    if (prev >= 0) res.sites.add(prev);
                    emu.writeRegister("r3", u(calls < 16 ? v.rets[calls] : 0));
                    emu.writeRegister("f1", u64(calls < 16 ? v.frets[calls] : 0));
                    if (!TcStrict.call(res, emu, calls)) clobber(emu);  // mock_junk: junk instead (TcStrict)
                    calls++;
                    emu.writeRegister(emu.getPCRegister(), u(reg(emu, "LR")));
                    prev = -1;
                    continue;
                }
                Map.Entry<Long, Integer> b = blockIndex.floorEntry(pc);
                if (b != null && body.contains(pca)) res.cov.add(b.getValue());
                if (++steps > STEP_LIMIT) {
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
                fix.before(emu, in);
                bank.beginStep(pc);
                prev = pc;
                boolean stepped = emu.step(monitor);
                if (stepped && fault[0] == null) {
                    fix.after(emu, ram, bank.stored, bank.reads);
                    TcTrack.step(emu, in, bank);
                }
                for (long[] ld : bank.reads) {
                    res.loads.add(ld);
                    if (res.loads.size() > LOAD_LOG) res.loads.remove(0);
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

// The end of an oracle run (for TcOracle). Not a script itself.
//
// collect: the observable results of a run that reached the sentinel, or halted; res.ok unless it wrote outside RAM.
//
// halts: an endless `bra .` (a branch to itself with a `nop` in its delay slot) ends the run as a halt. Spawn
// 8c0502d0 and 8c058760 are only that; 8c050250 shows an error colour, calls 8c0d9016 and runs into 8c0502d0. Such
// code never returns, so every vector used to run to the step limit and the function was refused. Now the run stops
// there: expected.txt gets a HALT line (TcVectors.expected), the return value is not compared (core/compare.py), and
// everything else the run did (writes, calls, state at that point) is. A callee that halts (its entry is the `bra .`)
// is recorded as a call and then halts the run. The C version ends that path with tc_halt() (tc_rt.h), after the
// tc_call when a callee halts; the runner reports HALT for it.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;

public class TcExit {
    static boolean halts(Listing listing, Address pc) {
        Instruction in = listing.getInstructionAt(pc);
        if (in == null || !in.getMnemonicString().equalsIgnoreCase("bra")) return false;
        Address[] to = in.getFlows();
        Instruction slot = listing.getInstructionAt(in.getMaxAddress().next());
        return to.length == 1 && to[0].equals(pc) && slot != null
            && slot.toString().toLowerCase().replaceFirst("^_", "").equals("nop");
    }

    static void collect(TcOracle o, EmulatorHelper emu, TcOracle.Result res) {
        res.r0 = o.reg(emu, "r0");
        res.fr0 = (int) o.reg(emu, "fr0");
        if (o.entryRet != null) res.retx = o.reg(emu, o.entryRet);
        for (int i = 0; i < 16; i++) res.xf[i] = (int) o.reg(emu, "xf" + i);
        for (int i = 0; i < 4; i++) res.fh[i] = (int) o.reg(emu, "fr" + (12 + i));
        for (int i = 0; i < 7; i++) res.gi[i] = o.reg(emu, "r" + (8 + i));
        for (int i = 0; i < 16; i++) res.fq[i] = (int) o.reg(emu, "fr" + i);
        res.mode = o.modeWord(emu);
        AddressSpace space = o.fn.getEntryPoint().getAddressSpace();
        AddressSet ws = new AddressSet();
        for (AddressRange rg : emu.getTrackedMemoryWriteSet()) {
            if (rg.getMinAddress().getAddressSpace() != space) continue;
            long flo = TcBase.fold(rg.getMinAddress().getOffset());
            ws.add(o.toAddr(flo), o.toAddr(flo + rg.getLength() - 1));
        }
        for (AddressRange rg : ws) {
            long lo = rg.getMinAddress().getOffset(), hi = rg.getMaxAddress().getOffset() + 1;
            if (rg.getMinAddress().getAddressSpace() != space) continue;
            if (lo >= TcBase.P4_OCA_LO && hi <= TcBase.P4_OCA_HI) { // cache address array stores: logged (tc_p4.c)
                res.writes.add(TcBase.hex(lo) + " " + TcBase.bytesHex(emu.readMemory(o.toAddr(lo), (int) (hi - lo))));
                continue;
            }
            if (lo < TcBase.RAM_LO || hi > TcBase.RAM_HI) {
                res.reason = "write outside RAM";
                res.faultAddr = lo;
                res.faultWrite = true;
                return;
            }
            long cutHi = Math.min(hi, TcBase.STACK_LO);
            if (lo < cutHi) {
                byte[] data = emu.readMemory(o.toAddr(lo), (int) (cutHi - lo));
                res.writes.add(TcBase.hex(lo) + " " + TcBase.bytesHex(data));
            }
        }
        res.ok = true;
    }
}

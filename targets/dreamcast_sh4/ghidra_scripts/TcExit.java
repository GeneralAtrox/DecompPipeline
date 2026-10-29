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
// tc_call when a callee halts; the runner reports HALT for it. A wait loop the run never leaves (TcSpin) ends the
// same way, as HALT SPIN (tc_spin() in C).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.util.*;

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
        for (int i = 0; i < 4; i++) res.ro[i] = o.reg(emu, "r" + (4 + i));  // TcOut
        res.ro[4] = o.reg(emu, "T");  // TcTflag
        for (int i = 0; i < 16; i++) res.fq[i] = (int) o.reg(emu, "fr" + i);
        res.mode = o.modeWord(emu);
        if (o.sr != null) res.imask = TcSr.exitMask(emu);  // SR.IMASK, compared (TcSr)
        if (TcHw.on != null && TcHw.on.gbr) res.gbr = o.reg(emu, "GBR");  // GBR, compared (TcHw)
        TcFpscr.exit(emu); TcTrack.exit(emu);  // FPSCR, compared when it is state (TcFpscr); float NaN words (TcTrack)
        AddressSpace space = o.fn.getEntryPoint().getAddressSpace();
        AddressSet ws = new AddressSet();
        for (AddressRange rg : emu.getTrackedMemoryWriteSet()) {
            if (rg.getMinAddress().getAddressSpace() != space) continue;
            long flo = TcBase.fold(rg.getMinAddress().getOffset());
            ws.add(o.toAddr(flo), o.toAddr(flo + rg.getLength() - 1));
        }
        if (o.sq != null) ws.add(o.sq.sent);  // store-queue sends, written between steps (untracked): TcSq
        for (AddressRange rg : ws) {
            long lo = rg.getMinAddress().getOffset(), hi = rg.getMaxAddress().getOffset() + 1;
            if (rg.getMinAddress().getAddressSpace() != space) continue;
            if (p4(lo, hi)) { // cache address array and QACR stores: logged (tc_p4.c)
                res.writes.add(TcBase.hex(lo) + " " + TcBase.bytesHex(emu.readMemory(o.toAddr(lo), (int) (hi - lo))));
                continue;
            }
            if (lo < TcBase.RAM_LO || hi > TcBase.RAM_HI) {
                res.reason = "write outside RAM";
                res.faultAddr = lo;
                res.faultWrite = true;
                return;
            }
            for (long[] s : logged(lo, hi, TcBase.frameLo, o.namedWords)) {
                byte[] data = emu.readMemory(o.toAddr(s[0]), (int) (s[1] - s[0]));
                res.writes.add(TcBase.hex(s[0]) + " " + TcBase.bytesHex(data));
            }
        }
        res.ok = true;
    }

    // A written range of P4 registers logged as W lines: in the operand-cache address array, or in QACR0/1; or a
    // listed hardware register's (TcHw.written).
    static boolean p4(long lo, long hi) {
        return lo >= TcBase.P4_OCA_LO && hi <= TcBase.P4_OCA_HI || lo >= TcBase.P4_QACR_LO && hi <= TcBase.P4_QACR_HI
            || TcHw.written(lo, hi);
    }

    // The logged parts of a written RAM range [lo, hi): everything below the own frame (frameLo: the frame and the
    // stack above it are the function's scratch; [STACK_LO, frameLo) is memory, Spawn's save data at 0x8cff9000), and
    // above it the words of named globals (meta.json "globals"). core/compare.judged compares the same.
    static List<long[]> logged(long lo, long hi, long frameLo, Set<Long> named) {
        List<long[]> out = new ArrayList<>();
        if (lo < Math.min(hi, frameLo)) out.add(new long[] {lo, Math.min(hi, frameLo)});
        long from = Math.max(lo, frameLo);
        for (long w = from & ~3L; w < hi; w += 4) {
            if (!named.contains(w)) continue;
            long a = Math.max(w, from), b = Math.min(w + 4, hi);
            long[] last = out.isEmpty() ? null : out.get(out.size() - 1);
            if (last != null && last[1] == a) last[1] = b;
            else out.add(new long[] {a, b});
        }
        return out;
    }
}

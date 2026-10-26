// The oracle's view of the data reads a step makes. Not a script itself; used by TcOracle.
//
// (1) The load log for pointer repair (TcInputs.repairVec): every mov.b/w/l load, {pc, address, size, value}.
// (2) Rule R8 on the oracle side: the C runner fails any read whose first byte is an instruction byte (state/
// code_ranges.txt), so a run of the original that reads machine code through an input pointer is no usable test:
// it is stopped like a fault at that address, and the pointer that led there is repaired or the vector rejected.
// The rule is the runner's exactly: data reads only (not literal-pool loads, which C code never makes, nor fetches),
// first byte only, after folding the P0 mirror. Every instruction with a p-code LOAD is checked on all its memory
// operands except pc-relative ones: mov/fmov/lds.l/ldc.l/mac/tas.b and the @(r0,gbr) read-modify-writes.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import java.util.*;
import java.util.regex.*;

public class TcLoads {
    static final Pattern LOAD_RE = Pattern.compile(
        "^mov\\.([bwl])\\s+@(?:\\(([^,]+),([^)]+)\\)|(r\\d+)\\+?)\\s*,\\s*(r\\d+)$");
    static final Pattern MEM_RE = Pattern.compile("@(?:\\(([^,()]+),([^,()]+)\\)|(r\\d+)\\+?)");

    // A memory operand: base + index, each a register name or a constant.
    static class Operand {
        String regA, regB;
        long constA, constB;
    }

    // A mov.b/w/l load into a register (the repair log).
    static class LoadSpec extends Operand {
        long pc, size;
        String dest;
    }

    final Listing listing;
    final TcFacts facts;
    final Map<Long, List<LoadSpec>> stepLoads = new HashMap<>();
    final Map<Long, List<Operand>> stepReads = new HashMap<>();
    final Map<Long, List<Operand>> stepWrites = new HashMap<>();

    TcLoads(Listing listing, TcFacts facts) {
        this.listing = listing;
        this.facts = facts;
    }

    // The instructions one step executes: the instruction and, after a delayed branch, its delay slot.
    List<Instruction> stepInstructions(Address pca) {
        List<Instruction> out = new ArrayList<>();
        Instruction in = listing.getInstructionAt(pca);
        if (in == null) return out;
        out.add(in);
        if (in.getDelaySlotDepth() > 0) {
            Instruction slot = listing.getInstructionAfter(in.getAddress());
            if (slot != null) out.add(slot);
        }
        return out;
    }

    static String text(Instruction in) {
        return in.toString().toLowerCase().replaceFirst("^_", "");
    }

    static void operand(Operand o, String a, String b) {
        if (a.matches("r\\d+|gbr")) o.regA = a.equals("gbr") ? "GBR" : a;
        else o.constA = Long.decode(a);
        if (b != null && b.matches("r\\d+|gbr")) o.regB = b.equals("gbr") ? "GBR" : b;
        else if (b != null) o.constB = Long.decode(b);
    }

    // Per step address: the loads that step executes (the instruction and its delay slot). Decoded once.
    List<LoadSpec> loadsAt(Address pca) {
        List<LoadSpec> out = stepLoads.get(pca.getOffset());
        if (out != null) return out;
        out = new ArrayList<>();
        for (Instruction in : stepInstructions(pca)) {
            Matcher m = LOAD_RE.matcher(text(in));
            if (!m.matches() || "pc".equals(m.group(3))) continue; // a pool load (`@(0x...,pc)` on the SH-2) is text
            LoadSpec ls = new LoadSpec();
            ls.pc = in.getAddress().getOffset();
            ls.size = m.group(1).equals("b") ? 1 : m.group(1).equals("w") ? 2 : 4;
            ls.dest = m.group(5);
            operand(ls, m.group(4) != null ? m.group(4) : m.group(2), m.group(4) != null ? null : m.group(3));
            out.add(ls);
        }
        stepLoads.put(pca.getOffset(), out);
        return out;
    }

    // Per step address: the memory operands its reading instructions read from (pc-relative ones left out).
    List<Operand> readsAt(Address pca) {
        List<Operand> out = stepReads.get(pca.getOffset());
        if (out != null) return out;
        out = new ArrayList<>();
        for (Instruction in : stepInstructions(pca)) {
            // A delayed branch reads no memory itself: `jsr @r1` names its target, and Ghidra folds the delay slot's
            // p-code (with the slot's LOAD) into the branch's. The slot is checked as an instruction of its own.
            if (in.getDelaySlotDepth() > 0) continue;
            boolean loads = false;
            for (PcodeOp op : in.getPcode()) loads |= op.getOpcode() == PcodeOp.LOAD;
            if (!loads || text(in).matches("(pref|ocb\\w*|movca\\.l)\\s.*")) continue; // cache operations read nothing
            Matcher m = MEM_RE.matcher(text(in));
            while (m.find()) {
                if (m.group(0).contains("pc")) continue;
                Operand o = new Operand();
                operand(o, m.group(3) != null ? m.group(3) : m.group(1), m.group(3) != null ? null : m.group(2));
                out.add(o);
            }
        }
        stepReads.put(pca.getOffset(), out);
        return out;
    }

    // Per step address: the memory operands its storing instructions write to (batch 29, TcOracle.storeFaults). Left
    // out: pc-relative operands and pushes (@-rN, which MEM_RE does not match: below r15, inside RAM while r15 is).
    List<Operand> writesAt(Address pca) {
        List<Operand> out = stepWrites.get(pca.getOffset());
        if (out != null) return out;
        out = new ArrayList<>();
        for (Instruction in : stepInstructions(pca)) {
            if (in.getDelaySlotDepth() > 0) continue;  // as readsAt: the slot is an instruction of its own
            boolean stores = false;
            for (PcodeOp op : in.getPcode()) stores |= op.getOpcode() == PcodeOp.STORE;
            if (!stores || text(in).matches("(pref|ocb\\w*)\\s.*")) continue;
            Matcher m = MEM_RE.matcher(text(in));
            while (m.find()) {
                if (m.group(0).contains("pc")) continue;
                Operand o = new Operand();
                operand(o, m.group(3) != null ? m.group(3) : m.group(1), m.group(3) != null ? null : m.group(2));
                out.add(o);
            }
        }
        stepWrites.put(pca.getOffset(), out);
        return out;
    }

    // The first address outside RAM the step at pca is about to store to (cache address array stores and listed
    // hardware registers aside, which TcOracle.collect logs: TcHw), or -1; base[0] gets the operand's base register value (the register other than r0
    // in @(r0,rN)).
    long storeOutside(EmulatorHelper emu, Address pca, long[] base) {
        for (Operand o : writesAt(pca)) {
            long ea = address(emu, o);
            if (TcBase.inRam(ea, 1) || ea >= TcBase.P4_OCA_LO && ea < TcBase.P4_OCA_HI) continue;
            if (TcHw.written(ea, ea + 1)) continue;
            String b = o.regB != null && "r0".equals(o.regA) ? o.regB : o.regA;
            base[0] = b != null ? reg(emu, b) : -1;
            return ea;
        }
        return -1;
    }

    static long reg(EmulatorHelper emu, String name) {
        return emu.readRegister(name).longValue() & 0xffffffffL;
    }

    static long address(EmulatorHelper emu, Operand o) {
        long ea = (o.regA != null ? reg(emu, o.regA) : o.constA) + (o.regB != null ? reg(emu, o.regB) : o.constB);
        return TcBase.fold(ea);
    }

    // {pc, effective address, size, 0, 0}; the value is filled in after the step from the destination register, the
    // number of calls made before the load by TcOracle (TcProvenance).
    long[] loadBefore(EmulatorHelper emu, LoadSpec ls) {
        return new long[] {ls.pc, address(emu, ls), ls.size, 0, 0};
    }

    // The first instruction byte the step at pca is about to read as data (R8), or -1.
    long codeRead(EmulatorHelper emu, Address pca) {
        if (!facts.haveCode) return -1;
        for (Operand o : readsAt(pca)) {
            long ea = address(emu, o);
            if (facts.isCode(ea)) return ea;
        }
        return -1;
    }
}

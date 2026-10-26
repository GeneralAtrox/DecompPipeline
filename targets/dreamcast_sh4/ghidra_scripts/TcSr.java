// The status register, for a function that reads or writes SR itself (`stc sr,Rn`, `ldc Rm,sr` and their .l forms):
// Spawn's serial driver (8c05bbe4..8c05c338) raises the interrupt mask to 15 around its buffer updates and puts the
// saved SR back. Not a script itself; TcPrepare finds the fact (of), TcOracle sets SR at entry and checks each step,
// TcExit reads the mask at exit, TcVectors writes the lines.
//
// At entry SR is MD=1 (privileged, as the game runs), RB=0, BL=0, IMASK from the vector, the rest as before (T 0). The
// mask is the vector's own (imask: a hash of r4..r7 and fr4, so a repair or a reach re-run keeps it with its inputs):
// vectors.txt line "I mask", which the C runners load into their SR word. An `ldc` that sets RB (the other register
// bank) or BL (exceptions blocked) refuses the run: the C has no banks and no exceptions (BL is allowed where the
// project lists it: TcHw, project.json "hw_ops"). At exit the mask is compared
// (expected.txt "IM mask"); the C reads and writes SR with sh4_stc_sr() and sh4_ldc_sr(v) (tc_rt.h).
// The stock SLEIGH's `ldc Rm,SR` unpacks SR into its field registers (T, IMASK...) before it assigns SR, so the fields
// kept the old value and the next `stc sr` (which packs the fields) lost the new one: after any step that writes SR the
// fields are unpacked here from SR again. A function without an SR access gets none of this (its harness unchanged).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.math.BigInteger;
import java.util.*;

final class TcSr {
    static final long MASK = 0x700083f3L;  // the SR bits an ldc writes
    static final String[] FIELD = {"T", "S", "IMASK", "Q", "M", "FD", "BL", "RB", "MD"};
    static final int[] SHIFT = {0, 1, 4, 8, 9, 15, 28, 29, 30};
    static final int[] WIDTH = {1, 1, 4, 1, 1, 1, 1, 1, 1};

    final Set<Long> writes = new HashSet<>();  // the steps that write SR (an ldc, or a branch whose delay slot is one)

    // "stc sr" or "ldc sr" (sh4_special: agent.py's helper), for an instruction reading or writing SR, else null.
    static String op(Instruction in) {
        String m = in.getMnemonicString().toLowerCase().replace("_", "");
        if (!m.matches("(ldc|stc)(\\.l)?")) return null;
        String t = in.toString().toLowerCase().replace("_", "");
        String[] ops = t.substring(t.indexOf(' ') + 1).split(",");
        String cr = m.startsWith("ldc") ? ops[ops.length - 1].trim() : ops[0].trim();
        return cr.equals("sr") ? m.substring(0, 3) + " sr" : null;
    }

    // The fact: the body reads or writes SR. Otherwise null.
    static TcSr of(Listing listing, AddressSetView body) {
        TcSr s = null;
        for (Instruction in : listing.getInstructions(body, true)) {
            String o = op(in);
            if (o == null) continue;
            if (s == null) s = new TcSr();
            Address step = in.isInDelaySlot() ? in.getFallFrom() : in.getAddress();  // a slot: its branch's step
            if (o.equals("ldc sr")) s.writes.add(step.getOffset());
        }
        return s;
    }

    static int imask(TcInputs.Vec v) {
        long h = 0x9e3779b97f4a7c15L ^ (v.fr.length > 0 ? v.fr[0] : 0);
        for (long x : v.r) h = (h ^ x) * 0xff51afd7ed558ccdL;
        return (int) (h >>> 60);
    }

    void enter(EmulatorHelper emu, TcInputs.Vec v) {
        long sr = reg(emu, "SR") & ~(0xfL << 4 | 1L << 28 | 1L << 29) | 1L << 30 | (long) imask(v) << 4;
        emu.writeRegister("SR", BigInteger.valueOf(sr));
        split(emu, sr & ~1L | reg(emu, "T") & 1);  // T stays as it was
    }

    // After the step at pc: null, or why the run is refused.
    String after(EmulatorHelper emu, long pc) {
        if (!writes.contains(pc)) return null;
        long sr = reg(emu, "SR") & MASK;
        split(emu, sr);
        if ((sr >> 28 & (TcHw.on != null && TcHw.on.bl ? 2 : 3)) != 0) {  // BL: when listed (TcHw)
            return "ldc sets SR.RB or SR.BL at " + TcBase.hex(pc) + " (no banks or blocked exceptions in C)";
        }
        return null;
    }

    static int exitMask(EmulatorHelper emu) {
        return (int) (reg(emu, "IMASK") & 15);
    }

    static void split(EmulatorHelper emu, long sr) {
        for (int i = 0; i < FIELD.length; i++) {
            emu.writeRegister(FIELD[i], BigInteger.valueOf(sr >> SHIFT[i] & ((1L << WIDTH[i]) - 1)));
        }
    }

    static long reg(EmulatorHelper emu, String name) {
        return emu.readRegister(name).longValue();
    }

    // vectors.txt line I, or "" for a function without the fact (TcVectors.writeVector).
    static String line(TcSr s, TcInputs.Vec v) {
        return s == null ? "" : "\nI " + Integer.toHexString(imask(v));
    }

    // expected.txt line IM, or "" (TcVectors.writeVector; Result.imask is -1 without the fact).
    static String exit(int imask) {
        return imask < 0 ? "" : "IM " + Integer.toHexString(imask) + "\n";
    }
}

// 64-bit fmov pairs as the SH-4 moves them (TcFacts.fpuState; TcOracle runs it around each step). Not a script.
//
// With FPSCR.SZ = 1 every fmov moves a register pair: an even register field n names DRn (frN, frN+1), an odd one
// XD(n-1) (xfN-1, xfN). The hardware keeps the even register at the lower address (fmov DR0,@r4: fr0 at r4, fr1 at
// r4+4; the same for XD), and a register move pairs naturally (fmov DR0,XD12: xf12 = fr0, xf13 = fr1). The SH-4
// matrix library (Toy Commander 8c061500/8c061520 push and pop XMTRX with XD moves, 8c061560 swaps matrices with DR
// moves) is only consistent that way. Ghidra's little-endian register file stores each FR pair swapped (dr0 is fr1
// in its low word, fr0 in its high word) while XD pairs are not, so its 64-bit moves of a DR put fr1 first and
// cross the pair in a DR-XD move. The fix is local: before a step that moves a DR under SZ = 1, the two halves of
// each DR operand are exchanged, and after it exchanged back; single moves (SZ = 0) and XD operands are untouched.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.listing.Instruction;
import java.math.BigInteger;
import java.util.*;

public class TcPair {
    // The even register numbers of the DR operands of the fmov instructions in one step (the instruction and its
    // delay slot), when FPSCR.SZ is 1 before it; empty otherwise.
    static List<Integer> drOperands(EmulatorHelper emu, List<Instruction> step) {
        List<Integer> out = new ArrayList<>();
        if (emu.readRegister("FPSCR_SZ").signum() == 0) return out;
        for (Instruction in : step) {
            byte[] b;
            try {
                b = in.getBytes();
            } catch (Exception e) {
                continue;
            }
            if (b.length != 2) continue;
            int w = (b[0] & 0xff) | (b[1] & 0xff) << 8;
            if ((w >> 12) != 0xf) continue;
            int op = w & 0xf, n = (w >> 8) & 0xf, m = (w >> 4) & 0xf;
            List<Integer> regs = new ArrayList<>();
            if (op == 0xc) {                                // fmov FRm,FRn
                regs.add(m);
                regs.add(n);
            } else if (op == 0x8 || op == 0x9 || op == 0x6) { // fmov @Rm,FRn / @Rm+ / @(R0,Rm)
                regs.add(n);
            } else if (op == 0xa || op == 0xb || op == 0x7) { // fmov FRm,@Rn / @-Rn / @(R0,Rn)
                regs.add(m);
            }
            for (int r : regs) if ((r & 1) == 0 && !out.contains(r)) out.add(r);
        }
        return out;
    }

    // Exchange frN and frN+1 for each listed N (its own inverse: called before and after the step).
    static void swap(EmulatorHelper emu, List<Integer> drs) {
        for (int r : drs) {
            BigInteger a = emu.readRegister("fr" + r), b = emu.readRegister("fr" + (r + 1));
            emu.writeRegister("fr" + r, b);
            emu.writeRegister("fr" + (r + 1), a);
        }
    }
}

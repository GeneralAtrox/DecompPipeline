// The exit state of the original (target.json "exit_state", TcFacts.exitState; core/strict_judge EXIT_CLASSES): every
// register at exit and the stack bytes the run wrote, as the expected.txt lines XS, XF, XC, XV, XW (x360_compare.py),
// and the seeded entry values of r14..r31, f14..f31 and CR2..CR4 the run starts from (vectors.txt Q, G, K; runner.c).
// LOCKED: the watcher agent may not change this file. Not a script itself.
//
// TcVectors runs each valid vector a second time, from the seeds, for these lines only: the vector's RET, W and CALL
// lines stay the unseeded run's. The normal judge's C takes r14..r31 as 0 at entry, which is sound because a value it
// only saves and restores reaches nothing compared, except a save slot that is also a call's stack word (r1+0x50..
// 0x8f: s0..s7 of a call record), as when a frame of 0x60 saves r31 at r1+0x58 (s1) and a callee is judged on s1. So
// the seeds never reach the normal lines, and the exit state, from the seeded run, sees a callee-saved register the
// function fails to restore even where 0 would hide it.
//
// Seeds depend on the function's entry and the vector's id only: the same on every machine and in every prepare.
// The f seeds are finite doubles; the CR seed sets only CR2..CR4 (the 0x00fff000 bits of the 32-bit CR).
// FPSCR is printed as 0: the oracle refuses the instructions that move it (TcClass "fp_mode") and models none of its
// status bits, and the C has no FPSCR (the runtime prints 0 too), so the class shows only that both sides agree.
// The vector registers print when not all zero (they start at zero: TcOracle); the C prints those it wrote.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import java.math.BigInteger;
import java.util.*;

public final class TcExit {
    static final long CR_SEEDED = 0x00fff000L;

    private TcExit() {
    }

    static BigInteger u64(long v) {
        return new BigInteger(Long.toUnsignedString(v));
    }

    // The entry values of the exit-state run of one vector.
    static final class Seed {
        final long[] r = new long[18], f = new long[18];
        final long cr;

        Seed(long entry, int id) {
            Random g = new Random(entry * 0x9e3779b97f4a7c15L + id);
            for (int i = 0; i < 18; i++) r[i] = g.nextLong();
            for (int i = 0; i < 18; i++) {
                f[i] = Double.doubleToRawLongBits((g.nextDouble() - 0.5) * Math.scalb(1.0, g.nextInt(64) - 32));
            }
            cr = g.nextInt() & CR_SEEDED;
        }

        void apply(EmulatorHelper emu) {
            for (int i = 0; i < 18; i++) {
                emu.writeRegister("r" + (14 + i), u64(r[i]));
                emu.writeRegister("f" + (14 + i), u64(f[i]));
            }
            for (int k = 2; k <= 4; k++) emu.writeRegister("cr" + k, BigInteger.valueOf(crField(cr, k)));
        }

        // vectors.txt Q, G, K (runner.c reads them; a vector without them has zeros). Q, not N: N is the stricter
        // judge's junk key (TcStrict, tc_strict.h).
        String vectorLines() {
            StringBuilder b = new StringBuilder("Q");
            for (long x : r) b.append(' ').append(TcBase.hex64(x));
            b.append("\nG");
            for (long x : f) b.append(' ').append(TcBase.hex64(x));
            return b.append("\nK ").append(TcBase.hex(cr)).append('\n').toString();
        }
    }

    // CR field k (0..7) of a 32-bit CR: lt 8, gt 4, eq 2, so 1, as Ghidra's crN registers hold it.
    static int crField(long cr, int k) {
        return (int) (cr >>> (28 - 4 * k)) & 15;
    }

    // The expected.txt lines of an exit state. xer: CA, SO, OV; v: the 128 vector registers, 4 words each; stack:
    // {address, bytes} of the stack writes.
    static String lines(long[] gpr, long[] fpr, int[] crf, int[] xer, long ctr, long lr, long fpscr, int[][] v,
                        List<Object[]> stack) {
        StringBuilder b = new StringBuilder("XS");
        for (long x : gpr) b.append(' ').append(TcBase.hex64(x));
        b.append("\nXF");
        for (long x : fpr) b.append(' ').append(TcBase.hex64(x));
        long cr = 0;
        for (int k = 0; k < 8; k++) cr |= (long) (crf[k] & 15) << (28 - 4 * k);
        b.append("\nXC ").append(TcBase.hex(cr));
        for (int x : xer) b.append(' ').append(x & 1);
        b.append(' ').append(TcBase.hex64(ctr)).append(' ').append(TcBase.hex64(lr)).append(' ')
            .append(TcBase.hex(fpscr)).append('\n');
        for (int n = 0; n < v.length; n++) {
            int[] w = v[n];
            if ((w[0] | w[1] | w[2] | w[3]) == 0) continue;
            b.append("XV ").append(n);
            for (int x : w) b.append(' ').append(TcBase.hex(x & 0xffffffffL));
            b.append('\n');
        }
        for (Object[] s : stack) {
            b.append("XW ").append(TcBase.hex((Long) s[0])).append(' ').append(TcBase.bytesHex((byte[]) s[1])).append('\n');
        }
        return b.toString();
    }

    // The exit state of a run that reached the sentinel: the emulator's registers, the vector file and the stack part
    // of the written ranges ws.
    static String of(TcOracle o, EmulatorHelper emu, TcVmx vm, AddressSet ws) {
        long[] gpr = new long[32], fpr = new long[32];
        for (int i = 0; i < 32; i++) {
            gpr[i] = o.reg64(emu, "r" + i);
            fpr[i] = o.reg64(emu, "f" + i);
        }
        int[] crf = new int[8];
        for (int k = 0; k < 8; k++) crf[k] = (int) o.reg64(emu, "cr" + k) & 15;
        int[] xer = {(int) o.reg64(emu, "xer_ca"), (int) o.reg64(emu, "xer_so"), (int) o.reg64(emu, "xer_ov")};
        List<Object[]> stack = new ArrayList<>();
        for (AddressRange rg : ws) {
            long lo = Math.max(rg.getMinAddress().getOffset(), TcBase.STACK_LO);
            long hi = Math.min(rg.getMaxAddress().getOffset() + 1, TcBase.STACK_HI);
            if (lo < hi) stack.add(new Object[] {lo, emu.readMemory(o.toAddr(lo), (int) (hi - lo))});
        }
        return lines(gpr, fpr, crf, xer, o.reg64(emu, "CTR"), o.reg64(emu, "LR"), 0, vm.v, stack);
    }

    // The seeded run of a valid vector: its Q, G, K lines into r.exitIn and its exit lines into r.exit, or nothing when
    // the seeded run is not valid (that vector then has no exit state: x360_compare compares none). The oracle's other
    // state (a refusal it found) is left as it was.
    static void seeded(TcOracle o, TcOracle.Vec v, TcOracle.Result r, int id) {
        Seed s = new Seed(o.fn.getEntryPoint().getOffset(), id);
        String refused = o.refused;
        o.seed = s;
        try {
            TcOracle.Result x = o.runOracle(v);
            if (x.ok) {
                r.exitIn = s.vectorLines();
                r.exit = x.exit;
            }
        } finally {
            o.seed = null;
            o.refused = refused;
        }
    }
}

// tests/test_xbox360_scripts.py: the Xbox 360 oracle's pure helpers (TcClass.classify, TcFixups.fcti, singleBits,
// fused and trapTaken, TcBase.stackSlot, TcCatalog.mmio) and TcFacts, compiled against targets/xbox360_ppc with no
// Ghidra application. Reads a cases file (arg 0) and prints one line per case; arg 1, if given, is a facts file.
//   w WORD           -> w WORD class,class (or -)
//   c BITS           -> c BITS fctiw fctiwz fctid fctidz singleBits (64-bit hex)
//   m A C B          -> m A C B, then fused() for single x sub x neg (8 results, 64-bit hex)
//   t TO A B         -> t TO A B 0|1 (trapTaken, A and B 64-bit hex)
import java.nio.file.*;
import java.util.*;

public class X360Stub {
    static long h(String s) {
        return Long.parseUnsignedLong(s, 16);
    }

    static String x(long v) {
        return String.format("%016x", v);
    }

    public static void main(String[] args) throws Exception {
        StringBuilder out = new StringBuilder();
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.trim().split("\\s+");
            if (t[0].equals("w")) {
                List<String> c = TcClass.classify((int) h(t[1]));
                out.append(line).append(' ').append(c.isEmpty() ? "-" : String.join(",", c)).append('\n');
            } else if (t[0].equals("c")) {
                long b = h(t[1]);
                out.append(line).append(' ').append(x(TcFixups.fcti(b, true, false))).append(' ')
                    .append(x(TcFixups.fcti(b, true, true))).append(' ').append(x(TcFixups.fcti(b, false, false)))
                    .append(' ').append(x(TcFixups.fcti(b, false, true))).append(' ').append(x(TcFixups.singleBits(b)))
                    .append('\n');
            } else if (t[0].equals("m")) {
                double a = Double.longBitsToDouble(h(t[1])), c = Double.longBitsToDouble(h(t[2]));
                double b = Double.longBitsToDouble(h(t[3]));
                out.append(line);
                for (int k = 0; k < 8; k++) {
                    double r = TcFixups.fused(a, c, b, (k & 4) != 0, (k & 2) != 0, (k & 1) != 0);
                    out.append(' ').append(x(Double.doubleToRawLongBits(r)));
                }
                out.append('\n');
            } else if (t[0].equals("t")) {
                out.append(line).append(TcFixups.trapTaken(Integer.parseInt(t[1]), h(t[2]), h(t[3])) ? " 1\n" : " 0\n");
            }
        }
        out.append("k STACK_ARGS ").append(TcBase.STACK_ARGS).append('\n');
        out.append("k CALL_FLOATS ").append(TcOracle.CALL_FLOATS).append('\n');
        out.append("k CALL_LIMIT ").append(TcOracle.CALL_LIMIT).append('\n');
        out.append("k FMA_FUSED ").append(TcFixups.FMA_FUSED ? 1 : 0).append('\n');
        out.append("k GEN_STACK_ARGS ").append(TcInputs.GEN_STACK_ARGS).append('\n');
        for (int off : new int[] {0x50, 0x54, 0x58, 0x8c, 0x90, 0x4c, 0x52}) {
            out.append("k slot_").append(Integer.toHexString(off)).append(' ').append(TcBase.stackSlot(off)).append('\n');
        }
        for (long a : new long[] {0x7fc80000L, 0x7fcfffffL, 0x7fd00000L, 0xe0000000L, 0xffffffffL, 0x82000000L}) {
            out.append("k mmio_").append(Long.toHexString(a)).append(' ').append(TcCatalog.mmio(a) ? 1 : 0).append('\n');
        }
        if (args.length > 1) {  // a facts file core/ghidra.facts_body wrote from target.json: TcFacts.verify agrees
            TcFacts f = TcFacts.load(args[1]);
            out.append("k facts_ram ").append(Long.toHexString(f.ramLo)).append('\n');
            out.append("k facts_sp ").append(Long.toHexString(f.entrySp)).append('\n');
            out.append("k facts_r13 ").append(Long.toHexString(f.fixed.getOrDefault("r13", -1L))).append('\n');
            out.append("k facts_mmio ").append(f.mmio.size()).append('\n');
            out.append("k facts_global ").append(f.randomGlobal(0x82400000L) ? 1 : 0).append('\n');
        }
        System.out.print(out);
    }
}

// Register start values (tests/test_reghint.py): TcLate.regs takes "range rN LO HI" for r0..r3 and r8..r14 and
// "range gbr LO HI" out of a function's hints, sets TcOracle.entryLow / entryGi and the vector's GBR; TcLate.gbrLine
// writes line b; TcLate.copy shares the GBR. No Ghidra application and no program (a bare TcPrepare holds the fields).
//
// usage: java TcRegHintStub CASES   CASES lines:
//   H TEXT                     add a hint line (TEXT as written in oracle/hints/<addr>.txt)
//   R NAME SEED ID TRY [L|G]   a fresh vector, its hints (the H lines so far, then cleared) through TcLate.regs, with
//                              entryLow (L) or entryGi (G) drawn before as 0x11*(i+1) words, else none:
//                              "NAME: same|new | rest A;B | low W0..W3|null | gi W0..W6|null | b LINE" or
//                              "NAME: ERROR message"
//   C NAME                     line b of a TcLate.copy of the last R vector: "NAME: LINE"
import java.nio.file.*;
import java.util.*;

public class TcRegHintStub {
    static String words(long[] a) {
        if (a == null) return "null";
        StringBuilder b = new StringBuilder();
        for (long x : a) b.append(b.length() == 0 ? "" : " ").append(Long.toHexString(x));
        return b.toString();
    }

    static long[] filled(int n) {
        long[] a = new long[n];
        for (int i = 0; i < n; i++) a[i] = 0x11L * (i + 1);
        return a;
    }

    public static void main(String[] args) throws Exception {
        List<String> hints = new ArrayList<>();
        TcInputs.Vec v = null;
        TcPrepare g = new TcPrepare();
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            if (line.trim().isEmpty() || line.startsWith("#")) continue;
            if (line.startsWith("H ")) {
                hints.add(line.substring(2));
                continue;
            }
            String[] t = line.trim().split("\\s+");
            if (t[0].equals("R")) {
                v = new TcInputs.Vec();
                List<String> in = hints;
                hints = new ArrayList<>();
                g.entryLow = t.length > 5 && t[5].equals("L") ? filled(4) : null;
                g.entryGi = t.length > 5 && t[5].equals("G") ? filled(7) : null;
                try {
                    List<String> rest = TcLate.regs(g, v, in, Long.parseLong(t[2]), Integer.parseInt(t[3]),
                        Integer.parseInt(t[4]));
                    System.out.println(t[1] + ": " + (rest == in ? "same" : "new") + " | rest " + String.join(";", rest)
                        + " | low " + words(g.entryLow) + " | gi " + words(g.entryGi) + " | b "
                        + TcLate.gbrLine(v).trim());
                } catch (IllegalArgumentException e) {
                    System.out.println(t[1] + ": ERROR " + e.getMessage());
                }
            } else if (t[0].equals("C")) {
                TcInputs.Vec q = new TcInputs.Vec();
                TcLate.copy(v, q);
                System.out.println(t[1] + ": " + TcLate.gbrLine(q).trim());
            }
        }
    }
}

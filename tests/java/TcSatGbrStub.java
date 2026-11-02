// Saturn register start value (tests/test_sat_gbr.py): TcLate.regs takes "range gbr LO HI" out of a function's hints and
// sets the vector's GBR; TcLate.gbrLine writes line b; TcLate.copy shares it. No Ghidra application and no program.
//
// usage: java TcSatGbrStub CASES   CASES lines:
//   H TEXT                    add a hint line (TEXT as written in oracle/hints/<addr>.txt)
//   R NAME SEED ID TRY        a fresh vector, its hints (the H lines so far, then cleared) through TcLate.regs:
//                             "NAME: same|new | rest A;B | b LINE" or "NAME: ERROR message"
//   C NAME                    line b of a TcLate.copy of the last R vector: "NAME: LINE"
import java.nio.file.*;
import java.util.*;

public class TcSatGbrStub {
    public static void main(String[] args) throws Exception {
        List<String> hints = new ArrayList<>();
        TcInputs.Vec v = null;
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
                try {
                    List<String> rest = TcLate.regs(v, in, Long.parseLong(t[2]), Integer.parseInt(t[3]), Integer.parseInt(t[4]));
                    System.out.println(t[1] + ": " + (rest == in ? "same" : "new") + " | rest " + String.join(";", rest)
                        + " | b " + TcLate.gbrLine(v).trim());
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

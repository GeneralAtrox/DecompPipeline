// TcCatalog.isSrOp / isSq and TcSr.imask on tables (tests/test_sr_sq.py): no Ghidra application, no program.
//
// usage: java TcSrStub CASES      CASES lines (# starts a comment):
//   O MNEMONIC TEXT...      prints "O: true|false" (TcCatalog.isSrOp(mnemonic, text))
//   Q HEX                   prints "Q: true|false" (TcCatalog.isSq)
//   M R4 R5 R6 R7 FR4       prints "M: mask" (TcSr.imask of a vector with these inputs, hex)
import java.nio.file.*;

public class TcSrStub {
    public static void main(String[] args) throws Exception {
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String l = line.replaceFirst("#.*", "").trim();
            if (l.isEmpty()) continue;
            String[] w = l.split("\\s+", 3);
            if (w[0].equals("O")) {
                System.out.println("O: " + TcCatalog.isSrOp(w[1], w[1] + " " + w[2]));
            } else if (w[0].equals("Q")) {
                System.out.println("Q: " + TcCatalog.isSq(Long.parseLong(w[1], 16)));
            } else if (w[0].equals("M")) {
                String[] x = l.split("\\s+");
                TcInputs.Vec v = new TcInputs.Vec();
                for (int i = 0; i < 4; i++) v.r[i] = Long.parseLong(x[1 + i], 16);
                v.fr[0] = (int) Long.parseLong(x[5], 16);
                System.out.println("M: " + Integer.toHexString(TcSr.imask(v)));
            }
        }
    }
}

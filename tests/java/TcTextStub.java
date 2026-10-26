// TcText.movaWords on a table of opcodes (tests/test_mova_pool.py): no Ghidra application, no program.
//
// usage: java TcTextStub CASES      CASES lines (hex; # starts a comment):
//   I ADDR OPCODE [r0]         a 2-byte instruction; "r0": it writes r0
//   X                          prints "X: RANGES", the bytes movaWords protects (inclusive ranges), or "X: -"
import java.nio.file.*;
import java.util.*;

public class TcTextStub {
    public static void main(String[] args) throws Exception {
        List<Long> at = new ArrayList<>();
        List<Integer> op = new ArrayList<>();
        List<Boolean> w0 = new ArrayList<>();
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String l = line.replaceFirst("#.*", "").trim();
            if (l.isEmpty()) continue;
            String[] w = l.split("\\s+");
            if (w[0].equals("I")) {
                at.add(Long.parseLong(w[1], 16));
                op.add(Integer.parseInt(w[2], 16));
                w0.add(w.length > 3 && w[3].equals("r0"));
            } else if (w[0].equals("X")) {
                StringBuilder out = new StringBuilder("X:");
                long lo = -1, hi = -1;
                for (long a : new TreeSet<>(TcText.movaWords(at, op, w0))) {
                    if (a != hi + 1) {
                        if (lo >= 0) out.append(String.format(" %x-%x", lo, hi));
                        lo = a;
                    }
                    hi = a;
                }
                if (lo >= 0) out.append(String.format(" %x-%x", lo, hi));
                System.out.println(lo < 0 ? "X: -" : out);
                at.clear();
                op.clear();
                w0.clear();
            }
        }
    }
}

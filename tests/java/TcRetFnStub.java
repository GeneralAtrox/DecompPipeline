// The genretfn hint (tests/test_genretfn.py): TcLate.ranges takes the genretfn lines out of a function's hints and
// draws every call's result for the callee they name; TcLate.ranged returns it; TcLate.copy shares it. No Ghidra
// application and no program.
//
// usage: java TcRetFnStub CASES   CASES lines:
//   H TEXT                     add a hint line (TEXT as written in oracle/hints/<addr>.txt)
//   R NAME SEED ID TRY         a fresh vector, its hints (the H lines so far, then cleared) through TcLate.ranges:
//                              "NAME: same|new | rest A;B | CALLEE=MIN..MAX/DISTINCT first V0 V1 V2 V3 ..." or
//                              "NAME: ERROR message"
//   Q NAME CALLEE CALL X       the last R vector's result of call CALL to CALLEE when drawn X: "NAME: VALUE"
//   C NAME CALLEE CALL         the same through a TcLate.copy of that vector: "NAME: VALUE"
import java.nio.file.*;
import java.util.*;

public class TcRetFnStub {
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
                    List<String> rest = TcLate.ranges(v, in, Long.parseLong(t[2]), Integer.parseInt(t[3]),
                        Integer.parseInt(t[4]));
                    StringBuilder b = new StringBuilder(rest == in ? "same" : "new").append(" | rest ")
                        .append(String.join(";", rest));
                    Map<Long, long[]> got = TcLate.RANGED.get(v);
                    for (long callee : got == null ? new TreeSet<Long>() : new TreeSet<>(got.keySet())) {
                        long[] d = got.get(callee);
                        long min = Long.MAX_VALUE, max = Long.MIN_VALUE;
                        Set<Long> distinct = new HashSet<>();
                        for (long x : d) {
                            min = Math.min(min, x);
                            max = Math.max(max, x);
                            distinct.add(x);
                        }
                        b.append(" | ").append(Long.toHexString(callee)).append('=').append(Long.toHexString(min))
                            .append("..").append(Long.toHexString(max)).append('/').append(distinct.size())
                            .append(" n ").append(d.length).append(" first");
                        for (int i = 0; i < 4; i++) b.append(' ').append(Long.toHexString(d[i]));
                    }
                    System.out.println(t[1] + ": " + b);
                } catch (IllegalArgumentException e) {
                    System.out.println(t[1] + ": ERROR " + e.getMessage());
                }
            } else if (t[0].equals("Q") || t[0].equals("C")) {
                TcInputs.Vec q = v;
                if (t[0].equals("C")) {
                    q = new TcInputs.Vec();
                    TcLate.copy(v, q);
                }
                long x = t.length > 4 ? Long.parseLong(t[4], 16) : 0x77;
                System.out.println(t[1] + ": " + Long.toHexString(TcLate.ranged(q, Long.parseLong(t[2], 16),
                    Integer.parseInt(t[3]), x)));
            }
        }
    }
}

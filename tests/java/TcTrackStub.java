// tests/test_track.py: TcTrack (Dreamcast and GameCube, compiled against each target's scripts) without Ghidra.
// One case per line of the file in args[0], one output line "N: result" per T-less line:
//   T CALLEES PARAMS   a new function (comma lists of hex entries and registers, "-" for none) and run
//   D REGS             the run sets these registers (a step's outputs)
//   C TARGET           a mocked call: TcTrack.call, "\n" shown as "|"
//   N HEX              TcTrack.nan
import java.nio.file.*;
import java.util.*;

public class TcTrackStub {
    static List<String> list(String s) {
        return s.equals("-") ? List.of() : Arrays.asList(s.split(","));
    }

    public static void main(String[] args) throws Exception {
        int n = 0;
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.replaceAll("#.*", "").trim().split("\\s+");
            if (t[0].isEmpty()) continue;
            String out = null;
            switch (t[0]) {
                case "T":
                    Set<Long> c = new HashSet<>();
                    for (String a : list(t[1])) c.add(Long.parseLong(a, 16));
                    TcTrack.on = new TcTrack(c, new HashSet<>(list(t[2])), true, false);
                    TcTrack.start();
                    break;
                case "D":
                    TcTrack.on.cur.defined.addAll(list(t[1]));
                    break;
                case "C":
                    out = TcTrack.call(Long.parseLong(t[1], 16)).replace("\n", "|");
                    break;
                case "N":
                    out = String.valueOf(TcTrack.nan(Long.parseLong(t[1], 16)));
                    break;
                default:
                    throw new IllegalArgumentException(line);
            }
            if (out != null) System.out.println(n++ + ": " + out);
        }
    }
}

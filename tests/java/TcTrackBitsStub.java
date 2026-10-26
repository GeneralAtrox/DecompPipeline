// tests/test_track.py: TcTrack's float bits through the integer registers (Dreamcast) without Ghidra. One case per
// line of the file in args[0], one output line "N: result" per case, a run's float-bit registers after it as
// "reg,reg" (one run, a function with float stores and no computed call):
//   F OP OUTS   TcTrack.flow of the opcode (hex) writing OUTS (a comma list, "-" for none): "code regs"
//   S OP        TcTrack.store
//   C TARGET    a mocked call: "regs"
import java.nio.file.*;
import java.util.*;

public class TcTrackBitsStub {
    static List<String> list(String s) {
        return s.equals("-") ? List.of() : Arrays.asList(s.split(","));
    }

    public static void main(String[] args) throws Exception {
        TcTrack.on = new TcTrack(new HashSet<>(), new HashSet<>(), false, true);
        Set<String> fb = TcTrack.start().fbits;
        int n = 0;
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.replaceAll("#.*", "").trim().split("\\s+");
            if (t[0].isEmpty()) continue;
            String out;
            switch (t[0]) {
                case "F":
                    int at = TcTrack.flow(fb, Integer.parseInt(t[1], 16), new HashSet<>(list(t[2])));
                    out = at + " " + String.join(",", new TreeSet<>(fb));
                    break;
                case "S":
                    out = String.valueOf(TcTrack.store(Integer.parseInt(t[1], 16)));
                    break;
                case "C":
                    out = TcTrack.call(Long.parseLong(t[1], 16)) + String.join(",", new TreeSet<>(fb));
                    break;
                default:
                    throw new IllegalArgumentException(line);
            }
            System.out.println(n++ + ": " + out);
        }
    }
}

// tests/test_track.py: TcTrack's float bits through the integer registers (GameCube) without Ghidra. One case per
// line of the file in args[0], one output line "N: result" per case, a run's float-bit registers after it as
// "reg,reg" (one run, a function with float stores and no computed call):
//   B REGS               these registers hold float bits (an lwz of a float store's word: TcTrack.step)
//   F MNEM D S OUTS      TcTrack.flow of an instruction (operand registers D and S, "-" for none) writing OUTS (a
//                        comma list, "-" for none): "stores regs"
//   C TARGET             a mocked call: "regs"
import java.nio.file.*;
import java.util.*;

public class TcTrackPpcBitsStub {
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
            String out = null;
            switch (t[0]) {
                case "B":
                    fb.addAll(list(t[1]));
                    break;
                case "F":
                    boolean st = TcTrack.flow(fb, t[1], t[2].replace("-", ""), t[3].replace("-", ""),
                        new HashSet<>(list(t[4])));
                    out = st + " " + String.join(",", new TreeSet<>(fb));
                    break;
                case "C":
                    out = TcTrack.call(Long.parseLong(t[1], 16)) + String.join(",", new TreeSet<>(fb));
                    break;
                default:
                    throw new IllegalArgumentException(line);
            }
            if (out != null) System.out.println(n++ + ": " + out);
        }
    }
}

// tests/test_track.py: the Saturn TcTrack without Ghidra. The lines of TcTrackStub: T CALLEES PARAMS, D REGS, C TARGET.
import java.nio.file.*;
import java.util.*;

public class TcTrackSh2Stub {
    static List<String> list(String s) {
        return s.equals("-") ? List.of() : Arrays.asList(s.split(","));
    }

    public static void main(String[] args) throws Exception {
        int n = 0;
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.replaceAll("#.*", "").trim().split("\\s+");
            if (t[0].isEmpty()) continue;
            if (t[0].equals("T")) {
                Set<Long> c = new HashSet<>();
                for (String a : list(t[1])) c.add(Long.parseLong(a, 16));
                TcTrack.on = new TcTrack(c, new HashSet<>(list(t[2])));
                TcTrack.start();
            } else if (t[0].equals("D")) {
                TcTrack.on.cur.defined.addAll(list(t[1]));
            } else if (t[0].equals("C")) {
                System.out.println(n++ + ": " + TcTrack.call(Long.parseLong(t[1], 16)).replace("\n", "|"));
            } else {
                throw new IllegalArgumentException(line);
            }
        }
    }
}

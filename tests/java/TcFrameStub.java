// The oracle's own-frame rule on stub data (tests/test_oracle_window.py): TcExit.logged, TcProvenance.ownFrame and
// TcFacts' frame_lo, no Ghidra application and no program.
//
// usage: java TcFrameStub CASES      CASES lines (hex; # starts a comment):
//   L LO HI FRAMELO [NAMED...]   prints "L LO HI: A-B ...", the logged parts of a write [LO, HI) (TcExit.logged)
//   O FRAMELO ADDR               prints "O ADDR: true|false", TcProvenance.ownFrame with TcBase.frameLo = FRAMELO
//   F FILE                       prints "F: FRAMELO" (TcFacts.load of the facts file) or "F: refused MESSAGE"
import java.nio.file.*;
import java.util.*;

public class TcFrameStub {
    static long h(String s) {
        return Long.parseLong(s, 16);
    }

    public static void main(String[] args) throws Exception {
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.trim().split("\\s+");
            if (t[0].isEmpty() || t[0].startsWith("#")) continue;
            if (t[0].equals("L")) {
                Set<Long> named = new HashSet<>();
                for (int i = 4; i < t.length; i++) named.add(h(t[i]));
                StringBuilder sb = new StringBuilder("L " + t[1] + " " + t[2] + ":");
                for (long[] s : TcExit.logged(h(t[1]), h(t[2]), h(t[3]), named)) {
                    sb.append(' ').append(Long.toHexString(s[0])).append('-').append(Long.toHexString(s[1]));
                }
                System.out.println(sb);
            } else if (t[0].equals("O")) {
                TcBase.frameLo = h(t[1]);
                System.out.println("O " + t[2] + ": " + TcProvenance.ownFrame(h(t[2])));
            } else if (t[0].equals("F")) {
                try {
                    System.out.println("F: " + Long.toHexString(TcFacts.load(t[1]).frameLo));
                } catch (IllegalStateException e) {
                    System.out.println("F: refused " + e.getMessage());
                }
            }
        }
    }
}

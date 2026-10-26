// TcAlign's bookkeeping (tests/test_sh4_align.py): which written vectors it leaves out, and the counts and coverage
// it keeps for the summary, from plain TcOracle.Results; no Ghidra application and no program.
//
// usage: java TcAlignDropStub CASES   CASES lines, one TcAlign per line: NAME VEC...   VEC: SET:MIS:COV:SITES, SET
//   dev or hidden, MIS a misaligned address (hex) or -, COV and SITES comma lists of block indices and site
//   addresses (hex), or -. Prints "NAME: WRITTEN | valid D H | covDev .. | hits b=n .. | sites a=n .. | NOTE"
import java.nio.file.*;
import java.util.*;

public class TcAlignDropStub {
    public static void main(String[] args) throws Exception {
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.trim().split("\\s+");
            if (t[0].isEmpty() || t[0].startsWith("#")) continue;
            TcAlign a = new TcAlign();
            StringBuilder written = new StringBuilder();
            for (int i = 1; i < t.length; i++) {
                String[] f = t[i].split(":", -1);
                TcOracle.Result r = new TcOracle.Result();
                r.misaligned = f[1].equals("-") ? -1 : Long.parseLong(f[1], 16);
                if (!f[2].equals("-")) for (String c : f[2].split(",")) r.cov.add(Integer.parseInt(c));
                if (!f[3].equals("-")) for (String s : f[3].split(",")) r.sites.add(Long.parseLong(s, 16));
                written.append(a.drop(f[0], r) ? "-" : "w");
            }
            StringBuilder sites = new StringBuilder();
            for (Map.Entry<Long, Integer> e : a.siteHits.entrySet()) {
                sites.append(' ').append(Long.toHexString(e.getKey())).append('=').append(e.getValue());
            }
            System.out.println(t[0] + ": " + written + " | valid " + a.valid[0] + " " + a.valid[1] + " | covDev "
                + a.covDev + " | hits " + a.blockHits + " | sites" + sites + " | " + (a.note().isEmpty() ? "-" : a.note()));
        }
    }
}

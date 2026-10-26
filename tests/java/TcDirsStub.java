// tests/test_branch_cov.py's view of TcDirs without Ghidra.
//
// usage: java TcDirsStub CASES   CASES: lines "B addr fall target" (a branch; target -1: computed), "H prev pc" (a step
//                                after prev; -1: after a mocked call), "D" (the hits so far count as the dev set's),
//                                all in hex; prints the directions hit, then TcDirs.summary(dev, all)
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

public class TcDirsStub {
    static long hex(String s) {
        return Long.parseLong(s, 16);
    }

    public static void main(String[] args) throws Exception {
        List<long[]> rows = new ArrayList<>();
        List<String> lines = java.nio.file.Files.readAllLines(java.nio.file.Path.of(args[0]));
        for (String line : lines) {
            String[] t = line.trim().split("\\s+");
            if (t[0].equals("B")) rows.add(new long[] {hex(t[1]), hex(t[2]), hex(t[3])});
        }
        TcDirs d = new TcDirs(rows);
        Set<Integer> all = new TreeSet<>(), dev = new TreeSet<>();
        for (String line : lines) {
            String[] t = line.trim().split("\\s+");
            if (t[0].equals("H")) d.hit(all, hex(t[1]), hex(t[2]));
            if (t[0].equals("D")) dev.addAll(all);
        }
        StringBuilder out = new StringBuilder();
        for (int i : all) out.append(i).append(' ');
        System.out.print(out.toString().trim() + "\n" + d.summary(dev, all) + "\n");
    }
}

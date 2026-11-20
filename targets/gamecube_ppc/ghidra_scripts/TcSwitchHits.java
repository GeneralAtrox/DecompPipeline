// Which way each bctr of the function went, over the kept vectors: oracle_summary.json "switch_hits"
// {bctr address: {CTR target: runs}} (report only: no verdict, vector or expected result depends on it). TcOracle notes
// the CTR value each time a run executes a bcctr without link (bctr, or a conditional one) in the function's body;
// TcVectors adds a run's notes when its vector is kept. A switch's jump goes through a table to one case per index, so
// the targets show which cases the judge's vectors reach: a PASS whose vectors never enter a case says nothing about it.
// Counts are per execution: a loop over a switch counts every pass. Ported from the Xbox 360 target's TcSwitchHits
// (Too Human, 48043fe9; Skies of Arcadia round r11, 2026-11-14). Not a script itself.
import java.util.*;

public class TcSwitchHits {
    final Map<Long, Map<Long, Integer>> hits = new TreeMap<>();

    // bcctr with LK = 0 (opcode 19, extended 528): bctr and its conditional forms; bctrl (a call) is not one.
    static boolean isBcctr(long word) {
        return (word & 0xFC0007FFL) == 0x4C000420L;
    }

    // One kept run's notes: {bctr address, CTR} per execution.
    void add(List<long[]> run) {
        for (long[] e : run) hits.computeIfAbsent(e[0], k -> new TreeMap<>()).merge(e[1] & 0xffffffffL, 1, Integer::sum);
    }

    // The JSON object: {"8xxxxxxx": {"8yyyyyyy": n, ...}, ...}.
    String json() {
        StringBuilder b = new StringBuilder("{");
        for (Map.Entry<Long, Map<Long, Integer>> s : hits.entrySet()) {
            if (b.length() > 1) b.append(',');
            b.append(String.format("\"%08x\":{", s.getKey()));
            int n = 0;
            for (Map.Entry<Long, Integer> t : s.getValue().entrySet()) {
                b.append(n++ > 0 ? "," : "").append(String.format("\"%08x\":%d", t.getKey(), t.getValue()));
            }
            b.append('}');
        }
        return b.append('}').toString();
    }
}

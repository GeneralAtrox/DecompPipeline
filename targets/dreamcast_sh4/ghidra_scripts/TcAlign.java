// SH-4 address errors (TcFold.misaligned: a 2-byte access at an odd address, a longer one off a multiple of 4). A
// valid vector whose run makes one is a run the console never makes (it stops at the access), so it is left out of
// the harness (TcVectors.writeVector), and nothing else changes: generation, its repairs, the rescue, reach and bound
// passes count it as before, so every other vector, with its id, is byte-identical to the harness made without this
// rule, and no kill of a vector the console runs can be lost. Refusing the access as a fault instead re-steered the
// repairs and the generators (ids, tries) and lost kills (Power Stone 2 8c0a19f2: 372 -> 270 of 974 mutants).
// After the reach and bound passes, replace draws as many vectors again from a generator of its own. The summary then
// counts the written vectors only (fix). Not a script itself; used by TcVectors and TcPrepare.
import java.util.*;

final class TcAlign {
    final int[] dropped = new int[2], replaced = new int[2], valid = new int[2];  // per set: dev, hidden
    // the written vectors' coverage and branch directions (TcDirs), as TcVectors counts every valid vector's
    final Set<Integer> covDev = new TreeSet<>(), dirsDev = new TreeSet<>(), dirsAll = new TreeSet<>();
    final Map<Integer, Integer> blockHits = new TreeMap<>();
    final Map<Long, Integer> siteHits = new TreeMap<>();

    // TcVectors.writeVector: true when r made an address error (the vector is not written); else counts it.
    boolean drop(String set, TcOracle.Result r) {
        int s = set.equals("dev") ? 0 : 1;
        if (r.misaligned >= 0) {
            dropped[s]++;
            return true;
        }
        valid[s]++;
        if (s == 0) {
            covDev.addAll(r.cov);
            dirsDev.addAll(r.dirs);
        }
        dirsAll.addAll(r.dirs);
        for (int c : r.cov) blockHits.merge(c, 1, Integer::sum);
        for (long site : r.sites) siteHits.merge(site, 1, Integer::sum);
        return false;
    }

    boolean any() {
        return dropped[0] + dropped[1] > 0;
    }

    // TcPrepare, after the reach and bound passes: per set, up to as many vectors as were left out, from a generator
    // of their own (ids after every other vector's), at most 8 tries each, within the first pass's budget. A try that
    // is invalid or makes an address error again is dropped (no refusal), as is one writeVector leaves out.
    void replace(TcVectors t, TcBase.Proto proto, List<Long> globals, List<String> hints, long seed) {
        if (!any()) return;
        boolean[] lowPtr = new boolean[4];
        for (int i = 0; i < proto.params.size(); i++) {
            String st = proto.params.get(i).storage;
            if (st.matches("r[0-3]")) lowPtr[st.charAt(1) - '0'] = proto.paramIsPointer.get(i);
        }
        t.storeFaults = t.rescued;
        List<Long> consts = t.facts.coverageInputs || t.rescued ? TcGuide.constants(t.listing, t.body, t::poolValue)
            : List.of();
        long end = TcBudget.of(t.facts, t.facts.oracleBudgetS);
        int id = t.valid[0] + t.valid[1];
        for (int s = 0; s < 2; s++) {
            t.rnd = new Random(seed * 31 + (s + 2) * 0x5bd1e995L);
            for (int want = dropped[s], tries = 0, idTry = 0; replaced[s] < want && tries < want * 8; tries++) {
                if (TcBudget.over(t.work(), end)) return;
                TcInputs.Vec[] box = {null};
                TcOracle.Result r = t.attempt(proto, globals, hints, seed, id, idTry++, lowPtr, consts, t.rescued, box,
                                              new int[1]);
                if (!r.ok || r.misaligned >= 0) continue;
                int before = t.vout.length();
                t.writeVector(id, s == 0 ? "dev" : "hidden", box[0], r);
                if (t.vout.length() == before) continue;  // its strict replay failed or made an address error
                id++;
                idTry = 0;
                replaced[s]++;  // counted with the written vectors (fix)
            }
        }
    }

    // TcPrepare, before the summary: the written vectors' counts and coverage in place of the generators' (only
    // when a vector was left out: other summaries are unchanged).
    void fix(TcVectors t) {
        if (!any()) return;
        t.valid = valid.clone();
        t.covDev = new TreeSet<>(covDev);
        t.covAll = new TreeSet<>(blockHits.keySet());
        t.dirsDev = new TreeSet<>(dirsDev);
        t.dirsAll = new TreeSet<>(dirsAll);
        t.blockHits = new TreeMap<>(blockHits);
        Map<Long, Integer> sites = new TreeMap<>();
        for (long site : t.siteHits.keySet()) sites.put(site, siteHits.getOrDefault(site, 0));
        t.siteHits = sites;
    }

    // oracle_summary.json's "address_errors" (with its comma), or "".
    String note() {
        return !any() ? "" : String.format("\"address_errors\":{\"left_out\":[%d,%d],\"replaced\":[%d,%d]},",
            dropped[0], dropped[1], replaced[0], replaced[1]);
    }
}

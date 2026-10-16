// The first vector pass's results, set aside while TcVectors makes its rescue pass (TcVectors.makeVectors), and put
// back when the rescue pass is no better. Not a script itself; used by TcVectors.
import java.util.*;

public class TcRescue {
    StringBuilder vout, eout;
    Map<String, Integer> invalid;
    Set<Integer> covDev, covAll;
    Map<Integer, Integer> blockHits;
    Map<Long, Integer> siteHits;
    List<TcReach.Entry> seeds;
    int[] valid, tried;
    int repaired;
    List<String> examples;
    boolean outOfTime;

    // Takes t's results and leaves t empty for the next pass.
    static TcRescue take(TcVectors t) {
        TcRescue s = new TcRescue();
        s.vout = t.vout;
        s.eout = t.eout;
        s.invalid = t.invalid;
        s.covDev = t.covDev;
        s.covAll = t.covAll;
        s.blockHits = t.blockHits;
        s.siteHits = t.siteHits;
        s.seeds = t.seeds;
        s.valid = t.valid;
        s.tried = t.tried;
        s.repaired = t.repaired;
        s.examples = t.examples;
        s.outOfTime = t.outOfTime;
        t.vout = new StringBuilder();
        t.eout = new StringBuilder();
        t.invalid = new TreeMap<>();
        t.covDev = new TreeSet<>();
        t.covAll = new TreeSet<>();
        t.blockHits = new TreeMap<>();
        t.siteHits = new TreeMap<>();
        t.seeds = new ArrayList<>();
        t.valid = new int[2];
        t.tried = new int[2];
        t.repaired = 0;
        t.examples = new ArrayList<>();
        t.outOfTime = false;
        return s;
    }

    void restore(TcVectors t) {
        t.vout = vout;
        t.eout = eout;
        t.invalid = invalid;
        t.covDev = covDev;
        t.covAll = covAll;
        t.blockHits = blockHits;
        t.siteHits = siteHits;
        t.seeds = seeds;
        t.valid = valid;
        t.tried = tried;
        t.repaired = repaired;
        t.examples = examples;
        t.outOfTime = outOfTime;
    }
}

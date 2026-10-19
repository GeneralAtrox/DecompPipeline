// The first vector pass's results, set aside while TcVectors makes its rescue pass (TcVectors.makeVectors), and put
// back when the rescue pass is no better. Not a script itself; used by TcVectors.
// overtime: the overtime rescue pass (TcVectors class comment).
import java.lang.management.ManagementFactory;
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

    // The overtime rescue: a pass with a tenth of the step limit in which a run hitting it gets its loop counts cut
    // (TcCount), kept when both sets reach the rescue threshold. It ends oracleRescueS after it starts; when the
    // step-limit fail-fast started it (TcVectors.earlyOvertime), at the first pass's own deadline plus oracleRescueS,
    // so it gets the time the first pass would have spent on runs to the full step limit (Spawn 8c01bde0: 33 of 111
    // dev tries valid in its old 90 s, and no hidden set).
    static void overtime(TcVectors t, TcBase.Proto proto, List<Long> globals, List<String> hints, long seed, int ndev,
                         int nhidden) {
        if (t.facts.oracleRescueS <= 0 || t.facts.minValid <= 0) return;
        TcRescue first = take(t);
        long started = System.currentTimeMillis();
        t.deadlineAt = started + t.facts.oracleRescueS * 1000;
        if (t.earlyOvertime && t.facts.oracleDeadlineS > 0) {
            t.deadlineAt = Math.max(t.deadlineAt, ManagementFactory.getRuntimeMXBean().getStartTime()
                + (t.facts.oracleDeadlineS + t.facts.oracleRescueS) * 1000);
        }
        t.stepLimit = TcBase.STEP_LIMIT / 10;
        t.countRepair = true;
        t.makeSets(proto, globals, hints, seed, ndev, nhidden, true);
        t.stepLimit = TcBase.STEP_LIMIT;
        t.countRepair = false;
        t.deadlineAt = 0;
        if (t.outside < 0 && t.valid[0] >= Math.min(t.facts.minValid, ndev)
            && t.valid[1] >= Math.min(t.facts.minValid, nhidden)) {
            t.outOfTime = false;
            t.rescued = true;
            return;
        }
        t.overtimeNote = String.format("%s; an overtime rescue cutting loop counts (step limit %d) made dev %d of %d,"
            + " hidden %d of %d valid in %d s%s%s", t.earlyOvertime ? "; the first pass stopped early (" + t.earlyNote
            + ")" : "", TcBase.STEP_LIMIT / 10, t.valid[0], t.tried[0], t.valid[1], t.tried[1],
            (System.currentTimeMillis() - started) / 1000, t.outside >= 0 ? ", then reached code outside the body at "
            + TcBase.hex(t.outside) : "", t.countSites.isEmpty() ? "" : "; no count found for the loop comparing at "
            + t.countSites);
        t.outside = -1;
        first.restore(t);
        t.outOfTime = true;
    }

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

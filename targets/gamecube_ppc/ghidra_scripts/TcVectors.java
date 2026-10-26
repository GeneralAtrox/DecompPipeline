// Test vector generation for the PowerPC target: the dev and hidden sets, each vector's inputs (TcInputs), the
// oracle run, pointer repair, and the vectors.txt / expected.txt text. LOCKED: the watcher agent may not change
// this file. Extended by TcPrepare.
//
// vectors.txt, per vector: V id set, R r3..r10, F f1..f8 (64-bit register bits), C the 16 scripted r3 results,
// D the 16 scripted f1 results (64-bit), M addr bytes (patches, big endian), E.
// expected.txt: V, RET r3 f1 (64-bit) r4, W writes (below the stack window), CALL records (TcOracle.callRecord),
// COV blocks, E.
// Deadline (TcFacts.oracleDeadlineS): generation stops before Ghidra's timeout; TcPrepare then refuses the function.
import java.lang.management.ManagementFactory;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;

public abstract class TcVectors extends TcOracle {
    static final int MAX_REPAIRS = 12;

    StringBuilder vout = new StringBuilder(), eout = new StringBuilder();
    Map<String, Integer> invalid = new TreeMap<>();
    Set<Integer> covDev = new TreeSet<>(), covAll = new TreeSet<>();
    Map<Integer, Integer> blockHits = new TreeMap<>();
    Map<Long, Integer> siteHits = new TreeMap<>();
    int[] valid = new int[2], tried = new int[2];
    int repaired = 0; Set<Integer> dirsDev = new TreeSet<>(), dirsAll = new TreeSet<>();  // branch directions (TcDirs)
    List<String> examples = new ArrayList<>();
    List<TcBoundPass.Base> bases = new ArrayList<>();  // the valid vectors, for targeted_vectors (TcBoundPass)
    long outside = -1;
    boolean outOfTime = false;

    // Each set has its own generator (rnd), so the hidden set does not depend on ndev.
    void makeVectors(Proto proto, List<Long> globals, List<String> hints, long seed, int ndev, int nhidden) {
        String[] sets = {"dev", "hidden"};
        int[] want = {ndev, nhidden};
        int id = 0;
        for (long site : callSites()) siteHits.put(site, 0);
        long deadline = facts.oracleDeadlineS <= 0 ? 0
            : ManagementFactory.getRuntimeMXBean().getStartTime() + facts.oracleDeadlineS * 1000;
        vectors:
        for (int s = 0; s < 2; s++) {
            rnd = new Random(seed * 31 + s * 0x5bd1e995L);
            while (valid[s] < want[s] && tried[s] < want[s] * 8) {
                if (deadline > 0 && System.currentTimeMillis() > deadline) {
                    outOfTime = true;
                    break vectors;
                }
                tried[s]++;
                Vec v = makeVec(id % 4, proto, new ArrayList<>(globals));
                applyHints(v, hints);
                int repairs = 0;
                Result r = runOracle(v);
                while (!r.ok && r.faultAddr >= 0 && repairs < MAX_REPAIRS) {
                    List<long[]> loads = new ArrayList<>();
                    for (long[] ld : r.loads) loads.add(ld.clone());
                    Vec nv = repairVec(v, r.faultAddr, r.faultWrite, loads);
                    if (nv == null) break;
                    v = nv;
                    repairs++;
                    r = runOracle(v);
                }
                if (r.ok && !patchesInRam(v)) {
                    r.ok = false;
                    r.reason = "input patches memory outside RAM";
                }
                if (!r.ok) {
                    invalid.merge(r.reason.replaceAll("[0-9a-f]{8}", "X").replaceAll("ram:\\w+", "X"), 1, Integer::sum);
                    if (examples.size() < 6) examples.add(describeFailure(r, repairs));
                    if (r.outside >= 0) {
                        outside = r.outside;
                        break vectors;
                    }
                    if (refused != null) break vectors;
                    continue;
                }
                if (repairs > 0) repaired++;
                valid[s]++;
                writeVector(id, sets[s], v, r);
                if (facts.targetedVectors) bases.add(new TcBoundPass.Base(v, r));
                if (s == 0) { covDev.addAll(r.cov); dirsDev.addAll(r.dirs); }
                covAll.addAll(r.cov); dirsAll.addAll(r.dirs);
                for (int c : r.cov) blockHits.merge(c, 1, Integer::sum);
                for (long site : r.sites) siteHits.merge(site, 1, Integer::sum);
                id++;
            }
        }
    }

    void writeVector(int id, String set, Vec v, Result r) {
        if ((r = TcStrict.replay(this, v, r, set)) == null) return;  // with the strict flags on (TcStrict)
        vout.append("V ").append(id).append(' ').append(set).append("\nR");
        for (long x : v.r) vout.append(' ').append(hex(x));
        vout.append("\nF");
        for (long x : v.f) vout.append(' ').append(hex64(x));
        vout.append("\nC");
        for (long x : v.rets) vout.append(' ').append(hex(x));
        vout.append("\nD");
        for (long x : v.frets) vout.append(' ').append(hex64(x));
        vout.append('\n');
        for (int i = 0; i < v.patchAddr.size(); i++) {
            vout.append("M ").append(hex(v.patchAddr.get(i))).append(' ').append(bytesHex(v.patchData.get(i))).append('\n');
        }
        vout.append(TcStrict.vec(r)).append("E\n");  // lines N, W (TcStrict)
        eout.append("V ").append(id).append(' ').append(set).append('\n');
        eout.append("RET ").append(hex(r.r3)).append(' ').append(hex64(r.f1)).append(' ').append(hex(r.r4)).append('\n');
        for (String w : r.writes) eout.append("W ").append(w).append('\n');
        for (String c : r.calls) eout.append("CALL ").append(c).append('\n');
        eout.append(TcTrack.exit(r.tr));
        eout.append(TcStrict.exp(r)).append("COV");  // lines CS, XR (TcStrict)
        for (int c : r.cov) eout.append(' ').append(c);
        eout.append("\nE\n");
    }

    // The call sites: call instructions to code outside the body (save helpers excepted), and branches to another
    // function's entry (tail calls).
    List<Long> callSites() {
        List<Long> out = new ArrayList<>();
        for (Instruction in : listing.getInstructions(body, true)) {
            boolean site = false;
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isFlow()) continue;
                Address to = r.getToAddress();
                if (body.contains(to) || inline.contains(to)) continue;
                if (r.getReferenceType().isCall() || fm.getFunctionAt(to) != null) site = true;
            }
            if (in.getFlowType().isCall() && in.getFlowType().isComputed()) site = true;
            if (site) out.add(in.getAddress().getOffset());
        }
        return out;
    }

    // Every input must be representable by the C runner, whose guest memory is exactly [RAM_LO, RAM_HI).
    boolean patchesInRam(Vec v) {
        for (int i = 0; i < v.patchAddr.size(); i++) {
            long a = v.patchAddr.get(i);
            if (a < RAM_LO || a + v.patchData.get(i).length > RAM_HI) return false;
        }
        return true;
    }

    String describeFailure(Result r, int repairs) {
        StringBuilder b = new StringBuilder(r.reason).append(" after ").append(repairs).append(" repair(s); last loads:");
        for (int i = Math.max(0, r.loads.size() - 4); i < r.loads.size(); i++) {
            long[] ld = r.loads.get(i);
            b.append(String.format(" [pc %s: %d bytes @%s = %s]", hex(ld[0]), ld[2], hex(ld[1]), Long.toHexString(ld[3])));
        }
        return b.toString();
    }
}

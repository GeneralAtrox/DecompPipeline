// Test vector generation for the Xbox 360 target: the dev and hidden sets, each vector's inputs (TcInputs), the
// oracle run, pointer repair, and the vectors.txt / expected.txt text (formats: x360_compare.py). LOCKED: the
// watcher agent may not change this file. Extended by TcPrepare.
//
// vectors.txt, per vector: V id set, R r3..r10, F f1..f13, C the 16 scripted r3 results, D the 16 scripted f1
// results (all 64-bit hex), M addr bytes (patches, big endian), E.
// expected.txt: V, RET r3 f1 (64-bit), W writes (outside the stack region), CALL records (TcOracle.callRecord),
// COV blocks, E.
// Deadline (TcFacts.oracleDeadlineS): generation stops before Ghidra's timeout; TcPrepare then refuses the function.
//
// Two checks on the finished set, both from more oracle runs of the valid vectors (the vectors stay as they are):
//  - multiply-add mode: a function with multiply-adds runs every vector in the other mode too (TcFixups); the count
//    of vectors whose results differ is "fma_sensitive" in oracle_summary.json (DESIGN.md, open question 1);
//  - widths: a 32-bit integer parameter whose register's upper word changes the results, run with that word
//    flipped, is really 64-bit (probeWidths); a 32-bit result whose r3 upper word is neither zero nor the sign
//    extension in every run is really 64-bit, and one the other extension fits is that kind (retKind).
import java.lang.management.ManagementFactory;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;

public abstract class TcVectors extends TcOracle {
    static final int MAX_REPAIRS = 12;
    static final int PROBE_VECTORS = 6;

    StringBuilder vout = new StringBuilder(), eout = new StringBuilder();
    Map<String, Integer> invalid = new TreeMap<>();
    Set<Integer> covDev = new TreeSet<>(), covAll = new TreeSet<>();
    Map<Integer, Integer> blockHits = new TreeMap<>();
    Map<Long, Integer> siteHits = new TreeMap<>();
    int[] valid = new int[2], tried = new int[2];
    int repaired = 0, fmaChecked = 0, fmaSensitive = 0;
    List<String> examples = new ArrayList<>();
    List<Vec> goodVecs = new ArrayList<>();
    List<Result> goodRes = new ArrayList<>();
    long outside = -1;
    boolean outOfTime = false, hasFma = false;

    // Each set has its own generator (rnd), so the hidden set does not depend on ndev.
    void makeVectors(Proto proto, List<Long> globals, List<String> hints, long seed, int ndev, int nhidden) {
        String[] sets = {"dev", "hidden"};
        int[] want = {ndev, nhidden};
        int id = 0;
        for (long site : callSites()) siteHits.put(site, 0);
        for (Instruction in : listing.getInstructions(body, true)) {
            if (TcClass.classify(TcFixups.word(in)).contains("fma")) hasFma = true;
        }
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
                if (r.ok && !patchesMapped(v)) {
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
                goodVecs.add(v);
                goodRes.add(r);
                if (hasFma) checkFma(v, r);
                if (s == 0) covDev.addAll(r.cov);
                covAll.addAll(r.cov);
                for (int c : r.cov) blockHits.merge(c, 1, Integer::sum);
                for (long site : r.sites) siteHits.merge(site, 1, Integer::sum);
                id++;
            }
        }
    }

    // The vector run in the other multiply-add mode: do the results differ?
    void checkFma(Vec v, Result r) {
        fix.fusedMode = !TcFixups.FMA_FUSED;
        try {
            Result o = runOracle(v);
            fmaChecked++;
            if (!o.ok || !o.observable().equals(r.observable())) fmaSensitive++;
        } finally {
            fix.fusedMode = TcFixups.FMA_FUSED;
        }
    }

    // The 32-bit integer register parameters whose upper word changes the results (they become u64).
    List<Param> probeWidths(Proto proto) {
        List<Param> out = new ArrayList<>();
        Random pr = new Random(0x360L);
        for (Param q : proto.params) {
            if (!q.storage.matches("r([3-9]|10)") || !q.kind.matches("u32|s32")) continue;
            int i = Integer.parseInt(q.storage.substring(1)) - 3;
            for (int k = 0; k < Math.min(PROBE_VECTORS, goodVecs.size()); k++) {
                Vec v = goodVecs.get(k), w = new Vec();
                w.r = v.r.clone();
                w.f = v.f;
                w.rets = v.rets;
                w.frets = v.frets;
                w.patchAddr = v.patchAddr;
                w.patchData = v.patchData;
                w.r[i] ^= (long) (pr.nextInt() | 1) << 32;
                Result o = runOracle(w);
                if (o.ok && !o.observable().equals(goodRes.get(k).observable())) {
                    out.add(q);
                    break;
                }
            }
        }
        return out;
    }

    // The integer result kind the runs support: ret if every r3 has its extension, else the one that fits, else u64.
    String retKind(String ret) {
        if (!ret.matches("u32|s32") || goodRes.isEmpty()) return ret;
        boolean zero = true, sign = true;
        for (Result r : goodRes) {
            zero &= (r.r3 >>> 32) == 0;
            sign &= r.r3 == (long) (int) r.r3;
        }
        if (ret.equals("u32") ? zero : sign) return ret;
        return ret.equals("u32") && sign ? "s32" : ret.equals("s32") && zero ? "u32" : "u64";
    }

    void writeVector(int id, String set, Vec v, Result r) {
        vout.append("V ").append(id).append(' ').append(set).append("\nR");
        for (long x : v.r) vout.append(' ').append(hex64(x));
        vout.append("\nF");
        for (long x : v.f) vout.append(' ').append(hex64(x));
        vout.append("\nC");
        for (long x : v.rets) vout.append(' ').append(hex64(x));
        vout.append("\nD");
        for (long x : v.frets) vout.append(' ').append(hex64(x));
        vout.append('\n');
        for (int i = 0; i < v.patchAddr.size(); i++) {
            vout.append("M ").append(hex(v.patchAddr.get(i))).append(' ').append(bytesHex(v.patchData.get(i))).append('\n');
        }
        vout.append("E\n");
        eout.append("V ").append(id).append(' ').append(set).append('\n');
        eout.append("RET ").append(hex64(r.r3)).append(' ').append(hex64(r.f1)).append('\n');
        for (String w : r.writes) eout.append("W ").append(w).append('\n');
        for (String c : r.calls) eout.append("CALL ").append(c).append('\n');
        eout.append("COV");
        for (int c : r.cov) eout.append(' ').append(c);
        eout.append("\nE\n");
    }

    // The call sites: call instructions to code outside the body (helper families excepted), and branches to another
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

    // Every input must be representable by the C runner, whose guest memory is exactly the four regions.
    boolean patchesMapped(Vec v) {
        for (int i = 0; i < v.patchAddr.size(); i++) {
            if (!mapped(v.patchAddr.get(i), v.patchData.get(i).length)) return false;
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

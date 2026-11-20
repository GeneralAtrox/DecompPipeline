// Test vector generation for the Xbox 360 target: the dev and hidden sets, each vector's inputs (TcInputs), the
// oracle run, pointer repair, and the vectors.txt / expected.txt text (formats: x360_compare.py). LOCKED: the
// watcher agent may not change this file. Extended by TcPrepare.
//
// vectors.txt, per vector: V id set, R r3..r10, F f1..f13, C the 16 scripted r3 results, D the 16 scripted f1
// results (all 64-bit hex), M addr bytes (patches, big endian), A r12 CR (the entry seeds, TcOracle: only for a
// function reading r12 or a CR field before setting it), N W (TcStrict), with the exit state on Q G K (TcExit), E.
// expected.txt: V, RET r3 f1 (64-bit), W writes (outside the stack region), CALL records (TcOracle.callRecord; a CA
// line after one through a function pointer: TcTrack), FN (the words a float store left a NaN in: TcTrack), COV
// blocks, with the exit state on XS XF XC XV XW (TcExit: from a second, seeded run of the vector), E.
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
    static final int RESCUE_AFTER = 64;    // tries of a set without a valid vector before the inputs change (genRescue)
    static final int GIVE_UP_AFTER = 160;  // and before the set is given up: no vector is valid for inputs of either kind
    static final int VMX_TRIES = 3;        // a function with vector words may try this many times the usual tries (8 per vector
    //                                        wanted) while the first half of the oracle deadline lasts: few of its inputs are valid
    static final int PATCH_LINE = 0x2000;  // bytes per M line (runner.c: 64 KB lines, 32 KB patch buffer)

    StringBuilder vout = new StringBuilder(), eout = new StringBuilder();
    Map<String, Integer> invalid = new TreeMap<>();
    Set<Integer> covDev = new TreeSet<>(), covAll = new TreeSet<>();
    Set<Integer> dirsDev = new TreeSet<>(), dirsAll = new TreeSet<>();  // branch directions (TcDirs)
    Map<Integer, Integer> blockHits = new TreeMap<>();
    Map<Long, Integer> siteHits = new TreeMap<>();
    TcSwitchHits switchHits = new TcSwitchHits();  // oracle_summary.json "switch_hits" (report only)
    int[] valid = new int[2], tried = new int[2];
    int repaired = 0, fmaChecked = 0, fmaSensitive = 0;
    List<String> examples = new ArrayList<>();
    List<Vec> goodVecs = new ArrayList<>();
    List<Result> goodRes = new ArrayList<>();
    long outside = -1;
    boolean outOfTime = false, hasFma = false;

    // Each set has its own generator (rnd), so the hidden set does not depend on ndev.
    void makeVectors(Proto proto, List<Long> globals, List<String> hints, long seed, int ndev, int nhidden) {
        int[] want = {ndev, nhidden};
        for (long site : callSites()) siteHits.put(site, 0);
        findEntrySeeds();
        TcGateScan.Seeds sd = new TcGateScan.Seeds(prog(), body);
        if (sd.fma) hasFma = true;
        genVmx = !sd.vmxOps.isEmpty();
        long start = ManagementFactory.getRuntimeMXBean().getStartTime();
        long deadline = facts.oracleDeadlineS <= 0 ? 0 : start + facts.oracleDeadlineS * 1000;
        long half = facts.oracleDeadlineS <= 0 ? 0 : start + facts.oracleDeadlineS * 500;
        vectors:
        for (int s = 0; s < 2; s++) {
            rnd = new Random(seed * 31 + s * 0x5bd1e995L);
            entryRnd = new Random((seed * 31 + s * 0x5bd1e995L) ^ 0x5eedc0de12L);
            if (s == 1 && valid[0] == 0) break;  // no dev vector was valid: the function gets no harness, no hidden pass
            int usual = want[s] * 8;
            while (valid[s] < want[s] && tried[s] < (genVmx ? usual * VMX_TRIES : usual)) {
                if (deadline > 0 && System.currentTimeMillis() > deadline) {
                    outOfTime = true;
                    break vectors;
                }
                if (tried[s] >= usual && half > 0 && System.currentTimeMillis() > half) break;  // the extra tries are for the first half
                tried[s]++;
                if (valid[s] == 0 && tried[s] > GIVE_UP_AFTER) break;
                Vec v = makeVec(nextId % 4, proto, new ArrayList<>(globals));
                if (valid[s] == 0 && tried[s] > RESCUE_AFTER) genRescue(v, proto, tried[s]);
                Result r = attempt(v, hints, "");
                if (r == null) continue;
                if (!r.ok) {   // it left the body, or ran a refused instruction: the function is refused
                    if (r.outside >= 0) outside = r.outside;
                    break vectors;
                }
                valid[s]++;
                keep(s, v, r, false);  // false: the strict replay failed (TcStrict.dropped; TcPrepare takes it off valid)
            }
        }
        gateFields = sd.gateFields;
        seedExtras(sd.cw, proto, globals, hints, seed, deadline);
        budget = new TcRun.StepBudget(STEP_LIMIT);  // the checks on the finished set (probeWidths) run in full
    }

    int nextId = 0, dataBranches = 0;   // dataBranches: the first "branches into data" run is always an example

    // One try of vector v: its entry seeds and hints, the run and its repairs. The result when it is valid; null when
    // it is not (counted in invalid, tag first); an invalid result when it left the body or ran a refused instruction.
    Result attempt(Vec v, List<String> hints, String tag) {
        drawEntry(v);
        applyHints(v, hints);
        int repairs = 0;
        Result r = runOracle(v);
        while (!r.ok && r.faultAddr >= 0 && repairs < MAX_REPAIRS) {
            List<long[]> loads = new ArrayList<>();
            for (long[] ld : r.loads) loads.add(ld.clone());
            Vec nv = TcPtrSeed.repair(this, v, r, loads);   // call results and arrays of pointers, then repairVec
            if (nv == null) nv = repairEntry(v, r.faultAddr);
            if (nv == null) break;
            v = nv;
            repairs++;
            r = runOracle(v);
        }
        if (r.ok && !patchesMapped(v)) {
            r.ok = false;
            r.reason = "input patches memory outside RAM";
        }
        budget.note(r.ok, r.stepLimited, r.steps);
        if (!r.ok) {
            invalid.merge(tag + r.reason.replaceAll("[0-9a-f]{8}", "X").replaceAll("ram:\\w+", "X"), 1, Integer::sum);
            if (examples.size() < 6 || r.reason.startsWith("branches into data") && dataBranches++ == 0) examples.add(tag + describeFailure(r, repairs));
            return r.outside >= 0 || refused != null ? r : null;
        }
        if (repairs > 0) repaired++;
        return r;
    }

    // A valid vector of set s (0 dev, 1 hidden) written and counted (extra: seedExtras); false: TcStrict.replay failed.
    boolean keep(int s, Vec v, Result r, boolean extra) {
        int id = nextId++;
        if (facts.exitState) TcExit.seeded(this, v, r, id);
        if (!writeVector(id, s == 0 ? "dev" : "hidden", v, r)) return false;
        goodVecs.add(v); goodRes.add(r);
        if (hasFma) checkFma(v, r);
        if (s == 0) { covDev.addAll(r.cov); dirsDev.addAll(r.dirs); }
        covAll.addAll(r.cov); dirsAll.addAll(r.dirs);
        for (int c : r.cov) blockHits.merge(c, 1, Integer::sum);
        for (long site : r.sites) siteHits.merge(site, 1, Integer::sum);
        switchHits.add(r.bctrs);
        (extra ? seedCov : oldCov).addAll(r.cov);
        (extra ? seedCases : oldCases).addAll(r.cases);
        return true;
    }

    // ---------- the extra vectors (facts "seed_extra", TcSeedx) ----------
    // A function with vector words whose ops pick classes (TcSeedx.classes) gets facts.seedExtra more valid vectors per
    // set, after both usual sets, with ids after theirs and from their own generators (sets 2 and 3 of the same seed
    // formula), so the usual vectors are exactly what they were. Extra try k draws from class focus[k % n]: the gen
    // overrides below bias the TcInputs draws while seedFocus is set; with it 0 they draw exactly what TcInputs does.
    // They never refuse the function: out of time they stop ("stopped"), and one that leaves the body or runs a refused
    // instruction is dropped (counted in invalid as "extra: "). oracle_summary.json "seedx" reports what they reached
    // that no usual vector did (seedxJson).
    int seedClasses, seedWidths, seedFocus;
    int[][] gateFields = new int[0][];
    int[] seedValid = new int[2], seedTried = new int[2], seedPlain = new int[2];
    int seedSkipped = 0;
    String seedStopped = null;
    Set<Integer> oldCov = new TreeSet<>(), seedCov = new TreeSet<>();
    Set<String> oldCases = new TreeSet<>(), seedCases = new TreeSet<>();

    void seedExtras(int[] cw, Proto proto, List<Long> globals, List<String> hints, long seed, long deadline) {
        seedClasses = cw[0];
        seedWidths = cw[1];
        int[] focus = TcSeedx.focus(seedClasses);
        if (facts.seedExtra <= 0 || focus.length == 0 || valid[0] == 0 || outOfTime || outside >= 0 || refused != null) return;
        for (int s = 0; s < 2 && seedStopped == null; s++) {
            rnd = new Random(seed * 31 + (s + 2) * 0x5bd1e995L);
            entryRnd = new Random((seed * 31 + (s + 2) * 0x5bd1e995L) ^ 0x5eedc0de12L);
            while (seedValid[s] < facts.seedExtra && seedTried[s] < facts.seedExtra * 8) {
                if (deadline > 0 && System.currentTimeMillis() > deadline) {
                    seedStopped = "deadline";
                    break;
                }
                int fo = seedFocus = focus[seedTried[s]++ % focus.length];
                Vec v = null;
                Result r;
                try {
                    v = makeVec(0, proto, new ArrayList<>(globals));
                    if (seedFocus == TcSeedx.GATE) TcSeedx.gateFill(gateFields, v.r, v.patchAddr, v.patchData, SCR, GEN_HEAP_LO, rnd, scrPtr());
                    r = attempt(v, hints, "extra: ");
                } finally {
                    seedFocus = 0;
                }
                if (r == null) continue;
                if (!r.ok) {
                    refused = null;  // the extra vector is dropped; the function keeps its usual ones
                    continue;
                }
                boolean novel = TcSeedx.novel(r.cov, r.cases, covAll, oldCases, seedCases) || fo == TcSeedx.GATE && TcSeedx.rare(r.cov, blockHits);
                if (!TcSeedx.keepExtra(novel, seedPlain[s], facts.seedExtra)) {
                    seedSkipped++;   // valid but nothing new, with half the quota already plain: search on
                    continue;
                }
                if (!keep(s, v, r, true)) {  // the strict replay failed (TcStrict): not written, and not a usual set's drop
                    TcStrict.dropped[s]--;
                    invalid.merge("extra: strict flags: the replay failed", 1, Integer::sum);
                    continue;
                }
                if (!novel) seedPlain[s]++;
                seedValid[s]++;
            }
        }
    }

    // The TcInputs draws an extra vector biases (seedFocus); with seedFocus 0 each is exactly TcInputs' own.
    long word() {
        if (seedFocus == TcSeedx.SAT && rnd.nextInt(10) < 6) return TcSeedx.satWord(rnd, seedWidths);
        if (seedFocus == TcSeedx.TINY && rnd.nextInt(10) < 5) return TcSeedx.tinyFloat(rnd) & 0xffffffffL;
        if (seedFocus == TcSeedx.ALIGN || seedFocus == TcSeedx.GATE) return TcSeedx.alignWord(rnd, scrPtr());
        return super.word();
    }

    int niceFloat() {
        if (seedFocus == TcSeedx.TINY && rnd.nextInt(10) < 6) return TcSeedx.tinyFloat(rnd);
        return super.niceFloat();
    }

    long scrPtr() {
        long p = super.scrPtr();
        return seedFocus == TcSeedx.ALIGN || seedFocus == TcSeedx.GATE ? TcSeedx.align(p) : p;
    }

    long genChunk(Vec v, long offset) {
        int n = v.patchData.size();
        long p = super.genChunk(v, offset);
        if (seedFocus == TcSeedx.SAT && v.patchData.size() > n) {
            byte[] d = v.patchData.get(n);
            for (int i = 0; i < d.length; i += 4) if (rnd.nextInt(10) < 4) genPut(d, i, TcSeedx.satWord(rnd, seedWidths));
        }
        return p;
    }

    // oracle_summary.json "seedx" (a function with vector words, seed_extra on): its classes, the extra vectors valid
    // and tried per set, why they stopped, and what they reached that no usual vector did: blocks ("new_blocks", their
    // start addresses) and vector-op cases ("new_cases", "pc:case", TcSeedx.event); "old_cases" the usual vectors'.
    String seedxJson(List<long[]> blocks) {
        if (facts.seedExtra <= 0 || !genVmx || valid[0] + valid[1] == 0) return "";
        List<String> nb = new ArrayList<>(), nc = new ArrayList<>();
        for (int c : seedCov) if (!oldCov.contains(c) && c < blocks.size()) nb.add(hex(blocks.get(c)[0]));
        for (String c : seedCases) if (!oldCases.contains(c)) nc.add(c);
        return String.format("\"seedx\":{\"extra\":%d,\"classes\":%s,\"dev_valid\":%d,\"dev_tried\":%d,"
                + "\"hidden_valid\":%d,\"hidden_tried\":%d,\"skipped\":%d,\"stopped\":%s,\"new_blocks\":%s,\"new_cases\":%s,"
                + "\"old_cases\":%s},", facts.seedExtra, TcSeedx.names(seedClasses), seedValid[0], seedTried[0],
            seedValid[1], seedTried[1], seedSkipped, seedStopped == null ? "null" : "\"" + seedStopped + "\"", TcPrepare.jsonList(nb),
            TcPrepare.jsonList(nc), TcPrepare.jsonList(oldCases));
    }

    // The entry seeds of one vector (TcOracle.findEntrySeeds), drawn after its inputs from their own generator (entryRnd,
    // per set), so a function without any draws nothing more and keeps its vectors, and one with seeds keeps every
    // other input's draws (rnd's stream is not moved by them): r12 a fresh pointer-rich chunk (a funclet reads its
    // parent's frame there; 32 bits, as the port's TcArgs.r12), the seeded CR fields random bits.
    Random entryRnd;

    void drawEntry(Vec v) {
        if (!seedR12 && seedCrMask == 0) return;
        Random keep = rnd;
        rnd = entryRnd;   // genChunk draws from rnd
        try {
            long r12 = seedR12 ? genChunk(v, 0) & 0xffffffffL : 0;
            entryVals.put(v, new long[] {r12, rnd.nextInt() & seedCrMask, 0});
        } finally {
            rnd = keep;
        }
    }

    // A fault the inputs' repair does not explain, near r12: once per vector, r12 becomes a chunk that holds the access.
    Vec repairEntry(Vec v, long faultAddr) {
        long[] e = entryVals.get(v);
        if (!seedR12 || e == null || e[2] > 0) return null;
        long d = faultAddr - e[0];
        if (d < -0x100 || d >= GEN_MAX_OFFSET) return null;
        Random keep = rnd;
        rnd = entryRnd;
        try {
            e[0] = genChunk(v, d) & 0xffffffffL;
        } finally {
            rnd = keep;
        }
        e[2]++;
        return v;
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
                if (entryVals.containsKey(v)) entryVals.put(w, entryVals.get(v));
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

    boolean writeVector(int id, String set, Vec v, Result r) {  // false: TcStrict.replay failed, nothing is written
        Result first = r;  // the vector's first run: TcExit.seeded's lines are on it, not on the strict replay's run
        if ((r = TcStrict.replay(this, v, r, set)) == null) return false;
        vout.append("V ").append(id).append(' ').append(set).append("\nR");
        for (long x : v.r) vout.append(' ').append(hex64(x));
        vout.append("\nF");
        for (long x : v.f) vout.append(' ').append(hex64(x));
        vout.append("\nC");
        for (long x : v.rets) vout.append(' ').append(hex64(x));
        vout.append("\nD");
        for (long x : v.frets) vout.append(' ').append(hex64(x));
        vout.append('\n');
        for (int i = 0; i < v.patchAddr.size(); i++) {  // runner.c reads lines of 64 KB: at most PATCH_LINE bytes each
            byte[] d = v.patchData.get(i);
            for (int o = 0; o < d.length; o += PATCH_LINE) {
                vout.append("M ").append(hex(v.patchAddr.get(i) + o)).append(' ')
                    .append(bytesHex(Arrays.copyOfRange(d, o, Math.min(d.length, o + PATCH_LINE)))).append('\n');
            }
        }
        vout.append(TcStrict.vec(r));  // lines N, W (TcStrict)
        long[] e = entryVals.get(v);
        if (e != null) vout.append("A ").append(hex64(e[0])).append(' ').append(hex(e[1])).append('\n');
        vout.append(first.exitIn).append("E\n");  // lines Q, G, K (TcExit), from the vector's first run
        eout.append("V ").append(id).append(' ').append(set).append('\n');
        eout.append("RET ").append(hex64(r.r3)).append(' ').append(hex64(r.f1)).append('\n');
        for (String w : r.writes) eout.append("W ").append(w).append('\n');
        for (String c : r.calls) eout.append("CALL ").append(c).append('\n');
        eout.append(TcTrack.exit(r.tr));
        eout.append(TcStrict.exp(r)).append(first.exit);  // lines CJ, CS, XR (TcStrict); XS..XW (TcExit)
        eout.append("COV");
        for (int c : r.cov) eout.append(' ').append(c);
        eout.append("\nE\n");
        return true;
    }

    // The call sites: call instructions to code outside the body (helper families excepted), and branches to another
    // function's entry or the other tail calls (TcRun.tailJump).
    List<Long> callSites() {
        List<Long> out = new ArrayList<>();
        for (Instruction in : listing.getInstructions(body, true)) {
            boolean site = false;
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isFlow()) continue;
                Address to = r.getToAddress();
                if (body.contains(to) && !selfCall(r) || inline.contains(to)) continue;
                if (r.getReferenceType().isCall() || fm.getFunctionAt(to) != null || tailJump(in, to)) site = true;
            }
            if (in.getFlowType().isCall() && in.getFlowType().isComputed()) site = true;
            if (in.getFlowType().isComputed() && tailJump(in, null)) site = true;
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

// Test vector generation: the dev and hidden sets, each vector's inputs (TcInputs, plus the entry registers the
// locked side decides), the oracle run, pointer repair, and the vectors.txt / expected.txt text. LOCKED: the watcher
// agent may not change this file. Extended by TcPrepare.
// vectors.txt, per vector: V id set, R r4..r7, F fr4..fr11, X xf0..xf15, H fr12..fr15 (runtime state), L r0..r3
// (parameters there), G r8..r14 (runtime state), A the live callees' drawn deltas (TcLive; after header lines K),
// Q fr0..fr15 (Proto.fpuState), C the scripted call results, M patches, E.
// expected.txt: V, RET r0 fr0, RETX (a nonstandard result register, or a u64's high word in r1), HALT [SPIN] (the run
// ended in an endless `bra .`: TcExit; or spun: TcSpin), W writes, CALL records (TcOracle.callRecord), XF, FH
// fr12..fr15 and GH r8..r14 at exit, QH fr0..fr15 and the FPSCR mode word (TcOracle.modeWord) at exit, COV blocks, E.
// Lines H, L, G, Q, RETX, FH, GH, QH (and TcFpscr's N, FS, FC) appear only for functions that have them, so other
// harnesses are unchanged. A first line "P fpscr" (fpscrLine) gives the entry FPSCR when the oracle models a rounding
// mode other than nearest (TcFacts.fpscrModel): the C runner sets MXCSR.RC from its RM. Else it rounds to nearest.
// Call-site coverage (report only): siteHits counts, per call site (callSites), the valid vectors whose run reached
// its mocked callee; TcPrepare reports them in oracle_summary.json "call_sites", the checker in the verdict detail.
// Vectors are float-guided (TcFguide) and, with TcFacts.coverageInputs, constant-guided (TcGuide) before the hints.
// Reach pass (TcFacts.reachRuns, TcReach): TcPrepare then adds vectors that reach call sites no vector reached.
// Budget (TcFacts.oracleBudgetS, from config "ghidra_timeout_s"; work, never the clock: TcBudget): when most tries
// run to STEP_LIMIT (a loop whose count comes from the inputs: a random count word), generation would outlast Ghidra's
// timeout and the preparation would die without a verdict. Past the budget it stops (outOfTime) and TcPrepare refuses
// the function instead.
// Call-cap fail-fast (CAP_FAIL_FAST): after that many tries in a set, at least 3/4 of them at CALL_LIMIT (a sentinel
// walk whose end marker mocked memory never holds), a pass whose valid rate would reach under half of minValid by the
// set's try cap ends there: each such try runs every repair at the call limit, so the full pass held a slot ~8 min for the same
// skip. A set on course for minValid is unchanged. A call-capped run first gets the walk's end marker planted
// (TcSentinel), so only walks it cannot see (a callee result, a marker the code transforms) still count.
// Rescue (TcFacts.minValid, config "min_valid_vectors"): when a set ends with fewer valid vectors than the checker needs
// (the function would be skipped), both sets are made again with every vector constant-guided and with the scripted
// call results repaired like pointers (repairRet): a handler whose path depends on an opcode a mocked fetch returns,
// and on the pointers mocked fetchers return. The rescue pass is kept when it gives more valid vectors (TcPrepare
// reports "rescued"); a function the first pass serves never sees it, so its vectors are unchanged. In the rescue
// pass a store outside RAM stops the run at the store, and the input its base came from is repaired (TcProvenance).
// Overtime rescue (TcFacts.oracleRescueS, TcCount): a first pass out of its budget gets that much more work for a
// rescue pass with a tenth of the step limit in which a run hitting it gets its loop counts cut (TcCount.repair). It
// is kept when both sets reach the rescue threshold; otherwise the function is refused as before, the reason saying
// what the overtime rescue made and where the loop compares. Functions the first pass finishes never see it.
// Step-limit fail-fast (STEP_FAIL_FAST): after that many first-pass dev tries, at least a quarter of them at the step
// limit (each such try costs seconds), a first pass that cannot finish in its budget ends: no valid vector and 3/4 of
// the tries at the step limit, or a valid rate too low to fill both sets by the budget's end (Spawn 8c01bde0: 27 of 64
// valid when its 750 s ran out). The overtime rescue starts at once and also gets the work the first pass would have
// spent (TcRescue.overtime).
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;

public abstract class TcVectors extends TcOracle {
    static final int MAX_REPAIRS = 12; // pointer repairs per vector (TcInputs.repairVec, TcCalls.repair)
    static final int CAP_FAIL_FAST = 64; // tries per set before the call-cap fail-fast may end a pass
    String capNote = null;  // the call-cap fail-fast ended a pass; TcPrepare refuses with it when no vector is left
    static final int STEP_FAIL_FAST = 16; // first-pass dev tries before the step-limit fail-fast may end it
    boolean earlyOvertime = false;  // the step-limit fail-fast ended the first pass (TcRescue.overtime)
    String earlyNote = "";

    StringBuilder vout = new StringBuilder(), eout = new StringBuilder();
    Map<String, Integer> invalid = new TreeMap<>();
    Set<Integer> covDev = new TreeSet<>(), covAll = new TreeSet<>();
    Map<Integer, Integer> blockHits = new TreeMap<>();
    Map<Long, Integer> siteHits = new TreeMap<>();
    List<TcReach.Entry> seeds = new ArrayList<>(); // the valid vectors, for the reach pass (TcReach)
    int[] valid = new int[2], tried = new int[2]; TcAlign align = new TcAlign();  // TcAlign: vectors left out
    int repaired = 0; Set<Integer> dirsDev = new TreeSet<>(), dirsAll = new TreeSet<>();  // branch directions (TcDirs)

    String fpscrLine() {
        return facts.fpscrModel && (facts.fpscrEntry & 3) != 0 ? "P " + hex(facts.fpscrEntry) + "\n" : "";
    }
    List<String> examples = new ArrayList<>();
    long outside = -1;
    boolean outOfTime = false, rescued = false;
    long workEnd = 0;        // the overtime rescue's own budget end (work()), else 0
    String overtimeNote = "", countSites = ""; // the overtime rescue's outcome (TcPrepare); TcCount's uncut loop compares
    boolean[] lowWhich = new boolean[4], giWhich = new boolean[7]; // the entry registers entryLow/entryGi really set

    long work() { return TcBudget.work(workSteps, workRuns); }  // the work so far (TcBudget)

    // The dev and hidden sets. Each set has its own generator (rnd), so the hidden set does not depend on ndev.
    void makeVectors(Proto proto, List<Long> globals, List<String> hints, long seed, int ndev, int nhidden) {
        makeSets(proto, globals, hints, seed, ndev, nhidden, false);
        if (outside < 0 && outOfTime) {
            TcRescue.overtime(this, proto, globals, hints, seed, ndev, nhidden);
            return;
        }
        if (outside >= 0 || outOfTime
            || (valid[0] >= Math.min(facts.minValid, ndev) && valid[1] >= Math.min(facts.minValid, nhidden))) return;
        TcRescue first = TcRescue.take(this);
        makeSets(proto, globals, hints, seed, ndev, nhidden, true);
        if (outside < 0 && (outOfTime || Math.min(valid[0], valid[1]) <= Math.min(first.valid[0], first.valid[1]))) {
            first.restore(this);  // no better: the first pass stands
        } else {
            rescued = true;
        }
    }

    void makeSets(Proto proto, List<Long> globals, List<String> hints, long seed, int ndev, int nhidden,
                  boolean rescue) {
        String[] sets = {"dev", "hidden"};
        int[] want = {ndev, nhidden};
        int id = 0, idTry = 0;
        storeFaults = rescue;  // TcProvenance
        boolean[] low = new boolean[4], lowPtr = new boolean[4];
        for (int i = 0; i < proto.params.size(); i++) {
            String st = proto.params.get(i).storage;
            if (!st.matches("r[0-3]")) continue;
            low[st.charAt(1) - '0'] = true;
            lowPtr[st.charAt(1) - '0'] = proto.paramIsPointer.get(i);
        }
        lowWhich = low;
        giWhich = giMask(proto.giState);
        entryRet = proto.retReg != null ? proto.retReg : proto.ret.equals("u64") ? facts.retInt64Hi : null;
        for (long site : callSites()) siteHits.put(site, 0);
        List<Long> consts = facts.coverageInputs || rescue ? TcGuide.constants(listing, body, this::poolValue) : List.of();
        long passStart = work(), end = workEnd > 0 ? workEnd : TcBudget.of(facts, facts.oracleBudgetS);
        int[] capped = new int[2];  // tries ending at CALL_LIMIT, per set (the call-cap fail-fast)
        int[] limited = new int[2]; // tries ending at the step limit, per set (the step-limit fail-fast)
        vectors:
        for (int s = 0; s < 2; s++) {
            rnd = new Random(seed * 31 + s * 0x5bd1e995L);
            while (valid[s] < want[s] && tried[s] < want[s] * 8) {
                if (TcBudget.over(work(), end)) {
                    outOfTime = true;
                    break vectors;
                }
                if (tried[s] >= CAP_FAIL_FAST && capped[s] * 4 >= tried[s] * 3
                    && (long) valid[s] * want[s] * 16 < (long) Math.min(facts.minValid, want[s]) * tried[s]) {
                    capNote = String.format("oracle: stopped after %d %s tries, %d valid, %d at the call limit (a loop"
                        + " whose end the mocks never give: a sentinel walk or a callee result)", tried[s], sets[s],
                        valid[s], capped[s]);
                    break vectors;
                }
                long left = want[0] - valid[0] + want[1];
                if (!rescue && s == 0 && facts.oracleRescueS > 0 && tried[s] >= STEP_FAIL_FAST
                    && limited[s] * 4 >= tried[s] && (valid[s] == 0 ? limited[s] * 4 >= tried[s] * 3
                    : TcBudget.wontFinish(work(), passStart, left, valid[s], end))) {  // the rescue now
                    earlyNote = String.format("%d of %d dev tries at the step limit, %d valid", limited[s], tried[s],
                        valid[s]);
                    earlyOvertime = outOfTime = true;
                    break vectors;
                }
                tried[s]++;
                Vec[] box = {null}; int[] nrep = {0};
                Result r = attempt(proto, globals, hints, seed, id, idTry++, lowPtr, consts, rescue, box, nrep);
                Vec v = box[0]; int repairs = nrep[0];
                if (!r.ok) {
                    if (r.reason.startsWith("more than " + CALL_LIMIT + " calls")) capped[s]++;
                    if (r.stepLimited) limited[s]++;
                    invalid.merge(r.reason.replaceAll("[0-9a-f]{8}", "X").replaceAll("ram:\\w+", "X"), 1, Integer::sum);
                    if (examples.size() < 6) examples.add(describeFailure(r, repairs));
                    if (r.outside >= 0) {  // TcOracle.entersCallee
                        outside = r.outside;
                        break vectors;
                    }
                    continue;
                }
                if (repairs > 0) repaired++;
                valid[s]++;
                writeVector(id, sets[s], v, r);
                if (facts.reachRuns > 0 || facts.targetedVectors) seeds.add(TcReach.entry(this, v, r));
                if (s == 0) { covDev.addAll(r.cov); dirsDev.addAll(r.dirs); }
                covAll.addAll(r.cov); dirsAll.addAll(r.dirs);
                for (int c : r.cov) blockHits.merge(c, 1, Integer::sum);
                for (long site : r.sites) siteHits.merge(site, 1, Integer::sum);
                id++; idTry = 0;
            }
        }
    }

    // Try idTry of vector id, run with its repairs (runRepaired): box[0] the vector as last run, nrep[0] its repairs.
    Result attempt(Proto proto, List<Long> globals, List<String> hints, long seed, int id, int idTry, boolean[] lowPtr,
                   List<Long> consts, boolean rescue, Vec[] box, int[] nrep) {
        Vec v = makeVec(id % 4, proto, new ArrayList<>(globals)); TcFpscr.Sz.draw(id);  // TcFpscr.Sz: the entry FPSCR.SZ
        entryLate = TcLate.draw(facts, seed, id, idTry, v.rets.length);  // TcLate: calls 17 on
        entryFh = proto.frState != 0 ? stateValues(seed, id, idTry) : null;
        boolean[] w = lowWhich; entryLow = w[0] || w[1] || w[2] || w[3] ? intValues(seed ^ 0x1b873593L, id, idTry, w, lowPtr) : null;
        entryGi = proto.giState != 0 ? intValues(seed ^ 0x5851f42dL, id, idTry, giWhich, null) : null;
        if (live != null) live.draw(seed, id, idTry);  // TcLive: the drawn deltas (line A)
        if (sq != null) sq.draw(entryGi, seed, id, idTry);  // TcSq: rN in the store-queue window (header line S)
        Random h = new Random(seed * 0x2545f4914f6cdd1dL + id * 0x632be59bd9b4e019L + idTry * 0x85ebca6bL);
        if (facts.coverageInputs || rescue) TcGuide.guide(v, proto, consts, globals, h, facts.scrLo, rescue);
        TcFguide.guide(v, proto, listing, body, mem, seed, id, idTry, rescue);
        applyHints(v, TcLate.ranges(v, hints, seed, id, idTry));  // TcLate takes the genretfn lines
        entryFq = proto.fpuState ? fpuValues(seed, id, idTry + 1, v.fr) : null;
        box[0] = v; return runRepaired(box, h, consts, rescue, nrep);
    }

    // One vector's run with its repairs (the overtime rescue's count cuts, TcProvenance, TcCalls, pointer, entry and
    // the rescue pass's result repair), at most MAX_REPAIRS: box[0] is the vector as last run, nrep[0] the repairs.
    Result runRepaired(Vec[] box, Random h, List<Long> consts, boolean rescue, int[] nrep) {
        Vec v = box[0];
        Result r = runOracle(v);
        int repairs = 0;
        TcCount.Cuts cuts = new TcCount.Cuts();
        TcSentinel ends = new TcSentinel();
        while (!r.ok && (r.faultAddr >= 0 || (countRepair && r.stepLimited) || r.callCapped) && repairs < MAX_REPAIRS) {
            // a table walk past its end: plant the end marker (TcSentinel); a rescue run falls back to a count cut
            if (r.callCapped || r.stepLimited && ends.walksToMarker(this, r, h)) {
                if (ends.repair(this, v, r, h)) {
                    repairs++;
                    r = runOracle(v);
                    continue;
                }
                if (r.callCapped) break;
            }
            if (r.stepLimited) {  // overtime rescue: cut the loop's counts (TcCount)
                if (!r.count.repair(v, r, stepLimit, h, cuts)) {
                    countSites = r.count.sites();
                    break;
                }
                repairs++;
                r = runOracle(v);
                continue;
            }
            if (!countRepair && (r.storeBase >= 0 && TcProvenance.repair(this, v, r) || TcLate.repair(this, v, r))) {
                repairs++;
                r = runOracle(v);
                continue;
            }
            List<long[]> loads = new ArrayList<>();
            for (long[] ld : r.storeBase >= 0 ? TcProvenance.inputLoads(r.loads) : r.loads) loads.add(ld.clone());
            cuts.hide(v);  // a cut count stays a count (empty outside the overtime rescue)
            Vec nv = r.callFault ? TcCalls.repair(this, v, r.faultAddr, loads, r.calls.size())
                : repairVec(v, r.faultAddr, r.faultWrite, loads);
            cuts.restore(v);
            ends.restore(v);
            if (nv != null) cuts.restore(nv);
            if (nv != null) ends.restore(nv);
            if (nv == null && (r.callFault || !TcRepair.entry(this, v, r.faultAddr)
                && !(rescue && TcRepair.ret(this, v, r.faultAddr, r.calls.size(), consts, h))
                && !TcRepair.index(this, v, r.faultAddr, loads))) break;
            if (nv != null) v = nv;
            repairs++;
            r = runOracle(v);
        }
        if (r.ok && !patchesInRam(v)) {
            r.ok = false;
            r.reason = "input patches memory outside RAM";
        }
        box[0] = v;
        nrep[0] = repairs;
        return r;
    }

    void writeVector(int id, String set, Vec v, Result r) {  // not written: a failed strict replay, an address error
        if ((r = TcStrict.replay(this, v, r, set)) == null || align.drop(set, r)) return;  // TcStrict; TcAlign
        vout.append("V ").append(id).append(' ').append(set).append("\nR");
        for (long x : v.r) vout.append(' ').append(hex(x));
        vout.append("\nF");
        for (int x : v.fr) vout.append(' ').append(hex(x));
        vout.append("\nX");
        for (int x : v.xf) vout.append(' ').append(hex(x));
        if (entryFh != null) {
            vout.append("\nH");
            for (int x : entryFh) vout.append(' ').append(hex(x));
        }
        if (entryLow != null) {
            vout.append("\nL");
            for (long x : entryLow) vout.append(' ').append(hex(x));
        }
        if (entryGi != null) {
            vout.append("\nG");
            for (long x : entryGi) vout.append(' ').append(hex(x));
        }
        if (live != null) vout.append(live.vectorLine(true));
        if (entryFq != null) {
            vout.append("\nQ");
            for (int x : entryFq) vout.append(' ').append(hex(x));
        }
        vout.append(TcSr.line(sr, v)).append(TcFpscr.line(r.fs)).append(TcFpscr.Sz.line()).append("\nC");  // lines I (TcSr), N (TcFpscr), z (TcFpscr.Sz)
        for (long x : v.rets) vout.append(' ').append(hex(x));
        vout.append(TcLate.tail(entryLate, r.calls.size() - v.rets.length)).append('\n');
        for (int i = 0; i < v.patchAddr.size(); i++) {
            vout.append("M ").append(hex(v.patchAddr.get(i))).append(' ').append(bytesHex(v.patchData.get(i))).append('\n');
        }
        vout.append(TcStrict.vec(r)).append("E\n");  // lines N, W (TcStrict)
        eout.append("V ").append(id).append(' ').append(set).append('\n');
        eout.append("RET ").append(hex(r.r0)).append(' ').append(hex(r.fr0)).append('\n').append(TcHw.exit(r));
        if (entryRet != null) eout.append("RETX ").append(hex(r.retx)).append('\n');
        if (r.halted) eout.append(r.spun ? "HALT SPIN\n" : "HALT\n");  // TcExit, TcSpin
        for (String w : r.writes) eout.append("W ").append(w).append('\n');
        for (String c : r.calls) eout.append("CALL ").append(c).append('\n');
        eout.append("XF");
        for (int x : r.xf) eout.append(' ').append(hex(x));
        if (entryFh != null) {
            eout.append("\nFH");
            for (int x : r.fh) eout.append(' ').append(hex(x));
        }
        if (entryGi != null) {
            eout.append("\nGH");
            for (long x : r.gi) eout.append(' ').append(hex(x));
        }
        if (entryFq != null) {
            eout.append("\nQH");
            for (int x : r.fq) eout.append(' ').append(hex(x));
            eout.append(' ').append(hex(r.mode));
        }
        eout.append(TcStrict.exp(r)).append(TcOut.exp(r)).append(TcTflag.exp(r)).append("\nCOV");  // lines CS, XR (TcStrict)
        for (int c : r.cov) eout.append(' ').append(c);
        eout.append("\nE\n");
    }

    // The call sites: call instructions to code outside the body, and jumps (bra, bt/bf) to another function's entry.
    List<Long> callSites() {
        List<Long> out = new ArrayList<>();
        for (Instruction in : listing.getInstructions(body, true)) {
            boolean site = in.getFlowType().isCall(), inside = false;
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isFlow()) continue;
                Address to = r.getToAddress();
                if (body.contains(to)) inside = true;
                else if (r.getReferenceType().isJump() && fm.getFunctionAt(to) != null) site = true;
            }
            if (site && !(inside && in.getFlowType().isCall())) out.add(in.getAddress().getOffset());
        }
        return out;
    }

    static boolean[] giMask(int giState) {
        boolean[] out = new boolean[7];
        for (int i = 0; i < 7; i++) out[i] = (giState & (1 << (24 + i))) != 0;
        return out;
    }

    // fr12..fr15 at entry for a function with runtime state: from their own generator, seeded by the function seed,
    // the vector id and the attempt for that id (a rejected attempt gets new values), so the TcInputs sequence
    // (rnd), and with it every other function's vectors, is unchanged. Values as TcInputs' niceFloat draws them.
    static int[] stateValues(long seed, int id, int attempt) {
        Random h = new Random(seed * 0x9e3779b97f4a7c15L + id * 0x632be59bd9b4e019L + attempt * 0x85ebca6bL);
        int[] out = new int[4];
        for (int i = 0; i < 4; i++) {
            switch (h.nextInt(10)) {
                case 0: out[i] = Float.floatToIntBits(0f); break;
                case 1: out[i] = Float.floatToIntBits(1f); break;
                case 2: out[i] = Float.floatToIntBits(-1f); break;
                case 3: out[i] = Float.floatToIntBits(0.5f); break;
                case 4: out[i] = Float.floatToIntBits((float) (h.nextInt(201) - 100)); break;
                case 5: out[i] = Float.floatToIntBits(h.nextFloat() * 2 - 1); break;
                case 6: out[i] = Float.floatToIntBits((h.nextFloat() * 2 - 1) * 1000f); break;
                case 7: out[i] = Float.floatToIntBits(h.nextFloat() * 255f); break;
                case 8: out[i] = Float.floatToIntBits((h.nextFloat() * 2 - 1) * 65536f); break;
                default:
                    do {
                        out[i] = h.nextInt();
                    } while (((out[i] >> 23) & 0xff) == 0xff);
            }
        }
        return out;
    }

    // fr0..fr15 at entry for a Proto.fpuState function: fr4..fr11 as TcInputs drew them (v.fr, hints applied), the
    // others from stateValues' generator under their own seeds (TcInputs' sequence is unchanged).
    static int[] fpuValues(long seed, int id, int attempt, int[] fr) {
        int[] lo = stateValues(seed ^ 0x27d4eb2f165667c5L, id, attempt);
        int[] hi = stateValues(seed ^ 0x165667b19e3779f9L, id, attempt);
        int[] out = new int[16];
        for (int i = 0; i < 4; i++) {
            out[i] = lo[i];
            out[12 + i] = hi[i];
        }
        System.arraycopy(fr, 0, out, 4, 8);
        return out;
    }

    // Integer entry registers outside TcInputs' Vec (r0..r3 parameters, r8..r14 state), from their own generator like
    // stateValues. Registers not in `which` stay 0. Pointer-like values point into the scratch window (target.json
    // memory "scratch"): state registers are mostly pointers (a display-list cursor), parameters mixed like intArg.
    long[] intValues(long seed, int id, int attempt, boolean[] which, boolean[] ptr) {
        Random h = new Random(seed * 0x9e3779b97f4a7c15L + id * 0x632be59bd9b4e019L + attempt * 0x85ebca6bL);
        long[] out = new long[which.length];
        long words = (facts.scrHi - facts.scrLo) / 4;
        for (int i = 0; i < which.length; i++) {
            if (!which[i]) continue;
            int k = h.nextInt(100);
            long scr = facts.scrLo + 4L * h.nextInt((int) words);
            if (ptr == null || ptr[i]) out[i] = k < 90 ? scr : 0;
            else if (k < 40) out[i] = scr;
            else if (k < 55) out[i] = 0;
            else if (k < 75) out[i] = h.nextInt(17);
            else if (k < 82) out[i] = (-1 - h.nextInt(8)) & 0xffffffffL;
            else out[i] = h.nextInt() & 0xffffffffL;
        }
        return out;
    }

    // Every input must be representable by the C runner, whose guest memory is exactly [RAM_LO, RAM_HI).
    // (A patch outside it would make the original's reads there succeed where any C must fault.)
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
            b.append(String.format(" [pc %s: %d bytes @%s = %s]", hex(ld[0]), ld[2], hex(ld[1]), hex(ld[3])));
        }
        return b.toString();
    }
}

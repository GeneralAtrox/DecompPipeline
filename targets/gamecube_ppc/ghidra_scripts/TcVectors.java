// Test vector generation for the PowerPC target: the dev and hidden sets, each vector's inputs (TcInputs), the
// oracle run, pointer repair, and the vectors.txt / expected.txt text. LOCKED: the watcher agent may not change
// this file. Extended by TcPrepare.
//
// vectors.txt, per vector: V id set, R r3..r10, F f1..f8 (64-bit register bits), C the 16 scripted r3 results (then
// the later calls' up to the last repaired one, TcOracle.lateOf: only in a vector that faulted through one),
// D the 16 scripted f1 results (64-bit), H the 16 scripted hardware words (only when the run read a hardware register),
// X r11 r14..r31 f14..f31 (64-bit; only for a save/restore helper entry: frame), Q the scripted r4 of each call to a
// callee returning r3:r4 (only when the run made one: TcCallFx), K call addr bytes (a callee write: only from a last
// resort, lastResort), M addr bytes (patches, big endian), E.
// expected.txt: V, RET r3 f1 (64-bit) r4, W writes (below the stack window), CALL records (TcOracle.callRecord),
// COV blocks, E.
// Hint lines "genretptr CALLEE [OFF [PTR..]]" are the oracle's (retPtrHints, TcCallFx.grow: every call to CALLEE returns a
// fresh chunk); the others go to the workspace's TcInputs.applyHints.
// Deadline: generation stops before Ghidra's timeout; TcPrepare then refuses the function. With oracle_work_per_s
// (target.json) the limit is work, never the clock (TcBudget, as on the Dreamcast and Saturn: TcFacts.oracleBudgetS),
// so machine load cannot change a harness; without it, the wall-clock TcFacts.oracleDeadlineS.
import java.lang.management.ManagementFactory;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;

public abstract class TcVectors extends TcOracle {
    static final int MAX_REPAIRS = 12;
    static final int LAST_REPAIRS = 12;  // lastResort's rounds, after the repairs gave up
    static final int GROW_RUNS = 8;      // runGrow's reruns of a vector whose run set genretptr results

    StringBuilder vout = new StringBuilder(), eout = new StringBuilder();
    Map<String, Integer> invalid = new TreeMap<>();
    Set<Integer> covDev = new TreeSet<>(), covAll = new TreeSet<>();
    Map<Integer, Integer> blockHits = new TreeMap<>();
    Map<Long, Integer> siteHits = new TreeMap<>();
    TcSwitchHits switchHits = new TcSwitchHits();  // oracle_summary.json "switch_hits" (report only)
    int[] valid = new int[2], tried = new int[2];
    int repaired = 0; Set<Integer> dirsDev = new TreeSet<>(), dirsAll = new TreeSet<>();  // branch directions (TcDirs)
    List<String> examples = new ArrayList<>();
    List<TcBoundPass.Base> bases = new ArrayList<>();  // the valid vectors, for targeted_vectors (TcBoundPass)
    long outside = -1;
    boolean outOfTime = false;
    static final int OVERRUNS = 8;  // runs at the step limit before the limit drops to STEP_LIMIT / OVERRUN_CUT
    static final int OVERRUN_CUT = 16;
    int overruns;

    long work() { return TcBudget.work(workSteps, workRuns); }  // the work so far (TcBudget)

    // A vector's hardware words, from their own generator so that no other input changes: a device's flags, set and clear.
    static long[] hwWords(long seed, int id) {
        Random h = new Random(seed * 31 + id * 0x9e3779b1L + 0x6877);
        long[] w = new long[16];
        for (int i = 0; i < 16; i++) {
            int k = h.nextInt(3);
            w[i] = k == 0 ? 0 : k == 1 ? 0xffffffffL : h.nextInt() & 0xffffffffL;
        }
        return w;
    }

    // A save/restore helper entry's frame (TcOracle.frameOf), from its own generator so that no other input changes,
    // and only for one (selfHelper): every other harness is byte for byte as before. r11 points at a save area just
    // below the stack window, where W lines compare what a save stores (inside the window they are cut), moved in
    // 8-byte steps so that no fixed address passes; the FRAME_AREA bytes up to STACK_LO are random (an M patch), so a
    // restore loads varied words; r14..r31 and f14..f31 are random (f: no NaN or infinity, whose exponent loses its
    // top bit), so a save stores varied words. The deepest CodeWarrior slot is -0x90 (f14), the highest +4 (lwz r0).
    static final int FRAME_AREA = 0x200;

    void frame(Vec v, long seed, int id) {
        Random h = new Random(seed * 37 + id * 0x2545f491L + 0x7231);
        long[] fr = new long[37];
        fr[0] = STACK_LO - 0x10 - 8L * h.nextInt(32);
        for (int i = 1; i < 19; i++) fr[i] = h.nextInt() & 0xffffffffL;
        for (int i = 19; i < 37; i++) {
            long b = h.nextLong();
            fr[i] = ((b >>> 52) & 0x7ff) == 0x7ff ? b & ~(1L << 62) : b;
        }
        byte[] area = new byte[FRAME_AREA];
        h.nextBytes(area);
        v.patchAddr.add(STACK_LO - FRAME_AREA);
        v.patchData.add(area);
        frameOf.put(v, fr);
    }

    // The game's repairVec, then the run's scripted call results (latest first) as the pointers it used: repairVec only
    // sees loads and parameters, and each workspace's TcInputs is its own.
    // This call's and every later one's at once: a loop over calls uses each result alike, and MAX_REPAIRS is below 16.
    // A call after Vec.rets' returned 0 (lateOf), so Skies 8012ab84 (bl; lwz r3,4(r3)) and the allocator loops of
    // 801978xx lost every vector. The newest call's result is tried first when it is one of those (repairVec would spend
    // the repairs on small words near address 0), an older one after repairVec. Only a run that made more calls than
    // Vec.rets holds reaches lateRepair, so every other vector, and every generator draw, is as before.
    Vec repair(Vec v, Result r, List<long[]> loads) {
        int made = Math.min(r.calls.size(), RETS), first = v.rets.length;
        if (made > first && lateRepair(v, r, made - 1)) return v;
        Vec nv = repairVec(v, r.faultAddr, r.faultWrite, loads);
        if (nv != null) return nv;
        for (int c = made - 2; c >= first; c--) if (lateRepair(v, r, c)) return v;
        for (int i = Math.min(r.calls.size(), v.rets.length) - 1; i >= 0; i--) {
            long d = r.faultAddr - v.rets[i];
            if (d < -0x100 || d >= GEN_MAX_OFFSET || genOwned(v.rets[i])) continue;
            for (int j = i; j < v.rets.length; j++) if (!genOwned(v.rets[j])) v.rets[j] = genChunk(v, d);
            return v;
        }
        return null;
    }

    Vec carry(Vec v, Vec nv) {  // the side maps follow a repaired copy
        if (nv == v) return v;
        if (frameOf.containsKey(v)) frameOf.put(nv, frameOf.get(v));
        if (lateOf.containsKey(v)) lateOf.put(nv, lateOf.get(v));
        if (r4Of.containsKey(v)) r4Of.put(nv, r4Of.get(v));
        if (fxOf.containsKey(v)) fxOf.put(nv, fxOf.get(v));
        return nv;
    }

    // The last resorts (TcCallFx), tried only once repair has given up on a run (returned null, or MAX_REPAIRS spent),
    // so every vector the repairs keep is as before; a function that lost no vector gets the same harness.
    Vec lastResort(Vec v, Result r) {
        long[] poll = r.stepLimited ? TcCallFx.polled(r.loads) : null;
        if (poll != null) return calleeWrite(v, r.calls.size() - 1, poll[1], poll[2], TcCallFx.flip(poll[3]));
        for (int i = r.loads.size() - 1; i >= 0; i--) {
            long[] ld = r.loads.get(i);
            long a = ld[1], x = ld[3], o = ld[2] == 4 ? globalImage(v, a) : -1;
            boolean table = o >= 0 && (r.outside >= 0 ? x == r.outside && facts.isCode(o)
                : r.faultAddr >= 0 && TcCallFx.tableIndex(r.faultAddr, x, o));
            if (table && restoreNear(v, a)) return v;
        }
        if (r.faultAddr < 0) return null;
        for (int i = r.loads.size() - 1; i >= 0; i--) {
            long[] ld = r.loads.get(i);
            long a = ld[1], x = ld[3];
            if (ld[2] != 4 || !genInRam(a)) continue;
            long d = (int) (r.faultAddr - x);
            Integer k = r.storedAt.get(a);
            if (d < -0x100 || d >= GEN_MAX_OFFSET || k == null || k >= r.calls.size() || genOwned(x)) continue;
            if (TcCallFx.has(fxOf.get(v), k, a)) continue;
            return calleeWrite(v, k, a, 4, genChunk(v, d));
        }
        return null;
    }

    // Mocked call k leaves value (size bytes) at a once it returns: vectors.txt K. null when it already does.
    Vec calleeWrite(Vec v, int k, long a, long size, long value) {
        if (k < 0 || TcCallFx.has(fxOf.get(v), k, a)) return null;
        fxOf.computeIfAbsent(v, x -> new ArrayList<>()).add(new long[] {k, a, size, value});
        return v;
    }

    // The image's word at a when the vector's last patch there is one of makeVec's randomised globals (32 bytes in a
    // global range) over initialised memory; else -1.
    long globalImage(Vec v, long a) {
        int p = lastPatch(v, a, 4);
        if (p < 0 || v.patchData.get(p).length != 32 || !facts.randomGlobal(v.patchAddr.get(p))) return -1;
        byte[] b = image(a, 4);
        return b == null ? -1 : (b[0] & 0xffL) << 24 | (b[1] & 0xffL) << 16 | (b[2] & 0xffL) << 8 | (b[3] & 0xffL);
    }

    int lastPatch(Vec v, long a, int n) {
        for (int i = v.patchAddr.size() - 1; i >= 0; i--) {
            long p = v.patchAddr.get(i);
            if (p < a + n && a < p + v.patchData.get(i).length) return i;
        }
        return -1;
    }

    byte[] image(long a, int n) {
        try {
            ghidra.program.model.mem.MemoryBlock blk = currentProgram.getMemory().getBlock(toAddr(a));
            if (blk == null || !blk.isInitialized() || !blk.contains(toAddr(a + n - 1))) return null;
            byte[] b = new byte[n];
            return currentProgram.getMemory().getBytes(toAddr(a), b) == n ? b : null;
        } catch (Exception e) {
            return null;
        }
    }

    // makeVec's randomised globals within 0x100 bytes of a table word get the image's bytes back (a later patch, over
    // repairVec's pokes there too: the table's words are not pointers), each once.
    boolean restoreNear(Vec v, long a) {
        boolean any = false;
        for (int i = 0, n = v.patchAddr.size(); i < n; i++) {
            long p = v.patchAddr.get(i);
            boolean near = v.patchData.get(i).length == 32 && facts.randomGlobal(p) && p + 32 > a - 0x100 && p < a + 0x100;
            byte[] img = near ? image(p, 32) : null;
            for (int j = i + 1; img != null && j < n; j++) {
                if (v.patchAddr.get(j) == p && Arrays.equals(v.patchData.get(j), img)) img = null;  // restored already
            }
            if (img == null) continue;
            v.patchAddr.add(p);
            v.patchData.add(img);
            v.genPoked.keySet().removeIf(w -> w >= p && w < p + 32);
            v.genNulled.removeIf(w -> w >= p && w < p + 32);
            any = true;
        }
        return any;
    }

    // Call c's result (c past Vec.rets') as the pointer the run faulted through: it and the next 15 get fresh chunks.
    boolean lateRepair(Vec v, Result r, int c) {
        long[] late = lateOf.get(v);
        int j = c - v.rets.length;
        long x = late == null ? 0 : late[j], d = r.faultAddr - x;
        if (d < -0x100 || d >= GEN_MAX_OFFSET || genOwned(x)) return false;
        if (late == null) lateOf.put(v, late = new long[RETS - v.rets.length]);
        for (int k = j; k < Math.min(late.length, j + 16); k++) if (!genOwned(late[k])) late[k] = genChunk(v, d);
        return true;
    }

    // vectors.txt C's words after Vec.rets': lateOf's up to its last that is not 0 (none: C as before).
    static String lateTail(long[] late) {
        int n = late == null ? 0 : late.length;
        while (n > 0 && late[n - 1] == 0) n--;
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n; i++) b.append(' ').append(hex(late[i]));
        return b.toString();
    }

    // Each set has its own generator (rnd), so the hidden set does not depend on ndev.
    void makeVectors(Proto proto, List<Long> globals, List<String> hints, long seed, int ndev, int nhidden) {
        String[] sets = {"dev", "hidden"};
        int[] want = {ndev, nhidden};
        int id = 0;
        for (long site : callSites()) siteHits.put(site, 0);
        long deadline = facts.oracleDeadlineS <= 0 ? 0
            : ManagementFactory.getRuntimeMXBean().getStartTime() + facts.oracleDeadlineS * 1000;
        long end = TcBudget.of(facts, facts.oracleBudgetS);  // 0 without a work budget
        List<String> rest = retPtrHints(hints, retPtr);
        vectors:
        for (int s = 0; s < 2; s++) {
            rnd = new Random(seed * 31 + s * 0x5bd1e995L);
            while (valid[s] < want[s] && tried[s] < want[s] * 8) {
                if (TcBudget.over(work(), end) || deadline > 0 && System.currentTimeMillis() > deadline) {
                    outOfTime = true;
                    break vectors;
                }
                tried[s]++;
                Vec v = makeVec(id % 4, proto, new ArrayList<>(globals));
                hwOf.put(v, hwWords(seed, tried[0] + tried[1]));
                r4Of.put(v, TcCallFx.r4Words(seed, tried[0] + tried[1]));
                applyHints(v, rest);
                if (selfHelper) frame(v, seed, tried[0] + tried[1]);
                int repairs = 0;
                Result r = runGrow(v);
                while (!r.ok && r.faultAddr >= 0 && repairs < MAX_REPAIRS) {
                    List<long[]> loads = new ArrayList<>();
                    for (long[] ld : r.loads) loads.add(ld.clone());
                    Vec nv = repair(v, r, loads);
                    if (nv == null) break;
                    v = carry(v, nv);
                    repairs++;
                    r = runGrow(v);
                }
                for (int last = 0; !r.ok && refused == null && last < LAST_REPAIRS; last++) {  // given up on: lastResort
                    Vec nv = lastResort(v, r);
                    if (nv == null && r.faultAddr >= 0) {
                        List<long[]> loads = new ArrayList<>();
                        for (long[] ld : r.loads) loads.add(ld.clone());
                        nv = repair(v, r, loads);
                    }
                    if (nv == null) break;
                    v = carry(v, nv);
                    repairs++;
                    r = runGrow(v);
                }
                if (r.stepLimited && ++overruns == OVERRUNS) stepLimit = STEP_LIMIT / OVERRUN_CUT;
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
                switchHits.add(r.bctrs);
                id++;
            }
        }
    }

    // runOracle while vectors are chosen: a run may set genretptr results (TcCallFx.grow), and then the vector runs again
    // until a run sets none, so what is kept comes from a run whose inputs were all in place at entry, as in the C
    // runner. A function without the hint runs once, as before.
    Result runGrow(Vec v) {
        growRets = !retPtr.isEmpty();
        Result r = runOracle(v);
        for (int i = 0; r.grew && i < GROW_RUNS; i++) r = runOracle(v);
        growRets = false;
        if (r.grew) {
            r.ok = false;
            r.faultAddr = -1;
            r.reason = "call results still growing";
        }
        return r;
    }

    // The genretptr CALLEE [OFF [PTR..]] lines of hints into out (callee -> {OFF (0 without one), PTR..}); the other
    // lines are returned.
    static List<String> retPtrHints(List<String> hints, Map<Long, long[]> out) {
        out.clear();
        List<String> rest = new ArrayList<>();
        for (String h : hints) {
            String[] t = h.trim().split("\\s+");
            if (!t[0].equals("genretptr") || t.length < 2) { rest.add(h); continue; }
            long[] s = new long[Math.max(1, t.length - 2)];
            for (int i = 2; i < t.length; i++) s[i - 2] = Long.decode(t[i]);
            out.put(Long.decode(t[1]), s);
        }
        return rest;
    }

    void writeVector(int id, String set, Vec v, Result r) {
        if ((r = TcStrict.replay(this, v, r, set)) == null) return;  // with the strict flags on (TcStrict)
        vout.append("V ").append(id).append(' ').append(set).append("\nR");
        for (long x : v.r) vout.append(' ').append(hex(x));
        vout.append("\nF");
        for (long x : v.f) vout.append(' ').append(hex64(x));
        vout.append("\nC");
        for (long x : v.rets) vout.append(' ').append(hex(x));
        vout.append(lateTail(lateOf.get(v))).append("\nD");
        for (long x : v.frets) vout.append(' ').append(hex64(x));
        vout.append('\n');
        if (r.hwReads) {
            vout.append('H');
            for (long x : hwOf.get(v)) vout.append(' ').append(hex(x));
            vout.append('\n');
        }
        long[] fr = frameOf.get(v);
        if (fr != null) {  // a helper entry's frame (frame)
            vout.append('X');
            for (int i = 0; i < 19; i++) vout.append(' ').append(hex(fr[i]));
            for (int i = 19; i < 37; i++) vout.append(' ').append(hex64(fr[i]));
            vout.append('\n');
        }
        vout.append(TcCallFx.qLine(r.r4s)).append(TcCallFx.kLines(fxOf.get(v)));
        for (int i = 0; i < v.patchAddr.size(); i++) {
            vout.append("M ").append(hex(v.patchAddr.get(i))).append(' ').append(bytesHex(v.patchData.get(i))).append('\n');
        }
        vout.append(TcStrict.vec(r)).append("E\n");  // lines N, W (TcStrict)
        eout.append("V ").append(id).append(' ').append(set).append('\n');
        eout.append("RET ").append(hex(r.r3)).append(' ').append(hex64(r.f1)).append(' ').append(hex(r.r4)).append('\n');
        if (r.msr != TcSystem.MSR_ENTRY) eout.append("MSR ").append(hex(r.msr)).append('\n');  // mtmsr's last value
        for (String w : r.writes) eout.append("W ").append(w).append('\n');
        for (String w : r.io) eout.append("IO ").append(w).append('\n');
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

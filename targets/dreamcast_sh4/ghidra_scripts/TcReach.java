// The reach pass (TcFacts.reachRuns, config "reach_calls"): call sites no valid vector reaches leave their callee's
// arguments uncompared, so matched C there is unverified (Toy Commander's port crashed on such calls: a frame address
// dropped to 0, a NULL matrix). After both sets are made, for each unreached call site the pass looks for a frontier:
// a block some valid vector reaches with a successor no vector reaches from which the site's block can be reached in
// the control flow. It reruns a vector reaching that block up to the frontier's branch (TcOracle.watchPc) to see the
// loads that came just before it, then mutates those inputs (the loaded words, the scripted results of the calls just
// before, the parameter registers) to compare constants (TcGuide), 0/1/-1, bit flips, neighbours, other loaded values
// and fresh pointers, and reruns with the usual repairs (TcVectors.runRepaired). A mutated vector that reaches a new
// block joins the corpus, so chained conditions are taken one at a time. Each newly reached site gets one added dev
// vector and, when a further mutation of it still reaches the site, one added hidden vector; they follow the first
// pass's vectors (ids after them), which stay byte-identical, and a function with every site reached is unchanged.
// A site still unreached is reported with the reason (TcPrepare: oracle_summary.json "reach"). Deterministic: own
// generators seeded by the function seed, ordered collections, a run budget and a work budget (TcBudget: steps and
// runs, never the clock; the reason says which ran out). LOCKED: the watcher agent may not change this file. Not a
// script itself.
import ghidra.app.emulator.EmulatorHelper;
import java.util.*;

final class TcReach {
    static final int WATCH_LOADS = 12;  // the latest loads before the branch that are mutated
    static final int BURST = 12;        // mutated runs per (frontier, vector) pick
    static final int PER_FRONTIER = 144; // mutated runs per frontier before it counts as tried
    static final int VARIANTS = 8;      // tries for a hidden vector per added dev vector
    static final int MAX_ADDED = 32;    // added vectors per set

    static final class Entry {  // a vector as run (v), its entry state and its result
        TcInputs.Vec v; int[] fh, fq; long[] low, gi, late; TcOracle.Result r;
    }

    final TcVectors g;
    final List<Long> consts;
    final Random h;
    final List<Integer> paramRegs = new ArrayList<>();
    final List<Entry> corpus = new ArrayList<>();
    final Set<Integer> covered = new TreeSet<>();
    TcCfg flow;  // the body's control flow
    int runs = 0;
    String budgetNote = "", lastWhy = "";  // why the budget ended the search; why the last rerun was invalid

    // A valid first-pass vector as TcVectors wrote it (v as run, the entry state it ran with), for the corpus.
    static Entry entry(TcVectors g, TcInputs.Vec v, TcOracle.Result r) {
        Entry e = new Entry();
        e.v = v;
        e.r = r;
        e.fh = g.entryFh;
        e.fq = g.entryFq;
        e.low = g.entryLow == null ? null : g.entryLow.clone();
        e.gi = g.entryGi == null ? null : g.entryGi.clone();
        e.late = g.entryLate == null ? null : g.entryLate.clone();
        return e;
    }

    // Called by TcOracle when a run first reaches watchPc: the load log, the calls made, r0..r15 (the compared values).
    static void snap(TcOracle.Result res, int calls, EmulatorHelper emu) {
        res.watched = new ArrayList<>();
        for (long[] ld : res.loads) res.watched.add(ld.clone());
        res.watchCalls = calls;
        res.watchRegs = new long[16];
        for (int i = 0; i < 16; i++) res.watchRegs[i] = emu.readRegister("r" + i).longValue() & 0xffffffffL;
    }

    private TcReach(TcVectors g, TcBase.Proto proto, long seed) {
        this.g = g;
        consts = TcGuide.constants(g.listing, g.body, g::poolValue);  // the same with targeted_vectors (TcBoundPass)
        h = new Random(seed * 0x9e3779b97f4a7c15L + 0x7265616368L);
        g.rnd = new Random(seed ^ 0x52454143485fL);  // the first pass is done: its sequence is not needed any more
        for (TcBase.Param q : proto.params) if (q.storage.matches("r[4-7]")) paramRegs.add(q.storage.charAt(1) - '4');
    }

    // The pass's oracle_summary.json fragment ("reach":{...}), or null when it is off.
    static String run(TcVectors g, TcBase.Proto proto, long seed, List<Entry> seeds) throws Exception {
        if (g.facts.reachRuns <= 0) return null;
        TcReach p = new TcReach(g, proto, seed);
        return p.pass(seeds);
    }

    String pass(List<Entry> seeds) throws Exception {
        Map<Long, String> reason = new TreeMap<>();
        Set<Long> targets = new TreeSet<>();
        for (Map.Entry<Long, Integer> e : g.siteHits.entrySet()) if (e.getValue() == 0) targets.add(e.getKey());
        Map<Long, Entry> found = new LinkedHashMap<>();
        if (!targets.isEmpty()) search(seeds, targets, found, reason);
        int[] added = emit(found, reason);
        StringBuilder b = new StringBuilder("\"reach\":{\"runs\":").append(runs).append(",\"added\":[")
            .append(added[0]).append(',').append(added[1]).append("],\"found\":[");
        int i = 0;
        for (long s : found.keySet()) b.append(i++ > 0 ? "," : "").append('"').append(TcBase.hex(s)).append('"');
        b.append("],\"unreached\":{");
        i = 0;
        for (Map.Entry<Long, String> e : reason.entrySet()) {
            b.append(i++ > 0 ? "," : "").append('"').append(TcBase.hex(e.getKey())).append("\":\"")
                .append(TcBase.esc(e.getValue())).append('"');
        }
        return b.append("}}").toString();
    }

    void search(List<Entry> seeds, Set<Long> targets, Map<Long, Entry> found, Map<Long, String> reason)
            throws Exception {
        if (seeds.isEmpty()) {
            for (long t : targets) reason.put(t, "no valid vector to start from");
            return;
        }
        corpus.addAll(seeds);
        for (Entry e : seeds) covered.addAll(e.r.cov);
        flow = new TcCfg(g);
        long all = g.facts.oracleBudgetS <= 0 ? Long.MAX_VALUE : TcBudget.of(g.facts, g.facts.oracleBudgetS - 30);
        long end = Math.min(all, g.work() + TcBudget.of(g.facts, g.facts.reachS));
        Set<Long> open = new TreeSet<>(targets);
        boolean progress = true;
        while (progress && !open.isEmpty()) {
            progress = false;
            for (long t : new ArrayList<>(open)) {
                if (!open.contains(t)) continue;
                if (runs >= g.facts.reachRuns || g.work() > end) {
                    budgetNote = runs >= g.facts.reachRuns ? "the run budget (" + g.facts.reachRuns + ") ran out"
                        : "the work budget ran out after " + runs + " runs";
                    progress = false;
                    break;
                }
                int[] f = null;  // the least tried frontier toward t that is not used up (targets share frontiers)
                for (int[] c : flow.frontier(covered, flow.block(t))) {
                    int n = stat(flow.branchPc.get(c[0]))[0];
                    if (n < PER_FRONTIER && (f == null || n < stat(flow.branchPc.get(f[0]))[0])) f = c;
                }
                if (f == null) continue;
                progress = true;
                burst(f[0], flow.branchPc.get(f[0]), open, found);
            }
        }
        Integer entry = flow.block(g.fn.getEntryPoint().getOffset());
        for (long t : open) {  // the reason from the stats of every frontier toward t
            Integer tb = flow.block(t);
            Set<Long> pcs = new TreeSet<>();
            for (int[] c : flow.frontier(covered, tb)) pcs.add(flow.branchPc.get(c[0]));
            int[] st = new int[3];
            Map<String, Integer> why = new TreeMap<>();
            for (long q : pcs) {
                for (int i = 0; i < 3; i++) st[i] += stat(q)[i];
                fwhy.getOrDefault(q, Map.of()).forEach((k, n) -> why.merge(k, n, Integer::sum));
            }
            String at = String.join(", ", pcs.stream().map(TcBase::hex).toList());
            if (tb == null || entry == null || !flow.reaches(entry, tb)) reason.put(t, "no path from the entry reaches it in the control flow");
            else if (badSites.containsKey(t)) {
                Map<String, Integer> b = badSites.get(t);
                reason.put(t, String.format("reached only by invalid runs (%d%s): no input tried lets a run that gets "
                    + "there end normally", b.values().stream().mapToInt(x -> x).sum(), top(b)));
            } else if (pcs.isEmpty()) {
                reason.put(t, "no reached block branches toward it (the blocks before it are reached only by"
                    + " fall-through from unreached code, or the run ends before them)");
            } else if (st[0] == 0) reason.put(t, "not tried (branch(es) at " + at + "): " + budgetNote);
            else if (st[2] >= st[0]) reason.put(t, "the branch(es) at " + at + " toward it read no input"
                + " the reach pass can set (no loads in RAM, scripted results or parameters before them)");
            else {
                reason.put(t, String.format("%d mutated runs of the inputs feeding the branch(es) at %s did not reach "
                    + "it (%d invalid%s)%s", st[0], at, st[1], top(why), budgetNote.isEmpty() ? "" : "; " + budgetNote));
            }
        }
    }

    final Map<Long, int[]> fstats = new TreeMap<>();               // frontier pc -> {mutated runs, invalid, no input}
    final Map<Long, Map<String, Integer>> fwhy = new TreeMap<>();  // frontier pc -> invalid reasons
    final Map<Long, Map<String, Integer>> badSites = new TreeMap<>();  // site -> why the runs reaching it were invalid
    TcOracle.Result lastBad;

    int[] stat(long pc) {
        return fstats.computeIfAbsent(pc, k -> new int[3]);
    }

    static String top(Map<String, Integer> m) {
        if (m == null || m.isEmpty()) return "";
        Map.Entry<String, Integer> best = null;
        for (Map.Entry<String, Integer> e : m.entrySet()) if (best == null || e.getValue() > best.getValue()) best = e;
        return ", mostly: " + best.getKey();
    }

    // One pick: a corpus vector reaching block b, its loads before the branch at pc, BURST mutations of them.
    void burst(int b, long pc, Set<Long> open, Map<Long, Entry> found) {
        int[] st = stat(pc);
        Map<String, Integer> why = fwhy.computeIfAbsent(pc, k -> new TreeMap<>());
        List<Entry> at = new ArrayList<>();
        for (Entry e : corpus) if (e.r.cov.contains(b)) at.add(e);
        Entry base = at.get(h.nextInt(at.size()));
        Entry w = rerun(base, copy(base.v), pc);
        runs++;
        if (w == null || w.r.watched == null) {
            st[0] += BURST;
            st[1] += BURST;
            why.merge("the vector no longer reaches the branch", BURST, Integer::sum);
            return;
        }
        List<long[]> loads = inputs(w.r.watched);
        List<Integer> rets = new ArrayList<>();
        for (int k = Math.max(0, w.r.watchCalls - 3); k < Math.min(w.r.watchCalls, 16); k++) rets.add(k);
        if (loads.isEmpty() && rets.isEmpty() && paramRegs.isEmpty()) {
            st[0] += BURST;
            st[2] += BURST;
            return;
        }
        long[] seen = seen(w.r.watched, w.r.watchRegs);
        List<Integer> cr = flow.cmpRegs.getOrDefault(b, List.of());
        long[] ops = new long[cr.size()];  // the compare's operands at the branch
        for (int i = 0; i < ops.length; i++) ops[i] = w.r.watchRegs[cr.get(i)];
        for (int m = 0; m < BURST; m++) {
            TcInputs.Vec v = copy(base.v);
            if (h.nextBoolean() || !direct(v, loads, rets, ops)) mutate(v, loads, rets, seen);
            if (h.nextInt(4) == 0) mutate(v, loads, rets, seen);  // sometimes two inputs at once
            Entry e = rerun(base, v, -1);
            runs++;
            st[0]++;
            if (e == null) {
                st[1]++;
                String y = lastWhy.replaceAll("[0-9a-f]{8}", "X");
                why.merge(y, 1, Integer::sum);
                for (long s : lastBad.sites) {
                    if (open.contains(s)) badSites.computeIfAbsent(s, k -> new TreeMap<>()).merge(y, 1, Integer::sum);
                }
                continue;
            }
            boolean fresh = false;
            for (int c : e.r.cov) fresh |= covered.add(c);
            for (long s : e.r.sites) {
                if (open.remove(s)) {
                    found.put(s, e);
                    fresh = true;
                }
            }
            if (fresh) corpus.add(e);
        }
    }

    // Runs v with base's entry state (and watchPc); the entry when the run is valid, else null (lastWhy says why).
    Entry rerun(Entry base, TcInputs.Vec v, long watch) {
        Entry e = new Entry();
        e.fh = base.fh;
        e.fq = base.fq;
        e.low = base.low == null ? null : base.low.clone();
        e.gi = base.gi == null ? null : base.gi.clone();
        e.late = base.late == null ? null : base.late.clone();
        g.entryFh = e.fh;
        g.entryFq = e.fq;
        g.entryLow = e.low;
        g.entryGi = e.gi;
        g.entryLate = e.late;
        g.watchPc = watch;
        g.storeFaults = g.rescued;
        TcInputs.Vec[] box = {v};
        try {
            e.r = g.runRepaired(box, h, consts, g.rescued, new int[1]);
        } finally {
            g.watchPc = -1;
        }
        e.v = box[0];
        if (!e.r.ok) {
            lastWhy = e.r.reason;
            lastBad = e.r;
            return null;
        }
        return e;
    }

    // The loads a vector can set: in RAM, not the own frame, not code or the function's constant data (literal pools,
    // jump tables: TcCfg.isConst), latest first, one per address.
    List<long[]> inputs(List<long[]> watched) {
        List<long[]> out = new ArrayList<>();
        Set<Long> seen = new HashSet<>();
        for (int i = watched.size() - 1; i >= 0 && out.size() < WATCH_LOADS; i--) {
            long[] ld = watched.get(i);
            long a = ld[1];
            if (ld[2] != 1 && ld[2] != 2 && ld[2] != 4) continue;
            if (a < TcBase.RAM_LO || a + ld[2] > TcBase.RAM_HI || TcProvenance.ownFrame(a)) continue;
            if (g.facts.isCode(a) || flow.isConst(a) || !seen.add(a)) continue;
            out.add(ld);
        }
        return out;
    }

    // The values a run had at the branch, a compare's operands among them: the loaded words and r0..r15.
    static long[] seen(List<long[]> loads, long[] regs) {
        int n = loads.size();
        long[] out = Arrays.copyOf(regs == null ? new long[0] : regs, n + (regs == null ? 0 : regs.length));
        for (int i = 0; i < n; i++) out[out.length - n + i] = loads.get(i)[3];
        return out;
    }

    // An input still holding a compare operand's value (cmp/hi r5,r4 with r4 the parameter as passed) set to an
    // operand +-1, the other side of the compare among them. False when no input holds one.
    boolean direct(TcInputs.Vec v, List<long[]> loads, List<Integer> rets, long[] ops) {
        Set<Long> o = new TreeSet<>();
        for (long x : ops) o.add(x);
        List<Integer> c = new ArrayList<>();  // load index, 1000 + call, 2000 + parameter register
        for (int i = 0; i < loads.size(); i++) if (o.contains(loads.get(i)[3])) c.add(i);
        for (int k : rets) if (o.contains(v.rets[k] & 0xffffffffL)) c.add(1000 + k);
        for (int r : paramRegs) if (o.contains(v.r[r] & 0xffffffffL)) c.add(2000 + r);
        if (c.isEmpty()) return false;
        int p = c.get(h.nextInt(c.size()));
        long val = (ops[h.nextInt(ops.length)] + h.nextInt(3) - 1) & 0xffffffffL;
        if (p >= 2000) v.r[p - 2000] = val;
        else if (p >= 1000) v.rets[p - 1000] = val;
        else patch(v, loads.get(p), val);
        return true;
    }

    static void patch(TcInputs.Vec v, long[] ld, long val) {
        int size = (int) ld[2];
        byte[] d = new byte[size];
        for (int i = 0; i < size; i++) d[i] = (byte) (val >> (8 * i));
        v.patchAddr.add(ld[1]);
        v.patchData.add(d);
        if (size == 4 && v.genPoked.containsKey(ld[1])) v.genPoked.put(ld[1], val);
    }

    void mutate(TcInputs.Vec v, List<long[]> loads, List<Integer> rets, long[] seen) {
        int k = h.nextInt(100);
        if (!loads.isEmpty() && (k < 70 || (rets.isEmpty() && paramRegs.isEmpty()))) {
            int j = 0;
            while (j < loads.size() - 1 && h.nextInt(100) < 35) j++;  // the latest loads first
            long[] ld = loads.get(j);
            patch(v, ld, value(ld[3], (int) ld[2], seen, v));
        } else if (!rets.isEmpty() && (k < 85 || paramRegs.isEmpty())) {
            int r = rets.get(h.nextInt(rets.size()));
            v.rets[r] = value(v.rets[r], 4, seen, v);
        } else if (!paramRegs.isEmpty()) {
            int r = paramRegs.get(h.nextInt(paramRegs.size()));
            v.r[r] = value(v.r[r], 4, seen, v);
        }
    }

    long value(long x, int size, long[] seen, TcInputs.Vec v) {
        long mask = size >= 4 ? 0xffffffffL : (1L << (8 * size)) - 1;
        int k = h.nextInt(100);
        long out;
        if (k < 25 && !consts.isEmpty()) out = TcGuide.pick(consts, h);
        else if (k < 35) out = new long[] {0, 1, -1}[h.nextInt(3)];
        else if (k < 43) out = x ^ (1L << h.nextInt(8 * size));
        else if (k < 50) out = x + h.nextInt(5) - 2;
        else if (k < 75 && seen.length > 0) out = seen[h.nextInt(seen.length)] + h.nextInt(3) - 1;  // a compared value
        else if (k < 85) out = h.nextInt(17);
        else if (k < 93 && size == 4) out = g.genChunk(v, 0);
        else out = h.nextLong();
        return out & mask;
    }

    // The added vectors: one dev vector per newly reached site (unless an earlier added one reaches it too), and a
    // hidden one when a further mutation of it still reaches the site. Returns {dev added, hidden added}.
    int[] emit(Map<Long, Entry> found, Map<Long, String> reason) {
        List<Entry> dev = new ArrayList<>(), hid = new ArrayList<>();
        Set<Long> got = new TreeSet<>();
        for (Map.Entry<Long, Entry> f : found.entrySet()) {
            if (got.contains(f.getKey())) continue;
            if (dev.size() >= MAX_ADDED) {
                reason.put(f.getKey(), "reached by the reach pass, but the cap of " + MAX_ADDED + " added vectors was full");
                continue;
            }
            Entry e = f.getValue();
            dev.add(e);
            got.addAll(e.r.sites);
            for (int t = 0; t < VARIANTS; t++) {
                TcInputs.Vec v = copy(e.v);
                mutate(v, inputs(e.r.loads), List.of(), seen(e.r.loads, null));
                Entry x = rerun(e, v, -1);
                runs++;
                if (x != null && x.r.sites.contains(f.getKey())) {
                    hid.add(x);
                    break;
                }
            }
        }
        int id = g.valid[0] + g.valid[1];
        for (int s = 0; s < 2; s++) {
            for (Entry e : s == 0 ? dev : hid) {
                g.entryFh = e.fh;
                g.entryFq = e.fq;
                g.entryLow = e.low;
                g.entryGi = e.gi; g.entryLate = e.late;
                g.writeVector(id++, s == 0 ? "dev" : "hidden", e.v, e.r);
                g.valid[s]++;
                if (s == 0) { g.covDev.addAll(e.r.cov); g.dirsDev.addAll(e.r.dirs); }
                g.covAll.addAll(e.r.cov); g.dirsAll.addAll(e.r.dirs);
                for (int c : e.r.cov) g.blockHits.merge(c, 1, Integer::sum);
                for (long site : e.r.sites) g.siteHits.merge(site, 1, Integer::sum);
            }
        }
        return new int[] {dev.size(), hid.size()};
    }

    static TcInputs.Vec copy(TcInputs.Vec v) {
        TcInputs.Vec c = new TcInputs.Vec();
        c.r = v.r.clone();
        c.fr = v.fr.clone();
        c.xf = v.xf.clone();
        c.rets = v.rets.clone();
        c.patchAddr = new ArrayList<>(v.patchAddr);
        c.patchData = new ArrayList<>();
        for (byte[] d : v.patchData) c.patchData.add(d.clone());
        c.genHeapNext = v.genHeapNext;
        c.genPoked = new HashMap<>(v.genPoked);
        return c;
    }
}

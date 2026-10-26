// The targeted-vector pass (target.json "targeted_vectors", TcBound; pre-wave item 1). After both sets and the reach
// pass, a function with a branch direction (TcDirs) no valid vector took gets up to TcBound.MAX_ADDED more vectors
// per set: copies of first-pass valid vectors (TcReach.Entry, with their entry state) with one to three of the inputs
// the copy's run read set to boundary values (TcBound): a non-pointer integer parameter (r4..r7, r0..r3), a scripted
// call result the run used (Vec.rets, or TcLate's later ones), a word it loaded (at the load's size; not its own
// frame, code or the function's constant data, TcCfg.isConst), a named global word, an f32 parameter, or an f32
// parameter copied into another (two equal floats). Each copy runs with the usual repairs (TcVectors.runRepaired);
// one that is valid and takes a direction still open for its set is added, with ids after every other vector (the
// first pass's and the reach pass's stay byte-identical), so a function's coverage only grows and a flip can only
// come from an added vector. A function with every direction taken, or the flag off, is unchanged. TcReach does not
// get TcBound's values: they steered its picks off the flag-off ones and lost kills (Power Stone 2 8c06a2e8,
// 8c16d394, Toy Commander 8c046402). Deterministic: a generator per (seed, set, try), ordered
// collections, a try cap and a work budget (TcBudget). The oracle_summary.json fragment: "bound":{open, runs, added
// [dev, hidden], hit (directions newly taken), note}. LOCKED: the watcher agent may not change this file. Not a script.
import java.util.*;

final class TcBoundPass {
    final TcVectors g;
    final TcBound b;
    final TcCfg flow;
    final List<Integer> ints = new ArrayList<>(), lows = new ArrayList<>(), flts = new ArrayList<>();
    final List<Long> globals;

    private TcBoundPass(TcVectors g, TcBase.Proto proto, List<Long> globals) throws Exception {
        this.g = g;
        b = bound(g);
        flow = new TcCfg(g);
        this.globals = globals;
        for (int i = 0; i < proto.params.size(); i++) {
            String st = proto.params.get(i).storage;
            if (st.matches("fr([4-9]|1[01])")) flts.add(Integer.parseInt(st.substring(2)) - 4);
            else if (proto.paramIsPointer.get(i) || proto.params.get(i).kind.equals("f32")) continue;
            else if (st.matches("r[4-7]")) ints.add(st.charAt(1) - '4');
            else if (st.matches("r[0-3]")) lows.add(st.charAt(1) - '0');
        }
    }

    static TcBound bound(TcVectors g) {
        return TcBound.of(g.listing, g.body, g.mem, TcGuide.constants(g.listing, g.body, g::poolValue),
            TcFguide.constants(g.listing, g.body, g.mem));
    }

    // The pass's oracle_summary.json fragment ("bound":{...}), or null when it is off.
    static String run(TcVectors g, TcBase.Proto proto, long seed, List<Long> globals) throws Exception {
        if (!g.facts.targetedVectors || g.dirs == null) return null;
        Set<Integer> open = TcBound.open(g.dirs.total(), g.dirsAll);
        if (open.isEmpty()) return TcBound.summary(0, 0, new int[2], 0, "");
        if (g.seeds.isEmpty()) return TcBound.summary(open.size(), 0, new int[2], 0, "no valid vector to start from");
        return new TcBoundPass(g, proto, globals).pass(seed, open);
    }

    String pass(long seed, Set<Integer> open) {
        long all = g.facts.oracleBudgetS <= 0 ? Long.MAX_VALUE : TcBudget.of(g.facts, g.facts.oracleBudgetS - 30);
        long end = Math.min(all, g.work() + TcBudget.of(g.facts, TcBound.BUDGET_S));
        List<Long> consts = TcGuide.constants(g.listing, g.body, g::poolValue);
        int[] added = new int[2];
        int runs = 0;
        Set<Integer> hit = new TreeSet<>();
        String note = "";
        sets:
        for (int s = 0; s < 2; s++) {
            Set<Integer> want = new TreeSet<>(open);
            for (int t = 0; t < TcBound.TRIES && added[s] < TcBound.MAX_ADDED && !want.isEmpty(); t++) {
                if (g.work() > end) {
                    note = "the work budget ran out after " + runs + " runs";
                    break sets;
                }
                Random h = new Random(seed * 0x9e3779b97f4a7c15L + s * 0x632be59bd9b4e019L + t * 0x85ebca6bL
                    + 0x626f756e64L);
                TcReach.Entry e = copy(g.seeds.get(h.nextInt(g.seeds.size())));
                for (int k = 1 + h.nextInt(3); k > 0; k--) place(e, h);
                g.entryFh = e.fh;
                g.entryFq = e.fq;
                g.entryLow = e.low;
                g.entryGi = e.gi;
                g.entryLate = e.late;
                g.storeFaults = g.rescued;
                TcInputs.Vec[] box = {e.v};
                TcOracle.Result r = g.runRepaired(box, h, consts, g.rescued, new int[1]);
                runs++;
                Set<Integer> got = new TreeSet<>(r.dirs);
                got.retainAll(want);
                if (!r.ok || got.isEmpty()) continue;
                want.removeAll(got);
                hit.addAll(got);
                g.writeVector(g.valid[0] + g.valid[1], s == 0 ? "dev" : "hidden", box[0], r);
                g.valid[s]++;
                added[s]++;
                if (s == 0) { g.covDev.addAll(r.cov); g.dirsDev.addAll(r.dirs); }
                g.covAll.addAll(r.cov); g.dirsAll.addAll(r.dirs);
                for (int c : r.cov) g.blockHits.merge(c, 1, Integer::sum);
                for (long site : r.sites) g.siteHits.merge(site, 1, Integer::sum);
            }
        }
        return TcBound.summary(open.size(), runs, added, hit.size(), note);
    }

    static TcReach.Entry copy(TcReach.Entry base) {
        TcReach.Entry e = new TcReach.Entry();
        e.v = TcReach.copy(base.v);
        e.r = base.r;
        e.fh = base.fh;
        e.fq = base.fq;
        e.low = base.low == null ? null : base.low.clone();
        e.gi = base.gi == null ? null : base.gi.clone();
        e.late = base.late == null ? null : base.late.clone();
        return e;
    }

    // The words the base run loaded that a vector may set: 1, 2 or 4 bytes, not its own frame, code or the
    // function's constant data, one per address (the first and the last loads of the run).
    List<long[]> loads(TcOracle.Result r) {
        List<long[]> out = new ArrayList<>();
        Set<Long> seen = new HashSet<>();
        for (List<long[]> log : List.of(r.firstLoads, r.loads)) {
            for (long[] ld : log) {
                long a = ld[1];
                if (ld[2] != 1 && ld[2] != 2 && ld[2] != 4) continue;
                if (TcProvenance.ownFrame(a) || g.facts.isCode(a) || flow.isConst(a) || !seen.add(a)) continue;
                out.add(ld);
            }
        }
        return out;
    }

    // One boundary value into one input the base run read: a kind first (each with even odds), then a slot of it.
    void place(TcReach.Entry e, Random h) {
        int calls = e.r.calls.size(), rets = Math.min(calls, e.v.rets.length);
        int late = e.late == null ? 0 : Math.min(e.late.length, calls - e.v.rets.length);
        List<long[]> lds = loads(e.r);
        List<Integer> kinds = new ArrayList<>();
        if (!ints.isEmpty()) kinds.add(0);
        if (!lows.isEmpty() && e.low != null) kinds.add(1);
        if (rets > 0) kinds.add(2);
        if (late > 0) kinds.add(3);
        if (!lds.isEmpty()) kinds.add(4);
        if (!globals.isEmpty()) kinds.add(5);
        if (!flts.isEmpty()) kinds.add(6);
        if (flts.size() > 1) kinds.add(7);
        if (kinds.isEmpty()) return;
        switch (kinds.get(h.nextInt(kinds.size()))) {
            case 0: e.v.r[ints.get(h.nextInt(ints.size()))] = b.intValue(h, 4); break;
            case 1: e.low[lows.get(h.nextInt(lows.size()))] = b.intValue(h, 4); break;
            case 2: e.v.rets[h.nextInt(rets)] = b.intValue(h, 4); break;
            case 3: e.late[h.nextInt(late)] = b.intValue(h, 4); break;
            case 4: {
                long[] ld = lds.get(h.nextInt(lds.size()));
                int n = (int) ld[2];
                TcReach.patch(e.v, ld, b.fcmp && n == 4 && h.nextBoolean() ? b.floatBits(h, false) : b.intValue(h, n));
                break;
            }
            case 5: {
                long a = globals.get(h.nextInt(globals.size())) & ~3L;
                long[] word = {0, a, 4, 0};  // a load-log entry's shape: {pc, address, size, value}
                if (!g.facts.isCode(a) && !flow.isConst(a)) TcReach.patch(e.v, word, b.intValue(h, 4));
                break;
            }
            case 6: e.v.fr[flts.get(h.nextInt(flts.size()))] = (int) b.floatBits(h, false); break;
            default: {  // two equal floats
                int i = flts.get(h.nextInt(flts.size())), j = flts.get(h.nextInt(flts.size()));
                e.v.fr[j] = e.v.fr[i];
            }
        }
    }
}

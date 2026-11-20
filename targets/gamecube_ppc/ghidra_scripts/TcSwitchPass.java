// The directed switch pass for the PowerPC target (Skies of Arcadia, 2026-11-15): vectors that reach the jump-table
// cases no random vector reached. Round r11's census (switch_hits) found 1,438 of 2,749 cases of 295 switches in PASS
// functions reached by no vector: the selector is a word the vector holds at random (a parameter, a word behind a
// pointer, a global, a mocked call's result), so a table of a dozen cases is hit only at 0..N, rarely or never.
//
// For each bctr of the body whose table is found, TcSwitchSel slices the selector to its root(s) and plan() gives, for a
// case index, the root values that make it. Each case target no kept vector reached gets a copy of a valid vector whose
// run executed that bctr, with the roots set (a LOAD root at the address the run's load at that pc used: the first, then
// the next distinct one; a CALL root at that callee's first, then next, call); it runs with the pointer repairs
// makeVectors uses and is kept when valid and it reaches a case target still open (any switch's). Kept vectors join the
// bases, so a switch inside a case is reached next.
// Guards (round 2, 2026-11-15): a switch no base runs is behind a conditional branch every base took the other way.
// The pass walks the body's flow back from the bctr, finds those branches (a direction no base took that leads to the
// bctr, the other direction taken), slices the register the branch's compare tests (TcSwitchSel.guard) and runs copies
// with values that send it the wanted way. A run taking that direction becomes a base (written only when it reaches an
// open case), so nested guards open in later rounds. In a loop both directions of a branch can lead back to the
// bctr: the way is then the nearer one (frontier). A switch on an argument tries every base, not only those that ran it, those first (round 4).
// Added after every other vector (TcBoundPass's too), dev and hidden in turn: a function with no open case, or no bctr,
// is byte for byte as before. What is not directed is counted by reason in oracle_summary.json "switch_pass"
// (undirected, guards, missed_why), never hidden. Deterministic: bases, cases and guards in order, try caps; the
// deadline (TcFacts.oracleDeadlineS, TcBudget) ends it early, as it ends makeVectors. LOCKED: the watcher agent may not
// change this file. Not a script itself.
import java.lang.management.ManagementFactory;
import java.util.*;

final class TcSwitchPass {
    static final int TRIES = 3, MAX_ADDED = 160, ROUNDS = 4, GUARD_TRIES = 4, MAX_GUARD_RUNS = 96;
    static final long M32 = 0xffffffffL;
    static final String[] MISSED = {"no root value", "no place for the root in the base run", "invalid run",
        "switch not reached with the root set", "another case ran"};

    final TcVectors g;
    final TcSwitchBody.Words img;
    final long fnStart;
    final Set<Long> open = new TreeSet<>();  // bctr << 32 | case target, for cases (all switches) no kept vector reached
    final List<TcBoundPass.Base> extra = new ArrayList<>();  // guard bases: valid runs, not written
    final Set<Integer> dirs = new TreeSet<>();               // directions any base took
    final Map<Long, Integer> why = new HashMap<>();          // open case -> MISSED index of its last try
    final Map<String, Integer> guards = new TreeMap<>();      // guard outcomes by reason
    int[] added = new int[2];
    int runs, guardRuns;

    private TcSwitchPass(TcVectors g, TcSwitchBody.Words img) {
        this.g = g;
        this.img = img;
        fnStart = g.fn.getEntryPoint().getOffset();
    }

    // The pass's oracle_summary.json fragment ("switch_pass":{...}), or null for a body with no table bctr.
    static String run(TcVectors g, long seed) {
        TcSwitchBody.Words img = a -> {
            byte[] b = g.image(a, 4);
            return b == null ? null : (b[0] & 0xffL) << 24 | (b[1] & 0xffL) << 16 | (b[2] & 0xffL) << 8 | (b[3] & 0xffL);
        };
        List<TcSwitchSel.Sel> sels = new ArrayList<>();
        for (ghidra.program.model.listing.Instruction in : g.listing.getInstructions(g.body, true)) {
            long w;
            try {
                w = in.getInt(0) & M32;
            } catch (Exception e) {
                continue;
            }
            if (TcSwitchBody.isBctr(w)) sels.add(TcSwitchSel.of(img, g.fn.getEntryPoint().getOffset(), in.getAddress().getOffset()));
        }
        sels.removeIf(s -> s.targets == null);
        return sels.isEmpty() ? null : new TcSwitchPass(g, img).pass(sels);
    }

    List<TcBoundPass.Base> all() {
        List<TcBoundPass.Base> a = new ArrayList<>(g.bases);
        a.addAll(extra);
        return a;
    }

    // The bases that ran the switch first, then the rest, each in order (round 4): an argument switch tries every base,
    // and round 3's plain order spent its tries on bases that never reach it (8003f5b8: 10 new cases in round 2, 1).
    static <T> List<T> ranFirst(List<T> pool, java.util.function.Predicate<T> ran) {
        List<T> out = new ArrayList<>(), rest = new ArrayList<>();
        for (T b : pool) (ran.test(b) ? out : rest).add(b);
        out.addAll(rest);
        return out;
    }

    static boolean ran(TcBoundPass.Base b, long pc) {
        for (long[] e : b.r.bctrs) if (e[0] == pc) return true;
        return false;
    }

    String pass(List<TcSwitchSel.Sel> sels) {
        long deadline = g.facts.oracleDeadlineS <= 0 ? 0
            : ManagementFactory.getRuntimeMXBean().getStartTime() + g.facts.oracleDeadlineS * 1000;
        long end = TcBudget.of(g.facts, g.facts.oracleBudgetS);
        for (TcSwitchSel.Sel s : sels) {
            Set<Long> h = g.switchHits.hits.getOrDefault(s.bctr, Map.of()).keySet();
            for (long t : s.targets) if (!h.contains(t)) open.add(s.bctr << 32 | t);
        }
        for (TcBoundPass.Base b : g.bases) dirs.addAll(b.r.dirs);
        int before = open.size();
        String note = "";
        boolean more = true;
        rounds:
        for (int round = 0; round < ROUNDS && more; round++) {
            more = false;
            for (TcSwitchSel.Sel s : sels) {
                for (int i = 0; i < s.targets.length && s.kind != 0; i++) {
                    long t = s.targets[i], key = s.bctr << 32 | t;
                    if (!open.contains(key)) continue;
                    Map<TcSwitchSel.Sel, Long> plan = TcSwitchSel.plan(s, i);
                    if (plan == null) {  // another index of the same target may still give it
                        why.putIfAbsent(key, 0);
                        continue;
                    }
                    int tries = 0;
                    List<TcBoundPass.Base> pool = ranFirst(all(), b -> ran(b, s.bctr));
                    for (int b = 0; b < pool.size() && tries < TRIES && open.contains(key); b++) {
                        if (stop(end, deadline)) {
                            note = "stopped after " + runs + " runs (" + (added[0] + added[1] >= MAX_ADDED ? "MAX_ADDED" : "the deadline") + ")";
                            break rounds;
                        }
                        TcBoundPass.Base base = pool.get(b);
                        if (!ran(base, s.bctr) && s.kind != TcSwitchSel.ARG) continue;  // an argument: any base
                        for (int var = 0; var < 2 && open.contains(key); var++) {
                            TcInputs.Vec v = direct(plan, base, var);
                            if (v == null) {
                                if (var == 0) why.put(key, 1);
                                break;
                            }
                            tries++;
                            TcOracle.Result r = try1(v);
                            if (r == null) why.put(key, 2);
                            else if (!ran(new TcBoundPass.Base(v, r), s.bctr)) why.put(key, 3);
                            else why.put(key, 4);
                            if (r != null && keep(v, r, null)) more = true;
                        }
                    }
                }
            }
            for (TcSwitchSel.Sel s : sels) {  // guards of switches no base runs
                boolean left = false;
                for (long t : s.targets) left |= open.contains(s.bctr << 32 | t);
                boolean any = false;
                for (TcBoundPass.Base b : all()) any |= ran(b, s.bctr);
                if (!left || any || g.dirs == null) continue;
                if (stop(end, deadline) || guardRuns >= MAX_GUARD_RUNS) break;
                more |= guard(s, sels);
            }
        }
        int notRun = 0, missed = 0;
        Map<String, Integer> undirected = new TreeMap<>(), missedWhy = new TreeMap<>();
        for (TcSwitchSel.Sel s : sels) {
            boolean ran = false;
            for (TcBoundPass.Base b : all()) ran |= ran(b, s.bctr);
            for (long t : new TreeSet<>(asList(s.targets))) {
                long key = s.bctr << 32 | t;
                if (!open.contains(key)) continue;
                if (!ran) notRun++;
                else if (s.kind == 0) undirected.merge(s.why, 1, Integer::sum);
                else {
                    missed++;
                    missedWhy.merge(MISSED[why.getOrDefault(key, 1)], 1, Integer::sum);
                }
            }
        }
        Map<String, Integer> kinds = new TreeMap<>();
        for (TcSwitchSel.Sel s : sels) kinds.merge(TcSwitchSel.kind(s), 1, Integer::sum);
        return String.format("\"switch_pass\":{\"switches\":%d,\"selectors\":{%s},\"open\":%d,\"reached\":%d,"
                + "\"added\":[%d,%d],\"runs\":%d,\"not_run\":%d,\"missed\":%d,\"undirected\":{%s},\"missed_why\":{%s},"
                + "\"guards\":{%s},\"guard_runs\":%d,\"note\":\"%s\"}",
            sels.size(), json(kinds), before, before - open.size(), added[0], added[1], runs, notRun, missed,
            json(undirected), json(missedWhy), json(guards), guardRuns, note);
    }

    boolean stop(long end, long deadline) {
        return added[0] + added[1] >= MAX_ADDED || TcBudget.over(g.work(), end)
            || deadline > 0 && System.currentTimeMillis() > deadline || g.refused != null;
    }

    static String json(Map<String, Integer> m) {
        StringBuilder u = new StringBuilder();
        for (Map.Entry<String, Integer> e : m.entrySet()) {
            u.append(u.length() > 0 ? "," : "").append('"').append(e.getKey().replace("\"", "'")).append("\":").append(e.getValue());
        }
        return u.toString();
    }

    static List<Long> asList(long[] a) {
        List<Long> out = new ArrayList<>();
        for (long x : a) out.add(x);
        return out;
    }

    // ---------- guards ----------

    // Distance (instructions, along the flow) from each address of the body to the bctr at a, for the addresses it is
    // reachable from (straight-line flow, b, bc, the tables' cases).
    static Map<Long, Integer> way(TcSwitchBody.Words img, Iterable<Long> body, Map<Long, long[]> tables, long a) {
        Map<Long, List<Long>> pred = new HashMap<>();
        for (long p : body) {
            Long wl = img.at(p);
            if (wl == null) continue;
            long w = wl;
            int op = (int) (w >>> 26), xo = (int) ((w >>> 1) & 0x3FF);
            boolean always = ((w >>> 21) & 0x14) == 0x14, link = (w & 1) != 0;
            List<Long> succ = new ArrayList<>();
            long[] bt = branch(p, w);
            if (bt != null && !link) {
                succ.add(bt[0]);
                if (op == 16 && !always) succ.add(p + 4);
            } else if (op == 19 && (xo == 16 || xo == 528) && !link) {
                if (tables.containsKey(p)) for (long t : tables.get(p)) succ.add(t);
                if (!always) succ.add(p + 4);
            } else {
                succ.add(p + 4);
            }
            for (long q : succ) pred.computeIfAbsent(q, k -> new ArrayList<>()).add(p);
        }
        Map<Long, Integer> d = new HashMap<>(Map.of(a, 0));
        Deque<Long> work = new ArrayDeque<>(List.of(a));
        while (!work.isEmpty()) {
            long q = work.removeFirst();
            for (long p : pred.getOrDefault(q, List.of())) if (d.putIfAbsent(p, d.get(q) + 1) == null) work.addLast(p);
        }
        return d;
    }

    static long[] branch(long p, long w) {  // {target} of a b or bc, else null
        int op = (int) (w >>> 26);
        if (op != 18 && op != 16) return null;
        long d = op == 18 ? w & 0x03FFFFFCL : w & 0xFFFCL, sign = op == 18 ? 0x02000000L : 0x8000L;
        return new long[] {(((d & sign) != 0 ? d - 2 * sign : d) + ((w & 2) != 0 ? 0 : p)) & M32};
    }

    // The conditional branches on the way to the bctr at a, nearest first: {branch, 1 when its taken direction is the
    // way}. The way is the direction the bctr is reachable from, or, when both are (a switch in a loop: round 1 found no
    // guard for the 112 Skies cases of the MusyX dispatch loops), the one nearer to it.
    static List<long[]> frontier(TcSwitchBody.Words img, Iterable<Long> body, Map<Long, long[]> tables, long a) {
        Map<Long, Integer> d = way(img, body, tables, a);
        List<long[]> out = new ArrayList<>();
        for (long p : body) {
            Long wl = img.at(p);
            if (wl == null || !d.containsKey(p) || (wl >>> 26) != 16 || (wl & 1) != 0 || ((wl >>> 21) & 0x14) == 0x14) continue;
            int t = d.getOrDefault(branch(p, wl)[0], Integer.MAX_VALUE), f = d.getOrDefault(p + 4, Integer.MAX_VALUE);
            if (t != f) out.add(new long[] {p, t < f ? 1 : 0, d.get(p)});
        }
        out.sort(Comparator.comparingLong((long[] x) -> x[2]).thenComparingLong(x -> x[0]));
        return out;
    }

    // Try the guards on the way to s's bctr: true when a run took a wanted direction (a new base).
    boolean guard(TcSwitchSel.Sel s, List<TcSwitchSel.Sel> sels) {
        Map<Long, long[]> tables = new HashMap<>();
        for (TcSwitchSel.Sel q : sels) tables.put(q.bctr, q.targets);
        List<Long> body = new ArrayList<>();
        for (ghidra.program.model.listing.Instruction in : g.listing.getInstructions(g.body, true)) body.add(in.getAddress().getOffset());
        TcDirs d = g.dirs;
        Map<Long, Integer> ix = new HashMap<>();
        for (int i = 0; i < d.at.length; i++) ix.put(d.at[i], i);
        boolean got = false;
        for (long[] fr : frontier(img, body, tables, s.bctr)) {
            Integer i = ix.get(fr[0]);
            if (i == null) continue;
            boolean tIn = fr[1] == 1;
            int want = tIn ? 2 * i + 1 : 2 * i, other = want ^ 1;
            if (dirs.contains(want) || !dirs.contains(other)) continue;
            TcSwitchSel.Guard gd = TcSwitchSel.guard(img, fnStart, fr[0], tIn);
            if (gd.why != null || gd.want.length == 0) {
                guards.merge(gd.why != null ? gd.why : "no value takes it", 1, Integer::sum);
                continue;
            }
            boolean took = false;
            int tries = 0;
            for (long x : gd.want) {
                Map<TcSwitchSel.Sel, Long> plan = TcSwitchSel.plan(gd.sel, x);
                if (plan == null) continue;
                for (TcBoundPass.Base b : all()) {
                    if (took || tries >= GUARD_TRIES || guardRuns >= MAX_GUARD_RUNS || !b.r.dirs.contains(other)) continue;
                    TcInputs.Vec v = direct(plan, b, 0);
                    if (v == null) continue;
                    tries++;
                    guardRuns++;
                    TcOracle.Result res = try1(v);
                    if (res == null || !res.dirs.contains(want)) continue;
                    took = true;
                    keep(v, res, extra);
                }
                if (took || tries >= GUARD_TRIES) break;
            }
            guards.merge(took ? "taken" : tries == 0 ? "no base gives the root a place" : "set, not taken", 1, Integer::sum);
            got |= took;
        }
        return got;
    }

    // ---------- runs ----------

    // A copy of base with each root of the plan set, or null when the base run gives one no place. var picks the
    // root's load or call: 0 the first at that pc (callee), 1 the next distinct one.
    TcInputs.Vec direct(Map<TcSwitchSel.Sel, Long> plan, TcBoundPass.Base base, int var) {
        TcInputs.Vec v = TcBoundPass.copy(base.v);
        boolean alt = false;
        for (Map.Entry<TcSwitchSel.Sel, Long> e : plan.entrySet()) {
            TcSwitchSel.Sel s = e.getKey();
            long x = e.getValue();
            if (s.kind == TcSwitchSel.ARG) {
                v.r[s.reg] = x;
            } else if (s.kind == TcSwitchSel.LOAD) {
                List<long[]> lds = new ArrayList<>();
                long[] first = base.r.loadAt.get(s.at);
                if (first != null) lds.add(first);
                for (long[] ld : base.r.loads) {
                    boolean seen = false;
                    for (long[] o : lds) seen |= o[1] == ld[1];
                    if (ld[0] == s.at && !seen) lds.add(ld);
                }
                if (lds.size() <= var) return null;
                long[] ld = lds.get(var);
                alt |= var > 0;
                if (ld[2] != s.size || g.facts.isCode(ld[1])) return null;
                TcBoundPass.patch(v, ld[1], s.size, x);
            } else {
                String c = TcBase.hex(s.callee);
                int k = -1, n = 0;
                for (int i = 0; i < base.r.calls.size() && k < 0; i++) if (base.r.calls.get(i).startsWith(c + " ") && n++ == var) k = i;
                if (k < 0 || k >= TcOracle.RETS) return null;
                alt |= var > 0;
                if (k < v.rets.length) {
                    v.rets[k] = x;
                } else {
                    long[] late = TcOracle.lateOf.get(v);
                    if (late == null) TcOracle.lateOf.put(v, late = new long[TcOracle.RETS - v.rets.length]);
                    late[k - v.rets.length] = x;
                }
            }
        }
        return var > 0 && !alt ? null : v;
    }

    // Run v as makeVectors does (pointer repairs): its result when valid, else null.
    TcOracle.Result try1(TcInputs.Vec v) {
        TcOracle.Result r = g.runGrow(v);
        for (int repairs = 0; !r.ok && r.faultAddr >= 0 && repairs < TcVectors.MAX_REPAIRS; repairs++) {
            List<long[]> loads = new ArrayList<>();
            for (long[] ld : r.loads) loads.add(ld.clone());
            TcInputs.Vec nv = g.repair(v, r, loads);
            if (nv == null) break;
            v = g.carry(v, nv);
            r = g.runGrow(v);
        }
        runs++;
        last = v;
        return r.ok && g.patchesInRam(v) ? r : null;
    }

    TcInputs.Vec last;  // try1's vector as repaired

    // Keep the run of v (as try1 repaired it) when it reaches a case still open: written, a base. Else, with into
    // (guards), a base only. True when written.
    boolean keep(TcInputs.Vec v0, TcOracle.Result r, List<TcBoundPass.Base> into) {
        TcInputs.Vec v = last != null ? last : v0;
        dirs.addAll(r.dirs);
        boolean any = false;
        for (long[] e : r.bctrs) any |= open.contains(e[0] << 32 | (e[1] & M32));
        if (!any) {
            if (into != null) into.add(new TcBoundPass.Base(v, r));
            return false;
        }
        for (long[] e : r.bctrs) open.remove(e[0] << 32 | (e[1] & M32));
        int s = (added[0] + added[1]) % 2;
        g.writeVector(g.valid[0] + g.valid[1], s == 0 ? "dev" : "hidden", v, r);
        g.valid[s]++;
        added[s]++;
        if (s == 0) { g.covDev.addAll(r.cov); g.dirsDev.addAll(r.dirs); }
        g.covAll.addAll(r.cov); g.dirsAll.addAll(r.dirs);
        for (int c : r.cov) g.blockHits.merge(c, 1, Integer::sum);
        for (long site : r.sites) g.siteHits.merge(site, 1, Integer::sum);
        g.switchHits.add(r.bctrs);
        g.bases.add(new TcBoundPass.Base(v, r));
        return true;
    }
}

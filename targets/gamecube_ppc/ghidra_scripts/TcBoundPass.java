// The targeted-vector pass for the PowerPC target (target.json "targeted_vectors", TcBound; pre-wave item 1): the
// Dreamcast pass (dreamcast_sh4 TcBoundPass) on this target's vectors. After both sets, a function with a branch
// direction (TcDirs) no valid vector took gets up to TcBound.MAX_ADDED more vectors per set: copies of valid vectors
// (bases, kept by TcVectors with the flag) with one to three of the inputs the copy's run read set to boundary values:
// a non-pointer integer parameter (r3..r10), a scripted r3 or f1 result the run used, a word it loaded at the load's
// size when a vector patch or a named writable global holds it (the image's constant data, .sdata2's floats among
// them, is never an input: a C literal does not read it), a float parameter (f1..f8, double bits), or one float
// parameter copied into another (two equal floats). Each copy runs with the pointer repairs makeVectors uses; one that
// is valid and takes a direction still open for its set is added after every other vector (those stay
// byte-identical). A function with every direction taken, or the flag off, is unchanged. Deterministic: a generator
// per (seed, set, try), ordered collections and a try cap; the deadline (TcFacts.oracleDeadlineS) ends it early, as it
// ends makeVectors. LOCKED: the watcher agent may not change this file. Not a script itself.
import java.lang.management.ManagementFactory;
import java.util.*;

final class TcBoundPass {
    static final class Base {  // a valid vector as run and its result
        final TcInputs.Vec v;
        final TcOracle.Result r;

        Base(TcInputs.Vec v, TcOracle.Result r) {
            this.v = v;
            this.r = r;
        }
    }

    final TcVectors g;
    final TcBound b;
    final List<Integer> ints = new ArrayList<>(), flts = new ArrayList<>();
    final List<Long> globals;

    private TcBoundPass(TcVectors g, TcBase.Proto proto, List<Long> globals) {
        this.g = g;
        b = TcBound.of(g.listing, g.body, g.mem, List.of(), List.of());
        this.globals = globals;
        for (int i = 0; i < proto.params.size(); i++) {
            String st = proto.params.get(i).storage;
            if (st.matches("f[1-8]")) flts.add(st.charAt(1) - '1');
            else if (st.matches("r([3-9]|10)") && !proto.paramIsPointer.get(i)) {
                ints.add(Integer.parseInt(st.substring(1)) - 3);
            }
        }
    }

    // The pass's oracle_summary.json fragment ("bound":{...}), or null when it is off.
    static String run(TcVectors g, TcBase.Proto proto, long seed, List<Long> globals) {
        if (!g.facts.targetedVectors || g.dirs == null) return null;
        Set<Integer> open = TcBound.open(g.dirs.total(), g.dirsAll);
        if (open.isEmpty()) return TcBound.summary(0, 0, new int[2], 0, "");
        if (g.bases.isEmpty()) return TcBound.summary(open.size(), 0, new int[2], 0, "no valid vector to start from");
        return new TcBoundPass(g, proto, globals).pass(seed, open);
    }

    String pass(long seed, Set<Integer> open) {
        long deadline = g.facts.oracleDeadlineS <= 0 ? 0
            : ManagementFactory.getRuntimeMXBean().getStartTime() + g.facts.oracleDeadlineS * 1000;
        int[] added = new int[2];
        int runs = 0;
        Set<Integer> hit = new TreeSet<>();
        String note = "";
        sets:
        for (int s = 0; s < 2; s++) {
            Set<Integer> want = new TreeSet<>(open);
            for (int t = 0; t < TcBound.TRIES && added[s] < TcBound.MAX_ADDED && !want.isEmpty(); t++) {
                if (deadline > 0 && System.currentTimeMillis() > deadline || g.refused != null) {
                    note = "the deadline came after " + runs + " runs";
                    break sets;
                }
                Random h = new Random(seed * 0x9e3779b97f4a7c15L + s * 0x632be59bd9b4e019L + t * 0x85ebca6bL
                    + 0x626f756e64L);
                Base base = g.bases.get(h.nextInt(g.bases.size()));
                TcInputs.Vec v = copy(base.v);
                for (int k = 1 + h.nextInt(3); k > 0; k--) place(v, base.r, h);
                TcOracle.Result r = g.runOracle(v);
                for (int repairs = 0; !r.ok && r.faultAddr >= 0 && repairs < TcVectors.MAX_REPAIRS; repairs++) {
                    List<long[]> loads = new ArrayList<>();
                    for (long[] ld : r.loads) loads.add(ld.clone());
                    TcInputs.Vec nv = g.repairVec(v, r.faultAddr, r.faultWrite, loads);
                    if (nv == null) break;
                    v = nv;
                    r = g.runOracle(v);
                }
                runs++;
                Set<Integer> got = new TreeSet<>(r.dirs);
                got.retainAll(want);
                if (!r.ok || !g.patchesInRam(v) || got.isEmpty()) continue;
                want.removeAll(got);
                hit.addAll(got);
                g.writeVector(g.valid[0] + g.valid[1], s == 0 ? "dev" : "hidden", v, r);
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

    static TcInputs.Vec copy(TcInputs.Vec v) {
        TcInputs.Vec c = new TcInputs.Vec();
        c.r = v.r.clone();
        c.f = v.f.clone();
        c.rets = v.rets.clone();
        c.frets = v.frets.clone();
        c.patchAddr = new ArrayList<>(v.patchAddr);
        c.patchData = new ArrayList<>();
        for (byte[] d : v.patchData) c.patchData.add(d.clone());
        c.genHeapNext = v.genHeapNext;
        c.genPoked = new HashMap<>(v.genPoked);
        return c;
    }

    // Is [a, a + n) an input: inside a patch of v, or a named writable global's word?
    boolean input(TcInputs.Vec v, long a, int n) {
        for (int i = 0; i < v.patchAddr.size(); i++) {
            long p = v.patchAddr.get(i);
            if (a >= p && a + n <= p + v.patchData.get(i).length) return true;
        }
        for (long gl : globals) if (a >= (gl & ~3L) && a + n <= (gl & ~3L) + 4) return true;
        return false;
    }

    // The words the base run loaded that a vector may set: 1, 2, 4 or 8 bytes, inputs only, one per address.
    List<long[]> loads(TcInputs.Vec v, TcOracle.Result r) {
        List<long[]> out = new ArrayList<>();
        Set<Long> seen = new HashSet<>();
        for (long[] ld : r.loads) {
            int n = (int) ld[2];
            if ((n == 1 || n == 2 || n == 4 || n == 8) && !g.facts.isCode(ld[1]) && input(v, ld[1], n)
                && seen.add(ld[1])) out.add(ld);
        }
        return out;
    }

    static void patch(TcInputs.Vec v, long a, int n, long val) {  // big endian
        byte[] d = new byte[n];
        for (int i = 0; i < n; i++) d[i] = (byte) (val >> (8 * (n - 1 - i)));
        v.patchAddr.add(a);
        v.patchData.add(d);
        if (n == 4 && v.genPoked.containsKey(a)) v.genPoked.put(a, val);
    }

    // One boundary value into one input the base run read: a kind first (each with even odds), then a slot of it.
    void place(TcInputs.Vec v, TcOracle.Result base, Random h) {
        int rets = Math.min(base.calls.size(), v.rets.length);
        List<long[]> lds = loads(v, base);
        List<Integer> kinds = new ArrayList<>();
        if (!ints.isEmpty()) kinds.add(0);
        if (rets > 0) kinds.add(1);
        if (rets > 0 && b.fcmp) kinds.add(2);
        if (!lds.isEmpty()) kinds.add(3);
        if (!flts.isEmpty()) kinds.add(4);
        if (flts.size() > 1) kinds.add(5);
        if (kinds.isEmpty()) return;
        switch (kinds.get(h.nextInt(kinds.size()))) {
            case 0: v.r[ints.get(h.nextInt(ints.size()))] = b.intValue(h, 4); break;
            case 1: v.rets[h.nextInt(rets)] = b.intValue(h, 4); break;
            case 2: v.frets[h.nextInt(rets)] = b.floatBits(h, true); break;
            case 3: {
                long[] ld = lds.get(h.nextInt(lds.size()));
                int n = (int) ld[2];
                boolean fl = b.fcmp && n >= 4 && h.nextBoolean();
                patch(v, ld[1], n, fl ? b.floatBits(h, n == 8) : b.intValue(h, n));
                break;
            }
            case 4: v.f[flts.get(h.nextInt(flts.size()))] = b.floatBits(h, true); break;
            default: {  // two equal floats
                int i = flts.get(h.nextInt(flts.size())), j = flts.get(h.nextInt(flts.size()));
                v.f[j] = v.f[i];
            }
        }
    }
}

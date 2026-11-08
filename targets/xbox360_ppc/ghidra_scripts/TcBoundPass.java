// The targeted-vector pass for the Xbox 360 target (target.json "targeted_vectors", TcBound; pre-wave item 1): the
// GameCube pass (gamecube_ppc TcBoundPass) on this target's 64-bit registers. After both sets, a function with a branch
// direction (TcDirs) no valid vector took gets up to TcBound.MAX_ADDED more vectors per set: copies of valid vectors
// (the bases: TcVectors.goodVecs and goodRes as makeVectors left them) with one to three of the inputs the copy's run
// read set to boundary values: a non-pointer integer parameter (r3..r10, at its kind's width: u32 zero-extended, s32
// sign-extended, u64/s64 all 64 bits), a scripted r3 result (a word, zero- or sign-extended, or a doubleword) or f1
// result the run used, a word it loaded at the load's size when a vector patch or a named writable global holds it
// (the image's constant data is never an input: a C literal does not read it), a float parameter (f1..f13: an f32 one
// gets a single widened to the double the register holds, an f64 one double bits), or one float parameter copied into
// another (two equal floats). Each copy runs with the pointer repairs makeVectors uses (TcInputs.repairVec); one that is
// valid, patches only mapped memory and takes a direction still open for its set is added after every other vector
// (those stay byte-identical). A function with every direction taken, or the flag off, is unchanged. Deterministic: a
// generator per (seed, set, try), ordered collections and a try cap; the deadline (TcFacts.oracleDeadlineS) ends it
// early, as it ends makeVectors. LOCKED: the watcher agent may not change this file. Not a script itself.
import java.lang.management.ManagementFactory;
import java.util.*;

final class TcBoundPass {
    final TcVectors g;
    final TcBound b;
    final List<Integer> ints = new ArrayList<>(), flts = new ArrayList<>();
    final List<String> intKind = new ArrayList<>();
    final List<Boolean> fltSingle = new ArrayList<>();
    final List<TcInputs.Vec> baseVecs;
    final List<TcOracle.Result> baseRes;
    final List<Long> globals;

    private TcBoundPass(TcVectors g, TcBase.Proto proto, List<Long> globals) {
        this.g = g;
        b = TcBound.of(g.listing, g.body, g.mem, List.of(), List.of());
        this.globals = globals;
        baseVecs = new ArrayList<>(g.goodVecs);
        baseRes = new ArrayList<>(g.goodRes);
        for (int i = 0; i < proto.params.size(); i++) {
            TcBase.Param q = proto.params.get(i);
            if (q.storage.matches("f([1-9]|1[0-3])") && q.kind.matches("f32|f64")) {
                flts.add(Integer.parseInt(q.storage.substring(1)) - 1);
                fltSingle.add(q.kind.equals("f32"));
            } else if (q.storage.matches("r([3-9]|10)") && !proto.paramIsPointer.get(i) && q.kind.matches(TcBase.INT_KIND)) {
                ints.add(Integer.parseInt(q.storage.substring(1)) - 3);
                intKind.add(q.kind);
            }
        }
    }

    // The pass's oracle_summary.json fragment ("bound":{...}), or null when it is off.
    static String run(TcVectors g, TcBase.Proto proto, long seed, List<Long> globals) {
        if (!g.facts.targetedVectors || g.dirs == null) return null;
        Set<Integer> open = TcBound.open(g.dirs.total(), g.dirsAll);
        if (open.isEmpty()) return TcBound.summary(0, 0, new int[2], 0, "");
        if (g.goodVecs.isEmpty()) return TcBound.summary(open.size(), 0, new int[2], 0, "no valid vector to start from");
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
                int k0 = h.nextInt(baseVecs.size());
                TcInputs.Vec v = copy(baseVecs.get(k0));
                for (int k = 1 + h.nextInt(3); k > 0; k--) place(v, baseRes.get(k0), h);
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
                if (!r.ok || !g.patchesMapped(v) || got.isEmpty()) continue;
                want.removeAll(got);
                hit.addAll(got);
                g.valid[s]++;
                added[s]++;
                if (!g.writeVector(g.valid[0] + g.valid[1] - 1, s == 0 ? "dev" : "hidden", v, r)) continue;
                g.goodVecs.add(v);
                g.goodRes.add(r);
                if (g.hasFma) g.checkFma(v, r);
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

    // An integer register's boundary value at a kind's width: u32 zero-extended, s32 sign-extended, else 64 bits.
    long intReg(Random h, String kind) {
        if (kind.equals("u64") || kind.equals("s64")) return b.intValue(h, 8);
        long w = b.intValue(h, 4);
        return kind.equals("s32") ? (long) (int) w : w;
    }

    // A float register's boundary value: a single widened to a double (an f32 parameter), or double bits.
    long fltReg(Random h, boolean single) {
        return single ? Double.doubleToRawLongBits(Float.intBitsToFloat((int) b.floatBits(h, false))) : b.floatBits(h, true);
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
            case 0: {
                int i = h.nextInt(ints.size());
                v.r[ints.get(i)] = intReg(h, intKind.get(i));
                break;
            }
            case 1: v.rets[h.nextInt(rets)] = intReg(h, new String[] {"u32", "s32", "u64"}[h.nextInt(3)]); break;
            case 2: v.frets[h.nextInt(rets)] = fltReg(h, h.nextBoolean()); break;
            case 3: {
                long[] ld = lds.get(h.nextInt(lds.size()));
                int n = (int) ld[2];
                boolean fl = b.fcmp && n >= 4 && h.nextBoolean();
                patch(v, ld[1], n, fl ? b.floatBits(h, n == 8) : b.intValue(h, n));
                break;
            }
            case 4: {
                int i = h.nextInt(flts.size());
                v.f[flts.get(i)] = fltReg(h, fltSingle.get(i));
                break;
            }
            default: {  // two equal floats
                int i = flts.get(h.nextInt(flts.size())), j = flts.get(h.nextInt(flts.size()));
                v.f[j] = v.f[i];
            }
        }
    }
}

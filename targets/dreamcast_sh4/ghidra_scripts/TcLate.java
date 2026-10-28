// The results of the mocked calls after the first 16 (r15). A vector scripted 16 call results (Vec.rets) and every
// later call returned 0, so a branch on a later call's result was never taken: TC 8c0117f4 Camera_UpdateKind10 calls
// Entity_HasCamView 17th, and the whole camera-view half (38 of 55 blocks) went untested while a wrong readable C
// passed 96/96. Now calls up to TcOracle.RETS get a result too (TcOracle.entryLate; vectors.txt line C carries them
// after Vec.rets, tc_rt.c's TCRT_RETS). Not a script itself; TcVectors draws them, TcOracle returns them.
//
// They come from their own generator, so the vector's other draws stay as they were, and line C carries them only
// as far as the run made calls: a vector with 16 calls or fewer is written byte for byte as before (only harnesses
// with a 17th call change: tools/selective_restamp.py --change late). No random words: each is a small number or a
// scratch pointer.
//
// Repair (decision 5): a run that faults through one of these results gets it repaired, as TcProvenance repairs
// Vec.rets. PS2's object-spawn loops (8c03373a and 9 more) call the allocator 8c03f448 36-120 times and write through
// each result, so one repair per call would never fit MAX_REPAIRS: the repair marks the callee instead, and every later
// call to it (this run and the reruns) returns a fresh chunk (Marks, drawn once per vector).
// The result repaired is the latest later-call result that is the faulting store's base (TcOracle.storeBase), or for
// a fault without one, the latest whose value lies just below the fault address. Only runs that fault after a 17th
// call can differ (tools/selective_restamp.py --change lrep).
//
// genretfn (decision 7): the hint line "genretfn 0xCALLEE LO HI" (oracle/hints/<addr>.txt; LO, HI decimal or 0x hex,
// LO <= HI, a signed or unsigned 32-bit range) makes every call to CALLEE among a vector's first TcOracle.RETS return
// a value drawn uniformly from LO..HI, whichever call number it is. Spawn 8c5e0f40 indexes a table with the result of
// 8c5f6154 (a remainder by 10), called every 3-4 calls up to call 36; "genret N VAL" sets only calls 0-15, to one
// constant. A call past RETS still returns 0 (the runtimes replay RETS results). The draws come from their own
// generator (seed, vector id, try), all RETS of them once per try and line, so no other draw moves; they land in
// Vec.rets or entryLate, so line C carries them. TcVectors passes the other lines on to TcInputs.applyHints (each
// workspace's own TcInputs.java needs no change); a hints file without the line draws nothing, so its harness stays
// byte for byte (tools/selective_restamp.py --change retfn: the functions whose hints have the line).
import java.util.*;

final class TcLate {
    private TcLate() {
    }

    // One vector's repair: the callees marked and a fresh chunk for each later call (by index); TcReach.copy copies it.
    static final class Marks {
        final Set<Long> callees = new HashSet<>();
        long[] pool;
    }
    static final Map<TcInputs.Vec, Marks> MARKS = Collections.synchronizedMap(new WeakHashMap<>());
    // genretfn: one vector try's draws, by callee (each RETS long; never changed once drawn); TcReach.copy shares them.
    static final Map<TcInputs.Vec, Map<Long, long[]>> RANGED = Collections.synchronizedMap(new WeakHashMap<>());

    static void copy(TcInputs.Vec from, TcInputs.Vec to) {
        Map<Long, long[]> d = RANGED.get(from);
        if (d != null) RANGED.put(to, d);
        Marks m = MARKS.get(from), c = new Marks();
        if (m == null) return;
        c.callees.addAll(m.callees);
        c.pool = m.pool == null ? null : m.pool.clone();
        MARKS.put(to, c);
    }

    // The results for calls `first` .. TcOracle.RETS - 1 of one vector try.
    static long[] draw(TcFacts facts, long seed, int id, int attempt, int first) {
        Random h = new Random(seed * 0xd6e8feb86659fd93L + id * 0x632be59bd9b4e019L + attempt * 0x85ebca6bL);
        long[] out = new long[Math.max(0, TcOracle.RETS - first)];
        long words = (facts.scrHi - facts.scrLo) / 4;
        for (int i = 0; i < out.length; i++) {
            int k = h.nextInt(10);
            out[i] = k < 5 ? h.nextInt(4) : k < 7 ? facts.scrLo + 4L * h.nextInt((int) words) : k < 9 ? 0 : 1;
        }
        return out;
    }

    // The result of later call k (to callee pc): the pool's chunk once a repair marked pc, else x as drawn.
    static long pooled(TcInputs.Vec v, long pc, int k, long x) {
        Marks m = MARKS.get(v);
        return m != null && m.pool != null && k < m.pool.length && m.pool[k] != 0 && m.callees.contains(pc)
            ? m.pool[k] : x;
    }

    // hints without their genretfn lines (the same list when it has none); v's draws for those lines go to RANGED.
    // A malformed line, or two for one callee, is refused like any bad hint line.
    static List<String> ranges(TcInputs.Vec v, List<String> hints, long seed, int id, int attempt) {
        List<String> rest = null;
        Map<Long, long[]> got = new HashMap<>();
        Random h = null;
        for (int i = 0; i < hints.size(); i++) {
            String s = hints.get(i).trim();
            String[] t = s.split("\\s+");
            if (!t[0].equals("genretfn")) {
                if (rest != null) rest.add(hints.get(i));
                continue;
            }
            if (rest == null) rest = new ArrayList<>(hints.subList(0, i));
            long callee, lo, hi;
            try {
                if (t.length != 4) throw new NumberFormatException();
                callee = num(t[1]);
                lo = num(t[2]);
                hi = num(t[3]);
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException("bad genretfn line (genretfn 0xCALLEE LO HI): " + s);
            }
            if (callee < 0 || callee > 0xffffffffL || lo > hi || lo < -0x80000000L || hi > 0xffffffffL) {
                throw new IllegalArgumentException("bad genretfn range (a callee address, LO <= HI in 32 bits): " + s);
            }
            if (got.containsKey(callee)) throw new IllegalArgumentException("genretfn twice for one callee: " + s);
            if (h == null) h = new Random(seed * 0x9e3779b97f4a7c15L + id * 0x632be59bd9b4e019L + attempt * 0x85ebca6bL + 0x2f);
            long[] d = new long[TcOracle.RETS];
            for (int c = 0; c < d.length; c++) d[c] = (lo + (long) (h.nextDouble() * (hi - lo + 1))) & 0xffffffffL;
            got.put(callee, d);
        }
        if (rest == null) return hints;
        RANGED.put(v, got);
        return rest;
    }

    static long num(String s) {
        return s.startsWith("0x") ? Long.parseLong(s.substring(2), 16) : Long.parseLong(s);
    }

    // The result of call c (0-based, all calls) to callee pc: its genretfn draw when a line names pc, else x.
    static long ranged(TcInputs.Vec v, long pc, int c, long x) {
        Map<Long, long[]> m = RANGED.get(v);
        long[] d = m == null ? null : m.get(pc);
        return d != null && c < d.length ? d[c] : x;
    }

    // Repairs the later call result the fault came through (see the header); false when none did.
    static boolean repair(TcVectors g, TcInputs.Vec v, TcOracle.Result r) {
        if (g.entryLate == null || r.faultAddr < 0 || r.callFault) return false;
        int first = v.rets.length;
        for (int c = Math.min(r.calls.size(), first + g.entryLate.length) - 1; c >= first; c--) {
            long x = g.entryLate[c - first], d = r.faultAddr - x;
            if (r.storeBase >= 0 ? x != r.storeBase : d < 0) continue;
            if (d < -0x100 || d >= TcInputs.GEN_MAX_OFFSET || g.genOwned(x)) continue;
            g.entryLate[c - first] = g.genChunk(v, d);
            Marks m = MARKS.computeIfAbsent(v, z -> new Marks());
            if (m.pool == null) {
                m.pool = new long[g.entryLate.length];
                for (int j = c - first + 1; j < m.pool.length; j++) m.pool[j] = g.genChunk(v, 0);
            }
            m.callees.add(Long.parseLong(r.calls.get(c).split(" ", 2)[0], 16));
            return true;
        }
        return false;
    }

    // Line C's tail: the results of the `made` later calls the run made (none: empty).
    static String tail(long[] late, int made) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; late != null && i < Math.min(late.length, made); i++) b.append(' ').append(TcBase.hex(late[i]));
        return b.toString();
    }
}

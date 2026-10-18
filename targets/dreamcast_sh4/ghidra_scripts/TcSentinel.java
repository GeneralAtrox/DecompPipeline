// Sentinel repair for a run that hits the call cap (TcVectors.runRepaired). Not a script itself; used by TcVectors.
//
// A loop that walks a table until an end marker and calls a function per entry (Power Stone 2 8c17f210:
// `for (p = r4; p->f4 != -1; p += 16) r |= fn(p, ...)`) never meets the marker in random input memory, so every vector
// runs to TcOracle.CALL_LIMIT mocked calls and is invalid; the call-cap fail-fast (TcVectors.CAP_FAIL_FAST) then refuses
// the function. The walk shows in the run's load log: one load pc whose effective addresses step by a constant stride
// in both the first and the last LOAD_LOG loads. Such a walker gets its end marker planted, as TcCount cuts a loop
// count: at iteration k (0..SMALL-1, from the vector's own random) of every walker load, the value is the immediate of
// a `cmp/eq #imm,r0` within LOOKAHEAD instructions after a walker load, then -1, then 0 (a NULL-terminated list), one
// candidate per rerun. The plant goes into the patch holding the word, else into a new patch of its own, and is kept
// through the vector's later repairs (restore). A loop whose end is a callee result (the mocks return 0 after 16 calls)
// shows no walker and is refused as before.
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import java.util.*;

final class TcSentinel {
    static final int SMALL = 8;       // the walk ends at iteration 0..SMALL-1
    static final int MIN_STEPS = 3;   // a walker pc has at least this many logged loads, all one stride apart
    static final long MAX_STRIDE = 0x1000;
    static final int LOOKAHEAD = 8;   // instructions after a walker load searched for its compare

    final List<long[]> walkers = new ArrayList<>(); // {first address, stride, size}
    final List<Long> values = new ArrayList<>();    // the end markers still to try
    final List<long[]> planted = new ArrayList<>(); // {address, size, value}: the current plant
    boolean found;
    int k;

    // Plants the next end marker in v for the call-capped run r. Returns whether anything changed.
    boolean repair(TcVectors g, TcInputs.Vec v, TcOracle.Result r, Random h) {
        if (!found) {
            found = true;
            find(g, r);
            k = h.nextInt(SMALL);
        }
        if (values.isEmpty()) return false;
        long value = values.remove(0);
        planted.clear();
        for (long[] w : walkers) {
            long a = w[0] + w[1] * k;
            if (a >= TcBase.RAM_LO && a + w[2] <= TcBase.RAM_HI) planted.add(new long[] {a, w[2], value});
        }
        if (planted.isEmpty()) return false;
        restore(v);
        return true;
    }

    // The planted end markers are back after a later repair.
    void restore(TcInputs.Vec v) {
        for (long[] p : planted) {
            int size = (int) p[1];
            if (TcCount.patchAt(v, p[0], size) == null) {
                v.patchAddr.add(p[0]);
                v.patchData.add(new byte[size]);
            }
            TcCount.put(v, p[0], size, p[2]);
        }
    }

    private void find(TcVectors g, TcOracle.Result r) {
        Map<Long, long[]> last = strides(r.loads), first = strides(r.firstLoads);
        Set<Long> marks = new LinkedHashSet<>();
        for (Map.Entry<Long, long[]> e : last.entrySet()) {
            long[] f = first.get(e.getKey()), l = e.getValue();
            if (f == null || f[1] != l[1] || f[2] != l[2]) continue;
            walkers.add(f);
            marks.addAll(compares(g, e.getKey(), (int) f[2]));
        }
        if (walkers.isEmpty()) return;
        marks.add(mask(-1, (int) walkers.get(0)[2]));
        marks.add(0L);
        values.addAll(marks);
    }

    // load pc -> {first address, stride, size} for the pcs whose logged loads step by one constant stride.
    private static Map<Long, long[]> strides(List<long[]> loads) {
        Map<Long, List<long[]>> by = new LinkedHashMap<>();
        for (long[] ld : loads) by.computeIfAbsent(ld[0], x -> new ArrayList<>()).add(ld);
        Map<Long, long[]> out = new LinkedHashMap<>();
        for (Map.Entry<Long, List<long[]>> e : by.entrySet()) {
            List<long[]> l = e.getValue();
            if (l.size() < MIN_STEPS) continue;
            long stride = l.get(1)[1] - l.get(0)[1];
            boolean even = stride != 0 && Math.abs(stride) <= MAX_STRIDE;
            for (int i = 2; even && i < l.size(); i++) even = l.get(i)[1] - l.get(i - 1)[1] == stride;
            if (even) out.put(e.getKey(), new long[] {l.get(0)[1], stride, l.get(0)[2]});
        }
        return out;
    }

    // The immediates of the `cmp/eq #imm,r0` compares just after the load at pc (as `size` bytes, unsigned).
    private static List<Long> compares(TcVectors g, long pc, int size) {
        List<Long> out = new ArrayList<>();
        Instruction in = g.listing.getInstructionAt(g.toAddr(pc));
        for (int i = 0; in != null && i < LOOKAHEAD; i++, in = in.getNext()) {
            if (i == 0 || !in.getMnemonicString().toLowerCase().replace("_", "").equals("cmp/eq")) continue;
            Scalar s = in.getScalar(0);
            if (s != null) out.add(mask(s.getSignedValue(), size));
        }
        return out;
    }

    private static long mask(long value, int size) {
        return size >= 4 ? value & 0xffffffffL : value & ((1L << (8 * size)) - 1);
    }
}

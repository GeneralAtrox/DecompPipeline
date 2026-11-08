// Targeted vectors (target.json "targeted_vectors"; pre-wave item 1): the boundary values a function's compares test.
// Random inputs and TcGuide's constants hit `x == c` but rarely c - 1 against c + 1 on an ordered compare, a byte at
// 0x80 or 0xff, INT_MIN, a float at +-0.0, infinity, FLT_MAX or a subnormal, or two equal floats; the mutants a wrong
// boundary makes (`<` for `<=`, signed for unsigned, a narrower type) survive (dcrecomp's vector_gap class: 4,130 on
// the Dreamcast games). This harvests, from the function's p-code (the same on every ISA), every compare
// (INT_EQUAL .. INT_SLESSEQUAL, FLOAT_EQUAL .. FLOAT_NAN) with a constant operand, or an operand a constant reaches
// through COPY, a load from a constant address the program image holds (a literal pool), an extension, a subpiece or
// integer arithmetic on known values; plus the target's own pattern constants (extra: TcGuide's, TcFguide's). The
// values it draws: a harvested c and c +- 1, then 0, 1, -1, 0xff, 0xffff, the signed minimum and maximum, all at the
// width asked for (a load's size: a byte flag gets byte boundaries); floats: harvested ones and their neighbours one
// ulp away, +-0.0, +-inf, +-FLT_MAX, the smallest subnormal (doubles: the same values widened, DBL_MAX, the smallest
// double subnormal). Each target's TcBoundPass puts them into the inputs of copies of valid vectors; this file is
// one file in every target (tests/test_targeted_vectors.py). Deterministic: no generator of its own (the pass hands
// one in), ordered collections. LOCKED: the watcher agent may not change this file. Not a script itself.
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.pcode.*;
import java.util.*;

final class TcBound {
    static final int MAX_ADDED = 16;  // added vectors per set
    static final int TRIES = 96;      // runs per set
    static final long BUDGET_S = 60;  // the pass's work budget in seconds (TcBudget.of)

    interface Reader {  // the program image's value at a constant address, or null (not initialized there)
        Long read(long addr, int size);
    }

    final LinkedHashSet<Long> ints = new LinkedHashSet<>();     // compared integer constants, as unsigned 64-bit
    final LinkedHashSet<Long> floats = new LinkedHashSet<>();   // compared floats (f32 bits)
    final LinkedHashSet<Long> doubles = new LinkedHashSet<>();  // compared doubles (bits)
    boolean fcmp;  // the function compares floats

    private static AddressSetView memoAt;
    private static TcBound memo;

    // The harvest for body (memoized: the pass and TcReach ask for the same function).
    static TcBound of(Listing listing, AddressSetView body, Memory mem, Collection<Long> extraInts,
                      Collection<Long> extraFloats) {
        if (body.equals(memoAt)) return memo;
        List<PcodeOp> ops = new ArrayList<>();
        for (Instruction in : listing.getInstructions(body, true)) ops.addAll(Arrays.asList(in.getPcode()));
        memo = of(ops, (a, n) -> image(mem, a, n), extraInts, extraFloats);
        memoAt = body;
        return memo;
    }

    static TcBound of(List<PcodeOp> ops, Reader rd, Collection<Long> extraInts, Collection<Long> extraFloats) {
        TcBound b = new TcBound();
        Map<Varnode, Long> known = new HashMap<>();
        for (PcodeOp op : ops) b.step(op, known, rd);
        for (long c : extraInts) b.ints.add(c & 0xffffffffL);
        for (long c : extraFloats) b.floats.add(c & 0xffffffffL);
        return b;
    }

    static Long image(Memory mem, long a, int n) {
        try {
            ghidra.program.model.address.Address at = mem.getProgram().getAddressFactory().getDefaultAddressSpace()
                .getAddress(a);
            ghidra.program.model.mem.MemoryBlock blk = mem.getBlock(at);
            if (blk == null || !blk.isInitialized() || n > 8) return null;
            byte[] d = new byte[n];
            if (mem.getBytes(at, d) != n) return null;
            long v = 0;
            for (int i = 0; i < n; i++) v |= (d[i] & 0xffL) << (8 * (mem.isBigEndian() ? n - 1 - i : i));
            return v;
        } catch (Exception e) {
            return null;
        }
    }

    static long mask(int size) {
        return size >= 8 ? -1L : (1L << (8 * size)) - 1;
    }

    Long value(Varnode v, Map<Varnode, Long> known) {
        return v == null ? null : v.isConstant() ? (Long) (v.getOffset() & mask(v.getSize())) : known.get(v);
    }

    // One p-code op in address order: harvest a compare's known operands, then track the value its output holds.
    void step(PcodeOp op, Map<Varnode, Long> known, Reader rd) {
        int code = op.getOpcode();
        boolean icmp = code >= PcodeOp.INT_EQUAL && code <= PcodeOp.INT_LESSEQUAL;
        boolean fc = code >= PcodeOp.FLOAT_EQUAL && code <= PcodeOp.FLOAT_NAN;
        if (icmp || fc) {
            fcmp |= fc;
            for (Varnode in : op.getInputs()) {
                Long c = value(in, known);
                if (c == null) continue;
                if (icmp) ints.add(c);
                else if (in.getSize() == 8) doubles.add(c);
                else if (in.getSize() == 4) floats.add(c);
            }
        }
        Varnode out = op.getOutput();
        if (out == null) return;
        Long a = op.getNumInputs() > 0 ? value(op.getInput(0), known) : null;  // before the output is forgotten
        Long b = op.getNumInputs() > 1 ? value(op.getInput(1), known) : null;  // (ori r3,r3,k reads r3)
        known.remove(out);
        Long r = null;
        switch (code) {
            case PcodeOp.COPY: case PcodeOp.INT_ZEXT: r = a; break;
            case PcodeOp.INT_SEXT:
                if (a != null) r = a << (64 - 8 * op.getInput(0).getSize()) >> (64 - 8 * op.getInput(0).getSize());
                break;
            case PcodeOp.LOAD: r = b == null ? null : rd.read(b, out.getSize()); break;
            case PcodeOp.SUBPIECE: r = a == null || b == null || b >= 8 ? null : a >>> (8 * b); break;
            case PcodeOp.INT_ADD: r = a == null || b == null ? null : a + b; break;
            case PcodeOp.INT_SUB: r = a == null || b == null ? null : a - b; break;
            case PcodeOp.INT_OR: r = a == null || b == null ? null : a | b; break;
            case PcodeOp.INT_AND: r = a == null || b == null ? null : a & b; break;
            case PcodeOp.INT_XOR: r = a == null || b == null ? null : a ^ b; break;
            case PcodeOp.INT_LEFT: r = a == null || b == null || b >= 64 ? null : a << b; break;
            case PcodeOp.INT_MULT: r = a == null || b == null ? null : a * b; break;
            case PcodeOp.FLOAT_FLOAT2FLOAT:
                if (a != null && op.getInput(0).getSize() == 4 && out.getSize() == 8) {
                    r = Double.doubleToRawLongBits(Float.intBitsToFloat((int) (long) a));
                } else if (a != null && op.getInput(0).getSize() == out.getSize()) r = a;
                break;
            default:
        }
        if (r != null && !out.isConstant()) known.put(out, r & mask(out.getSize()));
    }

    // An integer boundary value at `size` bytes (1, 2, 4 or 8), unsigned.
    long intValue(Random h, int size) {
        long min = 1L << (8 * Math.min(size, 8) - 1);
        if (!ints.isEmpty() && h.nextInt(100) < 55) return (pick(ints, h) + h.nextInt(3) - 1) & mask(size);
        long[] edge = {0, 1, -1, 0xff, 0xffff, min, min - 1};
        return edge[h.nextInt(edge.length)] & mask(size);
    }

    static final int[] F32 = {0, 0x80000000, 0x7f800000, 0xff800000, 0x7f7fffff, 0xff7fffff, 1, 0x80000001,
                              0x3f800000, 0xbf800000};  // +-0, +-inf, +-FLT_MAX, +-smallest subnormal, +-1

    // A float boundary value: f32 bits, or f64 bits when dbl (a 64-bit register or slot).
    long floatBits(Random h, boolean dbl) {
        int k = h.nextInt(100);
        if (dbl && !doubles.isEmpty() && k < 30) return pick(doubles, h) + h.nextInt(3) - 1;
        if (!floats.isEmpty() && k < 60) {
            long c = (pick(floats, h) + h.nextInt(3) - 1) & 0xffffffffL;  // one ulp either way (by magnitude)
            return dbl ? Double.doubleToRawLongBits(Float.intBitsToFloat((int) c)) : c;
        }
        if (dbl && k >= 90) return new long[] {0x7fefffffffffffffL, 1L, 0x8000000000000001L}[h.nextInt(3)];
        int f = F32[h.nextInt(F32.length)];
        return dbl ? Double.doubleToRawLongBits(Float.intBitsToFloat(f)) : f & 0xffffffffL;
    }

    static long pick(Collection<Long> c, Random h) {
        int i = h.nextInt(c.size());
        for (long x : c) if (i-- == 0) return x;
        throw new IllegalStateException();
    }

    // The branch directions no valid vector took (TcDirs indices), in order.
    static Set<Integer> open(int total, Set<Integer> hit) {
        Set<Integer> out = new TreeSet<>();
        for (int i = 0; i < total; i++) if (!hit.contains(i)) out.add(i);
        return out;
    }

    // The pass's oracle_summary.json fragment.
    static String summary(int open, int runs, int[] added, int hit, String note) {
        return String.format("\"bound\":{\"open\":%d,\"runs\":%d,\"added\":[%d,%d],\"hit\":%d%s}", open, runs,
            added[0], added[1], hit, note.isEmpty() ? "" : ",\"note\":\"" + note + "\"");
    }

    // Compare edges (target.json "boundary_vectors"; TcBoundPass.edges). A relational mutant (`<` for `<=`, a compared
    // constant one off) differs from the original only where the compare's operands are equal or one apart, which
    // random inputs taking both directions of a branch rarely make (the mutation audit's relop and const survivors). A
    // site is an integer or float compare in the p-code of a compare instruction of the body (mnemonic cmp* or fcmp*)
    // whose operands are registers the instruction has not written before it, or constants (directly, or through
    // COPY or an extension inside the instruction), at least one a register. An integer site i has three edges, its
    // operands' difference -1, 0 and +1 at the compare's width (3i, 3i+1, 3i+2); a float site one, equal operands
    // (3i+1). TcOracle records the edges a run makes (Result.edges) from the registers before the step at the site:
    // reading them changes no run. A compare in a delay slot runs in its branch's step and is not seen.
    static final class Edges {
        interface Regs {  // a register's value before the step (TcOracle: its MemoryState)
            long read(Varnode v);
        }

        static final class Site {
            final int index, size;
            final boolean flt;
            final Varnode a, b;  // a register operand, or null: the constant ca / cb
            final long ca, cb;

            Site(int index, int size, boolean flt, Varnode a, long ca, Varnode b, long cb) {
                this.index = index; this.size = size; this.flt = flt; this.a = a; this.ca = ca; this.b = b; this.cb = cb;
            }
        }

        final Map<Long, List<Site>> at = new HashMap<>();
        final Set<Integer> goals = new TreeSet<>();  // every edge a run can make
        int sites;

        static Edges of(Listing listing, AddressSetView body) {
            Edges e = new Edges();
            for (Instruction in : listing.getInstructions(body, true))
                e.add(in.getAddress().getOffset(), in.getMnemonicString(), in.getPcode());
            return e;
        }

        // One instruction's sites, in p-code order.
        void add(long pc, String mnemonic, PcodeOp[] ops) {
            String m = mnemonic.toLowerCase(Locale.ROOT);
            if (!m.startsWith("cmp") && !m.startsWith("fcmp")) return;
            Map<Varnode, Long> known = new HashMap<>();
            Set<Varnode> written = new HashSet<>();
            for (PcodeOp op : ops) {
                int code = op.getOpcode();
                boolean icmp = code >= PcodeOp.INT_EQUAL && code <= PcodeOp.INT_LESSEQUAL;
                boolean fc = code >= PcodeOp.FLOAT_EQUAL && code <= PcodeOp.FLOAT_LESSEQUAL;
                if ((icmp || fc) && op.getNumInputs() == 2) {
                    Varnode a = op.getInput(0), b = op.getInput(1);
                    Long ca = constant(a, known), cb = constant(b, known);
                    int size = a.getSize();
                    boolean ra = ca == null && a.isRegister() && !written.contains(a);
                    boolean rb = cb == null && b.isRegister() && !written.contains(b);
                    if ((ra || ca != null) && (rb || cb != null) && (ra || rb) && size <= 8
                        && (!fc || size == 4 || size == 8)) {
                        Site s = new Site(sites++, size, fc, ra ? a : null, ra ? 0 : ca, rb ? b : null, rb ? 0 : cb);
                        at.computeIfAbsent(pc, k -> new ArrayList<>()).add(s);
                        goals.add(3 * s.index + 1);
                        if (!fc) {
                            goals.add(3 * s.index);
                            goals.add(3 * s.index + 2);
                        }
                    }
                }
                Varnode out = op.getOutput();
                if (out == null) continue;
                Long x = op.getNumInputs() > 0 ? constant(op.getInput(0), known) : null;
                known.remove(out);
                if (out.isRegister()) written.add(out);
                if (x == null || out.getSize() > 8) continue;
                if (code == PcodeOp.COPY || code == PcodeOp.INT_ZEXT) known.put(out, x & mask(out.getSize()));
                int n = 64 - 8 * op.getInput(0).getSize();
                if (code == PcodeOp.INT_SEXT) known.put(out, (x << n >> n) & mask(out.getSize()));
            }
        }

        static Long constant(Varnode v, Map<Varnode, Long> known) {
            return v.isConstant() ? (Long) (v.getOffset() & mask(v.getSize())) : known.get(v);
        }

        // Before the step at pc: the edges its sites make, into got.
        void hit(Set<Integer> got, long pc, Regs rd) {
            List<Site> s = at.get(pc);
            if (s == null) return;
            for (Site x : s) {
                long m = mask(x.size);
                long a = (x.a != null ? rd.read(x.a) : x.ca) & m, b = (x.b != null ? rd.read(x.b) : x.cb) & m;
                if (x.flt) {
                    if (x.size == 4 ? Float.intBitsToFloat((int) a) == Float.intBitsToFloat((int) b)
                        : Double.longBitsToDouble(a) == Double.longBitsToDouble(b)) got.add(3 * x.index + 1);
                    continue;
                }
                long d = (a - b) & m;
                if (d == 0) got.add(3 * x.index + 1);
                else if (d == 1) got.add(3 * x.index + 2);
                else if (d == m) got.add(3 * x.index);
            }
        }
    }
}

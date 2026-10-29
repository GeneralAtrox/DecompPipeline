// Callee effects on r8..r14 ("live across calls"). Not a script itself; TcPrepare builds one per function, TcOracle
// applies it at each mocked call, TcVectors writes it to vectors.txt.
//
// Toy Commander's display-list library keeps the TA write pointer in r14: an emitter stores a vertex at r14 and returns
// with r14 advanced (8c075768 `add #0x20,r14`), its callers end a strip at the live r14. A mocked callee used to leave
// every register but r0 and fr0, so C that read r14 once before the calls matched, and the port drew strip ends over
// polygon headers. A mocked call to a callee that leaves rN changed (TcSum's writes, saves and restores excepted,
// transitively) advances rN by what the callee does to it on every return path (delta, below):
//   a constant d   - rN += d (the emitters: +0x20, +0x40);
//   a set of them  - rN += one of them, drawn per vector and call (line A);
//   unknown        - (a loop advancing it, a value loaded from memory) rN += STEP * (1..3), drawn the same way.
// The C runners (tc_rt.c, rd_veh.c) apply the same table (header lines K) and draws, so C must read rN after each such
// call: a stale copy writes elsewhere (W), passes another rN on (CALL) or leaves another at exit (GH).
//
// A context switch (PS2 8c2b99a0: loads r0..r15 from r4) returns with r15 loaded from memory (MEM): delta 0 (KEPT), not
// an untracked r15 (TOP). A large frame (`mov.w 0x..,r1; add r1,r15`) is followed exactly (TcTail.literal): else TC's
// display-list callers (8c052c78, 8c02872c) lost their r14 entry. Entries 7..10 are r4..r7 outputs (TcOut).
//
// The same pass finds the bytes stored through r4..r7 at entry (args; TcSq's header). delta: forward over the p-code
// (TcTail.extent), each integer register valued as (a register at entry, or a constant) + offset or unknown, with the
// 4-byte stack slots at r15 + c (a push and its pop restore). A call clobbers r0..r7 and applies its callee's own delta
// (bottom-up, a cycle unknown); a branch to another function is a tail call. rN at each return against entry: delta.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.*;
import ghidra.program.model.symbol.Reference;
import java.util.*;

final class TcLive {
    static final long STEP = 0x20;  // an unknown delta: STEP * (1 + draw % 3)
    static final int MAX_SET = 4;   // more distinct constants than this: unknown
    static final int MAX_VISITS = 64; // per instruction; then the function's deltas are unknown
    static final long[] NONE = null, UNKNOWN = new long[0], KEPT = {0}, OTHER = new long[0];  // OTHER: by identity

    final Map<Long, long[][]> table = new LinkedHashMap<>(); // callee -> per r8..r14: null kept, {d..} or {} unknown
    long[] draws = new long[16];                            // per call index (line A), set per vector try

    // The live callees of a function: those of `callees` that leave some r8..r14 changed. null when there is none.
    static TcLive of(TcBase s, List<Function> callees) {
        TcLive out = new TcLive();
        Deltas d = new Deltas(s.getCurrentProgram());
        for (Function g : callees) {
            long[][] e = new long[12][];  // all NONE; d's memo stays whole
            boolean any = false;
            for (int i = 0; i < 7 && (s.sig.summary(g).writes & TcFlow.ISTATE) != 0; i++) {
                e[i] = (s.sig.summary(g).writes & (1 << (24 + i))) == 0 || Arrays.equals(d.of(g)[i], KEPT) ? NONE : d.of(g)[i];
                any |= e[i] != NONE;
            }
            any |= TcOut.outs(s, g, e) | TcTflag.outs(s, g, e);  // r4..r7 (TcOut), T (TcTflag: entry 11)
            if (any) out.table.put(g.getEntryPoint().getOffset(), e);
        }
        return out.table.isEmpty() ? null : out;
    }

    // rN after the mocked call number k (0-based) to target; nothing without live callees.
    static void after(TcLive l, TcOracle o, EmulatorHelper emu, long target, int k) {
        if (l != null) l.apply(o, emu, target, k);
    }

    void apply(TcOracle o, EmulatorHelper emu, long target, int k) {
        long[][] e = table.get(target);
        if (e == null) return;
        for (int i = 0; i < e.length; i++) {
            if (e[i] == NONE || i == 11 && TcTflag.apply(o, emu, draws[k % 16])) continue;  // T (TcTflag)
            String r = "r" + (i < 7 ? 8 + i : i - 3);
            long was = o.reg(emu, r), now = (was + amount(e[i], draws[k % 16])) & 0xffffffffL;
            emu.writeRegister(r, o.u(now));
            if (o.sq != null && o.sq.reg == 8 + i) o.sq.called(was, now);  // the blocks it filled (TcSq)
        }
    }

    static long amount(long[] d, long draw) {
        return d.length == 1 ? d[0] : d.length > 1 ? d[(int) (draw % d.length)] : STEP * (1 + draw % 3);
    }

    void draw(long seed, int id, int attempt) {
        Random h = new Random(seed * 0x7fb5d329728ea185L + id * 0x81dadef4bc2dd44dL + attempt * 0xd6e8feb86659fd93L);
        for (int i = 0; i < 16; i++) draws[i] = h.nextInt(1 << 30);
    }

    // vectors.txt header: "K target reg n d..." per live callee register (n 0: unknown). Before the first V.
    String header() {
        StringBuilder b = new StringBuilder();
        for (Map.Entry<Long, long[][]> e : table.entrySet()) {
            for (int i = 0; i < e.getValue().length; i++) {
                long[] d = e.getValue()[i];
                if (d == NONE) continue;
                b.append(String.format("K %08x %d %d", e.getKey(), i < 7 ? 8 + i : i < 11 ? i - 3 : 16, d.length));
                for (long x : d) b.append(String.format(" %08x", x & 0xffffffffL));
                b.append('\n');
            }
        }
        return b.toString();
    }

    // Line A of a vector, after the line before it (TcVectors.writeVector starts each line with its newline).
    String vectorLine(boolean lead) {
        StringBuilder b = new StringBuilder(lead ? "\nA" : "A");
        for (long x : draws) b.append(String.format(" %08x", x));
        return b.toString();
    }

    // meta.json "r_live": {"callee": {"r14": "0x20" | "0x20|0x40" | "?"}, ...}
    String meta() {
        StringBuilder b = new StringBuilder();
        for (Map.Entry<Long, long[][]> e : table.entrySet()) {
            StringBuilder r = new StringBuilder();
            for (int i = 0; i < 7; i++) {
                long[] d = e.getValue()[i];
                if (d == NONE) continue;
                StringBuilder v = new StringBuilder();
                for (long x : d) v.append(v.length() > 0 ? "|" : "").append(x < 0 ? "-0x" + Long.toHexString(-x) : "0x" + Long.toHexString(x));
                r.append(r.length() > 0 ? "," : "").append(String.format("\"r%d\":\"%s\"", 8 + i, d.length == 0 ? "?" : v));
            }
            if (r.length() > 0) b.append(b.length() > 0 ? "," : "").append(String.format("\"%08x\":{%s}", e.getKey(), r));
        }
        return "{" + b + "}";
    }

    // Per function: the change of r8..r14 from entry to every return ({d..}, {} unknown), bottom-up with a memo.
    static final class Deltas {
        static final int TOP = -1, CONST = 16, MEM = 17; // a value's base: register 0..15 at entry, CONST, a 4-byte
                                                         // load from non-stack memory (MEM), or TOP (unknown)
        static final int ARG = 20;                       // ARG + k: from r(4+k) at entry, at an unknown offset
        final Program prog;
        final Map<Address, long[][]> memo = new HashMap<>(), stored = new HashMap<>(), outs = new HashMap<>();  // args, outs
        boolean follow;  // outs (TcOut): a call applies the callee's own r4..r7 outs; off, every other result stays
        final Set<Address> open = new HashSet<>();
        long fpscr; boolean marking = true;  // args: the entry FPSCR (TcSq.path); step marks stores (not on a forked step)

        Deltas(Program prog) {
            this.prog = prog;
        }

        long[][] of(Function f) {
            long[][] m = memo.get(f.getEntryPoint());
            if (m != null) return m;
            long[][] u = new long[7][];
            Arrays.fill(u, UNKNOWN);
            if (!open.add(f.getEntryPoint())) return u; // a cycle
            long[][] out = solve(f);
            open.remove(f.getEntryPoint());
            memo.put(f.getEntryPoint(), out == null ? u : out);
            return memo.get(f.getEntryPoint());
        }

        // Per r4..r7: at f's returns against entry: NONE kept, {d..}, UNKNOWN (from it, offset unknown), OTHER.
        long[][] outs(Function f) {
            of(f);
            return outs.getOrDefault(f.getEntryPoint(), new long[][] {OTHER, OTHER, OTHER, OTHER});
        }
        // Per r4..r7 at entry: the bytes f stores through it, {bits of offsets 0..63, 64..127}, or null (loose).
        long[][] args(Function f) {
            of(f);
            return stored.getOrDefault(f.getEntryPoint(), new long[4][]);
        }

        static int arg(int b) { return b >= 4 && b < 8 ? b - 4 : b >= ARG && b < ARG + 4 ? b - ARG : -1; }  // k of r(4+k)

        // State: base[16], off[16] for r0..r15, stack slots r15entry+c -> {base, off}.
        static final class St {
            int[] base = new int[16];
            long[] off = new long[16];
            Map<Long, long[]> stack = new HashMap<>();
            long[] must = new long[8], may = new long[8];  // per rK: bytes stored on every path, on some path
            int loose;                                      // bit k: rK's stores are not exact

            St copy() {
                St s = new St();
                s.base = base.clone();
                s.off = off.clone();
                s.stack = new HashMap<>(stack);
                s.must = must.clone(); s.may = may.clone(); s.loose = loose;
                return s;
            }

            boolean join(St o) { // this = this meet o; whether this changed
                boolean ch = false;
                for (int i = 0; i < 16; i++) {  // differing: TOP, or ARG + k when either came from rK
                    if (base[i] == o.base[i] && off[i] == o.off[i]) continue;
                    int k = arg(base[i]) >= 0 ? arg(base[i]) : arg(o.base[i]), b = k >= 0 ? ARG + k : TOP;
                    ch |= base[i] != b || off[i] != 0;
                    base[i] = b;
                    off[i] = 0;
                }
                for (int i = 0; i < 8; i++) {
                    ch |= (must[i] & o.must[i]) != must[i] || (may[i] | o.may[i]) != may[i];
                    must[i] &= o.must[i]; may[i] |= o.may[i];
                }
                ch |= (loose | o.loose) != loose; loose |= o.loose;
                for (Iterator<Map.Entry<Long, long[]>> it = stack.entrySet().iterator(); it.hasNext(); ) {
                    Map.Entry<Long, long[]> e = it.next();
                    if (!Arrays.equals(e.getValue(), o.stack.get(e.getKey()))) {
                        it.remove();
                        ch = true;
                    }
                }
                return ch;
            }
        }

        long[][] solve(Function f) {
            AddressSetView body = TcTail.extent(prog, f);
            Listing listing = prog.getListing();
            Map<Address, St> in = new HashMap<>();
            Map<Address, Integer> visits = new HashMap<>();
            St s0 = new St();
            for (int i = 0; i < 16; i++) s0.base[i] = i;
            in.put(f.getEntryPoint(), s0);
            Deque<Address> work = new ArrayDeque<>(List.of(f.getEntryPoint()));
            List<Set<Long>> exits = new ArrayList<>();
            for (int i = 0; i < 7; i++) exits.add(new TreeSet<>());
            boolean[] unknown = new boolean[7], ounk = new boolean[4], oth = new boolean[4];
            List<Set<Long>> ox = List.of(new TreeSet<>(), new TreeSet<>(), new TreeSet<>(), new TreeSet<>());
            St ret = null;  // the args' bytes over every return
            while (!work.isEmpty()) {
                Address a = work.pop();
                if (visits.merge(a, 1, Integer::sum) > MAX_VISITS) return null;
                Instruction ins = listing.getInstructionAt(a);
                if (ins == null) return null;
                St s = in.get(a).copy();
                if (ins.getMnemonicString().replaceFirst("^_", "").equalsIgnoreCase("pref")) s.loose = 15;
                Map<Long, long[]> uniq = new HashMap<>();
                boolean ends = false;
                long[] lit = TcTail.literal(prog, ins);
                if (lit != null) {
                    s.base[(int) lit[0]] = CONST;
                    s.off[(int) lit[0]] = lit[1];
                }
                PcodeOp[] ops = lit != null ? new PcodeOp[0] : ins.getPcode();
                marking = !TcSq.path(this, s, ops);  // an instruction forking on FPSCR.SZ (fmov): its stores on one path
                for (PcodeOp op : ops) {
                    int oc = op.getOpcode();
                    if (oc == PcodeOp.CALL || oc == PcodeOp.CALLIND) {
                        call(s, callee(ins, op));
                    } else if (oc == PcodeOp.RETURN) {
                        ends = true;
                    } else if ((oc == PcodeOp.BRANCH || oc == PcodeOp.BRANCHIND) && tail(ins, body) != null) {
                        call(s, tail(ins, body));
                        ends = true;
                    } else {
                        step(s, uniq, op);
                    }
                }
                if (ends) {
                    if (ret == null) ret = s.copy(); else ret.join(s);
                    for (int i = 0; i < 7; i++) {
                        if (s.base[15] == MEM) exits.get(i).add(0L);  // a stack switch: the caller resumes as it was
                        else if (s.base[8 + i] == 8 + i) exits.get(i).add(s.off[8 + i]);
                        else unknown[i] = true;
                    }
                    TcOut.exit(s, ox, ounk, oth);  // r4..r7 (outs)
                    continue;
                }
                for (Address n : successors(ins, body)) {
                    St old = in.get(n);
                    if (old == null) in.put(n, s.copy());
                    else if (!old.join(s)) continue;
                    work.push(n);
                }
            }
            long[][] out = new long[7][];
            for (int i = 0; i < 7; i++) {
                Set<Long> e = exits.get(i);
                if (unknown[i] || e.size() > MAX_SET) out[i] = UNKNOWN;
                else out[i] = e.stream().mapToLong(Long::longValue).toArray();
            }
            long[][] st = new long[4][];
            for (int k = 0; k < 4 && ret != null; k++) {  // exact: no loose store, the same bytes on every return path
                long[] m = Arrays.copyOfRange(ret.must, 2 * k, 2 * k + 2);
                st[k] = (ret.loose >> k & 1) == 0 && Arrays.equals(m, Arrays.copyOfRange(ret.may, 2 * k, 2 * k + 2)) ? m : null;
            }
            stored.put(f.getEntryPoint(), st);
            outs.put(f.getEntryPoint(), TcOut.classify(ox, ounk, oth));
            return out;
        }

        List<Address> successors(Instruction ins, AddressSetView body) {
            List<Address> out = new ArrayList<>();
            if (!ins.getFlowType().isCall()) {
                for (Address t : ins.getFlows()) if (body.contains(t)) out.add(t);
            }
            Address ft = ins.getFallThrough();
            if (ft != null && body.contains(ft)) out.add(ft);
            return out;
        }

        Function callee(Instruction ins, PcodeOp op) {
            FunctionManager fm = prog.getFunctionManager();
            if (op.getOpcode() == PcodeOp.CALL) return fm.getFunctionAt(op.getInput(0).getAddress());
            Function g = null;
            for (Reference r : ins.getReferencesFrom()) {
                if (!r.getReferenceType().isCall()) continue;
                if (g != null) return null; // more than one target: preserved (ABI)
                g = fm.getFunctionAt(r.getToAddress());
            }
            return g;
        }

        Function tail(Instruction ins, AddressSetView body) {
            for (Address t : ins.getFlows()) {
                Function g = prog.getFunctionManager().getFunctionAt(t);
                if (!body.contains(t) && g != null) return g;
            }
            return null;
        }

        void call(St s, Function g) {
            long[][] st = g != null ? args(g) : new long[4][];
            for (int k = 0; k < 4; k++) {  // the bytes g stores through its r(4+k), a pointer from this function's rT
                int t = arg(s.base[4 + k]);
                if (t < 0 || st[k] != null && st[k][0] == 0 && st[k][1] == 0) continue;
                if (st[k] == null || s.base[4 + k] != 4 + t) s.loose |= 1 << t;
                for (int j = 0; j < 128 && s.base[4 + k] == 4 + t && st[k] != null; j++) {
                    if ((st[k][j >> 6] >> (j & 63) & 1) != 0) mark(s, t, s.off[4 + k] + j);
                }
            }
            St was = follow && g != null ? s.copy() : null;
            for (int i = 0; i < 8; i++) s.base[i] = TOP;
            if (was != null) TcOut.follow(s, outs(g), was);  // the callee's own r4..r7 outs (TcOut)
            if (g == null) return; // an unknown target: r8..r14 kept (the ABI)
            long[][] d = of(g);
            for (int i = 0; i < 7; i++) {
                if (d[i].length == 1) s.off[8 + i] += d[i][0];
                else s.base[8 + i] = TOP;
            }
        }

        int regIndex(Varnode v) {
            if (!v.isRegister()) return -1;
            Register r = prog.getRegister(v.getAddress(), v.getSize());
            if (r == null || !r.getName().matches("r([0-9]|1[0-5])")) return -2; // another register (a sub-register: set())
            return Integer.parseInt(r.getName().substring(1));
        }

        long[] val(St s, Map<Long, long[]> uniq, Varnode v) {
            if (v.isConstant()) return new long[] {CONST, v.getOffset()};
            if (v.isUnique()) return uniq.getOrDefault(v.getOffset(), new long[] {TOP, 0});
            int r = regIndex(v);
            if (r >= 0 && v.getSize() == 4) return new long[] {s.base[r], s.off[r]};
            return new long[] {TOP, 0};
        }

        void set(St s, Map<Long, long[]> uniq, Varnode out, long[] x) {
            if (out == null) return;
            if (out.isUnique()) {
                uniq.put(out.getOffset(), x);
                return;
            }
            int r = regIndex(out);
            if (r < 0) {
                Register reg = out.isRegister() ? prog.getRegister(out.getAddress(), out.getSize()) : null;
                Register p = reg == null ? null : reg.getBaseRegister();
                if (p != null && p.getName().matches("r([0-9]|1[0-5])")) s.base[Integer.parseInt(p.getName().substring(1))] = TOP;
                return;
            }
            boolean whole = out.getSize() == 4 && x[0] != TOP;
            s.base[r] = whole ? (int) x[0] : TOP;
            s.off[r] = whole ? x[1] : 0;
        }

        void step(St s, Map<Long, long[]> uniq, PcodeOp op) {
            int oc = op.getOpcode();
            Varnode out = op.getOutput();
            if (oc == PcodeOp.STORE) {
                long[] a = val(s, uniq, op.getInput(1));
                int k = arg((int) a[0]);
                for (int j = 0; marking && k >= 0 && j < op.getInput(2).getSize(); j++) mark(s, k, a[0] == 4 + k ? a[1] + j : -1);
                if (a[0] == 15 && op.getInput(2).getSize() == 4) s.stack.put(a[1], val(s, uniq, op.getInput(2)));
                else if (a[0] == 15) s.stack.remove(a[1]);
                return;
            }
            long[] x = {TOP, 0};
            if (oc == PcodeOp.COPY) {
                x = val(s, uniq, op.getInput(0));
            } else if (oc == PcodeOp.INT_ADD || oc == PcodeOp.INT_SUB) {
                long[] p = val(s, uniq, op.getInput(0)), q = val(s, uniq, op.getInput(1));
                long sign = oc == PcodeOp.INT_SUB ? -1 : 1;
                if (p[0] != TOP && q[0] == CONST) x = new long[] {p[0], (int) (p[1] + sign * q[1])};
                else if (p[0] == CONST && q[0] != TOP && sign == 1) x = new long[] {q[0], (int) (q[1] + p[1])};
            } else if (oc == PcodeOp.LOAD && out != null && out.getSize() == 4) {
                long[] a = val(s, uniq, op.getInput(1));
                if (a[0] == 15 && s.stack.containsKey(a[1])) x = s.stack.get(a[1]);
                else if (a[0] != 15) x = new long[] {MEM, 0};
            }
            for (int i = 0; x[0] == TOP && oc != PcodeOp.LOAD && i < op.getNumInputs(); i++) {  // a value from rK
                int k = arg((int) val(s, uniq, op.getInput(i))[0]);
                if (k >= 0) x = new long[] {ARG + k, 0};
            }
            set(s, uniq, out, x);
        }

        static void mark(St s, int k, long at) {  // rK + at stored (at -1: an unknown offset, loose)
            if (at < 0 || at >= 128) s.loose |= 1 << k;
            else { s.must[2 * k + (int) (at >> 6)] |= 1L << (at & 63); s.may[2 * k + (int) (at >> 6)] |= 1L << (at & 63); }
        }
    }
}

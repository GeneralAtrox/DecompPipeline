// The FPU mode in the oracle. Ghidra's p-code float ops keep IEEE denormals and always round to nearest; the game
// runs with FPSCR.DN=1 and, in Toy Commander, RM=1 (round toward zero: TcFacts.fpscrEntry 0x00040001).
// DN (always): a denormal source operand of a float arithmetic instruction counts as a signed zero, and a denormal
// result is flushed to a signed zero. Around each such step the oracle zeroes denormal sources (restoring the
// registers that are not results afterwards) and flushes denormal results. The C runner does the same with SSE
// DAZ|FTZ (tc_rt.c). Intermediate values of fipr and ftrv live in p-code temporaries the oracle cannot flush,
// while the C runner's separately rounded operations do: a vector producing a nonzero intermediate below the smallest
// normal is rejected.
// RM (only with TcFacts.fpscrModel, target.json "fpscr_model"): every float result of the step is recomputed from the
// source operands with TcRound, in the precision FPSCR.PR selects, as the patched SLEIGH orders the operations
// (fipr/ftrv element order 0..3, fsrra = 1/sqrt, each rounded on its own). The recomputation is first done
// round-to-nearest as the p-code does it and must equal Ghidra's result (else the vector is rejected: the model and
// the p-code disagree), then with the live FPSCR.RM as the SH-4 does it, whose result replaces Ghidra's. A step
// running with RM or DN other than the entry mode is rejected (the C runner runs every vector in the entry mode:
// tc_rt.c sets MXCSR.RC once).
// fmac (always, with or without the model): the SH-4 rounds fr0*frm + frn once (software manual 6.4, 9.36
// normal_fmac), the p-code rounds the product and then the sum. Its result is recomputed as above, the p-code check
// with two roundings, the result with one (TcRound.fma; tc_rt.c's sh4_fmac). An exact value in
// [2^-126 - 2^-150, 2^-126) rejects the vector (TcRound.nearMinNormal: the manual and SSE flush it differently).
// ftrc (always): Ghidra's SLEIGH ftrc is trunc(), a 64-bit conversion of which FPUL keeps the low 32 bits; the SH-4
// saturates (0x7fffffff above the int range, 0x80000000 below it and for a NaN), as tc_rt.c's sh4_ftrc does. The
// oracle sets FPUL from the source operand after the step (r15: xemu found it in 225 functions).
// Not a script itself; used by TcOracle.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import java.math.BigInteger;
import java.util.*;
import java.util.regex.*;

public class TcFpu {
    // fsca's table (TcOracle.fscaCallbacks; TcPrepare: [0] round to nearest, [1] toward zero) is the one of the
    // rounding the FPU runs in. Under fpscr_model, FPSCR.RM's; an RM other than the entry's rejects the vector
    // (reject[0]), as before() rejects the other modal steps: the C runner runs every fsca in the entry mode (MXCSR.RC
    // from line P). Without the model the oracle's whole FPU rounds to nearest, and so does the C runner (no line P).
    static int fscaTable(boolean model, long rm, long entry, String[] reject) {
        if (!model) return 0;
        if (rm != (entry & 3) && reject[0] == null) reject[0] = "FPSCR mode RM=" + rm + " at fsca (not the entry's)";
        return rm == 1 ? 1 : 0;
    }

    static final Pattern OP_RE = Pattern.compile(
        "^(fadd|fsub|fmul|fdiv|fmac|fsqrt|fcmp/eq|fcmp/gt|fipr|ftrv|fsrra|float|fcnvds|fcnvsd|ftrc)\\s+(\\S+)$");
    static final Set<String> MODEL_ONLY = Set.of("float", "fcnvds", "fcnvsd"); // no denormal source: DN-only ignores them
    static final Set<String> PAIRED = Set.of("fadd", "fsub", "fmul", "fdiv", "fsqrt", "fcmp/eq", "fcmp/gt");

    // One float instruction: the registers it reads (as denormal-flushed operands), those it writes, and its operands.
    static class Op {
        String text, kind;
        String[] a;
        List<String> src = new ArrayList<>(), dst = new ArrayList<>();
        int m, n; // fipr: fvm, fvn; ftrv: fvn; fmac: frm
    }

    // What before() hands after(): the zeroed sources {reg -> original bits}, and for the RM model the operands.
    static class State {
        Map<String, Long> zeroed = new LinkedHashMap<>();
        Map<String, Long> snap; // model: every operand register's bits after zeroing
        Map<String, Long> ftrc = new LinkedHashMap<>(); // ftrc: its source register's bits
        List<Op> ops;
        int rm;
        boolean pr;
    }

    final TcLoads steps;
    final TcFacts facts;
    final Map<Long, List<Op>> ops = new HashMap<>();

    TcFpu(TcLoads steps, TcFacts facts) {
        this.steps = steps;
        this.facts = facts;
    }

    static int num(String reg) {
        return Integer.parseInt(reg.replaceAll("\\D", ""));
    }

    static String reg(String operand) {
        return operand.equals("fpul") ? "FPUL" : operand;
    }

    List<Op> at(Address pca) {
        List<Op> out = ops.get(pca.getOffset());
        if (out != null) return out;
        out = new ArrayList<>();
        for (Instruction in : steps.stepInstructions(pca)) {
            Matcher mt = OP_RE.matcher(TcLoads.text(in));
            if (!mt.matches() || (MODEL_ONLY.contains(mt.group(1)) && !facts.fpscrModel)) continue;
            Op op = new Op();
            op.text = mt.group(1) + " " + mt.group(2);
            op.kind = mt.group(1);
            String[] a = mt.group(2).split(",");
            for (int i = 0; i < a.length; i++) a[i] = reg(a[i]);
            op.a = a;
            switch (op.kind) {
                case "fipr":
                    op.m = num(a[0]);
                    op.n = num(a[1]);
                    for (int i = 0; i < 4; i++) {
                        op.src.add("fr" + (op.m + i));
                        op.src.add("fr" + (op.n + i));
                    }
                    op.dst.add("fr" + (op.n + 3));
                    break;
                case "ftrv":
                    op.n = num(a[1]);
                    for (int i = 0; i < 16; i++) op.src.add("xf" + i);
                    for (int i = 0; i < 4; i++) {
                        op.src.add("fr" + (op.n + i));
                        op.dst.add("fr" + (op.n + i));
                    }
                    break;
                case "fmac":
                    op.m = num(a[1]);
                    op.src.addAll(Arrays.asList(a));
                    op.dst.add(a[2]);
                    break;
                case "fcmp/eq":
                case "fcmp/gt":
                    op.src.addAll(Arrays.asList(a));
                    break;
                case "ftrc": // ftrc frm,FPUL: before() reads frm (drm under PR=1), after() sets FPUL; nothing to flush
                    break;
                case "float": // float FPUL,frn: an integer source
                    op.dst.add(a[1]);
                    break;
                case "fcnvds": // fcnvds drm,FPUL
                case "fcnvsd": // fcnvsd FPUL,drn
                    op.src.add(a[0]);
                    op.dst.add(a[1]);
                    break;
                default: // fadd fsub fmul fdiv: frm,frn -> frn; fsqrt fsrra: frn -> frn
                    op.src.addAll(Arrays.asList(a));
                    op.dst.add(a[a.length - 1]);
            }
            out.add(op);
        }
        ops.put(pca.getOffset(), out);
        return out;
    }

    // ---------- register access (fr/xf/FPUL 32 bits, dr 64 bits) ----------
    static boolean wide(String r) {
        return r.startsWith("dr");
    }

    static String dr(String r) {
        return r.startsWith("fr") ? "dr" + (num(r) & ~1) : r;
    }

    // Under FPSCR.PR=1 the instructions that have a double form name dr registers (Ghidra prints them as fr).
    static List<String> eff(List<String> regs, Op op, boolean pr) {
        if (!pr) return regs;
        if (!PAIRED.contains(op.kind) && !op.kind.equals("float")) {
            return op.kind.startsWith("fcnv") ? regs : List.of(); // fmac fipr ftrv fsrra do nothing when PR=1
        }
        List<String> out = new ArrayList<>();
        for (String r : regs) {
            String d = op.kind.equals("float") && num(r) % 2 == 1 ? r : dr(r); // float to an odd FRn stays single
            if (!out.contains(d)) out.add(d);
        }
        return out;
    }

    static long bits(EmulatorHelper emu, String r) {
        long v = emu.readRegister(r).longValue();
        return wide(r) ? v : v & 0xffffffffL;
    }

    static void set(EmulatorHelper emu, String r, long bits) {
        emu.writeRegister(r, wide(r) ? new BigInteger(Long.toUnsignedString(bits)) : BigInteger.valueOf(bits & 0xffffffffL));
    }

    static boolean denormal(String r, long bits) {
        if (wide(r)) return (bits & 0x7ff0000000000000L) == 0 && (bits & 0x000fffffffffffffL) != 0;
        return (bits & 0x7f800000L) == 0 && (bits & 0x007fffffL) != 0;
    }

    static long signOf(String r, long bits) {
        return bits & (wide(r) ? 0x8000000000000000L : 0x80000000L);
    }

    static boolean tiny(float x) {
        return x != 0 && Math.abs(x) < Float.MIN_NORMAL;
    }

    static float f(EmulatorHelper emu, String reg) {
        return Float.intBitsToFloat((int) TcLoads.reg(emu, reg));
    }

    // Before the step at pca: zero the denormal sources, snapshot the operands for the RM model, or reject the vector
    // (a reason in reject[0]) when an intermediate would be denormal or the FPU mode left the entry mode.
    State before(EmulatorHelper emu, Address pca, String[] reject) {
        List<Op> list = at(pca);
        if (list.isEmpty()) return null;
        State st = new State();
        st.ops = list;
        if (facts.fpscrModel) {
            st.rm = (int) emu.readRegister("FPSCR_RM").longValue();
            st.pr = emu.readRegister("FPSCR_PR").longValue() != 0;
            long dn = emu.readRegister("FPSCR_DN").longValue();
            long want = facts.fpscrEntry;
            boolean modal = list.stream().anyMatch(o -> !o.kind.equals("ftrc")); // ftrc ignores RM and DN
            if (modal && (st.rm != (want & 3) || dn != ((want >> 18) & 1) || st.rm > 1)) { // RM 2, 3: reserved
                reject[0] = "FPSCR mode RM=" + st.rm + " DN=" + dn + " at " + list.get(0).text
                    + " differs from the entry mode " + TcBase.hex(want) + " (the C runs every vector in the entry mode)";
                return st;
            }
            st.snap = new HashMap<>();
        }
        if (st.snap == null && list.stream().anyMatch(o -> o.kind.equals("fmac"))) st.snap = new HashMap<>(); // fmac
        for (Op op : list) {
            for (String r : eff(op.src, op, st.pr)) {
                long b = bits(emu, r);
                if (!denormal(r, b) || st.zeroed.containsKey(r)) continue;
                st.zeroed.put(r, b);
                set(emu, r, signOf(r, b));
            }
            if (op.kind.equals("ftrc")) {
                String r = emu.readRegister("FPSCR_PR").longValue() != 0 ? dr(op.a[0]) : op.a[0];
                st.ftrc.put(r, bits(emu, r));
            }
            String why = st.pr ? null : intermediate(emu, op, st.rm);
            if (why != null) reject[0] = "denormal intermediate value (" + op.text + ": " + why + ")";
        }
        if (st.snap != null) {
            for (Op op : list) {
                for (String r : eff(op.src, op, st.pr)) st.snap.put(r, bits(emu, r));
                if (op.kind.equals("fmac")) st.snap.put("fr0", bits(emu, "fr0"));
                if (op.kind.equals("float")) st.snap.put("FPUL", bits(emu, "FPUL"));
            }
        }
        return st;
    }

    // A nonzero intermediate below the smallest normal, in the order the patched SLEIGH and tc_rt.c compute them (fmac
    // has none: its exact product is never rounded).
    String intermediate(EmulatorHelper emu, Op op, int rm) {
        if (op.kind.equals("fipr")) {
            float t = 0;
            for (int i = 0; i < 4; i++) {
                float p = TcRound.mul(f(emu, "fr" + (op.m + i)), f(emu, "fr" + (op.n + i)), rm);
                if (tiny(p)) return "product " + i;
                t = i == 0 ? p : TcRound.add(t, p, rm);
                if (i > 0 && i < 3 && tiny(t)) return "partial sum " + i;
            }
            return null;
        }
        if (op.kind.equals("ftrv")) {
            for (int row = 0; row < 4; row++) {
                float t = 0;
                for (int k = 0; k < 4; k++) {
                    float p = TcRound.mul(f(emu, "xf" + (row + 4 * k)), f(emu, "fr" + (op.n + k)), rm);
                    if (tiny(p)) return "row " + row + " product " + k;
                    t = k == 0 ? p : TcRound.add(t, p, rm);
                    if (k > 0 && k < 3 && tiny(t)) return "row " + row + " partial sum " + k;
                }
            }
        }
        return null;
    }

    // After the step: restore zeroed sources that are not results (if the step left them zero), apply the RM model,
    // flush denormal results. Returns a reason to reject the vector, or null.
    String after(EmulatorHelper emu, Address pca, State st) {
        if (st == null) return null;
        Set<String> dst = new LinkedHashSet<>();
        for (Op op : st.ops) dst.addAll(eff(op.dst, op, st.pr));
        for (Map.Entry<String, Long> e : st.zeroed.entrySet()) {
            if (dst.contains(e.getKey())) continue;
            long now = bits(emu, e.getKey());
            if (now == signOf(e.getKey(), e.getValue())) set(emu, e.getKey(), e.getValue());
        }
        if (st.snap != null) {
            Map<String, Long> rn = new HashMap<>(st.snap), rz = new HashMap<>(st.snap);
            Set<String> mod = new LinkedHashSet<>(); // the results recomputed: all, or without the model fmac's
            for (Op op : st.ops) {
                boolean fmac = op.kind.equals("fmac") && !st.pr;
                if (!facts.fpscrModel && !fmac) continue;
                if (fmac && TcRound.nearMinNormal(TcRound.f(rz, "fr0"), TcRound.f(rz, op.a[1]),
                                                  TcRound.f(rz, op.a[2]))) {
                    return "fmac result just below the smallest normal (" + op.text + ": TcRound.nearMinNormal)";
                }
                TcRound.apply(op, rn, 0, st.pr, false);
                TcRound.apply(op, rz, st.rm, st.pr, true);
                mod.addAll(eff(op.dst, op, st.pr));
            }
            for (String r : mod) {
                long got = bits(emu, r);
                Long want = rn.get(r), mine = rz.get(r);
                if (want == null || mine == null) continue;
                if (TcRound.isNaN(r, got) || TcRound.isNaN(r, want)) {
                    // NaN payloads are not modelled. When both say NaN the model agrees; a non-NaN RM result
                    // (round toward zero saturates an overflow at the largest finite value, so inf - inf
                    // never happens) replaces it.
                    if (TcRound.isNaN(r, got) && TcRound.isNaN(r, want) && !TcRound.isNaN(r, mine)) set(emu, r, mine);
                    continue;
                }
                if (want != got) {
                    return "the FPU model disagrees with the p-code at " + st.ops.get(0).text + " (" + r + " "
                        + Long.toHexString(want) + " vs " + Long.toHexString(got) + ")";
                }
                if (mine != got) set(emu, r, mine);
            }
        }
        for (String r : dst) {
            long b = bits(emu, r);
            if (denormal(r, b)) set(emu, r, signOf(r, b));
        }
        for (Map.Entry<String, Long> e : st.ftrc.entrySet()) set(emu, "FPUL", ftrc(e.getKey(), e.getValue()));
        return null;
    }

    // The SH-4's ftrc of a source register's bits: truncation toward zero, saturating; a NaN gives 0x80000000.
    static long ftrc(String r, long bits) {
        double d = wide(r) ? Double.longBitsToDouble(bits) : Float.intBitsToFloat((int) bits);
        return Double.isNaN(d) ? 0x80000000L : (long) (int) d & 0xffffffffL; // Java's (int) truncates and saturates
    }
}

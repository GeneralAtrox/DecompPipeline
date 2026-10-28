// FPSCR as state, for a function whose body reads or writes it (sts / sts.l FPSCR, lds / lds.l ...,FPSCR: Spawn
// 8c010080 clears RM before the main loop, Toy Commander 8c0611d8 sets or clears the V, Z and O enables, Spawn's
// overlays set or clear PR around their calls). Not a script itself; TcPrepare sets `on` for every prepare (null: the
// body has neither, and nothing changes). With it:
// - the oracle sets every FPSCR field (the SLEIGH's sts rebuilds FPSCR from them, lds splits it into them) from the
//   vector's entry FPSCR: the project's (TcFacts.fpscrEntry; PR, SZ and FR 0 without fpscr_model, as before) with
//   FLAG, CAUSE and RM drawn per vector (entry): RM the run mode's or the other of 0 and 1, so a C that writes a
//   constant, or leaves RM as it was, where the original changes some bits of what it read fails. vectors.txt line
//   "N fpscr" after V gives it to the runner (line);
// - expected.txt "FS fpscr" is FPSCR at exit and "FC v..." FPSCR at each mocked call (exit), PR, SZ and FR included
//   (the p-code follows them after an lds, as after fpchg, fschg and frchg): the C writes FPSCR with sh4_lds_fpscr(v)
//   and reads it with sh4_sts_fpscr() where the original does (tc_rt.h);
// - FLAG and CAUSE are carried, never set by a float operation: the p-code sets neither, nor do the C runtimes, so
//   both sides read back the same bits (on the SH-4 a float operation sets CAUSE and FLAG: software manual 6.3);
// - a vector is rejected (before) where a float operation runs while RM, DN or ENABLE differ from the run mode, the
//   one the p-code and the C runner round, flush and trap (never) in whatever FPSCR says (TcFpu, tc_rt.c's MXCSR):
//   the project's, with RM 0 (round to nearest) without fpscr_model.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.math.BigInteger;
import java.util.*;
import java.util.regex.Pattern;

final class TcFpscr {
    static TcFpscr on;  // the function being prepared reads or writes FPSCR, or null
    static final long FLAGS = 0x0003f07cL;  // CAUSE (bits 12..17) and FLAG (2..6)
    static final long MODES = 0x00380000L;  // FR, SZ, PR (21, 20, 19): TcOracle.modeWord
    static final long ROUND = 0x00040f83L;  // DN, ENABLE, RM
    static final String[] FIELD = {"FPSCR_RM", "FPSCR_FLAG", "FPSCR_ENABLE", "FPSCR_CAUSE", "FPSCR_DN", "FPSCR_PR",
        "FPSCR_SZ", "FPSCR_FR"};
    static final int[] SHIFT = {0, 2, 7, 12, 18, 19, 20, 21}, WIDTH = {2, 5, 5, 6, 1, 1, 1, 1};
    // the float operations that set CAUSE, round (RM), flush (DN) or may trap (ENABLE) on the SH-4
    static final Pattern ARITH = Pattern.compile(
        "(fadd|fsub|fmul|fdiv|fmac|fsqrt|fcmp/eq|fcmp/gt|fipr|ftrv|fsrra|fsca|float|fcnvds|fcnvsd|ftrc)\\s.*");
    static final int OP_ARITH = 1, OP_STS = 2, OP_LDS = 4;

    final boolean model;
    final Map<Long, Integer> kinds = new HashMap<>();
    Run cur;   // the run in progress (runOracle: start)
    long run;  // the run mode: RM, DN and ENABLE the oracle's FPU and the C runner run in (entry)

    // A run's FPSCR: at entry, at each mocked call, at exit.
    static final class Run {
        long entry, exit = -1;
        final List<Long> calls = new ArrayList<>();
    }

    TcFpscr(boolean model) {
        this.model = model;
    }

    // `on` for a body with an lds or sts of FPSCR, else null (a float operation alone is no access).
    static TcFpscr of(Listing listing, AddressSetView body, boolean model) {
        for (Instruction in : listing.getInstructions(body, true)) {
            if (access(TcLoads.text(in))) return new TcFpscr(model);
        }
        return null;
    }

    // an instruction's text reads or writes FPSCR (sts / lds, their .l forms)
    static boolean access(String t) {
        return (kind(t) & (OP_STS | OP_LDS)) != 0;
    }

    static int kind(Instruction in) {
        return kind(TcLoads.text(in));
    }

    // an instruction's text (TcLoads.text: lower case, no delay-slot "_"): OP_ARITH, OP_STS, OP_LDS or 0
    static int kind(String t) {
        if (ARITH.matcher(t).matches()) return OP_ARITH;
        if (!t.contains("fpscr")) return 0;
        return t.startsWith("sts") ? OP_STS : t.startsWith("lds") ? OP_LDS : 0;
    }

    int kinds(TcLoads steps, Address pca) {
        Integer k = kinds.get(pca.getOffset());
        if (k == null) {
            k = 0;
            for (Instruction in : steps.stepInstructions(pca)) k |= kind(in);
            kinds.put(pca.getOffset(), k);
        }
        return k;
    }

    // TcOracle.setEntry: the vector's entry FPSCR (the project's when FPSCR is not state, unchanged).
    static long entry(long project, TcInputs.Vec v) {
        if (on == null) return project;
        long h = 0x9e3779b97f4a7c15L;
        for (long r : v.r) h = (h ^ r) * 0xbf58476d1ce4e5b9L;
        for (int f : v.fr) h = (h ^ f) * 0x94d049bb133111ebL;
        h ^= h >>> 29;
        on.run = (on.model ? project : project & ~3L) & ROUND;
        long rm = (h >>> 40 & 1) == 0 ? on.run & 3 : (on.run & 3) ^ 1;
        long fp = ((on.model ? project : project & ~MODES) & ~(FLAGS | 3)) | (h & FLAGS) | rm;
        if (on.cur != null) on.cur.entry = fp;
        return fp;
    }

    // vectors.txt line N (TcVectors.writeVector: the entry of the vector's final run), or "".
    static String line(Run r) {
        return r == null ? "" : "\nN " + TcBase.hex(r.entry);
    }

    // TcOracle.runOracle: a new run's record, or null.
    static Run start() {
        return on == null ? null : (on.cur = new Run());
    }

    static long get(EmulatorHelper emu) {
        long fp = 0;
        for (int i = 0; i < FIELD.length; i++) {
            fp |= (emu.readRegister(FIELD[i]).longValue() & ((1L << WIDTH[i]) - 1)) << SHIFT[i];
        }
        return fp;
    }

    static void set(EmulatorHelper emu, long fp) {
        for (int i = 0; i < FIELD.length; i++) {
            emu.writeRegister(FIELD[i], BigInteger.valueOf((fp >> SHIFT[i]) & ((1L << WIDTH[i]) - 1)));
        }
    }

    // TcOracle: at a mocked call (the record of its FPSCR).
    static void call(EmulatorHelper emu) {
        if (on != null) on.cur.calls.add(get(emu));
    }

    // TcExit.collect: FPSCR at exit.
    static void exit(EmulatorHelper emu) {
        if (on != null) on.cur.exit = get(emu);
    }

    // Before the step at pca: reject[0] gets the reason a vector is not judged here, if any.
    static void before(EmulatorHelper emu, TcLoads steps, Address pca, String[] reject) {
        if (on == null || reject[0] != null || (on.kinds(steps, pca) & OP_ARITH) == 0) return;
        long fp = get(emu);
        if ((fp & ROUND) != on.run) {
            reject[0] = "float operation at " + TcBase.hex(pca.getOffset()) + " with FPSCR " + TcBase.hex(fp)
                + " (RM, DN or ENABLE not the run mode's " + TcBase.hex(on.run) + ": the oracle and the C round as it)";
        }
    }

    // expected.txt lines FS and FC (TcVectors.writeVector), or "".
    static String exit(Run r) {
        if (r == null) return "";
        StringBuilder s = new StringBuilder("FS ").append(TcBase.hex(r.exit)).append('\n');
        if (!r.calls.isEmpty()) {
            s.append("FC");
            for (long c : r.calls) s.append(' ').append(TcBase.hex(c));
            s.append('\n');
        }
        return s.toString();
    }

    // FPSCR.SZ from the callers' code (core/sz_entry.py; TcPrepare script arg 13, "sz:MODES[:CALLEE.XY_...]"): the
    // oracle runs a function in the mode the game calls it in, and a mocked call returns with SZ as the callee would.
    // - MODES: the SZ each of its callers runs it with ("1", "01"; "0" with callees only). A vector draws one of them
    //   (pick: a hash of its id, so its repairs and replays keep it); with SZ=1 among them the FPU is state (TcSig
    //   fpuProto: line Q in, QH and the mode word out) and vectors.txt line z gives the entry mode word, which the C
    //   runners and xemu take before the call (runner.c: sh4_fschg after the vector's reset).
    // - CALLEE.XY: a callee that does not return with SZ as it found it, X its SZ at return entered with 0, Y with 1
    //   (0, 1; u unknown, n never returns): after its mocked call the oracle sets SZ to it, as the translator does
    //   (`if (sh4_sz() != X) sh4_fschg();` after the call) and xemu do. An unknown one keeps SZ, as before
    //   (core/sz_entry.py reports those call sites).
    // Without the arg (every other function) nothing changes: no line z, FPSCR.SZ 0 at entry, a call keeps it.
    static class Sz {
        static Sz on;  // the function being prepared has the fact, or null

        final boolean zero, one;
        final Map<Long, String> exits = new HashMap<>();
        int cur;  // the drawn vector's entry SZ

        Sz(boolean zero, boolean one) {
            this.zero = zero;
            this.one = one;
        }

        static Sz parse(String a) {
            if (a == null || !a.startsWith("sz:")) return null;
            String[] p = a.substring(3).split(":");
            Sz s = new Sz(p[0].contains("0"), p[0].contains("1"));
            if (!s.zero && !s.one) throw new IllegalArgumentException("TcFpscr.Sz: no entry mode in " + a);
            if (p.length > 1) for (String c : p[1].split("_")) {
                String[] kv = c.split("\\.");
                if (kv.length != 2 || kv[1].length() != 2) throw new IllegalArgumentException("TcFpscr.Sz: bad callee " + c);
                s.exits.put(Long.parseLong(kv[0], 16), kv[1]);
            }
            return s;
        }

        // TcSig: SZ=1 at entry makes the whole FPU state (fpuProto), as a mode switch in the body does.
        static boolean wide() {
            return on != null && on.one;
        }

        // TcVectors.attempt: vector id's entry SZ.
        static void draw(int id) {
            if (on != null) on.cur = !on.zero ? 1 : !on.one ? 0 : (int) ((id * 0x9e3779b97f4a7c15L) >>> 63);
        }

        // TcOracle.setEntry, after FPSCR: the drawn SZ, in the field register the SLEIGH reads and in FPSCR.
        static void enter(EmulatorHelper emu) {
            if (on == null) return;
            long fp = emu.readRegister("FPSCR").longValue() & ~(1L << 20) | (long) on.cur << 20;
            emu.writeRegister("FPSCR", BigInteger.valueOf(fp & 0xffffffffL));
            emu.writeRegister("FPSCR_SZ", BigInteger.valueOf(on.cur));
        }

        // TcOracle, at a mocked call to pc (after its record): the callee's SZ at return.
        static void call(EmulatorHelper emu, long pc) {
            String x = on == null ? null : on.exits.get(pc);
            if (x == null) return;
            char c = x.charAt((int) (emu.readRegister("FPSCR_SZ").longValue() & 1));
            if (c != '0' && c != '1') return;
            long fp = emu.readRegister("FPSCR").longValue() & ~(1L << 20) | (long) (c - '0') << 20;
            emu.writeRegister("FPSCR", BigInteger.valueOf(fp & 0xffffffffL));
            emu.writeRegister("FPSCR_SZ", BigInteger.valueOf(c - '0'));
        }

        // TcVectors.writeVector: line z (every capital letter is taken), the entry mode word (FPSCR bit 20), for a
        // function some caller runs with SZ=1.
        static String line() {
            return wide() ? "\nz "+ TcBase.hex((long) on.cur << 20) : "";
        }
    }
}

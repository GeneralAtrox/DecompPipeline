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
}

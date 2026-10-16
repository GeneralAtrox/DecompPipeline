// Target facts for the oracle scripts: the calling convention and the memory map, from target.json ("abi",
// "memory"), written by core/ghidra.py as one line per fact ("key value..."), and the project's code ranges
// (state/code_ranges.txt, the same file the C runner marks instruction bytes from: rule R8). Not a script itself.
import java.nio.file.*;
import java.util.*;

public class TcFacts {
    // TcFlow register masks
    int intArgs, floatArgs, extraInputs, calleeSaved;
    String retInt = "r0", retFloat = "fr0", retInt64Hi = "r1";
    long ramLo, ramHi, entrySp, stackLo, sentinel, scrLo, scrHi;
    List<long[]> mmio = new ArrayList<>();
    BitSet code = new BitSet(); // bit (addr - ramLo): an instruction byte
    boolean haveCode;
    // FPSCR at entry (target.json / project.json "fpscr_entry") and whether the oracle models it exactly
    // ("fpscr_model": every FPSCR field set at entry, RM rounding in TcFpu/TcRound, the C runner's MXCSR.RC).
    long fpscrEntry = 0x00040001L;
    boolean fpscrModel;
    boolean paramSummaries; // "param_summaries": callee summaries decide compared callee registers (TcSum, TcSig)
    boolean coverageInputs; // "coverage_inputs": constant-guided inputs (TcGuide) in about a third of the vectors
    long oracleDeadlineS;   // seconds after the JVM started by which vector generation must end (0: none; TcVectors)
    int minValid;           // valid vectors per set the checker needs; fewer starts TcVectors' rescue pass (0: never)
    long oracleRescueS;     // seconds an out-of-time first pass gets for its overtime rescue (TcCount; 0: none)
    int reachRuns;          // "reach_calls": the reach pass's run budget per function (TcReach; 0: off)
    long reachS;            // and its wall-clock limit in seconds
    // "fpu_state": a function that switches FPU modes (fschg, frchg, fpchg) is tested with every float register,
    // XMTRX and the FPSCR mode bits as state (TcSig.fpuProto, TcVectors line Q, TcOracle), and 64-bit fmov pairs move
    // as the hardware does (TcPair).
    boolean fpuState;

    // The values the scripts were written with (TcBase constants); a facts file must agree with them.
    static TcFacts defaults() {
        TcFacts f = new TcFacts();
        f.intArgs = mask("r4 r5 r6 r7");
        f.floatArgs = mask("fr4 fr5 fr6 fr7 fr8 fr9 fr10 fr11");
        f.extraInputs = 0;
        f.calleeSaved = mask("r8 r9 r10 r11 r12 r13 r14 fr12 fr13 fr14 fr15");
        f.ramLo = TcBase.RAM_LO;
        f.ramHi = TcBase.RAM_HI;
        f.entrySp = TcBase.SP;
        f.stackLo = TcBase.STACK_LO;
        f.sentinel = TcBase.SENTINEL;
        f.scrLo = TcBase.SCR;
        f.scrHi = TcBase.SCR + TcBase.SCR_SIZE;
        return f;
    }

    static int mask(String regs) {
        int m = 0;
        for (String r : regs.trim().split("\\s+")) if (!r.isEmpty()) m |= TcFlow.maskOf(r);
        return m;
    }

    static long num(String s) {
        return Long.parseLong(s.replaceFirst("^0x", ""), 16);
    }

    static TcFacts load(String file) throws Exception {
        TcFacts f = defaults();
        if (file == null || file.equals("-")) return f;
        for (String line : Files.readAllLines(Paths.get(file))) {
            String[] t = line.trim().split("\\s+", 2);
            if (t[0].isEmpty() || t[0].startsWith("#")) continue;
            String v = t.length > 1 ? t[1].trim() : "";
            String[] w = v.split("\\s+");
            switch (t[0]) {
                case "int_args": f.intArgs = mask(v); break;
                case "float_args": f.floatArgs = mask(v); break;
                case "extra_inputs": f.extraInputs = mask(v); break;
                case "callee_saved": f.calleeSaved = mask(v); break;
                case "return_int": f.retInt = v; break;
                case "return_float": f.retFloat = v; break;
                case "return_int64_hi": f.retInt64Hi = v; break;
                case "ram": f.ramLo = num(w[0]); f.ramHi = num(w[1]); break;
                case "entry_sp": f.entrySp = num(w[0]); break;
                case "stack_lo": f.stackLo = num(w[0]); break;
                case "return_sentinel": f.sentinel = num(w[0]); break;
                case "scratch": f.scrLo = num(w[0]); f.scrHi = num(w[1]); break;
                case "mmio": f.mmio.add(new long[] {num(w[0]), num(w[1])}); break;
                case "code_ranges": f.loadCode(v); break;
                case "fpscr_entry": f.fpscrEntry = num(w[0]); break;
                case "fpscr_model": f.fpscrModel = v.equals("1") || v.equals("true"); break;
                case "param_summaries": f.paramSummaries = v.equals("1") || v.equals("true"); break;
                case "coverage_inputs": f.coverageInputs = v.equals("1") || v.equals("true"); break;
                case "oracle_deadline_s": f.oracleDeadlineS = Long.parseLong(v); break;
                case "min_valid": f.minValid = Integer.parseInt(v); break;
                case "oracle_rescue_s": f.oracleRescueS = Long.parseLong(v); break;
                case "reach_calls": f.reachRuns = Integer.parseInt(w[0]); f.reachS = Long.parseLong(w[1]); break;
                case "fpu_state": f.fpuState = v.equals("1") || v.equals("true"); break;
                default: throw new IllegalArgumentException("unknown target fact: " + line);
            }
        }
        f.verify();
        return f;
    }

    // The scripts (and every project's TcInputs) use TcBase's constants; the target data is the source of truth.
    void verify() {
        long[][] pairs = {{ramLo, TcBase.RAM_LO}, {ramHi, TcBase.RAM_HI}, {entrySp, TcBase.SP},
            {stackLo, TcBase.STACK_LO}, {sentinel, TcBase.SENTINEL}, {scrLo, TcBase.SCR}, {scrHi, TcBase.SCR + TcBase.SCR_SIZE}};
        for (long[] p : pairs) {
            if (p[0] != p[1]) {
                throw new IllegalStateException("target.json memory map disagrees with TcBase constants: "
                    + Long.toHexString(p[0]) + " vs " + Long.toHexString(p[1]));
            }
        }
        if (!retInt.equals("r0") || !retFloat.equals("fr0")) throw new IllegalStateException("unsupported return registers");
    }

    void loadCode(String file) throws Exception {
        Path p = Paths.get(file);
        if (!Files.exists(p)) return;
        for (String line : Files.readAllLines(p)) {
            String[] t = line.trim().split("\\s+");
            if (t.length < 2) continue;
            long lo = Math.max(num(t[0]), ramLo), hi = Math.min(num(t[1]), ramHi);
            if (hi > lo) code.set((int) (lo - ramLo), (int) (hi - ramLo));
        }
        haveCode = true;
    }

    // Is the (folded) address an instruction byte? The C runner checks a read's first byte the same way (R8).
    boolean isCode(long a) {
        return haveCode && a >= ramLo && a < ramHi && code.get((int) (a - ramLo));
    }

    boolean isMmio(long a) {
        for (long[] r : mmio) if (a >= r[0] && a < r[1]) return true;
        return false;
    }

    int args() {
        return intArgs | floatArgs | extraInputs;
    }
}

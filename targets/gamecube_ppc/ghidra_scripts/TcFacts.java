// Target facts for the PowerPC oracle scripts: the calling convention and the memory map from target.json ("abi",
// "memory"), the project's fixed registers (project.json "fixed_regs": r2/r13 SDA bases, GQRs) and the globals the
// vectors may randomise (project.json "global_ranges": the writable sections), written by core/ghidra.py as one line
// per fact ("key value..."), plus the project's code ranges (state/code_ranges.txt: rule R8). Not a script itself.
import java.nio.file.*;
import java.util.*;

public class TcFacts {
    long ramLo, ramHi, entrySp, stackLo, sentinel, scrLo, scrHi;
    String retInt = "r3", retFloat = "f1", retInt64Hi = "r3";
    List<long[]> mmio = new ArrayList<>();
    List<long[]> globalRanges = new ArrayList<>();
    Map<String, Long> fixed = new LinkedHashMap<>(); // register name -> value at entry (r2, r13, GQR0..7)
    BitSet code = new BitSet(); // bit (addr - ramLo): an instruction byte
    boolean haveCode;
    long oracleDeadlineS, oracleRescueS;
    int minValid;
    boolean targetedVectors; // "targeted_vectors": boundary-value vectors for untaken branch directions (TcBound)
    boolean branchCoverage; // "branch_coverage": record branch directions (TcDirs; report only)
    // The stricter judge (TcStrict; target.json "mock_junk", "mock_writes", "call_snapshots", "exit_frame"): bits
    // 1, 2, 4 and 8 of strict (0: all off), and the callees known to write through a pointer argument (project.json
    // "mock_write_facts", lines "mock_write callee field size": TcStrict.facts).
    int strict;
    List<String[]> mockWrites = new ArrayList<>();

    static TcFacts defaults() {
        TcFacts f = new TcFacts();
        f.ramLo = TcBase.RAM_LO;
        f.ramHi = TcBase.RAM_HI;
        f.entrySp = TcBase.SP;
        f.stackLo = TcBase.STACK_LO;
        f.sentinel = TcBase.SENTINEL;
        f.scrLo = TcBase.SCR;
        f.scrHi = TcBase.SCR + TcBase.SCR_SIZE;
        return f;
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
                case "int_args": case "float_args": case "extra_inputs": case "callee_saved":
                    break; // the PowerPC EABI is built into TcBase/TcFlow; core/ghidra.py writes them for every target
                case "return_int": f.retInt = v; break;
                case "return_float": f.retFloat = v; break;
                case "return_int64_hi": f.retInt64Hi = v; break;
                case "ram": f.ramLo = num(w[0]); f.ramHi = num(w[1]); break;
                case "entry_sp": f.entrySp = num(w[0]); break;
                case "stack_lo": f.stackLo = num(w[0]); break;
                case "return_sentinel": f.sentinel = num(w[0]); break;
                case "scratch": f.scrLo = num(w[0]); f.scrHi = num(w[1]); break;
                case "mmio": f.mmio.add(new long[] {num(w[0]), num(w[1])}); break;
                case "global_range": f.globalRanges.add(new long[] {num(w[0]), num(w[1])}); break;
                case "fixed_reg": f.fixed.put(w[0].toLowerCase(), num(w[1])); break;
                case "code_ranges": f.loadCode(v); break;
                case "oracle_deadline_s": f.oracleDeadlineS = Long.parseLong(v); break;
                case "oracle_rescue_s": f.oracleRescueS = Long.parseLong(v); break;
                case "targeted_vectors": f.targetedVectors = v.equals("1") || v.equals("true"); break;
                case "branch_coverage": f.branchCoverage = v.equals("1") || v.equals("true"); break;
                case "min_valid": f.minValid = Integer.parseInt(v); break;
                case "mock_junk": f.strict |= v.equals("1") || v.equals("true") ? 1 : 0; break;
                case "mock_writes": f.strict |= v.equals("1") || v.equals("true") ? 2 : 0; break;
                case "call_snapshots": f.strict |= v.equals("1") || v.equals("true") ? 4 : 0; break;
                case "exit_frame": f.strict |= v.equals("1") || v.equals("true") ? 8 : 0; break;
                case "mock_write": f.mockWrites.add(w); break;
                default: throw new IllegalArgumentException("unknown target fact: " + line);
            }
        }
        f.verify();
        return f;
    }

    void verify() {
        long[][] pairs = {{ramLo, TcBase.RAM_LO}, {ramHi, TcBase.RAM_HI}, {entrySp, TcBase.SP},
            {stackLo, TcBase.STACK_LO}, {sentinel, TcBase.SENTINEL}, {scrLo, TcBase.SCR}, {scrHi, TcBase.SCR + TcBase.SCR_SIZE}};
        for (long[] p : pairs) {
            if (p[0] != p[1]) {
                throw new IllegalStateException("target.json memory map disagrees with TcBase constants: "
                    + Long.toHexString(p[0]) + " vs " + Long.toHexString(p[1]));
            }
        }
        if (!retInt.equals("r3") || !retFloat.equals("f1")) throw new IllegalStateException("unsupported return registers");
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

    boolean isCode(long a) {
        return haveCode && a >= ramLo && a < ramHi && code.get((int) (a - ramLo));
    }

    boolean isMmio(long a) {
        for (long[] r : mmio) if (a >= r[0] && a < r[1]) return true;
        return false;
    }

    // May a vector randomise the global at a? (a writable section: the game changes it; constants never)
    boolean randomGlobal(long a) {
        for (long[] r : globalRanges) if (a >= r[0] && a < r[1]) return true;
        return false;
    }
}

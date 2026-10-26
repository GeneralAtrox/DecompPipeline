// Instruction classes of the Xbox 360 target by encoding (not a script itself): the Java twin of
// tools/ppcscan.classify, so the oracle refuses exactly what the catalog refuses (tests/test_xbox360_scripts.py
// checks the tables agree). Encodings, not Ghidra's mnemonics: Ghidra 12.1.2 decodes no VMX128 (opcodes 5 and 6
// are undefined there) and some VMX128 words in opcode 4 decode as other AltiVec instructions.
import java.util.*;

public class TcClass {
    static final Set<Integer> VMX31 = Set.of(6, 7, 38, 39, 71, 103, 135, 167, 199, 231, 359, 487, 519, 551, 647, 679,
        775, 807, 903, 935, 342, 374, 822);
    static final Set<Integer> FLOAD31 = Set.of(535, 567, 599, 631);
    static final Set<Integer> FSTORE31 = Set.of(663, 695, 727, 759, 983);
    static final Set<Integer> ATOMIC31 = Set.of(20, 84, 150, 214);
    static final Set<Integer> CACHE31 = Set.of(1014, 86, 54, 470, 982);
    static final Set<Integer> BARRIER31 = Set.of(598, 854);
    static final Set<Integer> SYS31 = Set.of(83, 146, 178, 371, 306, 566, 402, 434, 498, 915, 851);
    static final Set<Integer> TRAP31 = Set.of(4, 68);
    static final Set<Integer> FPMODE63 = Set.of(38, 70, 134, 583, 711, 64);
    static final Set<Integer> FCONV63 = Set.of(14, 15, 814, 815, 846);
    static final Set<Integer> SPR_PLAIN = Set.of(1, 8, 9);
    static final int SPR_VRSAVE = 256;

    // ppcscan.REFUSE in order: the class, then the reason the catalog and the oracle give.
    static final String[][] REFUSE = {{"vmx", "VMX"}, {"fp_mode", "FPSCR"}, {"estimate", "estimate"},
        {"atomic", "atomic"}, {"cache", "cache"}, {"sys", "system"}};

    static List<String> classify(int w) {
        int op = w >>> 26;
        if (op == 4 || op == 5 || op == 6) return List.of("vmx");
        if (op == 2 || op == 3) return List.of("trap");
        if (op == 17) return List.of("sys");
        if (op >= 48 && op <= 55) return List.of("fpu");
        if (op == 58 || op == 62) return List.of("ld64");
        int xo5 = (w >>> 1) & 0x1f, xo10 = (w >>> 1) & 0x3ff;
        if (op == 59 || op == 63) {
            if (op == 63 && FPMODE63.contains(xo10)) return List.of("fpu", "fp_mode");
            if (xo5 >= 28 && xo5 <= 31) return List.of("fpu", "fma");
            if ((op == 59 && xo5 == 24) || (op == 63 && xo5 == 26)) return List.of("fpu", "estimate");
            if (op == 63 && FCONV63.contains(xo10)) return List.of("fpu", "fconv");
            return List.of("fpu");
        }
        if (op == 19) return xo10 == 18 || xo10 == 274 ? List.of("sys") : xo10 == 150 ? List.of("barrier") : List.of();
        if (op == 31) {
            if (VMX31.contains(xo10)) return List.of("vmx");
            if (FLOAD31.contains(xo10) || FSTORE31.contains(xo10)) return List.of("fpu");
            if (ATOMIC31.contains(xo10)) return List.of("atomic");
            if (CACHE31.contains(xo10)) return List.of("cache");
            if (BARRIER31.contains(xo10)) return List.of("barrier");
            if (TRAP31.contains(xo10)) return List.of("trap");
            if (xo10 == 339 || xo10 == 467) {
                int spr = ((w >>> 16) & 31) | (((w >>> 11) & 31) << 5);
                if (SPR_PLAIN.contains(spr)) return List.of();
                return spr == SPR_VRSAVE ? List.of("vmx") : List.of("sys");
            }
            if (SYS31.contains(xo10)) return List.of("sys");
        }
        return List.of();
    }

    // The phase 1 refusal of one instruction word ("VMX", "FPSCR", ...), or null.
    static String refusal(int w) {
        List<String> c = classify(w);
        for (String[] r : REFUSE) if (c.contains(r[0])) return r[1];
        return null;
    }

    // A word of a VMX/VMX128 instruction, or of a register save/restore of vector registers.
    static boolean isVector(int w) {
        return classify(w).contains("vmx");
    }
}

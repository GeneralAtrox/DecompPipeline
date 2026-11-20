// The kind of a prototype parameter by where it lives (not a script itself: TcBase.prototypeOf calls it; pure Java so
// tests/test_x360_sigkind.py runs it with javac and java alone).
// Ghidra types a parameter from how the code uses it, whatever register holds it. 82c14050 stores r3 and reloads it
// with lfs, so the decompiler calls its first parameter "float" in r3. A GPR holds an integer: the 32 bits the caller
// put there, which the code may read as a single. Its kind is u32 (the word, as the judge thunk and the translator's
// entry pass it), never f32: x360_check.gen_thunk refuses f32 in r3..r10 and the whole function ended ERROR.
// An FPR always holds a double (f64). A double Ghidra puts in a GPR (a parameter past f13) is kept: the register's
// 64 bits are the double (tc_bits_f64, tests/test_x360_f64_gpr.py).
public class TcSigKind {
    static boolean gpr(String storage) {
        return storage.matches("r([3-9]|10)");
    }

    static boolean fpr(String storage) {
        return storage.matches("f([1-9]|1[0-3])");
    }

    // The kind to record for a register parameter that Ghidra typed `kind` and placed in `storage`.
    static String registerKind(String storage, String kind) {
        if (fpr(storage)) return "f64";
        if (gpr(storage) && kind.equals("f32")) return "u32";
        return kind;
    }
}

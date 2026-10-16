// Call targets from the inputs (batch 28). A mocked call goes wherever the run's register says: a function pointer
// the function reads from its input memory, an argument register or a scripted call result. A random word there is a
// call to an address with no memory on the Dreamcast (the game would crash on that input), and in the readable-C
// runner, whose guest RAM is the only memory it traps calls into, such an address can be a host DLL's code: the
// readable function then runs it natively and crashes, or worse. So a call to a target outside RAM that is no constant
// of the body is an invalid input, repaired like a fault: the word holding it becomes a fresh chunk pointer (data in
// RAM: the oracle never runs a mocked callee, and the readable runner traps and logs a call there). NULL stays a valid
// target (a pointer legitimately holds it; both judges trap a call to it). Constant targets are unchanged.
import java.util.*;

final class TcCalls {
    private TcCalls() {
    }

    // A call to t (folded) is from the inputs and invalid: not NULL, outside guest RAM, not a constant of the body.
    static boolean fromInputs(long t, Set<Long> bodyConsts) {
        return t != 0 && (t < TcBase.RAM_LO || t >= TcBase.RAM_HI) && !bodyConsts.contains(t);
    }

    // The input word the target came from becomes a fresh chunk pointer: the latest logged 4-byte load of exactly that
    // value (a word in RAM the vector may set), else an entry register, else a scripted call result the run used.
    // Exact matches only (a small target equals many unrelated words). The repaired vector, or null.
    static TcInputs.Vec repair(TcVectors g, TcInputs.Vec v, long target, List<long[]> loads, int used) {
        for (int i = loads.size() - 1; i >= 0; i--) {
            long[] ld = loads.get(i);
            if (ld[2] != 4 || (ld[3] & 0xffffffffL) != target || !g.genInRam(ld[1]) || v.genPoked.containsKey(ld[1])) {
                continue;
            }
            g.genPoke(v, ld[1], g.genChunk(v, 0));
            return v;
        }
        for (int i = 0; i < 4; i++) {
            if (v.r[i] != target) continue;
            v.r[i] = g.genChunk(v, 0);
            return v;
        }
        long[][] regs = {g.entryLow, g.entryGi};
        boolean[][] which = {g.lowWhich, g.giWhich};
        for (int k = 0; k < 2; k++) {
            for (int i = 0; regs[k] != null && i < regs[k].length; i++) {
                if (!which[k][i] || regs[k][i] != target) continue;
                regs[k][i] = g.genChunk(v, 0);
                return v;
            }
        }
        for (int k = Math.min(used, v.rets.length) - 1; k >= 0; k--) {
            if (v.rets[k] != target) continue;
            v.rets[k] = g.genChunk(v, 0);
            return v;
        }
        return null;
    }
}

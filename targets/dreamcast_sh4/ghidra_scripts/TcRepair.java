// Input repairs beyond TcInputs.repairVec, used by TcVectors.runRepaired (moved out of TcVectors unchanged when the
// reach pass was added, to keep files short). LOCKED: the watcher agent may not change this file. Not a script itself.
import java.util.*;

final class TcRepair {
    private TcRepair() {
    }

    // Pointer repair for the entry registers TcInputs does not see (entryLow, entryGi), as repairVec does for r4..r7:
    // a value just below the fault address that is not already a generator pointer becomes a fresh chunk.
    static boolean entry(TcVectors g, TcInputs.Vec v, long faultAddr) {
        long[][] regs = {g.entryLow, g.entryGi};
        boolean[][] which = {g.lowWhich, g.giWhich};
        for (int k = 0; k < 2; k++) {
            for (int i = 0; regs[k] != null && i < regs[k].length; i++) {
                long d = faultAddr - regs[k][i];
                if (!which[k][i] || d < -0x100 || d >= TcInputs.GEN_MAX_OFFSET || g.genOwned(regs[k][i])) continue;
                regs[k][i] = g.genChunk(v, d);
                return true;
            }
        }
        return false;
    }

    // Rescue only: a scripted call result (v.rets) just below the fault address that is not already a generator pointer
    // becomes a fresh chunk, as entry() does for entry registers, the latest result the run used first. Half the
    // time the faulting offset then holds a harvested compare constant (a fetched record's tag or opcode).
    static boolean ret(TcVectors g, TcInputs.Vec v, long faultAddr, int used, List<Long> consts, Random h) {
        for (int k = Math.min(used, v.rets.length) - 1; k >= 0; k--) {
            long d = faultAddr - v.rets[k];
            if (d < -0x100 || d >= TcInputs.GEN_MAX_OFFSET || g.genOwned(v.rets[k])) continue;
            int n = v.patchAddr.size();
            v.rets[k] = g.genChunk(v, d);
            if (consts.isEmpty() || v.patchAddr.size() == n || !h.nextBoolean()) return true;
            long c = TcGuide.pick(consts, h), off = v.rets[k] + d - v.patchAddr.get(n);
            byte[] data = v.patchData.get(n);
            for (int j = 0; j < TcGuide.size(c) && off >= 0 && off + j < data.length; j++) {
                data[(int) off + j] = (byte) (c >> (8 * j));
            }
            return true;
        }
        return false;
    }
}

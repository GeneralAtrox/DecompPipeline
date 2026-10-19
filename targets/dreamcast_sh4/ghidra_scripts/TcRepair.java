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

    // The last fallback: a scaled index or a self-relative offset. No input is near the fault, but a loaded word n,
    // shifted left 0..5, plus another recent load's value (a table base) or plus n's own address (p + *p) lands within
    // GEN_MAX_OFFSET below it. Spawn 8c055180 and 8c056970 read a table pointer at 8c3227b4 plus (the base index at
    // 8c36200c + i) * 8, both inputs: 706 of 750 invalid runs there. n gets a small value (its low 4 bits, so the
    // random stream is untouched); a base that is still no pointer is then near the next fault and repaired as usual.
    static boolean index(TcVectors g, TcInputs.Vec v, long faultAddr, List<long[]> loads) {
        int from = Math.max(0, loads.size() - 16);
        for (int i = loads.size() - 1; i >= from; i--) {
            long[] ix = loads.get(i);
            long n = ix[3] & 0xffffffffL;
            if (ix[2] != 4 || !g.genInRam(ix[1]) || v.genPoked.containsKey(ix[1])) continue;
            for (int sh = 0; sh <= 5; sh++) {
                long off = (n << sh) & 0xffffffffL;
                if (off < TcInputs.GEN_MAX_OFFSET || off > 0xffffff00L) continue; // already a small offset
                boolean hit = ((faultAddr - ix[1] - off) & 0xffffffffL) < TcInputs.GEN_MAX_OFFSET;
                for (int j = from; j < loads.size() && !hit; j++) {
                    hit = j != i && ((faultAddr - loads.get(j)[3] - off) & 0xffffffffL) < TcInputs.GEN_MAX_OFFSET;
                }
                if (!hit) continue;
                g.genPoke(v, ix[1], n & 0xf);
                return true;
            }
        }
        // An entry register as the index into a table the body names: Spawn 8c011f98 reads @(r5 * 4 + 0x0c08f068).
        for (int i = 0; i < 4; i++) {
            long n = v.r[i] & 0xffffffffL;
            for (int sh = 0; sh <= 5; sh++) {
                long off = (n << sh) & 0xffffffffL;
                if (off < TcInputs.GEN_MAX_OFFSET || off > 0xffffff00L) continue;
                for (long c : g.bodyConsts()) {
                    if (((faultAddr - c - off) & 0xffffffffL) >= TcInputs.GEN_MAX_OFFSET) continue;
                    v.r[i] = n & 0xf;
                    return true;
                }
            }
        }
        return false;
    }
}

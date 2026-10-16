// Rescue pass, a store outside RAM (batch 29; TcOracle.storeFaults): the input that gave the store its base, and a
// repair of that input. Not a script itself; used by TcVectors.
//
// TcOracle.collect sees a store outside RAM only when the run ends, after the epilogue's register pops have filled
// the load log; TcInputs.repairVec then patched the latest load whose value was near the fault, which were those
// stack words, and spent every repair there (TC 8c079c1e: a store at 0x10 through a base a mocked callee returned as
// 0; 744 of 768 runs rejected, the function refused). In the rescue pass the run stops at the store, with the base
// register's value (Result.storeBase). Its source is the most recent input that produced exactly that value:
// - a mocked callee's return (Vec.rets[k], in r0 after call k), or
// - a 4-byte load the vector can set: in RAM and outside the function's own frame [STACK_LO, SP) (those words hold
//   what the function saved or spilled itself, so a poke there changes nothing; the batch 27 rule pins uninitialized
//   ones to 0).
// Which came last is told by the calls made before the load (the load log's fifth word). The source gets a pointer
// to a generated chunk, as repairVec / repairRet give. No exact source: the usual repairs follow, on the load log
// without the own frame's words (inputLoads).
import java.util.*;

public class TcProvenance {
    static boolean ownFrame(long a) {
        return a >= TcBase.STACK_LO && a < TcBase.SP;
    }

    // The load log without loads from the function's own frame.
    static List<long[]> inputLoads(List<long[]> loads) {
        List<long[]> out = new ArrayList<>();
        for (long[] ld : loads) if (!ownFrame(ld[1])) out.add(ld);
        return out;
    }

    // Repairs the source of r.storeBase in v; false when there is none (or the store is too far from the base).
    static boolean repair(TcInputs t, TcInputs.Vec v, TcOracle.Result r) {
        long base = r.storeBase, d = r.faultAddr - base;
        if (base < 0 || d < -0x100 || d >= TcInputs.GEN_MAX_OFFSET) return false;
        int ret = -1;
        for (int k = Math.min(r.calls.size(), v.rets.length) - 1; k >= 0 && ret < 0; k--) {
            if (v.rets[k] == base && !t.genOwned(base)) ret = k;
        }
        long[] load = null;
        for (int i = r.loads.size() - 1; i >= 0 && load == null; i--) {
            long[] ld = r.loads.get(i);
            if (ld[3] == base && ld[2] == 4 && t.genInRam(ld[1]) && !ownFrame(ld[1]) && !v.genPoked.containsKey(ld[1])) {
                load = ld;
            }
        }
        if (load != null && (ret < 0 || load[4] > ret)) {
            t.genPoke(v, load[1], t.genChunk(v, d));
            return true;
        }
        if (ret >= 0) {
            v.rets[ret] = t.genChunk(v, d);
            return true;
        }
        return false;
    }
}

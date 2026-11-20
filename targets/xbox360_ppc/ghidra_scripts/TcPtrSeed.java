// Pointer seeding for the Xbox 360 oracle's inputs: pointer repair that reaches what a mocked callee returned
// (TcVectors.attempt calls repair, then its own entry repair).
//
// TcInputs.repairVec makes a pointer valid when a word the run loaded, or an entry register, was near the fault. A
// mocked call's result (Vec.rets) never was one, so a function that stores through what a callee returned faulted in
// every vector: Too Human's push-buffer users (82dfd460, 82e61c00, 82e699e8: bl 8312ec1c, then lwz r31,0x20(r3)), and
// a null check after an allocation (82ea4c48), had no valid vector (NO_VECTORS). Ported from the GameCube target
// (DecompPipeline b110a1a2): its TcVectors.repair (a call's result near the fault, and every later result not already
// ours, become fresh chunks). Its array case (6c6d61b1: one shared chunk for every element) was tried here and left
// out: on Too Human it cost 82e69df8 all its vectors (48/48 to 0/0) and 8263d428 and 82d13a18 most of theirs. The newest
// candidate goes first, as the GameCube's lateRepair note asks: a call's result near the fault is the pointer when no
// load near the fault that repairVec could repair came after the call (TcOracle's Result.callSeen holds, per call, how
// many loads were logged before it; Result.seen counts them all). Hints can seed a call's result too: "genptr
// ret2+0x20" (TcInputs.genPlace, the GameCube's retN). A store outside RAM stops the run where it happens
// (TcBank.badWrite), so repair sees the loads that led to it (8263d428: 8/3 valid vectors without it, 26/17 with it).
// pick is pure; tests/java/X360PtrSeedStub.java checks it all.
// LOCKED like the other oracle scripts.
import java.util.*;

public final class TcPtrSeed {
    private TcPtrSeed() {
    }

    // The scratch window and the generator's heap, contiguous: TcInputs.genOwned.
    static final long OWN_LO = 0x40000000L, OWN_HI = 0x40100000L;

    // The fault's distance from a pointer's low word (addresses are 32 bits).
    static long off(long fault, long ptr) {
        return fault - (ptr & 0xffffffffL);
    }

    static boolean near(long d, long max) {
        return d >= -0x100 && d < max;
    }

    static boolean owned(long ptr) {
        long a = ptr & 0xffffffffL;
        return a >= OWN_LO && a < OWN_HI;
    }

    // The call (an index into rets) whose result to repair, or -1. callSeen.get(k): the loads logged before call k
    // (one entry per call the run made); seen: all loads logged, of which loads holds the last loads.size(); poked: the
    // words this vector already set (repairVec leaves them). newest: only when no load near the fault, of a word or a
    // doubleword repairVec could still set, came after that call (else repairVec goes first); otherwise the newest
    // call near the fault. A call past rets' 16 returns 0 in the runner and is not repaired.
    static int pick(long fault, long[] rets, List<Integer> callSeen, List<long[]> loads, int seen, Set<Long> poked,
                    long max, boolean newest) {
        long last = -1;
        for (int i = loads.size() - 1; i >= 0 && last < 0; i--) {
            long[] ld = loads.get(i);
            if ((ld[2] == 4 || ld[2] == 8) && !poked.contains(ld[1]) && near(off(fault, ld[3]), max)) {
                last = seen - loads.size() + i;
            }
        }
        for (int k = Math.min(callSeen.size(), rets.length) - 1; k >= 0; k--) {
            if (owned(rets[k]) || !near(off(fault, rets[k]), max)) continue;
            return newest && last >= callSeen.get(k) ? -1 : k;
        }
        return -1;
    }

    // The repair order: the newest call near the fault, repairVec, then any call near the fault.
    static TcInputs.Vec repair(TcVectors g, TcInputs.Vec v, TcOracle.Result r, List<long[]> loads) {
        if (repairRet(g, v, r, loads, true)) return v;
        TcInputs.Vec nv = g.repairVec(v, r.faultAddr, r.faultWrite, loads);
        return nv != null ? nv : repairRet(g, v, r, loads, false) ? v : null;
    }

    static boolean repairRet(TcInputs g, TcInputs.Vec v, TcOracle.Result r, List<long[]> loads, boolean newest) {
        int k = pick(r.faultAddr, v.rets, r.callSeen, loads, r.seen, v.genPoked.keySet(), TcInputs.GEN_MAX_OFFSET,
                     newest);
        if (k < 0) return false;
        long d = off(r.faultAddr, v.rets[k]);
        for (int j = k; j < v.rets.length; j++) if (!owned(v.rets[j])) v.rets[j] = g.genChunk(v, d);
        return true;
    }
}

// The GameCube oracle's results for calls after the 16th (TcOracle.lateOf, TcOracle.ret, TcVectors.repair and
// lateRepair, TcVectors.lateTail) on fixed cases (tests/test_gc_calls.py). No Ghidra application and no program: a bare
// TcPrepare holds the generator. Each case prints "NAME: <what changed>".
import java.util.*;

public class TcGcCallsStub {
    static TcInputs.Vec vec() {
        TcInputs.Vec v = new TcInputs.Vec();
        for (int i = 0; i < 8; i++) v.r[i] = TcBase.SCR;  // our own pointers: never repaired
        return v;
    }

    static TcOracle.Result run(int calls, long fault) {
        TcOracle.Result r = new TcOracle.Result();
        for (int i = 0; i < calls; i++) r.calls.add(String.format("%08x", 0x80003200L + 4 * i));
        r.faultAddr = fault;
        return r;
    }

    // Which results are our chunks now: "rets 3 4 .. late 2 3 ..", or "null" when the repair gave up.
    static String owned(TcPrepare g, TcInputs.Vec v) {
        if (v == null) return "null";
        StringBuilder b = new StringBuilder("rets");
        for (int i = 0; i < v.rets.length; i++) if (g.genOwned(v.rets[i])) b.append(' ').append(i);
        long[] late = TcOracle.lateOf.get(v);
        b.append(late == null ? " nolate" : " late");
        for (int i = 0; late != null && i < late.length; i++) if (g.genOwned(late[i])) b.append(' ').append(16 + i);
        return b.toString();
    }

    public static void main(String[] args) {
        TcPrepare g = new TcPrepare();
        g.rnd = new Random(1);
        List<long[]> none = new ArrayList<>();
        // Skies 8012ab84: bl 801db124 (the 20th call here); lwz r3,4(r3) reads address 4. Call 19 (0-based) and the 15 after
        // it get chunks; Vec.rets is untouched.
        TcInputs.Vec a = vec();
        System.out.println("call_past_16: " + owned(g, g.repair(a, run(20, 4), none)));
        // then a fault through call 18's result (a value kept across the 20th call): the newest is ours now, repairVec
        // finds nothing, the older later result is the pointer.
        System.out.println("older_late: " + owned(g, g.repair(a, run(20, 8), none)));
        // a run of 16 calls: the repair is as before this change (Vec.rets from the faulting call on, no later results).
        TcInputs.Vec b = vec();
        System.out.println("sixteen_calls: " + owned(g, g.repair(b, run(16, 4), none)));
        // TcOracle.ret: Vec.rets, then lateOf, then 0 (past RETS, or no later result repaired).
        TcInputs.Vec c = vec();
        c.rets[15] = 0x15;
        System.out.println("ret_plain: " + Long.toHexString(TcOracle.ret(c, 15)) + " " + TcOracle.ret(c, 16) + " "
            + TcOracle.ret(c, 63) + " " + TcOracle.ret(c, 64) + " " + TcOracle.ret(c, 4095));
        long[] late = new long[TcOracle.RETS - 16];
        late[2] = 0x81001100L;
        late[47] = 0x81001500L;
        TcOracle.lateOf.put(c, late);
        System.out.println("ret_late: " + Long.toHexString(TcOracle.ret(c, 18)) + " " + Long.toHexString(TcOracle.ret(c, 63))
            + " " + TcOracle.ret(c, 64) + " " + TcOracle.ret(c, 17));
        // vectors.txt C's tail: empty without a repaired later result, else up to the last one.
        late[47] = 0;
        System.out.println("tail_none: [" + TcVectors.lateTail(null) + "][" + TcVectors.lateTail(new long[48]) + "]");
        System.out.println("tail_late: [" + TcVectors.lateTail(late) + "]");
        System.out.println("rets_length: " + vec().rets.length + " " + TcOracle.RETS);
    }
}

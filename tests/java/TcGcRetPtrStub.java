// genretptr (TcVectors.retPtrHints, runGrow; TcCallFx.grow; TcOracle.RETS) on fixed cases (tests/test_gc_retptr.py).
// No Ghidra program: runOracle is a stand-in for a loop that calls CALLEE n times and stores through each result at
// +0x14 (Skies 80197270 / 80195f9c), faulting at the first result that is not one of our pointers. Each case prints
// "NAME: <what happened>".
import java.util.*;

public class TcGcRetPtrStub {
    static final long CALLEE = 0x801d68c4L, OTHER = 0x801d52a8L;

    static class Fake extends TcPrepare {
        int n, runs, deref = -1;  // deref >= 0: each CALLEE result's word there is stored through too (80131a04: +4)

        @Override
        Result runOracle(Vec v) {
            runs++;
            Result r = new Result();
            for (int k = 0; k < n; k++) {
                long target = k % 4 == 3 ? OTHER : CALLEE;  // every 4th call is another callee, never dereferenced
                r.calls.add(String.format("%08x", target));
                int g = growRets ? TcCallFx.grow(this, v, k, retPtr.get(target)) : -1;
                r.grew |= g >= 0;
                long x = ret(v, k);
                if (target == CALLEE && !genOwned(x) || target == CALLEE && deref >= 0 && !genOwned(peek(v, x + deref))) {
                    r.reason = "write outside RAM";
                    r.faultAddr = x + 0x14;
                    r.faultWrite = true;
                    return r;
                }
            }
            r.ok = true;
            return r;
        }
    }

    // The word at a in v's patches (the last patch holding it), 0 when none does.
    static long peek(TcInputs.Vec v, long a) {
        for (int i = v.patchAddr.size() - 1; i >= 0; i--) {
            long b = v.patchAddr.get(i);
            byte[] d = v.patchData.get(i);
            if (a < b || a + 4 > b + d.length) continue;
            int o = (int) (a - b);
            return ((d[o] & 0xffL) << 24) | ((d[o + 1] & 0xffL) << 16) | ((d[o + 2] & 0xffL) << 8) | (d[o + 3] & 0xffL);
        }
        return 0;
    }

    static Fake fake(int n, String... hints) {
        Fake f = new Fake();
        f.rnd = new Random(1);
        f.n = n;
        f.retPtrHints(Arrays.asList(hints), f.retPtr);
        return f;
    }

    static TcInputs.Vec vec(Fake f) {
        TcInputs.Vec v = new TcInputs.Vec();
        for (int i = 0; i < 16; i++) v.rets[i] = i % 4;  // small words: no pointer of ours
        return v;
    }

    // How many of the first n results are ours, and how many CALLEE results are not.
    static String owned(Fake f, TcInputs.Vec v) {
        int own = 0, bad = 0;
        for (int k = 0; k < f.n; k++) {
            boolean o = f.genOwned(TcOracle.ret(v, k));
            if (o) own++;
            if (!o && k % 4 != 3) bad++;
        }
        return "own " + own + " bad " + bad;
    }

    static String run(Fake f) {
        TcInputs.Vec v = vec(f);
        TcOracle.Result r = f.runGrow(v);
        return "ok " + r.ok + " runs " + f.runs + " " + owned(f, v) + (r.ok ? "" : " (" + r.reason + ")");
    }

    public static void main(String[] args) {
        Map<Long, long[]> m = new TreeMap<>();
        List<String> rest = TcVectors.retPtrHints(Arrays.asList("genretptr 0x801d68c4 0x14", "genptr r3", "# note",
            "genretptr 0x801e1c7c", "genretptr", "genretptr 0x1 0x2 0x3"), m);
        StringBuilder b = new StringBuilder();
        for (Map.Entry<Long, long[]> e : m.entrySet()) b.append(Long.toHexString(e.getKey())).append('=').append(Arrays.toString(e.getValue()).replace(" ", "")).append(' ');
        System.out.println("parse: " + b + "| " + String.join(" | ", rest));
        System.out.println("no_hint_80: " + run(fake(80)));
        System.out.println("hint_80: " + run(fake(80, "genretptr 0x801d68c4 0x14")));
        System.out.println("hint_700: " + run(fake(700, "genretptr 0x801d68c4 0x14")));
        System.out.println("other_callee: " + run(fake(80, "genretptr 0x801e1c7c")));
        // each result's +4 word is stored through as well: only PTR fields make it one of ours
        Fake p = fake(80, "genretptr 0x801d68c4 0x18 0x4");
        p.deref = 4;
        System.out.println("ptr_field: " + run(p));
        Fake q0 = fake(80, "genretptr 0x801d68c4 0x18");
        q0.deref = 4;
        System.out.println("no_ptr_field: " + run(q0));
        // a vector's later runs outside runGrow (TcStrict's replay, TcBoundPass) set nothing
        Fake f = fake(80, "genretptr 0x801d68c4 0x14");
        TcInputs.Vec v = vec(f);
        TcOracle.Result r = f.runOracle(v);
        System.out.println("plain_run: ok " + r.ok + " grew " + r.grew + " " + owned(f, v));
        // the kept run is the last one, which set nothing; its results are the vector's (lateTail: C's words)
        Fake h = fake(40, "genretptr 0x801d68c4");
        TcInputs.Vec w = vec(h);
        TcOracle.Result q = h.runGrow(w);
        long[] late = TcOracle.lateOf.get(w);
        int words = TcVectors.lateTail(late).trim().split(" ").length;
        System.out.println("kept: ok " + q.ok + " grew " + q.grew + " tail " + words + " len " + late.length);
        // the index limit: call RETS-1 can be set, call RETS cannot
        Fake e = fake(1, "genretptr 0x801d68c4");
        TcInputs.Vec u = vec(e);
        System.out.println("limit: " + (TcCallFx.grow(e, u, TcOracle.RETS - 1, new long[] {0}) >= 0) + " "
            + TcCallFx.grow(e, u, TcOracle.RETS, new long[] {0}) + " " + TcCallFx.grow(e, u, TcOracle.RETS - 1, new long[] {0}) + " "
            + TcCallFx.grow(e, u, 3, null) + " rets " + TcOracle.RETS);
    }
}

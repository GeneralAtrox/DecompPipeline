// GameCube template TcInputs hints: the retN location and the genstream "ptr" value (tests/test_gc_hintret.py).
// No Ghidra application and no program: a bare TcPrepare holds the generator. Each case prints "NAME: <facts>" or
// "NAME: ERROR message".
import java.util.*;

public class TcGcHintRetStub {
    static String hex(long x) {
        return Long.toHexString(x);
    }

    static void run(TcPrepare g, String name, String... hints) {
        TcInputs.Vec v = new TcInputs.Vec();
        long[] r0 = v.rets.clone();
        try {
            g.applyHints(v, Arrays.asList(hints));
        } catch (IllegalArgumentException e) {
            System.out.println(name + ": ERROR " + e.getMessage());
            return;
        }
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < v.rets.length; i++) {
            if (v.rets[i] != r0[i]) b.append(" ret").append(i).append(g.genOwned(v.rets[i]) ? "=own" : "=" + hex(v.rets[i]));
        }
        for (Map.Entry<Long, Long> e : new TreeMap<>(v.genPoked).entrySet()) {
            b.append(" poke ").append(g.genOwned(e.getKey()) ? "own" : hex(e.getKey()))
                .append("=").append(g.genOwned(e.getValue()) ? "own" : hex(e.getValue()));
        }
        System.out.println(name + ":" + b);
    }

    public static void main(String[] args) {
        TcPrepare g = new TcPrepare();
        g.rnd = new Random(1);
        run(g, "ret_value", "genrange ret5 7 7");                  // call 5 returns 7, nothing else changes
        run(g, "ret_ptr", "genptr ret3+0x24");                     // call 3 returns our chunk; its +0x24 holds another
        run(g, "ret_chain", "genptr ret0+0x24+0x4c");              // two steps, each dereferenced
        run(g, "ret_twice", "genptr ret2+0x8", "genptr ret2+0xc"); // the same chunk both times
        run(g, "ret_bad", "genptr ret16+0x4");
        run(g, "reg_unchanged", "genptr r3+0x10");                 // rN as before: no call result changes
        TcInputs.Vec v = new TcInputs.Vec();                       // genstream with ptr: every word a chunk of ours
        g.applyHints(v, Arrays.asList("genstream r3 6 ptr"));
        int own = 0, words = 0;
        for (int i = 0; i < v.patchAddr.size(); i++) {
            if (v.patchAddr.get(i).longValue() != v.r[0]) continue;
            byte[] d = v.patchData.get(i);
            for (int k = 0; k < d.length; k += 4) {
                long w = ((d[k] & 0xffL) << 24) | ((d[k + 1] & 0xffL) << 16) | ((d[k + 2] & 0xffL) << 8) | (d[k + 3] & 0xffL);
                words++;
                if (g.genOwned(w)) own++;
            }
        }
        System.out.println("stream_ptr: " + own + " of " + words);
    }
}

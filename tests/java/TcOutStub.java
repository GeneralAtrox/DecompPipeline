// tests/test_regout.py's driver: the real TcOut and TcLive (no Program needed for these parts), printing one line per
// check for the test to compare.
import java.util.*;

public class TcOutStub {
    static String show(long[] o) {
        return o == TcLive.OTHER ? "OTHER" : o == null ? "null" : Arrays.toString(o);
    }

    static long[] cls(Set<Long> exits, boolean unk, boolean oth) {
        List<Set<Long>> ox = List.of(new TreeSet<>(exits), new TreeSet<>(), new TreeSet<>(), new TreeSet<>());
        return TcOut.classify(ox, new boolean[] {unk, false, false, false}, new boolean[] {oth, false, false, false})[0];
    }

    // r4 after a call in follow mode: the caller's r4 was (base, off), the callee's r4 output o.
    static String fol(int base, long off, long[] o) {
        TcLive.Deltas.St was = new TcLive.Deltas.St(), s = new TcLive.Deltas.St();
        was.base[4] = base;
        was.off[4] = off;
        Arrays.fill(s.base, TcLive.Deltas.TOP);  // the call clobbered r0..r7
        TcOut.follow(s, new long[][] {o, null, null, null}, was);
        return s.base[4] + "+" + s.off[4];
    }

    public static void main(String[] a) {
        System.out.println("classify kept=" + show(cls(Set.of(0L), false, false)) + " const=" + show(cls(Set.of(32L), false, false))
            + " set=" + show(cls(Set.of(16L, 32L), false, false)) + " unknown=" + show(cls(Set.of(32L), true, false))
            + " other=" + show(cls(Set.of(32L), true, true)));
        long[] c32 = {32};
        System.out.println("follow kept=" + fol(4, 0, TcLive.NONE) + " const=" + fol(4, 8, c32) + " unknown="
            + fol(4, 8, TcLive.UNKNOWN) + " other=" + fol(4, 8, TcLive.OTHER) + " fromtop=" + fol(TcLive.Deltas.TOP, 0, c32));
        System.out.println("follow r8base const=" + fol(8, 0, c32) + " unknown=" + fol(8, 0, TcLive.UNKNOWN));
        TcLive l = new TcLive();
        long[][] e = new long[11][];
        e[7] = TcLive.UNKNOWN;  // r4, unknown
        l.table.put(0x8c0602a0L, e);
        long[][] f = new long[11][];
        f[0] = new long[] {0x20};  // r8
        f[9] = new long[] {0x10};  // r6
        l.table.put(0x8c060600L, f);
        System.out.println(l.header().replace("\n", "|"));
        System.out.println("r_live " + l.meta());
        TcOut.exported = TcOut.bit(0) | TcOut.bit(2);
        TcOracle.Result r = new TcOracle.Result();
        r.ro = new long[] {0x8c010120L, 0, 2, 0};
        System.out.println(TcOut.header().replace("\n", "") + "|" + TcOut.exp(r).replace("\n", ""));
    }
}

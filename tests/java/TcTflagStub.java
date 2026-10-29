// tests/test_tflag.py's and tests/test_zeroread.py's driver: the real TcTflag, TcLive, TcHw and TcBase (no Program
// needed for these parts), printing one line per check for the tests to compare.
import java.util.*;

public class TcTflagStub {
    public static void main(String[] a) {
        TcLive l = new TcLive();
        long[][] e = new long[12][];
        e[11] = TcLive.UNKNOWN;  // T drawn (TcTflag)
        l.table.put(0x8c1967c0L, e);
        long[][] f = new long[12][];
        f[7] = new long[] {0x20};  // r4 + 0x20 (TcOut) beside T
        f[11] = TcLive.UNKNOWN;
        l.table.put(0x8c197240L, f);
        System.out.println(l.header().replace("\n", "|"));
        System.out.println("r_live " + l.meta());
        TcTflag.exported = false;
        TcTflag.out.clear();
        TcOracle.Result r = new TcOracle.Result();
        r.ro[4] = 1;
        System.out.println("off [" + TcTflag.header() + "][" + TcTflag.exp(r) + "][" + TcTflag.meta() + "]");
        TcTflag.exported = true;
        TcTflag.out.add(0x8c1967c0L);
        TcTflag.out.add(0x8c197240L);
        System.out.println("on " + (TcTflag.header() + TcTflag.exp(r) + TcTflag.meta()).replace("\n", "|"));
        r.halted = true;
        System.out.println("halted [" + TcTflag.exp(r) + "]");
        // area 5 (TcBase.area5, TcHw "read area5")
        long[][] at = {{0x144f2564L, 2}, {0x944f2564L, 4}, {0xb4000000L, 1}, {0xd7fffffcL, 4}, {0x17fffffeL, 4},
            {0x13fffffcL, 4}, {0x18000000L, 4}, {0xf4000000L, 4}, {0x8c010000L, 4}, {0x34000000L, 4}};
        StringBuilder b = new StringBuilder("area5");
        for (long[] x : at) b.append(' ').append(TcBase.area5(x[0], x[1]) ? 1 : 0);
        System.out.println(b);
        TcHw h = TcHw.parse("hw:z5");
        byte[] z = h.read(0x144f2564L, 2);
        System.out.println("hw z5 " + (z == null ? "null" : Arrays.toString(z)) + " " + (h.read(0x8c010000L, 4) == null)
            + " " + (h.read(0x18000000L, 4) == null) + " " + h.meta());
        TcHw.on = h;
        System.out.println("header " + TcHw.header().replace("\n", "|"));
        TcHw g = TcHw.parse("hw:gbr");
        System.out.println("gbr " + (g.read(0x144f2564L, 2) == null) + " " + g.meta());
        TcHw.on = g;
        System.out.println("header " + TcHw.header().replace("\n", "|"));
    }
}

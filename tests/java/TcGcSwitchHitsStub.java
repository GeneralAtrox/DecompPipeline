// TcSwitchHits and oracle_summary.json "switch_hits" (tests/test_gc_switch_hits.py), no Ghidra program: two kept runs
// through the bctr at 80001018 (cases 8000101c twice, 8000102c once) in TcPrepare.summary. Each case prints "NAME: ...".
import java.util.*;

public class TcGcSwitchHitsStub {
    public static void main(String[] args) {
        System.out.println("bcctr: " + TcSwitchHits.isBcctr(0x4E800420L) + " " + TcSwitchHits.isBcctr(0x4E800421L) + " "
            + TcSwitchHits.isBcctr(0x4D820420L) + " " + TcSwitchHits.isBcctr(0x4E800020L));
        List<long[]> blocks = List.of(new long[] {0x80001000L, 0x80001018L});
        TcPrepare p = new TcPrepare();
        p.switchHits.add(List.of(new long[] {0x80001018L, 0x8000101cL}, new long[] {0x80001018L, 0x8000102cL}));
        p.switchHits.add(List.of(new long[] {0x80001018L, 0x8000101cL}));
        String s = p.summary(blocks, 0);
        System.out.println("summary: " + s.substring(s.indexOf("\"switch_hits\""), s.indexOf("\"hints\"")));
        System.out.println("none: " + new TcPrepare().summary(blocks, 0).contains("switch_hits"));
    }
}

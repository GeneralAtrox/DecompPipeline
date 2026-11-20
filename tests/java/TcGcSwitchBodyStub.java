// TcSwitchBody (tests/test_gc_switch_body.py) on a made-up function, no Ghidra program. Body [1000, 101c) and
// [1060, 1064) at 0x80001000: cmplwi r30,2; bgt 1060; lis r3,0x8000; addi r3,r3,0x2000; lwzx r0,r3,r29; mtctr r0;
// bctr; then three cases in the gap (101c: li r3,1; b 1060. 1024: li r3,2; b 1060. 102c: bl 80005000; b 1060), and
// blr at 1060. The table at 0x80002000 holds the three case addresses. Each case prints "NAME: <ranges>".
import java.util.*;

public class TcGcSwitchBodyStub {
    static Map<Long, Long> image() {
        Map<Long, Long> m = new HashMap<>();
        long[] code = {0x281E0002L, 0x4181005CL, 0x3C608000L, 0x38632000L, 0x7C03E82EL, 0x7C0903A6L, 0x4E800420L,
            0x38600001L, 0x48000040L, 0x38600002L, 0x48000038L, 0x48003FD5L, 0x48000030L};
        for (int i = 0; i < code.length; i++) m.put(0x80001000L + 4 * i, code[i]);
        for (long a = 0x80001034L; a < 0x80001060L; a += 4) m.put(a, 0x60000000L);  // nop
        m.put(0x80001060L, 0x4E800020L);
        long[] table = {0x8000101cL, 0x80001024L, 0x8000102cL};
        for (int i = 0; i < table.length; i++) m.put(0x80002000L + 4 * i, table[i]);
        return m;
    }

    static String run(Map<Long, Long> m, List<long[]> body, java.util.function.LongPredicate taken) {
        List<long[]> ext = TcSwitchBody.extend(m::get, body, taken);
        return TcSwitchBody.json(ext);
    }

    public static void main(String[] args) {
        List<long[]> body = List.of(new long[] {0x80001000L, 0x8000101cL}, new long[] {0x80001060L, 0x80001064L});
        Map<Long, Long> m = image();
        System.out.println("ext: " + run(m, body, a -> false));
        System.out.println("taken: " + run(m, body, a -> a == 0x80001024L));
        System.out.println("whole: " + run(m, List.of(new long[] {0x80001000L, 0x80001064L}), a -> false));
        Map<Long, Long> nb = image();
        nb.put(0x80001000L, 0x60000000L);  // no cmplwi: no case count
        System.out.println("no_bound: " + run(nb, body, a -> false));
        Map<Long, Long> uk = image();
        uk.put(0x80001010L, 0x10000000L);  // a word whose writes are unknown before the mtctr: no table
        uk.put(0x8000100cL, 0x7C03E82EL);
        uk.put(0x80001008L, 0x38632000L);
        uk.put(0x80001004L, 0x3C608000L);
        uk.put(0x80001000L, 0x281E0002L);
        System.out.println("unknown: " + run(uk, body, a -> false));
        Map<Long, Long> far = image();
        far.put(0x80002008L, 0x80009000L);  // a case outside the body's hull
        System.out.println("hull: " + run(far, body, a -> false));
        System.out.println("dest: " + Long.toHexString(TcSwitchBody.dest(0x3C608000L)) + " "
            + Long.toHexString(TcSwitchBody.dest(0x7C03E82EL)) + " " + TcSwitchBody.dest(0x10000000L));
    }
}

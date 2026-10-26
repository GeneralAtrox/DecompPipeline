// slw / srw (tests/test_gc_shift.py): TcFixups.shift against the 750CL's rule and Ghidra's own p-code shifts (the
// Gekko SLEIGH's A = S << B and A = S >> B, 4-byte INT_LEFT and INT_RIGHT).
//
// usage: java TcShiftStub N SEED   prints "key count" lines, up to 5 "example key ..." lines per key, and "case" lines:
//   tried           (op, rS, rB) triples tried (random words, every count 0..255, counts with high bits set)
//   fix_wrong       TcFixups.shift differs from the 750CL's result (rB bit 0x20 set: 0; else a shift by rB & 0x1f)
//   differ          Ghidra's result differs from the 750CL's (the steps the fixup corrects)
//   differ_outside  ... for an rB that is not >= 64 with bit 0x20 clear (the fixup would change a right result)
//   case NAME GHIDRA FIX   fixed examples
import ghidra.pcode.opbehavior.OpBehaviorIntLeft;
import ghidra.pcode.opbehavior.OpBehaviorIntRight;
import java.util.*;

public class TcShiftStub {
    static final OpBehaviorIntLeft LEFT = new OpBehaviorIntLeft();
    static final OpBehaviorIntRight RIGHT = new OpBehaviorIntRight();
    static final Map<String, Integer> count = new TreeMap<>();
    static final Map<String, List<String>> ex = new TreeMap<>();

    static void note(String key, String what) {
        count.merge(key, 1, Integer::sum);
        List<String> l = ex.computeIfAbsent(key, k -> new ArrayList<>());
        if (l.size() < 5) l.add(what);
    }

    static long ghidra(String op, long s, long b) {
        return op.startsWith("slw") ? LEFT.evaluateBinary(4, 4, s, b) : RIGHT.evaluateBinary(4, 4, s, b);
    }

    // The 750CL (Book I 3.3.13; Dolphin's slwx / srwx): bit 0x20 of rB gives 0, else a shift by rB's low 5 bits.
    static long hw(String op, long s, long b) {
        if ((b & 0x20) != 0) return 0;
        int n = (int) (b & 0x1f);
        return op.startsWith("slw") ? (s << n) & 0xffffffffL : s >>> n;
    }

    static void one(String op, long s, long b) {
        count.merge("tried", 1, Integer::sum);
        long g = ghidra(op, s, b), h = hw(op, s, b), f = TcFixups.shift(op, s, b);
        String what = String.format("%s %08x %08x ghidra %08x hw %08x fix %08x", op, s, b, g, h, f);
        if (f != h) note("fix_wrong", what);
        if (g != h) {
            note("differ", what);
            if (!(b >= 64 && (b & 0x20) == 0)) note("differ_outside", what);
        }
    }

    public static void main(String[] a) {
        int n = Integer.parseInt(a[0]);
        Random rnd = new Random(Long.parseLong(a[1]));
        String[] ops = {"slw", "srw", "slw.", "srw."};
        long[] edge = {0, 1, 0x7fffffffL, 0x80000000L, 0xffffffffL, 0x12345678L};
        for (String op : ops) {
            for (long s : edge) for (long b = 0; b < 256; b++) one(op, s, b);
            for (long s : edge) for (long b = 0xffffff00L; b <= 0xffffffffL; b++) one(op, s, b);
            for (int i = 0; i < n; i++) {
                long s = rnd.nextInt() & 0xffffffffL, b = rnd.nextInt() & 0xffffffffL;
                one(op, s, b);
                one(op, s, b & 0xff);
                one(op, s, b & 0x3f);
            }
        }
        for (Map.Entry<String, Integer> e : count.entrySet()) System.out.println(e.getKey() + " " + e.getValue());
        for (Map.Entry<String, List<String>> e : ex.entrySet()) {
            for (String w : e.getValue()) System.out.println("example " + e.getKey() + " " + w);
        }
        long[][] cases = {{1, 0x41}, {0x80000000L, 0xffffffc1L}, {0x12345678L, 0x20}, {0x12345678L, 0x1f},
                          {0xffffffffL, 0xffffffffL}, {0xffffffffL, 0x5f}};
        for (String op : new String[] {"slw", "srw"}) {
            for (long[] c : cases) {
                System.out.printf("case %s_%x_%x %08x %08x%n", op, c[0], c[1], ghidra(op, c[0], c[1]),
                                  TcFixups.shift(op, c[0], c[1]));
            }
        }
    }
}

// The store-queue window and QACR rules on stub data (tests/test_p4_window.py): TcSq.Window and TcExit.p4, no Ghidra
// application and no program.
//
// usage: java TcP4Stub CASES      CASES lines (hex; # starts a comment):
//   P LO HI                 prints "P LO HI: true|false", whether TcExit logs a written P4 range (TcExit.p4)
//   Q NAME STEP...          prints "Q NAME: rA rB ...", the registers holding 0xe0000000|x after the steps
//                           (TcSq.Window): WORD (an instruction word), WORD=POOL (a PC-relative mov.l loading
//                           POOL), WORD>N (an instruction Window does not know, writing rN), or "call" (a call's
//                           delay slot ran)
//   B WORD...               prints "B WORD: N" per word, the register a store writes through (TcSq.Window.storeBase)
//   H REG NAME              prints "H NAME: HEADER META", a TcSq's vectors.txt line S and meta.json sq_window, after
//                           its draw and begin on r8..r14 = 8c100000 (REG -1: a built pointer, nothing drawn)
//   C CODE P [A+N...]       prints "C P A+N...: REST0 REST1", TcSq.codeRest of both queues ("-": none) with the code
//                           ranges of file CODE, after begin with r8 = P (a window address) and N bytes stored at A
//   M NAME R4 SPEC          prints "M NAME: BLOCK0 BLOCK1 | HEADER META": Toy Commander 8c0702b0's sends (TcSq.send)
//                           with r14 = e0100440 and RAM byte A = A & 0xff: 16 bytes stored at r14 (0xaa), a mocked
//                           call to 8c074f18 with r4 = R4 storing SPEC (TcSq.callee: "10-17,20-2f", "?" loose, "-"
//                           none), 4 bytes stored at r14 + 0x20 (0xbb), then pref r14 and pref r14 + 0x20
import java.nio.file.*;

public class TcP4Stub {
    static long h(String s) {
        return Long.parseLong(s, 16);
    }

    public static void main(String[] args) throws Exception {
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.trim().split("\\s+");
            if (t[0].isEmpty() || t[0].startsWith("#")) continue;
            if (t[0].equals("P")) {
                System.out.println("P " + t[1] + " " + t[2] + ": " + TcExit.p4(h(t[1]), h(t[2])));
            } else if (t[0].equals("Q")) {
                TcSq.Window w = new TcSq.Window();
                for (int i = 2; i < t.length; i++) {
                    String s = t[i];
                    if (s.equals("call")) w.called();
                    else if (s.contains("=")) w.step((int) h(s.split("=")[0]), h(s.split("=")[1]), -1);
                    else if (s.contains(">")) w.step((int) h(s.split(">")[0]), null, Integer.parseInt(s.split(">")[1]));
                    else w.step((int) h(s), null, -1);
                }
                StringBuilder sb = new StringBuilder("Q " + t[1] + ":");
                for (int r = 0; r < 16; r++) if (w.holds("r" + r)) sb.append(" r").append(r);
                System.out.println(sb);
            } else if (t[0].equals("B")) {
                for (int i = 1; i < t.length; i++) {
                    System.out.println("B " + t[i] + ": " + TcSq.Window.storeBase((int) h(t[i])));
                }
            } else if (t[0].equals("H")) {
                TcSq q = new TcSq(Integer.parseInt(t[1]), t[2], new java.util.HashMap<>());
                long[] gi = {0x8c100000L, 0x8c100000L, 0x8c100000L, 0x8c100000L, 0x8c100000L, 0x8c100000L, 0x8c100000L};
                q.draw(gi, 1, 0, 0);
                q.begin(gi);
                System.out.println("H " + t[2] + ": " + q.header().trim() + " " + q.meta());
            } else if (t[0].equals("M")) {
                TcSq q = new TcSq(14, "r14", new java.util.HashMap<>());
                long[] spec = null;
                if (!t[3].equals("?")) {
                    spec = new long[2];
                    for (String part : t[3].equals("-") ? new String[0] : t[3].split(",")) {
                        String[] lh = part.split("-");
                        for (long j = h(lh[0]); j <= h(lh[lh.length - 1]); j++) spec[(int) (j >> 6)] |= 1L << (j & 63);
                    }
                }
                if (!t[3].equals("-")) q.callee.put(0x8c074f18L, new long[][] {spec, new long[2], new long[2], new long[2]});
                q.begin(new long[] {0, 0, 0, 0, 0, 0, 0xe0100440L});
                byte[] aa = new byte[16], bb = new byte[4];
                java.util.Arrays.fill(aa, (byte) 0xaa);
                java.util.Arrays.fill(bb, (byte) 0xbb);
                q.store(0xe0100440L, 16, aa);
                q.callee(0x8c074f18L, new long[] {h(t[2]), 0, 0, 0});
                q.store(0xe0100460L, 4, bb);
                StringBuilder sb = new StringBuilder("M " + t[1] + ":");
                for (long a : new long[] {0xe0100440L, 0xe0100460L}) {
                    sb.append(' ');
                    for (byte x : q.send(TcSq.queue(a), TcSq.ram(a & ~0x1fL), m -> (byte) m)) sb.append(String.format("%02x", x & 0xff));
                }
                System.out.println(sb + " | " + q.header().trim() + " " + q.meta().replace('\n', ' '));
            } else if (t[0].equals("C")) {
                TcFacts f = TcFacts.defaults();
                f.loadCode(t[1]);
                TcSq q = new TcSq(8, "r8", new java.util.HashMap<>());
                q.begin(new long[] {h(t[2]), 0, 0, 0, 0, 0, 0});
                for (int i = 3; i < t.length; i++) {
                    String[] s = t[i].split("\\+");
                    q.store(h(s[0]), Integer.parseInt(s[1]), new byte[Integer.parseInt(s[1])]);
                }
                String key = String.join(" ", java.util.Arrays.copyOfRange(t, 2, t.length));
                StringBuilder sb = new StringBuilder("C " + key + ":");
                for (int k = 0; k < 2; k++) {
                    long c = q.codeRest(f, k, -1);
                    sb.append(' ').append(c < 0 ? "-" : Long.toHexString(c));
                }
                System.out.println(sb);
            }
        }
    }
}

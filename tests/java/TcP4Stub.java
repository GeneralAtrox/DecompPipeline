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
                    long c = q.codeRest(f, k);
                    sb.append(' ').append(c < 0 ? "-" : Long.toHexString(c));
                }
                System.out.println(sb);
            }
        }
    }
}

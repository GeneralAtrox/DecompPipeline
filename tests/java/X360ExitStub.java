// tests/test_x360_judge_exit.py's view of the Xbox 360 oracle's exit state (TcExit) and its facts line (TcFacts
// "exit_state"): no Ghidra application, no program.
//
// usage: java X360ExitStub FACTS
//   prints "facts 0|1" (FACTS' exitState), "default 0|1" (no facts file), the N G K lines of the seeds of entry
//   82010000 vector 0, "seeds same|differ" for vector 0 taken twice and vectors 0 and 1, and TcExit.lines of a
//   made-up exit state: r_i = i, f_i = (double) i, CR fields 0 0 9 8 6 0 0 1, CA 1 SO 0 OV 1, CTR 8, LR the sentinel,
//   v3 = 10 10 10 11, a stack write of 00001234 at 700fdffc.
import java.util.*;

public class X360ExitStub {
    public static void main(String[] args) throws Exception {
        System.out.println("facts " + (TcFacts.load(args[0]).exitState ? 1 : 0));
        System.out.println("default " + (TcFacts.load("-").exitState ? 1 : 0));
        TcExit.Seed s = new TcExit.Seed(0x82010000L, 0), t = new TcExit.Seed(0x82010000L, 0);
        TcExit.Seed u = new TcExit.Seed(0x82010000L, 1);
        System.out.print(s.vectorLines());
        System.out.println("seeds " + (s.vectorLines().equals(t.vectorLines()) ? "same" : "differ") + " "
            + (s.vectorLines().equals(u.vectorLines()) ? "same" : "differ"));
        long[] g = new long[32], f = new long[32];
        for (int i = 0; i < 32; i++) {
            g[i] = i;
            f[i] = Double.doubleToRawLongBits(i);
        }
        int[][] v = new int[128][4];
        v[3] = new int[] {10, 10, 10, 11};
        List<Object[]> stack = new ArrayList<>();
        stack.add(new Object[] {0x700fdffcL, new byte[] {0, 0, 0x12, 0x34}});
        System.out.print(TcExit.lines(g, f, new int[] {0, 0, 9, 8, 6, 0, 0, 1}, new int[] {1, 0, 1}, 8, TcBase.SENTINEL, 0,
                                      v, stack));
    }
}

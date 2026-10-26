// The oracle's work budgets on stub data (tests/test_oracle_budget.py): TcBudget's rule and TcFacts' budget facts,
// no Ghidra application and no program.
//
// usage: java TcBudgetStub CASES      CASES lines (decimal; # starts a comment):
//   F FILE                        prints "F FILE: BUDGET RESCUE REACH" (TcFacts.load, then TcBudget.of of its
//                                 oracle_budget_s, oracle_rescue_s and reach seconds) or "F FILE: refused MESSAGE"
//   W STEPS RUNS                  prints "W STEPS RUNS: WORK" (TcBudget.work)
//   O WORK END                    prints "O WORK END: true|false" (TcBudget.over)
//   R WORK START LEFT VALID END   prints "R WORK START LEFT VALID END: true|false" (TcBudget.wontFinish)
import java.nio.file.*;

public class TcBudgetStub {
    public static void main(String[] args) throws Exception {
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.trim().split("\\s+");
            if (t[0].isEmpty() || t[0].startsWith("#")) continue;
            long[] n = new long[t.length];
            for (int i = 1; i < t.length; i++) n[i] = t[0].equals("F") ? 0 : Long.parseLong(t[i]);
            String out;
            if (t[0].equals("F")) {
                try {
                    TcFacts f = TcFacts.load(t[1]);
                    out = TcBudget.of(f, f.oracleBudgetS) + " " + TcBudget.of(f, f.oracleRescueS) + " "
                        + TcBudget.of(f, f.reachS);
                } catch (RuntimeException e) {
                    out = "refused " + e.getMessage();
                }
            } else if (t[0].equals("W")) {
                out = String.valueOf(TcBudget.work(n[1], n[2]));
            } else if (t[0].equals("O")) {
                out = String.valueOf(TcBudget.over(n[1], n[2]));
            } else {
                out = String.valueOf(TcBudget.wontFinish(n[1], n[2], n[3], (int) n[4], n[5]));
            }
            System.out.println(line.trim() + ": " + out);
        }
    }
}

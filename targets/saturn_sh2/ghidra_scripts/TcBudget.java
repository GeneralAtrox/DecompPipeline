// The oracle's budgets (r15): work, never the clock. Work is the emulated steps plus RUN_WORK per run (TcOracle's
// workSteps and workRuns); a budget of N seconds is N times TcFacts.workPerS ("oracle_work_per_s": the work per
// second measured on a calibration sample). Every stop decision of vector generation compares work (TcVectors' budget
// and its step-limit fail-fast, TcRescue's overtime pass, TcReach's search), so a harness is a function of its
// inputs: the same bytes on a faster or slower machine and under any load (TC 8c062050 was rescued or not by how long
// its first pass took). Ghidra's timeout (config ghidra_timeout_s) is the only clock left: past it the prepare fails
// as an error, never as another harness. Not a script itself.
final class TcBudget {
    static final long RUN_WORK = 1000;  // one run's setup (emulator, memory bank, entry state) in steps: ~700 measured

    private TcBudget() {
    }

    static long work(long steps, long runs) {
        return steps + RUN_WORK * runs;
    }

    // A budget of `seconds` (0: none).
    static long of(TcFacts f, long seconds) {
        return seconds <= 0 ? 0 : seconds * f.workPerS;
    }

    // Past a budget's end (end 0: none).
    static boolean over(long work, long end) {
        return end > 0 && work > end;
    }

    // A pass that started at passStart and made `valid` vectors so far would end past `end` at its rate so far, with
    // `left` vectors still wanted (end 0: never).
    static boolean wontFinish(long work, long passStart, long left, int valid, long end) {
        return end > 0 && work + (work - passStart) * left / valid > end;
    }
}

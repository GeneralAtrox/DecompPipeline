// A run's decisions that need no program (not a script itself): how TcOracle reads a jump out of the body.
import ghidra.program.model.symbol.FlowType;

final class TcRun {
    private TcRun() {
    }

    // A jump out of the body is a tail call, mocked like a branch to a function's entry, when
    //  - it is computed and Ghidra knows no target inside the body (`bctr` through a pointer read from memory: the
    //    target is an input, so a vector's random one must end the run, not refuse the function, 8226e148; the C calls
    //    it the same way). A jump table's targets are in the body, and one that leaves it stays refused;
    //  - or it is direct, to code in no function: the thunks between the image's .pdata ranges have no function of their
    //    own (821ba310's `b 0x821b9330`, 821ba330's `b 0x821b9328`).
    // A direct branch into the middle of another function stays refused, and so does anything but a jump: a fall-through
    // out of the body means the body is incomplete.
    static boolean tailJump(FlowType flow, boolean flowsIntoBody, boolean targetInFunction) {
        if (!flow.isJump()) return false;
        return flow.isComputed() ? !flowsIntoBody : !targetInFunction;
    }

    // The steps a run may take. A function whose size or count inputs can be huge (memset 822bae30, 82283fc8) spent the
    // oracle's whole deadline on runs that hit the step limit, each worth seconds, and was refused out of time with 48
    // valid dev vectors in hand. Once a run of this function hit the limit and MIN_VALID runs were valid, later runs get
    // RATIO times the longest valid run (at least FLOOR): an input that much longer is one that would hit the limit too.
    // A function that never hits the limit is untouched, and the rule uses no clock, so the vectors stay reproducible.
    static final class StepBudget {
        static final int MIN_VALID = 8, RATIO = 16, FLOOR = 20000;
        final int full;
        int limit, valid, longest;
        boolean limited;

        StepBudget(int full) {
            this.full = full;
            limit = full;
        }

        void note(boolean ok, boolean stepLimited, int steps) {
            if (ok) {
                valid++;
                longest = Math.max(longest, steps);
            }
            limited |= stepLimited;
            if (limited && valid >= MIN_VALID) {
                limit = (int) Math.min(full, Math.max(FLOOR, (long) RATIO * longest));
            }
        }
    }

    // A step's loads go in the run's load log unless it is in a frame helper (TcOracle.runOracle).
    static boolean logsLoads(boolean inInlineHelper) {
        return !inInlineHelper;
    }

    // The PC left the body with no instruction stepped since a mocked call (prev < 0, calls > 0), at the word after the
    // body's last: that call returned past the body's end.
    static boolean pastEnd(long prev, int calls, boolean wordBeforeInBody) {
        return prev < 0 && calls > 0 && wordBeforeInBody;
    }
}

// Loop-count repair for the overtime rescue (TcVectors.makeVectors). Not a script itself; used by TcOracle and
// TcVectors.
//
// A function whose loop count comes from the inputs (a count word in a generated chunk, Toy Commander 8c01ad3a
// `cmp/hs r2,r6` against @(4,r4); a count register decremented to zero, 8c039990 `cmp/pl r4`) runs most random
// vectors to the step limit, each costing seconds, and used to run out of time and be refused. When the first pass
// runs out of time (and only then: a function the first pass serves never sees this), TcVectors makes a rescue pass
// with a lower step limit in which a run that hits it gets its loop counts cut, as a fault gets its pointer repaired:
// TcOracle watches the compares of the last WINDOW steps (observe) and the run's first and last loads; a loaded input
// word (inside one of the vector's memory patches) or an entry register r4..r7 is a count when its value equals an
// operand a compare kept constant (the bound), or lies within the steps run of an operand the loop moved, on the side
// it moved away from (a counter that started at that value). Each such count becomes a small value (1..8), kept so
// through the vector's later pointer repairs (Cuts), and the vector runs again. A count the code transforms before
// comparing (scaled, shifted) is not found: the function is then refused as before, with the loop's compare
// addresses in the reason, with one exception (zeroCut): a `dt` counter the window saw coming down from near 2^32
// was a count of 0 when its loop began, and wrapped (Toy Commander's Duck TM2X block handlers, 8c09d820: r5 = the
// sum of two input bytes >> 1, then `dt r5` to zero). On real data such a count is never 0. Zero counts come from
// zero bytes, so when nothing else was cut, the run's first byte and halfword input loads that read 0 or 1 get 2..9
// (in RAM below the stack; bytes no patch holds get a patch of their own), kept as cuts.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.listing.*;
import java.math.BigInteger;
import java.util.*;
import java.util.regex.*;

public class TcCount {
    static final int WINDOW = 4000;     // steps before the limit in which compares are watched
    static final int SMALL = 8;         // a cut count is 1..SMALL
    static final Pattern CMP = Pattern.compile("^_?(cmp/(eq|hs|ge|hi|gt|pl|pz|str)|tst|dt)\\s+(.*)$");
    static final Pattern REG = Pattern.compile("\\br(\\d+)\\b");

    // compare address -> register -> {first value, last value} over the window
    final Map<Long, Map<Integer, long[]>> ops = new TreeMap<>();
    final Set<Long> dts = new TreeSet<>();  // the watched compares that are dt

    void observe(EmulatorHelper emu, List<Instruction> step) {
        for (Instruction in : step) {
            Matcher m = CMP.matcher(in.toString().toLowerCase());
            if (!m.matches() || m.group(3).contains("#")) continue;
            Matcher r = REG.matcher(m.group(3));
            Map<Integer, long[]> at = ops.computeIfAbsent(in.getAddress().getOffset(), k -> new TreeMap<>());
            if (m.group(1).equals("dt")) dts.add(in.getAddress().getOffset());
            while (r.find()) {
                int n = Integer.parseInt(r.group(1));
                long v = new BigInteger(1, emu.readRegister("r" + n).toByteArray()).longValue() & 0xffffffffL;
                long[] fl = at.get(n);
                if (fl == null) at.put(n, new long[] {v, v});
                else fl[1] = v;
            }
        }
    }

    // The loop's compare addresses (for a refusal reason).
    String sites() {
        StringBuilder b = new StringBuilder();
        for (long a : ops.keySet()) b.append(b.length() == 0 ? "" : ", ").append(String.format("%08x", a));
        return b.toString();
    }

    // Whether a value (as `bits` wide, unsigned) can be the count of the watched loop after `steps` steps.
    boolean isCount(long value, int bits, long steps) {
        long mask = bits >= 32 ? 0xffffffffL : (1L << bits) - 1;
        if ((value & mask) <= SMALL) return false;
        for (Map<Integer, long[]> at : ops.values()) {
            for (long[] fl : at.values()) {
                long first = fl[0] & mask, last = fl[1] & mask, v = value & mask;
                if (first == last) {
                    if (v == last) return true;                        // the bound a counter runs to
                } else if (last < first) {
                    if (v >= first && v - last <= steps) return true;  // a counter coming down from v
                } else if (v <= first && last - v <= steps) {
                    return true;                                       // a counter going up from v
                }
            }
        }
        return false;
    }

    // The counts cut in one vector, kept across all its repairs: a later pointer repair (TcInputs.repairVec takes the
    // first register or loaded word just below the fault address) must not turn a cut count back into a pointer and
    // its count huge again (Toy Commander 8c050fe8: a count of 1..8 in r4 lies just below a fault through r5 = 3).
    static class Cuts {
        static final long HIDDEN = 0x7ffffff0L; // a register value no fault address lies just above
        final Map<Integer, Long> regs = new TreeMap<>();
        final Map<Long, long[]> words = new TreeMap<>(); // address -> {value, size}

        // Before a pointer repair: the cut registers are out of its reach.
        void hide(TcInputs.Vec v) {
            for (int i : regs.keySet()) v.r[i] = HIDDEN;
        }

        // After it: the cut counts are back (a word it made a pointer is a count again, and stays marked as poked).
        void restore(TcInputs.Vec v) {
            for (Map.Entry<Integer, Long> e : regs.entrySet()) v.r[e.getKey()] = e.getValue();
            for (Map.Entry<Long, long[]> e : words.entrySet()) put(v, e.getKey(), (int) e.getValue()[1], e.getValue()[0]);
        }
    }

    // The latest patch of v holding [addr, addr + size), as {patch index, offset}, or null.
    static int[] patchAt(TcInputs.Vec v, long addr, int size) {
        for (int i = v.patchAddr.size() - 1; i >= 0; i--) {
            long a = v.patchAddr.get(i);
            if (addr >= a && addr + size <= a + v.patchData.get(i).length) return new int[] {i, (int) (addr - a)};
        }
        return null;
    }

    static void put(TcInputs.Vec v, long addr, int size, long value) {
        int[] p = patchAt(v, addr, size);
        if (p == null) return;
        byte[] d = v.patchData.get(p[0]);
        for (int k = 0; k < size; k++) d[p[1] + k] = (byte) (value >> (8 * k));
    }

    // Cuts the counts of v (TcInputs.Vec) the failed run r used, recording them in cuts. Returns whether anything
    // changed.
    boolean repair(TcInputs.Vec v, TcOracle.Result r, long steps, Random h, Cuts cuts) {
        boolean any = false;
        Set<Long> seen = new HashSet<>();
        List<long[]> loads = new ArrayList<>(r.firstLoads);
        loads.addAll(r.loads);
        for (long[] ld : loads) {
            long addr = ld[1];
            int size = (int) ld[2];
            if ((size != 2 && size != 4) || !seen.add(addr)) continue;
            int[] p = patchAt(v, addr, size); // the latest patch holding it wins
            if (p == null) continue;
            byte[] d = v.patchData.get(p[0]);
            long word = 0;
            for (int k = size - 1; k >= 0; k--) word = word << 8 | (d[p[1] + k] & 0xff);
            if (!isCount(word, 8 * size, steps)) continue;
            long c = 1 + h.nextInt(SMALL);
            put(v, addr, size, c);
            cuts.words.put(addr, new long[] {c, size});
            any = true;
        }
        for (int i = 0; i < v.r.length; i++) {
            if (!isCount(v.r[i], 32, steps)) continue;
            v.r[i] = 1 + h.nextInt(SMALL);
            cuts.regs.put(i, v.r[i]);
            any = true;
        }
        return any || (wrapped() && zeroCut(v, r, h, cuts));
    }

    // A dt counter the window saw coming down from near 2^32: its loop began at a count of 0 and wrapped.
    boolean wrapped() {
        for (long a : dts) {
            for (long[] fl : ops.get(a).values()) if (fl[0] >= 0x80000000L && fl[1] < fl[0]) return true;
        }
        return false;
    }

    // The zero counts' bytes (class comment): the run's first byte and halfword input loads reading 0 or 1 get 2..9.
    static boolean zeroCut(TcInputs.Vec v, TcOracle.Result r, Random h, Cuts cuts) {
        boolean any = false;
        for (long[] ld : r.firstLoads) {
            long addr = ld[1];
            int size = (int) ld[2];
            if ((size != 1 && size != 2) || cuts.words.containsKey(addr) || addr < TcBase.RAM_LO
                || addr + size > TcBase.STACK_LO) continue;
            int[] p = patchAt(v, addr, size);
            if ((ld[3] & (size == 1 ? 0xffL : 0xffffL)) > 1) continue;  // what the run read there
            if (p == null) {  // not an input yet: give the bytes a patch of their own
                v.patchAddr.add(addr);
                v.patchData.add(new byte[size]);
            }
            long c = 2 + h.nextInt(SMALL);
            put(v, addr, size, c);
            cuts.words.put(addr, new long[] {c, size});
            any = true;
        }
        return any;
    }
}

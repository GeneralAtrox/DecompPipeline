// The results of the mocked calls after the first 16 (r15). A vector scripted 16 call results (Vec.rets) and every
// later call returned 0, so a branch on a later call's result was never taken: TC 8c0117f4 Camera_UpdateKind10 calls
// Entity_HasCamView 17th, and the whole camera-view half (38 of 55 blocks) went untested while a wrong readable C
// passed 96/96. Now calls up to TcOracle.RETS get a result too (TcOracle.entryLate; vectors.txt line C carries them
// after Vec.rets, tc_rt.c's TCRT_RETS). Not a script itself; TcVectors draws them, TcOracle returns them.
//
// They come from their own generator, so the vector's other draws stay as they were, and line C carries them only
// as far as the run made calls: a vector with 16 calls or fewer is written byte for byte as before (only harnesses
// with a 17th call change: tools/selective_restamp.py --change late). No random words: a pointer result here has no
// repair (TcRepair, TcProvenance and TcCalls work on Vec.rets), so each is a small number or a scratch pointer.
import java.util.*;

final class TcLate {
    private TcLate() {
    }

    // range gbr: one vector try's GBR at entry; TcReach.copy shares it.
    static final Map<TcInputs.Vec, Long> GBRS = Collections.synchronizedMap(new WeakHashMap<>());

    static void copy(TcInputs.Vec from, TcInputs.Vec to) {
        Long gb = GBRS.get(from);
        if (gb != null) GBRS.put(to, gb);
    }

    // hints without the "range gbr LO HI" line (the same list when it has none), which sets GBRS for v. The Dreamcast
    // target's TcLate.regs (51cc9c6) also takes r0..r3 and r8..r14; the Saturn one takes GBR only (Robo Pit's globals
    // are read through the GBR its boot code sets once). A malformed line is refused like any bad hint line.
    static List<String> regs(TcInputs.Vec v, List<String> hints, long seed, int id, int attempt) {
        List<String> rest = null;
        for (int i = 0; i < hints.size(); i++) {
            String[] t = hints.get(i).trim().split("\\s+");
            if (!(t[0].equals("range") && t.length > 1 && t[1].equals("gbr"))) {
                if (rest != null) rest.add(hints.get(i));
                continue;
            }
            if (rest == null) rest = new ArrayList<>(hints.subList(0, i));
            long lo, hi;
            try {
                if (t.length != 4) throw new NumberFormatException();
                lo = num(t[2]);
                hi = num(t[3]);
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException("bad register range line (range gbr LO HI): " + hints.get(i).trim());
            }
            if (lo > hi || lo < -0x80000000L || hi > 0xffffffffL) {
                throw new IllegalArgumentException("bad register range (LO <= HI in 32 bits): " + hints.get(i).trim());
            }
            Random h = new Random(seed * 0x9e3779b97f4a7c15L + id * 0x632be59bd9b4e019L + attempt * 0x85ebca6bL + 0x3b);
            GBRS.put(v, (lo + (long) (h.nextDouble() * (hi - lo + 1))) & 0xffffffffL);
        }
        return rest == null ? hints : rest;
    }

    // GBR at entry for v (range gbr; TcOracle.setEntry); without the line the emulator's own start value, 0, stays.
    static void enter(ghidra.app.emulator.EmulatorHelper emu, TcInputs.Vec v) {
        Long x = GBRS.get(v);
        if (x != null) emu.writeRegister("GBR", java.math.BigInteger.valueOf(x));
    }

    // vectors.txt line b (GBR at entry), only for a vector with a range gbr line.
    static String gbrLine(TcInputs.Vec v) {
        Long x = GBRS.get(v);
        return x == null ? "" : "\nb " + TcBase.hex(x);
    }

    static long num(String s) {
        return s.startsWith("0x") ? Long.parseLong(s.substring(2), 16) : Long.parseLong(s);
    }

    // The results for calls `first` .. TcOracle.RETS - 1 of one vector try.
    static long[] draw(TcFacts facts, long seed, int id, int attempt, int first) {
        Random h = new Random(seed * 0xd6e8feb86659fd93L + id * 0x632be59bd9b4e019L + attempt * 0x85ebca6bL);
        long[] out = new long[Math.max(0, TcOracle.RETS - first)];
        long words = (facts.scrHi - facts.scrLo) / 4;
        for (int i = 0; i < out.length; i++) {
            int k = h.nextInt(10);
            out[i] = k < 5 ? h.nextInt(4) : k < 7 ? facts.scrLo + 4L * h.nextInt((int) words) : k < 9 ? 0 : 1;
        }
        return out;
    }

    // Line C's tail: the results of the `made` later calls the run made (none: empty).
    static String tail(long[] late, int made) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; late != null && i < Math.min(late.length, made); i++) b.append(' ').append(TcBase.hex(late[i]));
        return b.toString();
    }
}

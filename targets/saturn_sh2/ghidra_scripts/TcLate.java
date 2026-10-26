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
import java.util.Random;

final class TcLate {
    private TcLate() {
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

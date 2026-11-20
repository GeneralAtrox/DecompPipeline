// A block copy the store queues must count: project.json "sq_copy_callees" {"callee": {"rK": "lo-hi"}}. TcLive.Deltas.args
// walks a callee's stores at constant offsets, so a copy (its offset comes from a loop) is loose and counts as storing
// nothing; a pref then sends the block 0x40 back over what it copied (Spawn 8c058150 copies each vertex to the queue
// with 8c0d7b40, r4 the block: the score and rules panels never drew). The range is its bytes through rK: "0-3f" for a
// copy of 32 or 64 bytes, as a byte past one call's size belongs to the next store. Not a script itself; TcPrepare calls it.
import ghidra.program.model.listing.Function;
import java.util.List;

final class TcSqCopy {
    static void apply(TcSq sq, List<String[]> facts, List<Function> fs) {
        for (String[] w : facts)
            for (Function g : fs) {
                long t = g.getEntryPoint().getOffset();
                if (t != Long.parseLong(w[0], 16)) continue;
                long[][] a = sq.callee.computeIfAbsent(t, k -> new long[4][]);
                int lo = Integer.parseInt(w[2].split("-")[0], 16), hi = Integer.parseInt(w[2].split("-")[1], 16);
                long[] bits = new long[2];
                for (int j = lo; j <= hi; j++) bits[j >> 6] |= 1L << (j & 63);
                a[Integer.parseInt(w[1].substring(1)) - 4] = bits;
            }
    }
}

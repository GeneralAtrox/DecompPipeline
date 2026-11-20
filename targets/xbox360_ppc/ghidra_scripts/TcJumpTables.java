// The switch tables of the function under test that Ghidra's decompiler resolved, as rule R8's one exception (TcBank):
// MSVC puts a switch's table of targets in the code range right after the bctr, so its entries are instruction bytes
// to R8, and every vector that took a case above index 0 (the load sits 12 bytes before the table, inside TcBank's
// fetch window, only for index 0) faulted and was dropped (Too Human 82c6f098, 82ccf058, 82d89388, 82dcde50,
// 82e0a108). Not a script itself.
//
// The ranges come from TcPrepare's own decompile (HighFunction.getJumpTables with DecompInterface.toggleJumpLoads:
// each JumpTable's LoadTable {address, entry size, entries}), never from the translator. A table counts only when its
// switch is in the function and the whole table lies within the function's extent; a read is data only when it is one
// whole, aligned entry of such a table. Every other read of code stays R8. Without a resolved table nothing changes.
// The emulator still reads the image's real bytes, so a kept vector's expected results are the original's own.
import ghidra.program.model.pcode.HighFunction;
import ghidra.program.model.pcode.JumpTable;
import java.util.*;

public class TcJumpTables {
    static final long MAX_ENTRIES = 4096;  // the translator's bound too (translate.jump_table)

    final List<long[]> tables = new ArrayList<>();  // {first byte, end, entry size}

    // The load tables of hf's switches, for the function whose code spans [lo, hi).
    static TcJumpTables of(HighFunction hf, long lo, long hi) {
        TcJumpTables t = new TcJumpTables();
        JumpTable[] jts = hf == null ? null : hf.getJumpTables();
        if (jts == null) return t;
        for (JumpTable jt : jts) {
            long sw = jt.getSwitchAddress() == null ? -1 : jt.getSwitchAddress().getOffset();
            JumpTable.LoadTable[] lts = jt.getLoadTables();
            if (sw < lo || sw >= hi || lts == null) continue;
            for (JumpTable.LoadTable lt : lts) t.add(lt.getAddress().getOffset(), lt.getSize(), lt.getNum(), lo, hi);
        }
        return t;
    }

    // Adds the table of num entries of size bytes at at, when it lies whole within [lo, hi).
    void add(long at, long size, long num, long lo, long hi) {
        if (size <= 0 || size > 8 || num <= 0 || num > MAX_ENTRIES) return;
        long end = at + size * num;
        if (at < lo || end > hi) return;
        tables.add(new long[] {at, end, size});
    }

    // Is the read of size bytes at a one whole entry of a resolved table?
    boolean covers(long a, int size) {
        for (long[] t : tables) {
            if (a >= t[0] && a + size <= t[1] && size == t[2] && (a - t[0]) % t[2] == 0) return true;
        }
        return false;
    }

    @Override
    public String toString() {
        StringBuilder b = new StringBuilder();
        for (long[] t : tables) {
            b.append(b.length() > 0 ? " " : "").append(String.format("%08x+%dx%d", t[0], t[2], (t[1] - t[0]) / t[2]));
        }
        return b.length() > 0 ? b.toString() : "none";
    }
}

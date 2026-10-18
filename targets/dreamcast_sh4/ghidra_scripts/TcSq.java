// The SH-4 store queues, for a function that sends a runtime-state pointer's queue itself (`pref @rN`, rN one of
// r8..r14 in its state: Toy Commander's display-list code, r14 the TA write pointer). Not a script itself; TcPrepare
// finds the fact (of), TcVectors draws rN into the window (draw), TcFold and TcOracle model the queues.
//
// The window 0xe0000000..0xe3ffffff: with QACR0/1 at area 3, as the SDKs leave them, a store there fills store queue
// SQ[bit 5] at the address' bits 2..4, and `pref @a` sends that queue's 32 bytes to the RAM block at a's offset (the
// 16 MB of RAM repeat four times). The port maps the window straight onto RAM (port.h GUEST_SQ_BASE): the stores land
// at once and pref does nothing. The C runners map it the same way for these functions (vectors.txt header line S).
// One thing then differs, and the C must do it itself: a pref of a slot the code never stored to (the pref-skip
// `pref @r14; add #0x20,r14`) sends the queue's old contents, the block last sent through it (0x40 back in a stream).
// The oracle models it:
//   - a store in the window writes RAM and its queue's bytes (TcFold.setChunk);
//   - a mocked call that moved rN up through the window (TcLive) filled the blocks it passed, each through its queue;
//   - at entry the stream before rN is taken as contiguous: the queues hold the blocks 0x40 and 0x20 before it;
//   - pref @a sends a's queue: the bytes stored to it since, the rest from the block it last held (RAM, now).
// A stale queue then shows as a W mismatch at the skipped slot. Half the vectors draw rN in the window, the others
// keep their RAM pointer (the window off: the same harness as before for them).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import java.util.*;

final class TcSq {
    static final long LO = 0xe0000000L, HI = 0xe4000000L;
    final int reg;                      // N: the state register that may point into the window
    final Map<Long, String> prefs;      // the step that runs a pref (its own, or its branch's for a delay slot): register
    final long[] from = new long[2];    // per queue, the RAM block it last held, or -1
    final byte[][] buf = new byte[2][32];
    final boolean[][] stored = new boolean[2][32];

    TcSq(int reg, Map<Long, String> prefs) {
        this.reg = reg;
        this.prefs = prefs;
    }

    // The fact: a `pref @rN` in the body with rN in the function's runtime state (TcSig giState: r8 is bit 24), or null.
    static TcSq of(Listing listing, AddressSetView body, int giState) {
        Map<Long, String> prefs = new HashMap<>();
        int reg = -1;
        for (Instruction in : listing.getInstructions(body, true)) {
            if (!in.getMnemonicString().replaceFirst("^_", "").equals("pref")) continue;
            Object[] ops = in.getOpObjects(0);
            if (ops.length != 1 || !(ops[0] instanceof Register)) continue;
            String r = ((Register) ops[0]).getName();
            Instruction step = in.isInDelaySlot() ? listing.getInstructionBefore(in.getAddress()) : in;
            prefs.put((step != null ? step : in).getAddress().getOffset(), r);
            int n = r.matches("r([89]|1[0-4])") ? Integer.parseInt(r.substring(1)) : -1;
            if (n >= 0 && (giState & (1 << (16 + n))) != 0) reg = n;
        }
        return reg < 0 ? null : new TcSq(reg, prefs);
    }

    static boolean in(long a) {
        return a >= LO && a < HI;
    }

    static long ram(long a) {  // TcBase.fold for the window: its offset in RAM (four 16 MB views)
        return 0x8c000000L | (a & 0xffffffL);
    }

    static int queue(long a) {
        return (int) (a >> 5) & 1;
    }

    // TcVectors: rN in the window for half the vectors (one of its four views of the RAM block rN was drawn at).
    void draw(long[] gi, long seed, int id, int attempt) {
        if (gi == null) return;
        Random h = new Random(seed * 0x94d049bb133111ebL + id * 0xbf58476d1ce4e5b9L + attempt * 0x9e3779b97f4a7c15L);
        long p = gi[reg - 8];
        if (h.nextBoolean() && p >= TcBase.RAM_LO && p < TcBase.RAM_HI) {
            gi[reg - 8] = LO | (long) h.nextInt(4) << 24 | (p & 0xffffe0L);
        }
    }

    // A run's start (TcFold): empty queues, then the stream before rN as contiguous.
    void begin(long[] gi) {
        Arrays.fill(from, -1);
        for (boolean[] s : stored) Arrays.fill(s, false);
        long p = gi != null ? gi[reg - 8] & ~0x1fL : 0;
        if (!in(p)) return;
        filled(p - 0x40);
        filled(p - 0x20);
    }

    void filled(long b) {  // the block at b went out whole through its queue
        if (!in(b)) return;
        int k = queue(b);
        from[k] = ram(b & ~0x1fL);
        Arrays.fill(stored[k], false);
    }

    // TcFold: a store (unfolded address); the bytes in the window also fill their queue.
    void store(long off, int size, byte[] val) {
        for (int i = 0; i < size; i++) {
            long a = (off + i) & 0xffffffffL;
            if (!in(a)) continue;
            int k = queue(a), j = (int) (a & 31);
            buf[k][j] = val[i];
            stored[k][j] = true;
        }
    }

    // TcLive: a mocked callee moved rN from p up to q through the window: it filled the blocks between, in order.
    void called(long p, long q) {
        if (!in(p) || q <= p || q - p > 0x1000) return;
        for (long b = p & ~0x1fL; b < q; b += 0x20) filled(b);
    }

    // TcOracle, before the step at pca: a pref there (or in its delay slot) of a window address sends the queue.
    void step(TcOracle o, EmulatorHelper emu, Address pca) {
        String r = prefs.get(pca.getOffset());
        long a = r != null ? o.reg(emu, r) : 0;
        if (!in(a)) return;
        int k = queue(a);
        long to = ram(a & ~0x1fL);
        byte[] out = emu.readMemory(o.toAddr(from[k] >= 0 ? from[k] : to), 32);
        for (int j = 0; j < 32; j++) if (stored[k][j]) out[j] = buf[k][j];
        emu.writeMemory(o.toAddr(to), out);
        from[k] = to;
        Arrays.fill(stored[k], false);
    }

    String header() {  // vectors.txt, before the first vector: the C runners map the window onto RAM
        return "S " + reg + "\n";
    }

    String meta() {
        return "\"r" + reg + "\"";
    }
}

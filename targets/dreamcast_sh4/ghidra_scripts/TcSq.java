// The SH-4 store queues, for a function that sends a runtime-state pointer's queue itself (`pref @rN`, rN one of
// r8..r14 in its state: Toy Commander's display-list code, r14 the TA write pointer), or a pointer it builds itself
// as 0xe0000000|x in any register (Spawn 8c05fec2: r6 from a TA list pointer, Window). Not a script itself; TcPrepare
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
//   - pref @a sends a's queue: the bytes stored to it since, the rest from the block it last held (RAM, now). The C
//     copies that rest from the old block, so a rest in a code range is a code read (R8): the run faults there as a
//     load of code would (the vector is repaired or drawn again; Spawn 8c057ec0: r11 drawn by code at 8c030400).
// The oracle writes a send between steps, where Ghidra's write tracking does not see it (MemoryAccessFilter tracks
// only while the emulator executes), so sent keeps the blocks and TcExit.collect adds them to the run's writes.
// A stale queue then shows as a W mismatch at the skipped slot. Half the vectors draw rN in the window, the others
// keep their RAM pointer (the window off: the same harness as before for them).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import java.util.*;

final class TcSq {
    static final long LO = 0xe0000000L, HI = 0xe4000000L;
    final int reg;                      // N: the state register that may point into the window, or -1 (built: name)
    final String name;                  // the register sent through: rN, or the one Window found
    final Map<Long, String> prefs;      // the step that runs a pref (its own, or its branch's for a delay slot): register
    final long[] from = new long[2];    // per queue, the RAM block it last held, or -1
    final byte[][] buf = new byte[2][32];
    final boolean[][] stored = new boolean[2][32];
    final AddressSet sent = new AddressSet();  // the RAM blocks the run's prefs sent (TcExit.collect: its W lines)

    TcSq(int reg, String name, Map<Long, String> prefs) {
        this.reg = reg;
        this.name = name;
        this.prefs = prefs;
    }

    // The fact: a `pref @rN` in the body with rN in the function's runtime state (TcSig giState: r8 is bit 24), or
    // with rN a copy of the only state register among r8..r14 (Toy Commander 8c075898: `mov r14,r3`, then add and
    // @-r3 stores, `pref @r3`): copied in the same block (one starts at a referenced address and after a call) and
    // written since only by add or an @-rN / @rN+ access. Or a `pref @rN` with rN built as 0xe0000000|x (Window): no
    // state register, so the vectors draw nothing into the window and the queues start empty. Or, with no such pref,
    // a store through a built window pointer (Spawn 8c058b70: it fills the queue and a callee sends it): the window
    // maps onto RAM as in the port, and nothing is sent here (no prefs). Otherwise null.
    // selective_restamp.queue_pref scans the state-register cases (the built one lands with a full re-stamp).
    static TcSq of(Listing listing, AddressSetView body, int giState) {
        Map<Long, String> prefs = new HashMap<>();
        Window win = new Window();
        String built = null;  // the first register a pref sends through holding 0xe0000000|x
        String stores = null; // the first register a store goes through holding 0xe0000000|x
        Map<String, Integer> copy = new HashMap<>();  // register -> the state register it holds (plus an offset)
        int state = giState >>> 24 & 0x7f;
        int only = Integer.bitCount(state) == 1 ? 8 + Integer.numberOfTrailingZeros(state) : -1;
        int reg = -1, via = -1;
        boolean call = false;  // the previous instruction was a call: this one is its delay slot
        for (Instruction in : listing.getInstructions(body, true)) {
            if (call && !in.isInDelaySlot()) copy.clear();
            String mn = in.getMnemonicString().replaceFirst("^_", "").toLowerCase();
            int nops = in.getNumOperands();
            String dst = nops > 0 ? in.getDefaultOperandRepresentation(nops - 1) : "";
            if (in.getProgram().getReferenceManager().hasReferencesTo(in.getAddress())) copy.clear();
            if (mn.equals("pref")) {
                Object[] ops = in.getOpObjects(0);
                if (ops.length != 1 || !(ops[0] instanceof Register)) continue;
                String r = ((Register) ops[0]).getName();
                Instruction step = in.isInDelaySlot() ? listing.getInstructionBefore(in.getAddress()) : in;
                prefs.put((step != null ? step : in).getAddress().getOffset(), r);
                int n = r.matches("r([89]|1[0-4])") ? Integer.parseInt(r.substring(1)) : -1;
                if (n >= 0 && (giState & (1 << (16 + n))) != 0) reg = n;
                if (copy.getOrDefault(r, -1) == only && only >= 0) via = only;
                if (built == null && win.holds(r)) built = r;
            } else if (mn.equals("mov") && nops == 2 && dst.matches("r\\d+")
                    && in.getDefaultOperandRepresentation(0).matches("r\\d+")) {
                String src = in.getDefaultOperandRepresentation(0);
                int n = src.matches("r([89]|1[0-4])") ? Integer.parseInt(src.substring(1)) : copy.getOrDefault(src, -1);
                if (n >= 0 && !dst.matches("r([89]|1[0-4])")) copy.put(dst, n);
                else copy.remove(dst);
            } else if (!mn.equals("add") && !mn.startsWith("cmp") && !mn.startsWith("tst")) {
                copy.remove(dst);
            }
            int base = Window.storeBase(word(in));
            if (stores == null && base >= 0 && win.win[base]) stores = "r" + base;
            win.step(word(in), pool(listing, in), dst.matches("r\\d+") ? Integer.parseInt(dst.substring(1)) : -1);
            if (call && in.isInDelaySlot()) {  // the callee owns r0..r7 after it
                copy.clear();
                win.called();
            }
            call = in.getFlowType().isCall();
        }
        reg = reg >= 0 ? reg : via;
        if (reg >= 0) return new TcSq(reg, "r" + reg, prefs);
        built = built != null ? built : stores;
        return built == null ? null : new TcSq(-1, built, prefs);
    }

    static int word(Instruction in) {
        try {
            return in.getShort(0) & 0xffff;
        } catch (Exception e) {
            return 0;
        }
    }

    static Long pool(Listing listing, Instruction in) {  // the word a `mov.l @(d,pc),rN` loads, or null
        int w = word(in);
        if ((w & 0xf000) != 0xd000 || in.getLength() != 2) return null;
        long a = (in.getAddress().getOffset() & ~3L) + 4 + (w & 0xff) * 4L;
        try {
            return in.getProgram().getMemory().getInt(in.getAddress().getNewAddress(a)) & 0xffffffffL;
        } catch (Exception e) {
            return null;
        }
    }

    // The registers holding 0xe0000000|x, an address in the window, fed each instruction of the body in address order
    // (across branch targets: the build and the stores can sit in different blocks, as in Spawn 8c05fec2):
    //   - a known value: mov #imm, a PC-relative mov.l, then shll/shll2/shll8/shll16 and add #imm;
    //   - a window pointer: a known value in the window, `or` with one (0xe0000000|x), a copy, add (rN += x);
    //   - any other write to the register (Ghidra's last operand) forgets it, and so does a call for r0..r7.
    // Tested on raw words without Ghidra: tests/java/TcP4Stub.java.
    static final class Window {
        final Long[] val = new Long[16];
        final boolean[] win = new boolean[16];

        boolean holds(String r) {
            return r.matches("r\\d+") && win[Integer.parseInt(r.substring(1)) & 15];
        }

        void called() {
            for (int i = 0; i < 8; i++) {
                val[i] = null;
                win[i] = false;
            }
        }

        // w: the instruction word; pool: what a PC-relative mov.l loads; dst: the register it writes, or -1.
        void step(int w, Long pool, int dst) {
            int n = w >> 8 & 15, m = w >> 4 & 15;
            if ((w & 0xf000) == 0xe000) {
                set(n, (long) (byte) w & 0xffffffffL, false);
            } else if ((w & 0xf000) == 0xd000) {
                set(n, pool, false);
            } else if ((w & 0xf0cf) == 0x4008 || (w & 0xf0ff) == 0x4000) {  // shll2, shll8, shll16; shll
                int by = (w & 0xff) == 0 ? 1 : new int[] {2, 8, 16, 0}[w >> 4 & 3];
                set(n, by > 0 && val[n] != null ? val[n] << by & 0xffffffffL : null, false);
            } else if ((w & 0xf00f) == 0x200b) {                            // or rm,rn
                set(n, val[n] != null && val[m] != null ? val[n] | val[m] : null, win[n] || win[m]);
            } else if ((w & 0xf00f) == 0x6003) {                            // mov rm,rn
                set(n, val[m], win[m]);
            } else if ((w & 0xf000) == 0x7000) {                            // add #imm,rn
                set(n, val[n] != null ? val[n] + (byte) w & 0xffffffffL : null, win[n]);
            } else if ((w & 0xf00f) == 0x300c) {                            // add rm,rn
                set(n, null, win[n] || win[m]);
            } else if (dst >= 0) {
                set(dst & 15, null, false);
            }
        }

        // The register a store writes through (mov.b/w/l Rm to @Rn, @-Rn, @(disp,Rn), @(R0,Rn); mov.b/w R0 to
        // @(disp,Rn); fmov FRm to @Rn, @-Rn, @(R0,Rn); movca.l R0,@Rn; sts.l / stc.l to @-Rn), or -1 for any other
        // instruction word.
        static int storeBase(int w) {
            int hi = w >>> 12 & 15, lo = w & 15, n = w >> 8 & 15;
            if (hi == 2 && (lo <= 2 || lo >= 4 && lo <= 6)) return n;
            if (hi == 1) return n;
            if ((w & 0xfe00) == 0x8000) return w >> 4 & 15;
            if (hi == 0 && (lo >= 4 && lo <= 6 || (w & 0xff) == 0xc3)) return n;
            if (hi == 0xf && (lo == 7 || lo == 0xa || lo == 0xb)) return n;
            if (hi == 4 && (lo == 2 || lo == 3)) return n;
            return -1;
        }

        void set(int n, Long v, boolean w) {
            val[n] = v;
            win[n] = w || v != null && in(v);
        }
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
        if (gi == null || reg < 8) return;  // a pointer the function builds itself (Window): nothing to draw
        Random h = new Random(seed * 0x94d049bb133111ebL + id * 0xbf58476d1ce4e5b9L + attempt * 0x9e3779b97f4a7c15L);
        long p = gi[reg - 8];
        if (h.nextBoolean() && p >= TcBase.RAM_LO && p < TcBase.RAM_HI) {
            gi[reg - 8] = LO | (long) h.nextInt(4) << 24 | (p & 0xffffe0L);
        }
    }

    // A run's start (TcFold): empty queues, then the stream before rN as contiguous.
    void begin(long[] gi) {
        Arrays.fill(from, -1);
        sent.clear();
        for (boolean[] s : stored) Arrays.fill(s, false);
        long p = gi != null && reg >= 8 ? gi[reg - 8] & ~0x1fL : 0;
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
    // A rest read from code (codeRest) sets the run's fault (TcOracle's fault, faultAt) and sends nothing.
    void step(TcOracle o, EmulatorHelper emu, Address pca, String[] fault, long[] faultAt) {
        String r = prefs.get(pca.getOffset());
        long a = r != null ? o.reg(emu, r) : 0;
        if (!in(a)) return;
        int k = queue(a);
        long to = ram(a & ~0x1fL), code = codeRest(o.facts, k);
        if (code >= 0) {
            if (fault[0] == null) {
                fault[0] = "read machine code at " + TcBase.hex(code) + " (R8: a store-queue send's old block)";
                faultAt[0] = code;
            }
            return;
        }
        byte[] out = emu.readMemory(o.toAddr(from[k] >= 0 ? from[k] : to), 32);
        for (int j = 0; j < 32; j++) if (stored[k][j]) out[j] = buf[k][j];
        emu.writeMemory(o.toAddr(to), out);
        sent.add(o.toAddr(to), o.toAddr(to + 31));
        from[k] = to;
        Arrays.fill(stored[k], false);
    }

    // The first byte of queue k's rest (the bytes not stored since, from the block it last held) in a code range, else
    // -1: the C copies the rest from that block (R8 checks it as a load of code).
    long codeRest(TcFacts f, int k) {
        for (int j = 0; j < 32 && from[k] >= 0; j++) if (!stored[k][j] && f.isCode(from[k] + j)) return from[k] + j;
        return -1;
    }

    String header() {  // vectors.txt, before the first vector: the C runners map the window onto RAM
        return "S " + name.substring(1) + "\n";
    }

    String meta() {
        return "\"" + name + "\"";
    }
}

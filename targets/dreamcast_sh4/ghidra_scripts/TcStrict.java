// The stricter judge, the oracle's side (target.json "mock_junk", "mock_writes", "call_snapshots", "exit_frame", the
// facts lines of core/ghidra.py: TcFacts.strict). All four are off by default: then of() returns null, nothing here
// runs and every harness byte is as before. TcOracle calls it at three points of a run: of() after setEntry, call() at
// each mocked call right after its record (before the halt check: a callee that never returns had them too, as the C's
// tc_call did before its tc_halt), exit() after TcExit.collect. At a call, in this order (runtime/tc_strict.h keeps
// it): the snapshots, the junk, the writes.
//  - mock_junk: the mocked callee leaves junk in the scratch registers it may change but its result: of r1..r7 and
//    fr1..fr11, those its TcSum summary writes (r0 and fr0 keep the result; r8..r14 and TcLive's deltas are the
//    callee-saved ones). A callee the summary says keeps one keeps it, as the real one does (Toy Commander 8c061aac
//    passes its entry r6 on after two calls, 8c03a2f6 reads fr8 after 8c061394: TcSig.ownDefs). A target that is no
//    direct callee (a computed call: the compiler knew nothing of it), an opaque or unsolved summary, or a target
//    without param_summaries leaves all of them junk, as the ABI allows. A C that reads a scratch value back after a
//    call can then only match by chance. But no r1..r7 the function itself reads back (readsAfter: some path from the
//    return address reads it before writing): the C has no r1..r7, so junk there could only end the run or differ where
//    no C follows it, while hand-written code keeps values there across its own callees (Toy Commander 8c061698 its
//    return address in r2 for the final `jmp @r2`, 8c06161c its next call target in r1; every run of both called junk).
//    The value is TcJunk.at(seed, vid, call, slot): r1..r7 slot 1..7, fr1..fr11 16 + i (as TcJunk.fr, a normal float).
//    MACH, MACL, FPUL and T stay as they are: TcSum does not track them, no C reads them. The C models fr1..fr11 only
//    (sh4_fr, with the whole FPU as state), and so junks only them, by the same masks (header lines "U callee 0 mask");
//    it cannot read r1..r7. The SH-2 has no FPU (TcEmu drops the FPU registers). A compared argument that still holds a
//    callee's junk at a later call (the function never set it: Power Stone 2 8c05e598 hands its entry r6 to 8c05e75c
//    after 8c05e5b8) may also be the value it had without junk, the one the mocks keep (expected.txt "CJ call field
//    hex"): C that passes it on is as right as C that leaves the register, and no junk-free run tells them apart.
//  - mock_writes: a callee the project lists (project.json "mock_write_facts", evidence required: core/ghidra.py)
//    writes SIZE bytes through a pointer argument (record field r4..r7 = 1..4, s0..s7 = 13..20): byte k is
//    TcJunk.at8(seed, vid, call, 0x10000 * (1 + field), k). Only a range wholly in RAM (TcBase.inRam) with no
//    instruction byte (TcFacts.isCode) is written; the runtimes test the same. TcExit logs it like any write. An
//    advance fact ("aA.B:op.a.s:...", core/strict_judge.advance) moves the cursor word there instead (advance()).
//  - call_snapshots: for each pointer argument p (r4..r7, and the fields a fact names), the bytes this run's own
//    instructions stored at [p, p + extent) before the call (Stored: not the vector's patches, not mock_writes' bytes;
//    a filter sees every write into the emulator's state after it is added, EmulatorHelper.writeMemory's too, so
//    write() mutes it around its own): extent the fact's size, else the run of stored bytes from p, at most RUN_MAX. A
//    pointer into the stack window gets only a fact's extent: past a local, the stored bytes are saved registers and
//    spills the C does not have. expected.txt "CS call field hex", "xx" for a byte it did not store; vectors.txt "W
//    call field size" asks the runtime for the bytes, at most MAX_SNAPS per vector. The judge compares the stored
//    bytes, only for a field the callee's compared registers name (core/strict_judge.py).
//  - exit_frame: r15, r8..r14 and fr12..fr15 at the exit (expected.txt "XR"; the SH-2's fr are 0 on both sides), for a
//    run that reached the sentinel (a halted one stops wherever its frame is).
// The key (TcJunk): seed the function seed (TcPrepare sets it), vid the oracle's run number (workRuns: unique per run,
// so a repaired run or a vector TcReach renumbers keeps the key it ran with); vectors.txt "N seed vid" (hex).
// Header lines (TcPrepare): "J flags"; with mock_junk "U callee 0 mask" per direct callee that keeps some of fr1..fr11
// (mask: bit i set for an fri it may change; field 0 is no pointer field); with mock_writes "U callee field size" per
// fact. Not a script itself; the same file in the Dreamcast and Saturn targets (the GameCube has its own).
// The inputs are those of every flag off: TcVectors, TcReach and TcBound choose each vector with of() null, then
// writeVector runs it with the flags on (replay), its expected; one that fails then is left out. Junk had steered the
// choice (Power Stone 2 8c06a2e8, 8c16d394, Toy Commander 8c046402 lost the inputs killing a mutant flags off).
import ghidra.app.emulator.*;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.*;
import java.util.*;

final class TcStrict {
    static final int JUNK = 1, WRITES = 2, SNAPS = 4, EXIT = 8;
    static final int MAX_SNAPS = 64;  // snapshot requests per vector (tc_strict.h TCS_MAX_REQ)
    static final int RUN_MAX = 64;    // a snapshot's extent without a fact
    static final int SCRATCH = TcFlow.CLOBBER & ~TcFlow.RET; // r1..r7, fr1..fr11 (TcFlow masks)
    static final int FR = 0xffe;      // fr1..fr11 of a mask
    static final int R1_7 = (TcFlow.LOW | TcFlow.INTS) & ~(1 << 20); // r1..r7 of a mask
    static final int MAX_VISITS = 64; // readsAfter: per instruction; then all of r1..r7 count as read
    static final java.util.function.ToLongFunction<byte[]> LE = // 1 to 8 bytes, least significant first (advance)
        b -> java.nio.ByteBuffer.wrap(Arrays.copyOf(b, 8)).order(java.nio.ByteOrder.LITTLE_ENDIAN).getLong();
    static long seed;                 // the function seed (TcPrepare)
    static int exitLive;              // live at a return of the walked code: the function's own outputs (TcOut), or 0
    static boolean replaying;         // of() runs only inside replay()
    static final int[] dropped = new int[2]; // per set (dev, hidden): chosen vectors the replay failed (TcPrepare)
    // Per direct callee (TcPrepare: callees()), the scratch registers its summary may change (not in it: SCRATCH).
    static final Map<Long, Integer> changes = new TreeMap<>();
    // Per direct callee, the registers its harness compares (meta.json callees "regs"; TcPrepare): where CJ matters.
    static final Map<Long, List<String>> compared = new HashMap<>();
    // readsAfter per return address, and the function's extent (TcTail) it walks: per function (callees()).
    static final Map<Long, Integer> reads = new HashMap<>();
    static AddressSetView extent;
    static Set<Long> direct;          // callees(): the function's direct callees, whose facts count (null: all; stubs)

    // TcPrepare, before the runs: the direct callees, their masks (only with mock_junk and param_summaries), regs.
    static void callees(TcBase s, List<Function> cs, Map<Long, List<String>> regs) {
        dropped[0] = dropped[1] = 0;
        changes.clear();
        compared.clear();
        compared.putAll(regs);
        reads.clear();
        extent = null;
        direct = new HashSet<>();
        for (Function g : cs) direct.add(g.getEntryPoint().getOffset());
        if ((s.facts.strict & JUNK) == 0 || !s.facts.paramSummaries) return;
        for (Function g : cs) changes.put(g.getEntryPoint().getOffset(), s.sig.summary(g).writes & SCRATCH);
        // A call through a RAM mirror (Power Stone 2 8c0d761c: `jsr @r3`, r3 = 0x0c187958) is as direct, to the folded
        // address the oracle calls; cs misses it (8c187958 copies words: it keeps the fr4, fr5 the caller reads after).
        Program prog = s.getCurrentProgram();
        for (Instruction in : prog.getListing().getInstructions(TcTail.extent(prog, s.fn), true)) {
            for (ghidra.program.model.symbol.Reference r : in.getReferencesFrom()) {
                long a = r.getToAddress().getOffset(), f = TcBase.fold(a);
                if (!r.getReferenceType().isCall() || f == a || changes.containsKey(f)) continue;
                Function g = prog.getFunctionManager().getFunctionAt(r.getToAddress().getNewAddress(f));
                if (g != null) changes.put(f, s.sig.summary(g).writes & SCRATCH);
            }
        }
    }

    static int changes(long target) {
        return changes.getOrDefault(target, SCRATCH);
    }

    // The r1..r7 some path from the return address reads before writing (TcFlow masks), over the function's p-code
    // (TcTail.extent, the walk of TcLive.Deltas): a mocked call writes none of them, a return or a jump out of the
    // extent (a tail call: exitLive is read at both) ends a path. All of them when the walk cannot finish (no
    // instruction there, a loop past MAX_VISITS).
    static int readsAfter(Program prog, Function f, long ret) {
        return reads.computeIfAbsent(ret, a -> {
            if (extent == null) extent = TcTail.extent(prog, f);
            return walk(prog, extent, f.getEntryPoint().getAddressSpace().getAddress(a));
        });
    }

    static int walk(Program prog, AddressSetView body, Address start) {
        Listing listing = prog.getListing();
        Map<Address, Integer> in = new HashMap<>(), visits = new HashMap<>(); // in: written on every path there
        in.put(start, 0);
        Deque<Address> work = new ArrayDeque<>(List.of(start));
        int live = 0;
        while (!work.isEmpty()) {
            Address a = work.pop();
            Instruction ins = listing.getInstructionAt(a);
            if (ins == null || visits.merge(a, 1, Integer::sum) > MAX_VISITS) return R1_7;
            int w = in.get(a);
            boolean ends = false;
            for (PcodeOp op : ins.getPcode()) { // a delayed branch's own p-code holds its slot's
                for (Varnode v : op.getInputs()) live |= mask(prog, v) & ~w;
                w |= mask(prog, op.getOutput());
                ends |= op.getOpcode() == PcodeOp.RETURN;
            }
            if (ends) { live |= exitLive & ~w; continue; }  // a function's own outputs (TcOut)
            List<Address> to = new ArrayList<>(ins.getFlowType().isCall() ? List.of() : List.of(ins.getFlows()));
            if (ins.getFallThrough() != null) to.add(ins.getFallThrough());
            List<Address> next = to.stream().filter(body::contains).toList();  // the rest leave the extent: a tail call
            if (next.size() < to.size() || to.isEmpty()) live |= exitLive & ~w;  // its rK is the exit's (`jmp @r3`)
            for (Address n : next) {
                Integer old = in.get(n);
                if (old != null && (old & w) == old) continue;
                in.put(n, old == null ? w : old & w);
                work.push(n);
            }
        }
        return live;
    }

    static int mask(Program prog, Varnode v) {
        if (v == null || !v.isRegister()) return 0;
        Register r = prog.getRegister(v.getAddress(), v.getSize());
        if (r == null) r = prog.getRegister(v.getAddress());
        return r == null ? 0 : TcFlow.maskOf(r.getName().toLowerCase()) & R1_7;
    }

    // A junked register's CALL record field (TcOracle.callRecord, core/compare.py REG_INDEX); -1: none.
    static int recordField(String reg) {
        if (reg.matches("r[4-7]")) return reg.charAt(1) - '3';
        if (reg.matches("fr([4-9]|1[01])")) return Integer.parseInt(reg.substring(2)) + 1;
        if (reg.matches("r[1-3]")) return 25 + reg.charAt(1) - '0';
        return reg.matches("fr[1-3]") ? 36 + reg.charAt(2) - '0' : -1;
    }

    // A pointer argument's record field: r4..r7 = 1..4, s0..s7 = 13..20 (TcOracle.callRecord); -1: none.
    static int field(String name) {
        if (name.matches("r[4-7]")) return name.charAt(1) - '3';
        return name.matches("s[0-7]") ? 13 + name.charAt(1) - '0' : -1;
    }

    // The facts of the function's direct callees (listed()) in file order, the U lines' (TcFacts.fact's form).
    static List<long[]> facts(TcFacts f) {
        return listed(f).stream().map(w -> TcFacts.fact(w, field(w[1]))).toList();
    }

    // The mock_write lines of the function's direct callees: another function's harness stays byte for byte.
    static List<String[]> listed(TcFacts f) {
        return f.mockWrites.stream().filter(w -> direct == null || direct.contains(Long.parseLong(w[0], 16))).toList();
    }

    static String header(TcFacts f) {
        if (f.strict == 0) return "";
        StringBuilder h = new StringBuilder("J ").append(f.strict).append('\n');
        for (Map.Entry<Long, Integer> c : changes.entrySet()) {  // empty without mock_junk (callees)
            if ((c.getValue() & FR) != FR) h.append("U ").append(TcBase.hex(c.getKey())).append(" 0 ")
                .append(c.getValue() & FR).append('\n');
        }
        if ((f.strict & WRITES) != 0) {
            for (String[] w : listed(f)) h.append("U ").append(TcBase.hex(Long.parseLong(w[0], 16))).append(' ')
                .append(field(w[1])).append(' ').append(w[2]).append('\n');
        }
        return h.toString();
    }

    // meta.json: the facts the writes came from (only with mock_writes and a fact: other metas are unchanged).
    static String meta(TcFacts f) {
        List<String[]> ws = listed(f);
        if ((f.strict & WRITES) == 0 || ws.isEmpty()) return "";
        StringBuilder m = new StringBuilder(",\n  \"mock_writes\": [");
        for (int i = 0; i < ws.size(); i++) {
            String[] w = ws.get(i);
            m.append(i > 0 ? ", " : "").append(String.format("{\"callee\": \"%s\", \"field\": \"%s\", \"%s\": %s}",
                w[0], w[1], w[2].startsWith("a") ? "advance" : "size", w[2].startsWith("a") ? '"' + w[2] + '"' : w[2]));
        }
        return m.append(']').toString();
    }

    // This run's own stores, per 4 KiB page: the instructions' and TcSq's sends (the store queue's `pref` is the
    // function's), not mock_writes' bytes (write() mutes it around them: the filter sees EmulatorHelper.writeMemory's).
    static final class Stored extends MemoryAccessFilter {
        final AddressSpace ram;
        final Map<Long, long[]> pages = new HashMap<>();
        boolean muted;

        Stored(AddressSpace ram) {
            this.ram = ram;
        }

        @Override
        protected void processRead(AddressSpace spc, long off, int size, byte[] values) {
        }

        @Override
        protected void processWrite(AddressSpace spc, long off, int size, byte[] values) {
            if (spc != ram || muted) return;
            for (int i = 0; i < size; i++) {
                long a = TcBase.fold(off + i);
                pages.computeIfAbsent(a >>> 12, k -> new long[64])[(int) (a & 0xfff) >>> 6] |= 1L << (a & 63);
            }
        }

        boolean has(long a) {
            long[] p = pages.get(a >>> 12);
            return p != null && (p[(int) (a & 0xfff) >>> 6] >>> (a & 63) & 1) != 0;
        }
    }

    final TcOracle o;
    final int flags;
    final long vid;
    final List<long[]> facts;
    final StringBuilder vec = new StringBuilder(), exp = new StringBuilder();
    Stored stored;
    int snaps;
    // mock_junk: per junked register, {the junk, the value it had without junk} (set; leftover reads it)
    final Map<String, long[]> left = new HashMap<>();

    private TcStrict(TcOracle o, int flags, long vid) {
        this.o = o;
        this.flags = flags;
        this.vid = vid;
        facts = facts(o.facts);
    }

    // TcVectors.writeVector: r with every flag off; else the vector's run with them, or null (dropped) if it failed.
    static TcOracle.Result replay(TcOracle o, TcInputs.Vec v, TcOracle.Result r, String set) {
        if (o.facts.strict == 0) return r;
        replaying = true;
        try {
            r = o.runOracle(v);
        } finally {
            replaying = false;
        }
        if (!r.ok) dropped[set.equals("dev") ? 0 : 1]++;
        return r.ok ? r : null;
    }

    // After setEntry: null with every flag off, and outside replay().
    static TcStrict of(TcOracle o, EmulatorHelper emu) {
        if (o.facts.strict == 0 || !replaying) return null;
        TcStrict s = new TcStrict(o, o.facts.strict, o.workRuns);
        if ((s.flags & SNAPS) != 0) {
            s.stored = new Stored(o.fn.getEntryPoint().getAddressSpace());
            emu.getEmulator().addMemoryAccessFilter(s.stored);
        }
        if ((s.flags & (JUNK | WRITES)) != 0) {
            s.vec.append("N ").append(Long.toHexString(seed)).append(' ').append(Long.toHexString(s.vid)).append('\n');
        }
        return s;
    }

    // At mocked call number `call` (from 0), its record the last of res.calls.
    static void call(TcOracle.Result res, EmulatorHelper emu, int call) {
        TcStrict s = res.strict;
        if (s == null) return;
        String[] rec = res.calls.get(res.calls.size() - 1).split(" ");
        long target = Long.parseLong(rec[0], 16);
        if ((s.flags & JUNK) != 0) s.leftover(emu, call, target, rec.length);
        if ((s.flags & SNAPS) != 0) s.snap(emu, call, target, rec);
        if ((s.flags & JUNK) != 0) s.junk(emu, call, target);
        if ((s.flags & WRITES) != 0) s.write(emu, call, target, rec);
    }

    int size(long target, int fld) {
        for (long[] w : facts) if (w[0] == target && w[1] == fld) return w[2] < 0 ? 4 : (int) w[2];
        return 0;
    }

    void snap(EmulatorHelper emu, int call, long target, String[] rec) {
        for (int fld = 1; fld <= 20 && snaps < MAX_SNAPS; fld++) {
            int n = size(target, fld);
            if (n == 0 && fld > 4) continue;
            long p = TcBase.fold(Long.parseLong(rec[fld], 16));
            if (n == 0) {
                if (p >= TcBase.STACK_LO && p < TcBase.RAM_HI) continue;  // the stack window: a fact's extent only
                while (n < RUN_MAX && TcBase.inRam(p + n, 1) && stored.has(p + n)) n++;
            }
            if (n == 0 || !TcBase.inRam(p, n)) continue;
            byte[] b = emu.readMemory(o.toAddr(p), n);
            StringBuilder hex = new StringBuilder();
            boolean any = false;
            for (int k = 0; k < n; k++) {
                boolean mine = stored.has(p + k);
                any |= mine;
                hex.append(mine ? String.format("%02x", b[k] & 0xff) : "xx");
            }
            if (!any) continue;
            exp.append("\nCS ").append(call).append(' ').append(fld).append(' ').append(hex);
            vec.append("W ").append(call).append(' ').append(fld).append(' ').append(n).append('\n');
            snaps++;
        }
    }

    // The registers the callee may change (changes) that the function does not read back (readsAfter).
    void junk(EmulatorHelper emu, int call, long target) {
        int m = changes(target) & ~readsAfter(o.getCurrentProgram(), o.fn, o.reg(emu, "PR"));
        for (int i = 1; i <= 7; i++) set(emu, "r" + i, i, call, m);
        for (int i = 1; i <= 11; i++) set(emu, "fr" + i, 16 + i, call, m);
    }

    void set(EmulatorHelper emu, String reg, int slot, int call, int m) {
        if ((m & TcFlow.maskOf(reg)) == 0 || o.getCurrentProgram().getRegister(reg) == null) return;
        long now = o.reg(emu, reg), z = TcJunk.at(seed, vid, call, slot), j = slot > 7 ? TcJunk.fr(z) : z & 0xffffffffL;
        long[] was = left.get(reg);  // still an earlier call's junk: the value without junk is the one before that
        left.put(reg, new long[] {j, was != null && was[0] == now ? was[1] : now});
        emu.writeRegister(reg, o.u(j));
    }

    // At a call's record, before its junk: each compared argument (meta fpu_state: every junked fr too) still holding
    // junk, as "CJ call field hex" with the value it had without junk (the record has the junk).
    void leftover(EmulatorHelper emu, int call, long target, int fields) {
        Set<String> regs = new LinkedHashSet<>(compared.getOrDefault(target, List.of()));
        for (int i = 1; o.entryFq != null && i <= 11; i++) regs.add("fr" + i);
        for (String reg : regs) {
            long[] j = left.get(reg);
            int fld = recordField(reg);
            if (j == null || fld < 0 || fld >= fields || o.reg(emu, reg) != j[0]) continue;
            exp.append("\nCJ ").append(call).append(' ').append(fld).append(' ').append(TcBase.hex(j[1]));
        }
    }

    // The runtimes' test too (tc_strict.h tcs_write): wholly in RAM, no instruction byte.
    boolean writable(long p, int n) {
        for (int k = 0; TcBase.inRam(p, n) && k < n; k++) if (o.facts.isCode(p + k)) return false;
        return TcBase.inRam(p, n);
    }

    void write(EmulatorHelper emu, int call, long target, String[] rec) {
        for (long[] w : facts) {
            int fld = (int) w[1], n = (int) w[2];
            long p = TcBase.fold(Long.parseLong(rec[fld], 16));
            if (w[0] != target || !writable(p, n < 0 ? 4 : n)) continue;
            byte[] b = n < 0 ? advance(emu, p, w) : new byte[n];
            for (int k = 0; k < n; k++) b[k] = (byte) TcJunk.at8(seed, vid, call, 0x10000L * (1 + fld), k);
            if (b == null) continue;
            if (stored != null) stored.muted = true;  // not the function's own store (Stored)
            emu.writeMemory(o.toAddr(p), b);
            if (stored != null) stored.muted = false;
        }
    }

    // An advance's new cursor word at p (core/strict_judge.advance; tc_strict.h tcs_advance), little-endian: pc =
    // up(pc, A), the B-byte op at pc, pc += B, then up(pc, a) + s for a listed op; null when the op is not in RAM.
    byte[] advance(EmulatorHelper emu, long p, long[] w) {
        long pc = LE.applyAsLong(emu.readMemory(o.toAddr(p), 4)) + w[3] - 1 & -w[3], at = TcBase.fold(pc);
        if (!TcBase.inRam(at, w[4])) return null;
        long op = LE.applyAsLong(emu.readMemory(o.toAddr(at), (int) w[4]));
        pc += w[4];
        for (int i = 5; i + 2 < w.length; i += 3) if (w[i] == op) pc = (pc + w[i + 1] - 1 & -w[i + 1]) + w[i + 2];
        return new byte[] {(byte) pc, (byte) (pc >>> 8), (byte) (pc >>> 16), (byte) (pc >>> 24)};
    }

    // After TcExit.collect: r15, r8..r14, fr12..fr15 of a run that returned.
    static void exit(TcOracle.Result res, EmulatorHelper emu) {
        TcStrict s = res.strict;
        if (s == null || (s.flags & EXIT) == 0 || res.halted) return;
        s.exp.append("\nXR ").append(TcBase.hex(s.o.reg(emu, "r15")));
        for (int i = 8; i < 15; i++) s.exp.append(' ').append(TcBase.hex(s.o.reg(emu, "r" + i)));
        for (int i = 12; i < 16; i++) s.exp.append(' ').append(TcBase.hex(s.o.reg(emu, "fr" + i)));
    }

    // TcVectors.writeVector: the vector's N and W lines (before its E), and its CJ, CS and XR lines (before COV).
    static String vec(TcOracle.Result r) {
        return r.strict == null ? "" : r.strict.vec.toString();
    }

    static String exp(TcOracle.Result r) {
        return r.strict == null ? "" : r.strict.exp.toString();
    }
}

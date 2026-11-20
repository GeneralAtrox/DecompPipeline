// The stricter judge, the oracle's side for the GameCube (the SH-4 targets' TcStrict says the same for theirs):
// target.json "mock_junk", "mock_writes", "call_snapshots", "exit_frame", the facts lines of core/ghidra.py
// (TcFacts.strict). All four are off by default: then of() returns null, nothing here runs and every harness byte is
// as before. TcOracle calls it after setEntry (of), at each mocked call after r3 and f1 got its results (call, in
// clobber's place) and after collect (exit). At a call, in this order (runtime/tc_strict.h keeps it): the snapshots,
// the junk, the writes.
//  - mock_junk: instead of clobber's zeroes, junk in every volatile register but the results r3 and f1: r0, r4..r12,
//    f0, f2..f13 (all 64 bits), CTR, XER (SO, OV, CA: the pseudo registers xer_so, xer_ov, xer_ca and the XER word
//    built from them) and the volatile CR fields cr0, cr1, cr5, cr6, cr7. TcJunk.at(seed, vid, call, slot): r0 slot
//    0, rN N, f0 32, fN 32 + N, CTR 64, xer_so 65, xer_ov 66, xer_ca 67 (their low bit), crN 80 + N (low 4 bits). A
//    zero is a value C can produce by accident (an unset local); junk is not. The C runtime models no register here.
//    A compared argument that still holds the last call's junk (the function never set it: Skies of Arcadia 800c7f64
//    passes r7..r10 on to 8025cb24) may also be the 0 clobber leaves there (expected.txt "CJ call field hex"): no
//    junk-free run tells C that passes 0 from C that leaves it. Not r4 after a callee that sets it on every path
//    (TcFlow.returnsR4): that is the low word of its 64-bit result, the C has it (tc_lastr4), and 0 is wrong.
//  - mock_writes: a callee the project lists (project.json "mock_write_facts") writes SIZE bytes through a pointer
//    argument (record field r3..r10 = 1..8, s0..s7 = 17..24): byte k is TcJunk.at8(seed, vid, call, 0x10000 *
//    (1 + field), k), only to a range wholly in MEM1 with no instruction byte (TcFacts.isCode), as the runtime tests.
//  - call_snapshots: for each pointer argument p (r3..r10, and the fields a fact names), the bytes this run stored
//    (TcBank.written: since setEntry) at [p, p + extent): extent the fact's size, else the run of stored bytes from p,
//    at most RUN_MAX (extent). A pointer into the stack window without a fact only into the own frame [r1, entry SP),
//    and its run stops below the frame's saved registers (saveLo: past a local are saved registers the C does not
//    have): a buffer the function fills and passes on (Skies of Arcadia 800a13d8 hands 80291bb4 a two-vertex array
//    at r1+8) is compared like one in the heap; a frame slot never passed out stays unjudged.
//    expected.txt "CS call field hex" ("xx": not stored), vectors.txt "W call field size".
//  - exit_frame: r1, r14..r31 and f14..f31 at the exit (expected.txt "XR"; the C runtime reports the entry values,
//    which a function that restores what it saves leaves there).
// The key: seed the function seed, vid this prepare's run number (runs: unique per run, so a repaired run keeps the
// key it ran with); vectors.txt "N seed vid". Header lines (TcPrepare): "J flags", with mock_writes "U callee field
// size" per fact. Not a script itself.
// The inputs are those of every flag off: TcVectors and TcBound choose and repair each vector with of() returning
// null (clobber's zeroes), and writeVector runs the chosen one once more with the flags on (replay); that run is its
// expected, so junk never steers the choice (the Dreamcast's TcStrict says why). A vector the replay fails is left
// out, its id unused.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;
import java.math.BigInteger;
import java.util.*;
import java.util.function.LongPredicate;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class TcStrict {
    static final int JUNK = 1, WRITES = 2, SNAPS = 4, EXIT = 8;
    static final int MAX_SNAPS = 64;  // snapshot requests per vector (tc_strict.h TCS_MAX_REQ)
    static final int RUN_MAX = 64;    // a snapshot's extent without a fact
    static long seed, runs;           // the function seed and the runs so far (TcPrepare: start)
    static boolean replaying;         // of() runs only inside replay()
    static final int[] dropped = new int[2]; // per set (dev, hidden): chosen vectors the replay failed (TcPrepare)
    // Per direct callee, the registers its harness compares (meta.json callees "regs"; TcPrepare: callees)
    static final Map<Long, List<String>> compared = new HashMap<>();

    static void callees(Map<Long, List<String>> regs) {
        compared.clear();
        compared.putAll(regs);
    }

    static long start(long functionSeed) {
        runs = 0;
        dropped[0] = dropped[1] = 0;
        return seed = functionSeed;
    }

    // A pointer argument's record field: r3..r10 = 1..8, s0..s7 = 17..24 (TcOracle.callRecord); -1: none.
    static int field(String name) {
        if (name.matches("r([3-9]|10)")) return Integer.parseInt(name.substring(1)) - 2;
        return name.matches("s[0-7]") ? 17 + name.charAt(1) - '0' : -1;
    }

    static List<long[]> facts(TcFacts f) {
        List<long[]> out = new ArrayList<>();
        for (String[] w : f.mockWrites) {
            int fld = field(w[1]);
            if (fld < 0) throw new IllegalArgumentException("mock_write: no pointer field " + w[1]);
            out.add(new long[] {Long.parseLong(w[0], 16), fld, Integer.parseInt(w[2])});
        }
        return out;
    }

    static String header(TcFacts f) {
        if (f.strict == 0) return "";
        StringBuilder h = new StringBuilder("J ").append(f.strict).append('\n');
        if ((f.strict & WRITES) != 0) {
            for (long[] w : facts(f)) h.append("U ").append(TcBase.hex(w[0])).append(' ').append(w[1]).append(' ')
                .append(w[2]).append('\n');
        }
        return h.toString();
    }

    static String meta(TcFacts f) {
        if ((f.strict & WRITES) == 0 || f.mockWrites.isEmpty()) return "";
        StringBuilder m = new StringBuilder(",\n  \"mock_writes\": [");
        for (int i = 0; i < f.mockWrites.size(); i++) {
            String[] w = f.mockWrites.get(i);
            m.append(i > 0 ? ", " : "").append(String.format("{\"callee\": \"%s\", \"field\": \"%s\", \"size\": %s}",
                w[0], w[1], w[2]));
        }
        return m.append(']').toString();
    }

    final TcOracle o;
    final int flags;
    final long vid;
    final List<long[]> facts;
    final TcBank bank;
    final StringBuilder vec = new StringBuilder(), exp = new StringBuilder();
    int snaps;
    long last = -1;  // the previous call's target

    private TcStrict(TcOracle o, TcBank bank) {
        this.o = o;
        this.bank = bank;
        flags = o.facts.strict;
        vid = runs++;
        facts = facts(o.facts);
    }

    // After setEntry and bank.track: null with every flag off.
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

    static TcStrict of(TcOracle o, TcBank bank) {
        if (o.facts.strict == 0 || !replaying) return null;
        TcStrict s = new TcStrict(o, bank);
        if ((s.flags & (JUNK | WRITES)) != 0) {
            s.vec.append("N ").append(Long.toHexString(seed)).append(' ').append(Long.toHexString(s.vid)).append('\n');
        }
        return s;
    }

    // At mocked call number `call` (from 0), its record the last of res.calls; false: no junk (clobber zeroes).
    static boolean call(TcOracle.Result res, EmulatorHelper emu, int call) {
        TcStrict s = res.strict;
        if (s == null) return false;
        String[] rec = res.calls.get(res.calls.size() - 1).split(" ");
        long target = Long.parseLong(rec[0], 16);
        if ((s.flags & JUNK) != 0) s.leftover(emu, call, target);
        s.last = target;
        if ((s.flags & SNAPS) != 0) s.snap(emu, call, target, rec);
        if ((s.flags & JUNK) != 0) s.junk(emu, call);
        if ((s.flags & WRITES) != 0) s.write(emu, call, target, rec);
        return (s.flags & JUNK) != 0;
    }

    int size(long target, int fld) {
        for (long[] w : facts) if (w[0] == target && w[1] == fld) return (int) w[2];
        return 0;
    }

    static boolean inRam(long a, long n) {
        return a >= TcBase.RAM_LO && a + n <= TcBase.RAM_HI;
    }

    boolean stored(long a) {
        return bank.written.contains(o.toAddr(a));
    }

    static boolean inStack(long p) {
        return p >= TcBase.STACK_LO && p < TcBase.RAM_HI;
    }

    // A snapshot's extent without a fact: the run of bytes this run stored from p, at most RUN_MAX. In the stack window
    // only inside the own frame [sp, SP) and below its saved registers (sp + saveLo); 0 elsewhere there.
    static int extent(long p, long sp, int saveLo, LongPredicate stored) {
        long cap = RUN_MAX;
        if (inStack(p)) {
            if (p < sp || p >= TcBase.SP) return 0;
            cap = Math.min(cap, Math.min(TcBase.SP, sp + saveLo) - p);
        }
        int n = 0;
        while (n < cap && inRam(p + n, 1) && stored.test(p + n)) n++;
        return n;
    }

    static final String NUM = "(-?(?:0x[0-9a-f]+|\\d+))";
    static final Pattern STWU = Pattern.compile("stwu r1," + NUM + "\\(r1\\)");
    static final Pattern ADDI11 = Pattern.compile("addi r11,r1," + NUM);
    static final Pattern SAVE =
        Pattern.compile("(stw|stmw|stfd|stfs|psq_st)u? ([rf])(\\d+)," + NUM + "\\((r1|r11)\\).*");

    static long num(String s) {
        boolean neg = s.startsWith("-");
        String b = neg ? s.substring(1) : s;
        long v = b.startsWith("0x") ? Long.parseLong(b.substring(2), 16) : Long.parseLong(b);
        return neg ? -v : v;
    }

    // The lowest offset from r1 after the prologue's stwu of a callee-saved register's save, or MAX_VALUE: stores of
    // r14..r31 and f14..f31 (stmw: rN..r31) based on r1, before the stwu entry-relative (moved by its size), and those
    // of CodeWarrior's save helpers based on r11 = r1 + K. ins: the function's instructions in address order as Ghidra
    // prints them (lower case), a save helper's after its bl. A local kept in such a register only lowers it.
    static int saveLo(List<String> ins) {
        long frame = 0, lo = Long.MAX_VALUE;
        for (String s : ins) {
            Matcher m = STWU.matcher(s);
            if (m.matches()) {
                frame = -num(m.group(1));
                break;
            }
        }
        boolean after = false;
        Long r11 = null;
        for (String s : ins) {
            if (STWU.matcher(s).matches()) {
                after = true;
                continue;
            }
            Matcher a = ADDI11.matcher(s);
            if (a.matches()) {
                r11 = num(a.group(1));
                continue;
            }
            Matcher m = SAVE.matcher(s);
            if (!m.matches() || !m.group(1).equals("stmw") && Integer.parseInt(m.group(3)) < 14) continue;
            long d = num(m.group(4)), off;
            if (m.group(5).equals("r1")) off = after ? d : d + frame;
            else if (r11 != null) off = r11 + d;
            else continue;
            if (off >= 0) lo = Math.min(lo, off);
        }
        return lo == Long.MAX_VALUE ? Integer.MAX_VALUE : (int) lo;
    }

    static long savedFor = -1;  // the function saveLo() last scanned, and its result
    static int savedLo;

    int saveLo() {
        long e = o.fn.getEntryPoint().getOffset();
        if (e != savedFor) {
            List<String> ins = new ArrayList<>();
            for (Instruction in : o.listing.getInstructions(o.fn.getBody(), true)) {
                ins.add(in.toString().toLowerCase());
                if (!in.getFlowType().isCall()) continue;
                for (Address t : in.getFlows()) {
                    if (!TcFlow.isSaveHelper(o.listing, t)) continue;
                    Instruction h = o.listing.getInstructionAt(t);
                    for (int n = 0; h != null && n < 40; n++, h = h.getNext()) {
                        String s = h.toString().toLowerCase();
                        if (s.startsWith("blr")) break;
                        ins.add(s);
                    }
                }
            }
            savedLo = saveLo(ins);
            savedFor = e;
        }
        return savedLo;
    }

    void snap(EmulatorHelper emu, int call, long target, String[] rec) {
        for (int fld = 1; fld <= 24 && snaps < MAX_SNAPS; fld++) {
            int n = size(target, fld);
            if (n == 0 && fld > 8 || fld > 8 && fld < 17) continue;
            long p = TcBase.fold(Long.parseLong(rec[fld], 16));
            if (n == 0) n = extent(p, o.reg(emu, "r1"), inStack(p) ? saveLo() : 0, this::stored);
            if (n == 0 || !inRam(p, n)) continue;
            byte[] b = emu.readMemory(o.toAddr(p), n);
            StringBuilder hex = new StringBuilder();
            boolean any = false;
            for (int k = 0; k < n; k++) {
                boolean mine = stored(p + k);
                any |= mine;
                hex.append(mine ? String.format("%02x", b[k] & 0xff) : "xx");
            }
            if (!any) continue;
            exp.append("CS ").append(call).append(' ').append(fld).append(' ').append(hex).append('\n');
            vec.append("W ").append(call).append(' ').append(fld).append(' ').append(n).append('\n');
            snaps++;
        }
    }

    long junk(int call, int slot) {
        return TcJunk.at(seed, vid, call, slot);
    }

    // At a call's record, before its junk: each compared argument still holding the last call's junk (every call junks
    // every volatile register), as "CJ call field hex" with clobber's 0 (the record has the junk).
    void leftover(EmulatorHelper emu, int call, long target) {
        if (call == 0) return;
        for (String reg : compared.getOrDefault(target, List.of())) {
            boolean f = reg.matches("f[2-8]");
            if (!f && !reg.matches("r([4-9]|10)")) continue;  // r3 and f1 are results: never junk
            if (reg.equals("r4") && last >= 0 && o.calleeSetsR4(last)) continue;
            int n = Integer.parseInt(reg.substring(1));
            long j = junk(call - 1, f ? 32 + n : n), now = f ? o.reg64(emu, reg) : o.reg(emu, reg);
            if (now != (f ? j : j & 0xffffffffL)) continue;
            exp.append("CJ ").append(call).append(' ').append(f ? 8 + n : n - 2).append(' ')
                .append(f ? TcBase.hex64(0) : TcBase.hex(0)).append('\n');
        }
    }

    void junk(EmulatorHelper emu, int call) {
        emu.writeRegister("r0", o.u(junk(call, 0)));
        for (int i = 4; i <= 12; i++) emu.writeRegister("r" + i, o.u(junk(call, i)));
        emu.writeRegister("f0", o.u64(junk(call, 32)));
        for (int i = 2; i <= 13; i++) emu.writeRegister("f" + i, o.u64(junk(call, 32 + i)));
        emu.writeRegister("CTR", o.u(junk(call, 64)));
        long so = junk(call, 65) & 1, ov = junk(call, 66) & 1, ca = junk(call, 67) & 1;
        emu.writeRegister("xer_so", BigInteger.valueOf(so));
        emu.writeRegister("xer_ov", BigInteger.valueOf(ov));
        emu.writeRegister("xer_ca", BigInteger.valueOf(ca));
        emu.writeRegister("XER", o.u(so << 31 | ov << 30 | ca << 29));
        for (int n : new int[] {0, 1, 5, 6, 7})
            emu.writeRegister("cr" + n, BigInteger.valueOf(junk(call, 80 + n) & 15));
    }

    boolean writable(long p, int n) {
        if (!inRam(p, n)) return false;
        for (int k = 0; k < n; k++) if (o.facts.isCode(p + k)) return false;
        return true;
    }

    void write(EmulatorHelper emu, int call, long target, String[] rec) {
        for (long[] w : facts) {
            int fld = (int) w[1], n = (int) w[2];
            long p = TcBase.fold(Long.parseLong(rec[fld], 16));
            if (w[0] != target || !writable(p, n)) continue;
            byte[] b = new byte[n];
            for (int k = 0; k < n; k++) b[k] = (byte) TcJunk.at8(seed, vid, call, 0x10000L * (1 + fld), k);
            emu.writeMemory(o.toAddr(p), b);
        }
    }

    // After collect: r1, r14..r31, f14..f31.
    static void exit(TcOracle.Result res, EmulatorHelper emu) {
        TcStrict s = res.strict;
        if (s == null || (s.flags & EXIT) == 0) return;
        s.exp.append("XR ").append(TcBase.hex(s.o.reg(emu, "r1")));
        for (int i = 14; i < 32; i++) s.exp.append(' ').append(TcBase.hex(s.o.reg(emu, "r" + i)));
        for (int i = 14; i < 32; i++) s.exp.append(' ').append(TcBase.hex64(s.o.reg64(emu, "f" + i)));
        s.exp.append('\n');
    }

    static String vec(TcOracle.Result r) {
        return r.strict == null ? "" : r.strict.vec.toString();
    }

    static String exp(TcOracle.Result r) {
        return r.strict == null ? "" : r.strict.exp.toString();
    }
}

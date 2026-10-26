// A wait loop the run can never leave (for TcOracle). Not a script itself.
//
// Spawn Game_Main (8c010080) ends each frame in `while (flag == 0 && cnt == start)` on words only its interrupt
// handlers write: in the oracle nothing changes them, so every vector ran to the step limit and the function was
// refused. The rule: when the run comes back to a PC by a backward branch (taken, or a loop to itself) and the whole
// register state (every base register of the language) equals the state at an earlier visit of that PC, with no store
// and no call in between, the memory and the registers repeat and the run can never leave the loop on its own. It ends
// there as a halt of its own kind: expected.txt gets "HALT SPIN" (TcVectors.expected), and everything else the run did
// is compared as for a `bra .` (TcExit). Only a run that would have run to the step limit changes: every other run
// ends as before. The C keeps the loop and calls tc_spin() in its body (tc_rt.h); the runner reports HALT SPIN there.
//
// Opt-in: only for a function the project lists in project.json "spin_waits" with its evidence (script arg 10).
// A register and memory state that repeats is not always a wait: TC 8c04362c searches a ring buffer for a free slot,
// and on random slots with none free it laps the ring (or, end <= start, resets to one slot) for ever; its C cannot
// keep the tc_spin() contract, so its matched C timed out. Every other function keeps the step-limit refusal.
//
// Cost: nothing before FROM steps (no function's normal run is touched by it). After that, at each backward arrival a
// compare against that PC's snapshot that stops at the first register that differs, and a new snapshot only at the
// 2nd, 4th, 8th... visit since the last one (Brent's checkpoints: a cycle of any length is found). A store (a STORE or
// CALLOTHER p-code op, the delay slot's too) or a mocked call drops every snapshot.
//
// A loop that calls functions (Spawn 8c0116c0's boot wait: 240 vblanks on the vblank counter 8cfe000c, each pass
// calling Disc_PollDriveStatus) never repeats a snapshot: the call drops them, and the run ran to the call limit. A
// mocked callee's result comes from the inputs, so a repeating state would prove nothing there; such a loop is proved
// from its code instead (proves), once, at the first backward arrival at its head h after a pass from h that made a
// call. It never ends when:
//   - the loop (what h reaches, a call falling through, that reaches the branch just taken without passing h) has no
//     store, and the pass stayed in it;
//   - every branch condition, computed jump and call target in it is the same on every pass: it depends only on
//     constants, registers nothing in the loop writes (a call writes all but r8..r15, and TcLive's r8..r14) and the
//     memory those address (no store in the loop, and a mocked callee stores nothing), so every pass takes this pass's
//     path, which came back to h;
//   - every exit's condition reads no memory but words the evidence names (8 hex digits, P0..P4 bits apart): words
//     only the hardware or an interrupt changes, so a real callee cannot end the loop either.
// Then the run halts there as HALT SPIN after one pass of the calls: the C calls tc_spin() last in the loop's body.
// Any other loop with calls keeps the call limit's refusal. A pass without a call is the snapshots' as before.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Language;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.symbol.FlowType;
import java.math.BigInteger;
import java.util.*;

public class TcSpin {
    static final int FROM = 1000;      // steps before the rule looks at all
    static final int MAX_LOOP = 4096;  // instructions h may reach (more: a loop with calls is not proved)
    static Language lang;
    static Register[] regs;            // the language's base registers, context excluded

    static class Snap {
        BigInteger[] v;
        long visits, next = 2;
    }

    final TcOracle o;
    final Program prog;
    final Listing listing;
    final Map<Long, Snap> seen = new HashMap<>();
    final Map<Long, Boolean> stores = new HashMap<>();
    final Map<Long, Integer> last = new HashMap<>();     // pc: the step it last ran at
    final Map<Long, Boolean> decided = new HashMap<>();  // the head of a loop with calls: proved never to end
    int t, callAt = -1;

    TcSpin(TcOracle o) {
        this.o = o;
        prog = o.getCurrentProgram();
        listing = prog.getListing();
        if (lang != prog.getLanguage()) {
            List<Register> rs = new ArrayList<>();
            for (Register r : prog.getLanguage().getRegisters()) {
                if (r.isBaseRegister() && !r.isProcessorContext()) rs.add(r);
            }
            regs = rs.toArray(new Register[0]);
            lang = prog.getLanguage();
        }
    }

    // A mocked call: what the callee would do is unknown here, so no earlier state counts.
    void call() {
        seen.clear();
        callAt = t;
    }

    // Before stepping pca (prev: the address last stepped, -1 right after a mocked call): true when the run is in a
    // loop it never leaves.
    boolean spins(EmulatorHelper emu, long prev, Address pca, int steps) {
        long pc = pca.getOffset();
        Integer from = last.put(pc, ++t);
        if (prev >= 0 && pc <= prev && from != null && callAt >= from && !decided.containsKey(pc)) {
            boolean never = proves(emu, o.toAddr(prev), pca, from);
            decided.put(pc, never);
            if (never) return true;
        }
        if (steps < FROM) return false;
        if (prev >= 0 && pc <= prev) {
            Snap s = seen.get(pc);
            if (s == null) {
                s = new Snap();
                s.v = read(emu);
                seen.put(pc, s);
            } else {
                if (same(emu, s.v)) return true;
                if (++s.visits == s.next) {
                    s.v = read(emu);
                    s.next *= 2;
                }
            }
        }
        if (stores(pca)) seen.clear();
        return false;
    }

    BigInteger[] read(EmulatorHelper emu) {
        BigInteger[] v = new BigInteger[regs.length];
        for (int i = 0; i < regs.length; i++) v[i] = emu.readRegister(regs[i]);
        return v;
    }

    boolean same(EmulatorHelper emu, BigInteger[] v) {
        for (int i = 0; i < regs.length; i++) if (!emu.readRegister(regs[i]).equals(v[i])) return false;
        return true;
    }

    // The instruction at pc, or its delay slot, stores to memory or does something the p-code does not show.
    boolean stores(Address pc) {
        return stores.computeIfAbsent(pc.getOffset(), a -> {
            Instruction in = listing.getInstructionAt(pc);
            if (in == null || writes(in)) return true;
            if (in.getDelaySlotDepth() == 0) return false;
            Instruction slot = listing.getInstructionAt(in.getMaxAddress().next());
            return slot == null || writes(slot);
        });
    }

    static boolean writes(Instruction in) {
        for (PcodeOp op : in.getPcode()) {
            if (op.getOpcode() == PcodeOp.STORE || op.getOpcode() == PcodeOp.CALLOTHER) return true;
        }
        return false;
    }

    // A value in the loop: k known (or null), and the memory words it was read from (folded; null: an unknown one).
    // No entry for a register the loop writes: it may differ from pass to pass.
    record V(Long k, Set<Long> loads) {}

    // The loop from h to the backward branch at latch, whose pass (since step from) made a call: true when it never ends.
    boolean proves(EmulatorHelper emu, Address latch, Address h, int from) {
        Map<Address, Address[]> next = new HashMap<>();  // what h reaches in the body: each instruction's successors
        Deque<Address> todo = new ArrayDeque<>(List.of(h));
        while (!todo.isEmpty()) {
            Address a = todo.pop();
            if (next.containsKey(a)) continue;
            Instruction in = listing.getInstructionAt(a);
            if (in == null || next.size() >= MAX_LOOP) return false;
            List<Address> out = new ArrayList<>();
            if (!in.getFlowType().isCall()) out.addAll(List.of(in.getFlows()));
            if (in.getFallThrough() != null) out.add(in.getFallThrough());
            next.put(a, out.toArray(new Address[0]));
            for (Address x : out) if (!x.equals(h) && o.body.contains(x)) todo.push(x);
        }
        if (!next.containsKey(latch)) return false;
        Map<Address, List<Address>> back = new HashMap<>();
        for (Map.Entry<Address, Address[]> e : next.entrySet()) {
            for (Address x : e.getValue()) back.computeIfAbsent(x, k -> new ArrayList<>()).add(e.getKey());
        }
        Set<Address> loop = new HashSet<>(List.of(h, latch));
        todo.push(latch);
        while (!todo.isEmpty()) {
            Address a = todo.pop();
            if (!a.equals(h)) for (Address p : back.getOrDefault(a, List.of())) if (loop.add(p)) todo.push(p);
        }
        for (Map.Entry<Long, Integer> e : last.entrySet()) {  // this pass, h to latch, stayed in the loop
            if (e.getValue() >= from && !loop.contains(o.toAddr(e.getKey()))) return false;
        }
        Set<Long> w = new HashSet<>(), clobbered = new HashSet<>();  // register bytes the loop, a call writes
        for (Register r : regs) {
            String n = r.getName().toLowerCase();
            boolean kept = n.matches("r(8|9|1[0-5])");
            if (kept && o.live != null && !n.equals("r15")) {
                for (long[][] e : o.live.table.values()) kept &= e[Integer.parseInt(n.substring(1)) - 8] == TcLive.NONE;
            }
            if (!kept) mark(clobbered, r.getAddress().getOffset(), r.getMinimumByteSize());
        }
        for (Address a : loop) {
            if (stores(a)) return false;
            Instruction in = listing.getInstructionAt(a);
            for (PcodeOp op : in.getPcode()) {
                Varnode out = op.getOutput();
                if (out != null && out.isRegister()) mark(w, out.getOffset(), out.getSize());
            }
            if (in.getFlowType().isCall()) w.addAll(clobbered);
        }
        Map<Address, Map<Long, V>> state = new HashMap<>();  // at each instruction's start
        state.put(h, new HashMap<>());
        Deque<Address> work = new ArrayDeque<>(List.of(h));
        for (int budget = 64 * loop.size(); !work.isEmpty(); ) {
            if (--budget < 0) return false;
            Address a = work.pop();
            Instruction in = listing.getInstructionAt(a);
            boolean exit = false;
            for (Address x : next.get(a)) exit |= !loop.contains(x);
            Map<Long, V> s = new HashMap<>(state.get(a));
            s.keySet().removeIf(k -> k < 0);  // the last instruction's temporaries
            for (PcodeOp op : in.getPcode()) if (!step(emu, op, s, w, exit)) return false;
            if (in.getFlowType().isCall()) s.keySet().removeIf(k -> k >= 0 && overlaps(clobbered, k >> 8, (int) (k & 0xff)));
            for (Address x : next.get(a)) {
                if (x.equals(h) || !loop.contains(x)) continue;
                Map<Long, V> was = state.get(x), now = was == null ? s : merge(was, s);
                if (was == null || !now.equals(was)) {
                    state.put(x, now);
                    work.push(x);
                }
            }
        }
        return true;
    }

    // One p-code op of the loop: false when a branch, computed jump or call target may differ between passes, or an
    // exit's condition reads memory the evidence does not name.
    boolean step(EmulatorHelper emu, PcodeOp op, Map<Long, V> s, Set<Long> w, boolean exit) {
        int c = op.getOpcode();
        if (c == PcodeOp.CBRANCH || c == PcodeOp.BRANCHIND || c == PcodeOp.CALLIND) {
            V x = value(emu, op.getInput(c == PcodeOp.CBRANCH ? 1 : 0), s, w);
            return x != null && !(c == PcodeOp.CBRANCH && exit && (x.loads() == null || !o.spinWords.containsAll(x.loads())));
        }
        Varnode out = op.getOutput();
        if (out == null) return true;
        V v;
        if (c == PcodeOp.LOAD) {  // a constant address is PC-relative: a literal-pool word, code that never changes
            Varnode at = op.getInput(1);
            V a = value(emu, at, s, w);
            v = a == null ? null : at.isConstant() ? new V(pool(a.k(), out.getSize()), a.loads())
                : new V(null, a.k() == null || a.loads() == null ? null : with(a.loads(), Set.of(fold(a.k()))));
        } else {
            Long[] k = new Long[op.getNumInputs()];
            Set<Long> loads = Set.of();
            boolean known = true, same = true;
            for (int i = 0; i < k.length && same; i++) {
                V x = value(emu, op.getInput(i), s, w);
                same = x != null;
                if (!same) break;
                k[i] = x.k();
                known &= x.k() != null;
                loads = loads == null || x.loads() == null ? null : with(loads, x.loads());
            }
            v = same ? new V(known ? eval(c, k, op, out.getSize()) : null, loads) : null;
        }
        long key = key(out);
        if (key >= 0) s.keySet().removeIf(j -> j >= 0 && overlap(j, key));
        if (v != null) s.put(key, v);
        else s.remove(key);
        return true;
    }

    // A varnode's value on this pass, the same on every pass; null when it may differ.
    V value(EmulatorHelper emu, Varnode x, Map<Long, V> s, Set<Long> w) {
        if (x.isConstant()) return new V(x.getOffset(), Set.of());
        if (x.isUnique() || x.isRegister() && s.containsKey(key(x))) return s.get(key(x));
        if (x.isAddress()) return new V(null, Set.of(fold(x.getOffset())));
        if (!x.isRegister() || overlaps(w, x.getOffset(), x.getSize())) return null;
        Register r = prog.getRegister(x);  // nothing in the loop writes it: its value now
        return new V(r == null ? null : mask(emu.readRegister(r).longValue(), x.getSize()), Set.of());
    }

    Long pool(long a, int size) {
        try {
            Address at = o.toAddr(a);
            return size == 4 ? prog.getMemory().getInt(at) & 0xffffffffL : size == 2 ? prog.getMemory().getShort(at) & 0xffffL : null;
        } catch (Exception e) {
            return null;
        }
    }

    // Enough of the p-code to follow an address; anything else is the same on every pass, its value unknown.
    static Long eval(int c, Long[] k, PcodeOp op, int size) {
        long a = k.length > 0 ? k[0] : 0, b = k.length > 1 ? k[1] : 0, v;
        switch (c) {
            case PcodeOp.COPY: case PcodeOp.INT_ZEXT: v = a; break;
            case PcodeOp.INT_SEXT: int n = 64 - 8 * op.getInput(0).getSize(); v = n <= 0 ? a : a << n >> n; break;
            case PcodeOp.INT_ADD: v = a + b; break;
            case PcodeOp.INT_SUB: v = a - b; break;
            case PcodeOp.INT_AND: v = a & b; break;
            case PcodeOp.INT_OR: v = a | b; break;
            case PcodeOp.INT_LEFT: v = b >= 64 ? 0 : a << b; break;
            case PcodeOp.SUBPIECE: v = b >= 8 ? 0 : a >>> (8 * b); break;
            default: return null;
        }
        return mask(v, size);
    }

    static Map<Long, V> merge(Map<Long, V> a, Map<Long, V> b) {
        Map<Long, V> m = new HashMap<>();
        for (Map.Entry<Long, V> e : a.entrySet()) {
            V x = e.getValue(), y = b.get(e.getKey());
            if (y == null) continue;
            m.put(e.getKey(), x.equals(y) ? x : new V(Objects.equals(x.k(), y.k()) ? x.k() : null,
                x.loads() == null || y.loads() == null ? null : with(x.loads(), y.loads())));
        }
        return m;
    }

    static Set<Long> with(Set<Long> a, Set<Long> b) {
        Set<Long> u = new TreeSet<>(a);
        u.addAll(b);
        return u;
    }

    static Set<Long> with(Set<Long> a, long x) {
        return with(a, Set.of(x));
    }

    static long fold(long a) {
        return a & 0x1fffffffL;
    }

    static long mask(long v, int size) {
        return size >= 8 ? v : v & ((1L << (8 * size)) - 1);
    }

    // A register varnode: offset << 8 | size; a temporary: below 0.
    static long key(Varnode x) {
        long k = x.getOffset() << 8 | x.getSize();
        return x.isUnique() ? -1 - k : k;
    }

    static boolean overlap(long k, long j) {
        long a = k >> 8, b = j >> 8;
        return a < b + (j & 0xff) && b < a + (k & 0xff);
    }

    static void mark(Set<Long> bytes, long off, int size) {
        for (int i = 0; i < size; i++) bytes.add(off + i);
    }

    static boolean overlaps(Set<Long> bytes, long off, int size) {
        for (int i = 0; i < size; i++) if (bytes.contains(off + i)) return true;
        return false;
    }
}

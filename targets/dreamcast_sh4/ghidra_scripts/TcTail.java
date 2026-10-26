// Shared tails: code a function runs outside its body (for TcPrepare, TcOracle and TcFlow). Not a script itself.
//
// A branch (bra, bt/bf, a jump with references) or a fall-through out of a function's body to an address that is not
// a function entry runs code Ghidra gave to another function, or to none:
//   hand-written asm ending in a branch into the end of another routine (Toy Commander's Vec3_NormalizeOrZero and
//   Vec3_NormalizeSafe into Vec3_Normalize at 8c061308, Mat3_Mul into 8c0615e4);
//   SHC's cross-jumping, one copy of a statement tail several functions share (Spawn 8c051fe0 into 8c051f50).
// That code is part of what the function does. Its extent is the body plus every instruction reached that way,
// following fall-throughs (also after a call returns), delay slots and jump references, and stopping at the body and at
// function entries (a tail call: a callee, mocked and compared). TcPrepare lists, blocks, covers and runs the extent;
// TcFlow reads the registers through it. A PR-redirected tail call (prContinuations) adds the code its callee returns
// to, even at a function entry: that code pops the function's own frame (Toy Commander 8c061aac). So does code that
// runs on (no jump) into a function entry (runOn: TC 8c061630 into 8c06163e after the entry split). A non-instruction
// is not followed: a run reaching one is refused (TcOracle.entersCallee), like a jump without references into another
// function. The catalog fixup TcCont decodes a continuation Ghidra left as undefined bytes and makes the code of it
// that lies in no function part of the redirecting function's body (TC 8c061ec4 returns into 8c061ee4).
// Such a tail is not split off as a function of its own: its entry registers are often outside the C ABI (the
// Vec3_Normalize tail takes the vector in fr0..fr3 and the outputs in r0, r1 and r4), so neither it nor a caller could
// be tested on them, and SHC's shared tails are statements the source repeated in each function.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;

public class TcTail {
    // f's body plus the shared tails it runs (the body itself when there are none).
    static AddressSetView extent(Program prog, Function f) {
        AddressSetView body = f.getBody();
        Listing listing = prog.getListing();
        FunctionManager fm = prog.getFunctionManager();
        AddressSet tail = new AddressSet();
        Deque<Address> work = new ArrayDeque<>();
        Set<Address> pr = new HashSet<>(); // PR continuations and run-ons: followed even into a function entry
        for (Instruction in : listing.getInstructions(body, true)) follow(prog, in, work, pr);
        while (!work.isEmpty()) {
            Address a = work.poll();
            if (body.contains(a) || tail.contains(a) || (fm.getFunctionAt(a) != null && !pr.contains(a))) continue;
            Instruction in = listing.getInstructionAt(a);
            if (in == null) continue;
            tail.add(in.getMinAddress(), in.getMaxAddress());
            follow(prog, in, work, pr);
            // A continuation that is another branch's delay slot (8c061eac returns to 8c061e60, the `_frchg` of a
            // `bsr` in 8c061e54) runs as a plain instruction there, and then falls through.
            if (pr.contains(a) && in.isInDelaySlot()) work.add(in.getMaxAddress().next());
        }
        return tail.isEmpty() ? body : body.union(tail);
    }

    static void follow(Program prog, Instruction in, Deque<Address> work, Set<Address> pr) {
        work.addAll(exits(prog, in));
        Address c = prTarget(prog, in);
        if (c != null) pr.add(c);
        Address on = runOn(in);
        if (on != null && prog.getFunctionManager().getFunctionAt(on) != null) pr.add(on);
    }

    // Where `in` runs on to without a jump: its fall-through, or after its delay slot (a call's return, a conditional
    // branch not taken); null for a delay slot (its branch's business) and an instruction that does not run on. Code
    // that runs on into another function's entry runs that function's code as its own, with no call and no return of
    // its own (Toy Commander 8c061630 saves PR in r2 and runs into 8c06163e, which returns by `jmp @r2`; 8c061a98's
    // `bsr` returns into 8c061aa0, which pops the PR 8c061a98 pushed): a mock there would return to the PR of the
    // last call, back into the function, without end. A run-on entry is followed like a PR continuation, but only
    // with PR not the function's own (callPr): with it (Spawn 8c020560 pops r13, r14 and runs into 8c0205a4), the
    // entry's code returns to the caller as a tail call's callee does, and stays a mocked tail call. Not after a call
    // to a callee that halts (TcExit: its run ends there; what follows the call is the next function).
    static Address runOn(Instruction in) {
        if (in.isInDelaySlot() || !in.getFlowType().hasFallthrough() || !callPr(in)) return null;
        if (in.getFlowType().isCall()) {
            for (Address t : in.getFlows()) if (TcExit.halts(in.getProgram().getListing(), t)) return null;
        }
        Instruction slot = in.getDelaySlotDepth() > 0 ? in.getNext() : null;
        return slot != null && slot.isInDelaySlot() ? slot.getMaxAddress().next() : in.getFallThrough();
    }

    // PR holds a return address into this code where `in` runs on: `in` is a call, or the straight line before it
    // (back to an instruction control can also enter from elsewhere) has a call after the last `lds`/`lds.l` to PR.
    static boolean callPr(Instruction in) {
        ReferenceManager refs = in.getProgram().getReferenceManager();
        Instruction p = in;
        for (int k = 0; k < LOOKBACK * 4 && p != null; k++, p = p.getPrevious()) {
            if (p.getFlowType().isCall()) return true;
            if (p.toString().toLowerCase().replaceFirst("^_", "").matches("^lds(\\.l)?\\s+\\S+,pr$")) return false;
            if (entered(refs, p)) return false;
        }
        return false;
    }

    // PR-redirected tail calls: `lds rN,PR` with rN a literal-pool constant (or `mova`) naming code, then a jump to a
    // callee (`bra`/`jmp`): the callee's rts returns to that code, so it runs after the call, as part of the function
    // (Toy Commander 8c061aac continues at 8c061aa0 inside 8c061a98). Returns {continuation -> the redirected jump}.
    // callJumps (callee summaries): a jump Ghidra typed as a call without a fall-through (8c061aac's `bra 0x8c061a60`,
    // a call terminator) is the redirected jump too; without them only a plain jump is (as before).
    static Map<Address, Address> prContinuations(Program prog, AddressSetView code, boolean callJumps) {
        Map<Address, Address> out = new LinkedHashMap<>();
        Listing listing = prog.getListing();
        for (Instruction in : listing.getInstructions(code, true)) {
            Address c = prTarget(prog, in);
            Address j = c == null ? null : redirectedJump(in, callJumps);
            if (j != null) out.putIfAbsent(c, j);
        }
        return out;
    }

    // The jump a PR load `in` redirects: the unconditional jump whose delay slot it is, else the next one on the
    // straight line after it (past calls, which return to it); null when the line ends or forks first.
    static Address redirectedJump(Instruction in, boolean callJumps) {
        Instruction own = in.isInDelaySlot() ? in.getPrevious() : null; // `bra x` / `_lds rN,PR` (8c061eac)
        if (own != null && !own.getFlowType().isConditional() && (own.getFlowType().isJump()
                || (callJumps && own.getFlowType().isCall() && !own.getFlowType().hasFallthrough()))) {
            return own.getAddress();
        }
        Instruction j = in;
        for (int k = 0; k < 8 && j != null; k++) {
            j = j.getNext();
            if (j == null || j.isInDelaySlot()) continue;
            boolean tail = callJumps && j.getFlowType().isCall() && !j.getFlowType().hasFallthrough();
            if (j.getFlowType().isCall() && !tail) continue;
            if (tail || (j.getFlowType().isJump() && !j.getFlowType().isConditional())) return j.getAddress();
            if (j.getFlowType().isTerminal() || j.getFlowType().isConditional()) return null;
        }
        return null;
    }

    // The code address `lds rN,PR` loads (prValue), when an instruction is there; else null.
    static Address prTarget(Program prog, Instruction in) {
        Address a = prValue(prog, in);
        return a != null && prog.getListing().getInstructionAt(a) != null ? a : null;
    }

    // The address `lds rN,PR` loads, when rN was set from a literal-pool constant or by `mova` on the straight
    // line before it; else null. The look-back runs up to LOOKBACK instructions (Toy Commander 8c061eac: the
    // constant is 8 instructions before the `lds r0,PR` in its `bra` delay slot) and gives up at anything that makes
    // the line not straight (a branch target, a call, a jump or a delay slot other than the lds's own branch) or
    // writes rN in a form without a `,rN` destination (dt, movt, shifts and rotates, @rN+ / @-rN addressing).
    // TcCont uses it before the code there is decoded (TC 8c061ec4's continuation 8c061ee4).
    static Address prValue(Program prog, Instruction in) {
        java.util.regex.Matcher m = LDS_PR.matcher(in.toString().toLowerCase().replaceFirst("^_", ""));
        if (!m.matches()) return null;
        String reg = m.group(1);
        ReferenceManager refs = prog.getReferenceManager();
        Instruction p = in;
        for (int k = 0; k < LOOKBACK; k++) {
            Instruction next = p;
            p = p.getPrevious();
            if (p == null) return null;
            boolean owner = k == 0 && in.isInDelaySlot(); // the branch whose delay slot the lds is
            if (!owner && entered(refs, next)) return null;
            if (!owner && (p.isInDelaySlot() || p.getFlowType().isCall() || p.getFlowType().isJump()
                || p.getFlowType().isTerminal())) return null;
            String t = p.toString().toLowerCase().replaceFirst("^_", "");
            // @-r1 must not match @-r15 (8c061698: `fmov.s fr13,@-r15` before `lds r1,PR` is not a write of r1)
            if (t.matches(ONE_OPERAND_WRITE + reg + "$") || t.contains("@" + reg + "+")
                || t.matches(".*@-" + reg + "(?!\\d).*")) return null;
            if (!t.matches("\\S+\\s+.*," + reg + "$")) continue; // does not write reg
            Long v = null;
            if (t.startsWith("mova ")) {
                for (Reference r : p.getReferencesFrom()) if (r.getToAddress().isMemoryAddress()) v = r.getToAddress().getOffset();
            } else if (t.startsWith("mov.l ") && !t.contains("@")) {
                for (Reference r : p.getReferencesFrom()) {
                    if (!r.getReferenceType().isRead() || !r.getToAddress().isMemoryAddress()) continue;
                    try {
                        v = prog.getMemory().getInt(r.getToAddress()) & 0xffffffffL;
                    } catch (Exception e) {
                        return null;
                    }
                }
            }
            if (v == null) return null;
            return prog.getAddressFactory().getDefaultAddressSpace().getAddress(TcBase.fold(v));
        }
        return null;
    }

    static final java.util.regex.Pattern LDS_PR = java.util.regex.Pattern.compile("^lds\\s+(r\\d+),pr$");
    static final int LOOKBACK = 12;
    static final String ONE_OPERAND_WRITE = "^(dt|movt|shll\\d*|shlr\\d*|shal|shar|rotl|rotr|rotcl|rotcr)\\s+";

    // Control can reach `in` other than from the instruction before it (a branch or call targets it).
    static boolean entered(ReferenceManager refs, Instruction in) {
        for (Reference r : refs.getReferencesTo(in.getAddress())) if (r.getReferenceType().isFlow()) return true;
        return false;
    }

    // Where control goes after `in` other than into a call: its fall-through (after a call, the return), its delay
    // slot and what follows the slot, and the targets of its jump references. A delay slot's own fall-through is its
    // branch's business (after `bra` or `rts` it is not executed).
    static List<Address> exits(Program prog, Instruction in) {
        List<Address> out = new ArrayList<>();
        Address pr = prTarget(prog, in); // a PR continuation (prContinuations) runs after the redirected jump's callee
        if (pr != null) out.add(pr);
        if (in.isInDelaySlot()) return out;
        Address ft = in.getFallThrough();
        if (ft != null) out.add(ft);
        Instruction slot = in.getDelaySlotDepth() > 0 ? in.getNext() : null;
        if (slot != null && slot.isInDelaySlot()) {
            out.add(slot.getAddress());
            if (in.getFlowType().hasFallthrough()) out.add(slot.getMaxAddress().next());
        }
        if (!in.getFlowType().isCall()) {
            for (Reference r : in.getReferencesFrom()) if (r.getReferenceType().isJump()) out.add(r.getToAddress());
        }
        return out;
    }

    // The entries of other functions an extent branches to without a call (SP 8c0587b0 `bra 0x8c0588d0`: a tail call
    // Function.getCalledFunctions does not list). Callee summaries (TcSum) treat them as tail calls.
    static Set<Address> jumpCallees(Program prog, AddressSetView extent) {
        Set<Address> out = new HashSet<>();
        FunctionManager fm = prog.getFunctionManager();
        for (Instruction in : prog.getListing().getInstructions(extent, true)) {
            for (Reference r : in.getReferencesFrom()) {
                Address to = r.getToAddress();
                if (r.getReferenceType().isFlow() && !r.getReferenceType().isCall() && !extent.contains(to)
                        && fm.getFunctionAt(to) != null) out.add(to);
            }
        }
        return out;
    }

    // The entries of the functions the tail part of an extent calls or jumps to (Function.getCalledFunctions covers
    // the body only).
    static Set<Address> tailCallees(Program prog, Function f, AddressSetView extent) {
        Set<Address> out = new HashSet<>();
        FunctionManager fm = prog.getFunctionManager();
        AddressSetView tail = extent.subtract(f.getBody());
        for (Instruction in : prog.getListing().getInstructions(tail, true)) {
            for (Reference r : in.getReferencesFrom()) {
                if (r.getReferenceType().isFlow() && fm.getFunctionAt(r.getToAddress()) != null) out.add(r.getToAddress());
            }
        }
        return out;
    }
}

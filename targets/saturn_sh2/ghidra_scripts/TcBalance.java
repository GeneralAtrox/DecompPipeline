// Stack balance, report only (TcPrepare: oracle_summary.json "stack_balance"; core/check.py copies it into the check
// result; no verdict depends on it): code that pops a callee-saved register (r8..r14, PR) it never pushed, i.e. a pop
// at or above the entry r15 (an epilogue fragment restoring its caller's registers, like Toy Commander's false entry
// 8c02a814 inside 8c02a810's prologue), or that leaves (rts, or a jump out of the code to another function) with r15
// different from its entry value. Not a script itself.
//
// One pass in address order over the function's code (TcTail.extent) like TcStack: pushes and pops (`mov.l Rm,@-r15`,
// `sts.l PR,@-r15`, `fmov.s`, an 8-byte `fmov DRm`), `add #imm,r15`, frame-pointer copies (`mov r15,rN`, `mov rN,r15`,
// `add #imm,rN`), the depth recorded at each branch target after the delay slot. r15 set any other way, a branch
// target reached with two depths, or a non-constant `add rN,r15` stops the pass with what it found so far. Code is
// tracked from the entry (depth 0) and the branch targets it reaches; leaving is rts, a jump to another function, or
// running on into another function's entry. A computed jump Ghidra knows no target of also stops the pass. A
// PR-redirected jump (TcTail.prContinuations: the return-address trick, Toy Commander 8c061aac, 8c061ec4) is a call,
// not leaving: its callee returns to the continuation, reached with the depth after the jump's delay slot.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;

public class TcBalance {
    static final Set<String> SAVED = Set.of("r8", "r9", "r10", "r11", "r12", "r13", "r14", "pr");

    static List<String> scan(Program prog, AddressSetView code, Address entry) {
        Listing listing = prog.getListing();
        FunctionManager fm = prog.getFunctionManager();
        Map<Address, List<Address>> redirect = new HashMap<>(); // a redirected jump -> its continuations
        for (Map.Entry<Address, Address> e : TcTail.prContinuations(prog, code, true).entrySet()) {
            redirect.computeIfAbsent(e.getValue(), k -> new ArrayList<>()).add(e.getKey());
        }
        Map<Address, Integer> atLabel = new HashMap<>(Map.of(entry, 0));
        List<String> out = List.of();
        for (int pass = 0; pass < 3; pass++) { // again while a pass found new branch targets (code below the entry)
            int n = atLabel.size();
            out = pass(listing, fm, code, atLabel, redirect);
            if (atLabel.size() == n) break;
        }
        return out;
    }

    static List<String> pass(Listing listing, FunctionManager fm, AddressSetView code, Map<Address, Integer> atLabel,
            Map<Address, List<Address>> redirect) {
        List<String> out = new ArrayList<>();
        Map<String, Integer> frame = new HashMap<>(); // register -> entry r15 minus this many bytes
        Integer depth = null;                         // bytes r15 is below its entry value; null: not reached
        List<Address> pending = new ArrayList<>();    // a branch's targets, recorded after its delay slot
        boolean exitPending = false, endPending = false, afterEnd = false;
        for (Instruction in : listing.getInstructions(code, true)) {
            Integer d = atLabel.get(in.getAddress());
            if (d != null && depth != null && !afterEnd && !d.equals(depth)) return stop(out, in, "two depths");
            if (d != null) depth = d;
            else if (afterEnd) depth = null;
            if (d != null || afterEnd) frame.clear();
            afterEnd = false;
            if (depth == null) continue;
            String t = in.toString().toLowerCase().replace("_", "");
            String mn = t.split(" ")[0];
            String[] ops = t.contains(" ") ? t.substring(t.indexOf(' ') + 1).split(",") : new String[0];
            String src = ops.length > 0 ? ops[0].trim() : "", dst = ops.length > 1 ? ops[ops.length - 1].trim() : "";
            int size = mn.endsWith(".b") ? 1 : mn.endsWith(".w") ? 2 : src.startsWith("dr") || src.startsWith("xd")
                || dst.startsWith("dr") || dst.startsWith("xd") ? 8 : 4;
            if (dst.equals("@-r15")) {
                depth += size;
            } else if (src.equals("@r15+")) {
                if (depth <= 0 && SAVED.contains(dst)) out.add(in.getAddress() + ": pops " + dst + " it never pushed");
                depth -= size;
            } else if (mn.equals("add") && dst.equals("r15")) {
                if (!src.startsWith("#")) return stop(out, in, "r15 += a register");
                depth -= Long.decode(src.substring(1)).intValue();
            } else if (mn.equals("mov") && dst.equals("r15") && frame.containsKey(src)) {
                depth = frame.get(src);
            } else if (in.getDelaySlotDepth() == 0 && writes(in, "r15")) { // a branch's results include its slot's
                return stop(out, in, "r15 set another way");
            } else if (mn.equals("mov") && src.equals("r15") && dst.matches("r\\d+")) {
                frame.put(dst, depth);
            } else if (mn.equals("add") && src.startsWith("#") && frame.containsKey(dst)) {
                frame.put(dst, frame.get(dst) - Long.decode(src.substring(1)).intValue());
            } else if (in.getDelaySlotDepth() == 0) {
                for (String r : new ArrayList<>(frame.keySet())) if (writes(in, r)) frame.remove(r);
            }
            if (in.isInDelaySlot() || in.getDelaySlotDepth() == 0) {  // a branch's effect, after its delay slot
                for (Address a : pending) atLabel.putIfAbsent(a, depth);
                pending.clear();
                leave(out, in, depth, exitPending || (in.isInDelaySlot() && !endPending && fallsOut(in, fm, code)));
                exitPending = false;
                afterEnd = endPending;
                endPending = false;
            }
            FlowType ft = in.getFlowType();
            if (in.isInDelaySlot() || ft.isCall() && ft.hasFallthrough()) continue;
            List<Address> cont = redirect.get(in.getAddress()); // a redirected jump: a call returning to the continuation
            boolean out1 = ft.isTerminal() && cont == null, flows = false;  // rts (Ghidra types 8c061aac's jump terminal)
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isFlow()) continue;
                Address to = r.getToAddress();
                flows = true;
                if (code.contains(to)) pending.add(to);
                else if (fm.getFunctionAt(to) != null && cont == null) out1 = true; // a tail jump to another function
            }
            if (cont != null) pending.addAll(cont);
            else if (ft.isComputed() && ft.isJump() && !flows) return out; // `jmp @rN`, no target known: stop
            boolean ends = !ft.hasFallthrough();
            if (in.getDelaySlotDepth() == 0) {
                for (Address a : pending) atLabel.putIfAbsent(a, depth);
                pending.clear();
                leave(out, in, depth, out1 || (!ends && fallsOut(in, fm, code)));
                afterEnd = ends;
            } else {
                exitPending = out1;
                endPending = ends;
            }
        }
        return out;
    }

    // Runs on into another function's entry (a fall-into entry: the code falls out of its extent).
    static boolean fallsOut(Instruction in, FunctionManager fm, AddressSetView code) {
        Address next = in.getAddress().add(in.getLength());
        return !code.contains(next) && fm.getFunctionAt(next) != null;
    }

    static void leave(List<String> out, Instruction in, int depth, boolean exits) {
        if (exits && depth != 0) {
            out.add(in.getAddress() + ": leaves with r15 " + Math.abs(depth) + " bytes "
                + (depth > 0 ? "below" : "above") + " its entry value");
        }
    }

    static boolean writes(Instruction in, String reg) {
        for (Object o : in.getResultObjects()) {
            if (o instanceof ghidra.program.model.lang.Register
                && ((ghidra.program.model.lang.Register) o).getName().equalsIgnoreCase(reg)) return true;
        }
        return false;
    }

    static List<String> stop(List<String> out, Instruction in, String why) {
        return out; // report only: an untracked r15 ends the pass without a finding
    }
}

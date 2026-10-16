// Stack parameters Ghidra's decompiler misses, for prototype repair (TcBase.prototypeOf). Not a script itself.
//
// A callee's 5th+ integer (or 9th+ float) argument is the word at @(0,r15), @(4,r15), ... at its entry. Ghidra
// misses some reads of it, e.g. `mov.l @(r0,r15),r8` with r0 = frame size (8c04cc28). This pass tracks r15's depth
// below its entry value through the code in address order (pushes, pops, `add #imm,r15`, `add rN,r15` with a
// known constant, frame-pointer copies `mov r15,rN`), restoring the depth recorded at each branch target, and
// reports every word read at or above the entry r15.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.util.function.Function;

public class TcStack {
    static final int MAX_OFFSET = 32; // the judge compares up to 8 stack words at a call (s0..s7)
    static final Map<Integer, String> where = new TreeMap<>(); // offset -> first reading instruction (last call)

    // Entry-relative offsets (0, 4, ...) of stack words the function reads before any write to them.
    static SortedSet<Integer> paramReads(Listing listing, ghidra.program.model.listing.Function f,
                                         Function<Instruction, Long> pool) {
        SortedSet<Integer> reads = new TreeSet<>(), written = new TreeSet<>();
        where.clear();
        Map<Address, Integer> atLabel = new HashMap<>();
        Map<String, Long> konst = new HashMap<>();   // register -> known constant
        Map<String, Integer> frame = new HashMap<>(); // register -> entry r15 minus this many bytes
        Integer depth = 0;                            // bytes r15 is below its entry value; null when unknown
        List<Address> pendingTargets = new ArrayList<>();
        boolean afterJump = false, pendingJump = false;
        for (Instruction in : listing.getInstructions(f.getBody(), true)) {
            Integer d = atLabel.get(in.getAddress());
            if (d != null) {
                depth = d;
            } else if (afterJump) {
                depth = null; // unreachable fall-through or a label only reached by an untracked jump
            }
            if (afterJump || d != null) {
                konst.clear();
                frame.clear();
            }
            afterJump = false;
            if (depth == null) continue;
            String t = in.toString().toLowerCase().replace("_", "");
            String mn = t.split(" ")[0];
            String[] ops = t.contains(" ") ? t.substring(t.indexOf(' ') + 1).split(",") : new String[0];
            String src = ops.length > 0 ? ops[0].trim() : "", dst = ops.length > 1 ? ops[ops.length - 1].trim() : "";
            int size = mn.endsWith(".b") ? 1 : mn.endsWith(".w") ? 2 : 4;
            // memory reads relative to r15 or a frame pointer
            Integer off = null;
            String mem = t.contains("@") ? t.substring(t.indexOf('@')) : "";
            boolean isRead = t.indexOf('@') >= 0 && t.indexOf('@') < t.indexOf(',') && !mem.startsWith("@-");
            java.util.regex.Matcher m;
            if ((m = java.util.regex.Pattern.compile("^@\\((0x[0-9a-f]+|\\d+),(r\\d+)\\)").matcher(mem)).find()) {
                off = base(m.group(2), depth, frame, Long.decode(m.group(1)));
            } else if ((m = java.util.regex.Pattern.compile("^@\\(r0,(r\\d+)\\)").matcher(mem)).find()) {
                Long r0 = konst.get("r0");
                if (r0 != null) off = base(m.group(1), depth, frame, r0);
            } else if ((m = java.util.regex.Pattern.compile("^@(r\\d+)(?![+\\d])").matcher(mem)).find()) {
                off = base(m.group(1), depth, frame, 0L);
            }
            if (off != null && off >= 0 && off < MAX_OFFSET) {
                int w = off & ~3;
                if (isRead && !written.contains(w)) {
                    if (reads.add(w)) where.put(w, in.getAddress() + " " + t + " depth " + depth);
                }
                else if (!isRead) written.add(w);
            }
            // r15 / frame-pointer / constant tracking
            if (mem.startsWith("@-r15")) {
                depth += mn.startsWith("fmov") && !mn.endsWith(".s") ? 4 : size;
            } else if (src.equals("@r15+")) {
                depth -= mn.startsWith("fmov") && !mn.endsWith(".s") ? 4 : size;
            } else if (mn.equals("add") && dst.equals("r15")) {
                Long v = src.startsWith("#") ? Long.decode(src.substring(1)) : konst.get(src);
                if (v == null) return reads; // unknown frame size: stop rather than guess
                depth -= v.intValue();
            } else if (mn.equals("mov") && dst.equals("r15") && frame.containsKey(src)) {
                depth = frame.get(src); // frame restore
            } else if (dst.equals("r15")) {
                return reads; // r15 set some other way: stop
            } else if (mn.equals("mov") && src.equals("r15") && dst.matches("r\\d+")) {
                frame.put(dst, depth);
                konst.remove(dst);
            } else if (mn.equals("add") && src.startsWith("#") && frame.containsKey(dst)) {
                frame.put(dst, frame.get(dst) - Long.decode(src.substring(1)).intValue());
            } else if (dst.matches("r\\d+")) {
                frame.remove(dst);
                Long v = mn.equals("mov") && src.startsWith("#") ? Long.decode(src.substring(1)) : pool.apply(in);
                if (v != null && (mn.equals("mov") || mn.equals("mov.w") || mn.equals("mov.l"))) konst.put(dst, v);
                else konst.remove(dst);
            }
            if (mn.equals("jsr") || mn.equals("bsr") || mn.equals("bsrf")) {
                for (String r : new String[] {"r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7"}) {
                    konst.remove(r);
                    frame.remove(r);
                }
            }
            // a branch takes effect after its delay slot: record targets (and stop) after the slot instruction
            boolean slot = in.getDelaySlotDepth() > 0;
            List<Address> targets = new ArrayList<>();
            for (Reference r : in.getReferencesFrom()) {
                if (r.getReferenceType().isJump()) targets.add(r.getToAddress());
            }
            FlowType ft = in.getFlowType();
            boolean stops = ft.isTerminal() || (ft.isJump() && !ft.isConditional());
            if (!slot) {
                for (Address a : pendingTargets) atLabel.putIfAbsent(a, depth);
                pendingTargets.clear();
                afterJump = pendingJump;
                pendingJump = false;
            }
            if (slot) {
                pendingTargets.addAll(targets);
                pendingJump = stops;
            } else {
                for (Address a : targets) atLabel.putIfAbsent(a, depth);
                afterJump |= stops;
            }
        }
        return reads;
    }

    // Entry-relative offset of base register + disp, or null when the base is not r15 or a frame pointer.
    static Integer base(String reg, int depth, Map<String, Integer> frame, long disp) {
        if (reg.equals("r15")) return (int) disp - depth;
        Integer d = frame.get(reg);
        return d == null ? null : (int) disp - d;
    }
}

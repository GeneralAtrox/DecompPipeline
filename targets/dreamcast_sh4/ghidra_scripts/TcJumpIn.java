// Jump-in continuations: code a function reaches by `jmp @rN` to a constant address while its frame is live, which
// Ghidra made a function of its own. Spawn 8c0129c6 pushes r8..r14 and PR, then leaves its body by
// `mov.l 0x8c012a38,r2` (= 0x0c012bf0) / `jmp @r2` at three places (to 8c012b90, 8c012be0, 8c012bf0); that code calls
// 8c05a000, pops 8c0129c6's frame and returns for it. Ghidra typed each jump as a tail call and made functions at the
// three targets. Neither side could be tested: a target run alone pops a frame it never pushed and returns to a popped
// PR of 0 ("jumps outside its body to 00000000"), and the caller's mocked "tail call" returned to the PR of its last
// `jsr`, looping until the call cap (660 of 768 vectors), or left r8..r14 unknown (TcLive "?").
//
// The rule, for every function G in the game ranges that is not a catalog_overrides "entry":
//  1. only jumps reach G (onlyJumps: `jmp` or a branch, no call, no data pointer), from other functions: the jumps'
//     functions, and the function of the instruction before G when it falls through into G, reach G;
//  2. G is reached with the frame live: no jump to G gave back stack first (tailCall: a pop or `add #+n,r15` in the
//     jump's block or delay slot, PS2 8c0aff20 `lds.l @r15+,pr` / `bra 0x8c0af1e8`), and a G a jump reaches opens no
//     frame of its own (PS2 8c052972 pushes r14 and PR: one of frameless dispatcher 8c052876's tail-called handlers);
//     a G only fall-through reaches may (PS2 8c17cb34 is the `sts.l pr` in the middle of 8c17cb20's prologue);
//  3. those come down to one F with a frame (roots): a frameless one that only jumps reach stands for those that
//     reach it (Spawn ovl 8c5a7e6e and 8c5a7c5e jump to each other, and only 8c5a7ba0 saves the frame);
//  4. G is F's code (continues): what runs from G comes back into F's body (8c5a7e6e falls through 8c5a7e9a into
//     8c5a7ba0's epilogue) or restores callee-saved registers F saves and it never did (8c0129c6's three targets;
//     8c5a70c0's epilogue 8c5a73a2, reached by `jmp @r3` and by `bt`).
// Then G stops being a function: its jump references become computed jumps (flow overrides cleared), G is removed and
// F's body is recomputed from flow, so G's code (in no other function) joins F. Code F or G held that the new body
// leaves out and no other function holds stays in F's body: no code is left in no function (Ghidra had given PS2
// 8c079cc8 the case targets of switch 8c07a72e, which flow alone drops). TcPrepare, the packet and the oracle
// then run it as F's own code. A target two frames reach (a shared tail, Toy Commander's math library) is kept, as is
// a real function (called, or returning by itself). The rule runs again until nothing changes: a joined
// continuation's own jump target (a loop body that falls back into it) is F's only once the continuation is.
//
// Script args: 0 output file (one line per removed entry: entry, the function it joined, the jumps); 1 the game
// ranges, "lo-hi,lo-hi" in hex; 2 (optional) the catalog_overrides file. Runs on the master after TcOrphans, before
// TcPrune, TcSwitch and TcCont (core/ghidra.py). Idempotent: a joined entry is no function, so a re-run finds nothing.
//@category DecompPipeline
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class TcJumpIn extends GhidraScript {
    static final int WALK = 4096;  // instructions; a longer walk from G is no continuation
    final List<long[]> game = new ArrayList<>();
    final Set<Address> keep = new HashSet<>();

    boolean inGame(long a) {
        for (long[] r : game) if (a >= r[0] && a < r[1]) return true;
        return game.isEmpty();
    }

    /** The functions with a frame that reach x: a frameless function reaching it that only jumps reach itself (a
     *  piece of the same routine) stands for those that reach it. null when some other function reaches x. */
    Set<Function> roots(Address x, Map<Address, Set<Function>> from, Map<Function, Boolean> frames, Listing listing) {
        Set<Function> out = new LinkedHashSet<>();
        Set<Address> seen = new HashSet<>(List.of(x));
        Deque<Function> work = new ArrayDeque<>(from.get(x));
        while (!work.isEmpty()) {
            Function f = work.poll();
            if (frames.computeIfAbsent(f, k -> !saved(listing, k).isEmpty())) out.add(f);
            else if (!from.containsKey(f.getEntryPoint())) return null;
            else if (seen.add(f.getEntryPoint())) work.addAll(from.get(f.getEntryPoint()));
        }
        return out;
    }

    /** The callee-saved registers f pushes in its prologue. A function with none has no frame of its own, so nothing
     *  joins it (not one piece into another: Spawn ovl 8c5a9400 into the 10-instruction 8c5a9660 they both jump
     *  between). Not its whole body: Ghidra's body can hold other functions' code (PS2 8c052876, a frameless
     *  dispatcher, held its handlers' pushes). */
    static Set<String> saved(Listing listing, Function f) {
        return pushes(listing, f.getEntryPoint());
    }

    /** The callee-saved registers pushed from a by fall-through, up to the first branch, call or return (at most 48
     *  instructions). */
    static Set<String> pushes(Listing listing, Address a) {
        Set<String> out = new HashSet<>();
        Instruction in = listing.getInstructionAt(a);
        for (int k = 0; k < 48 && in != null && !ends(in); k++) {
            java.util.regex.Matcher m = TcOrphans.PUSH.matcher(text(in));
            if (m.matches() && TcOrphans.CALLEE_SAVED.contains(m.group(1))) out.add(m.group(1));
            in = listing.getInstructionAt(in.getMaxAddress().next());
        }
        return out;
    }

    static final java.util.regex.Pattern GIVE = java.util.regex.Pattern.compile(
        "(?:mov\\.l|lds\\.l|fmov\\.s|fmov) @r15\\+,\\S+|add #0x[0-9a-f]+,r15");

    /** The jump j gives back stack before it leaves (a pop or `add #+n,r15` in its delay slot or earlier in its block):
     *  the jumper's frame is gone, so j is a tail call to a function of its own (PS2 8c05d322 `bra 0x8c05d988` /
     *  `add #0x4,r15`), not a jump into the rest of the jumper. */
    static boolean tailCall(Listing listing, Instruction j) {
        Instruction d = listing.getInstructionAt(j.getMaxAddress().next());
        if (d != null && d.isInDelaySlot() && GIVE.matcher(text(d)).matches()) return true;
        for (int k = 0; k < 12; k++) {
            Instruction p = listing.getInstructionBefore(j.getAddress());
            if (p == null || p.isInDelaySlot() || ends(p) || !p.getMaxAddress().next().equals(j.getAddress())) return false;
            if (GIVE.matcher(text(p)).matches()) return true;
            j = p;
        }
        return false;
    }

    static boolean ends(Instruction in) {
        FlowType t = in.getFlowType();
        return t.isJump() || t.isCall() || t.isTerminal();
    }

    static String text(Instruction in) {
        return in.toString().toLowerCase().replaceFirst("^_", "");
    }

    /** Every reference to g is a jump (`jmp`, `bra`, `bt`, `bf`), typed a call or not: no call, data pointer. */
    boolean onlyJumps(Address g) {
        for (Reference r : currentProgram.getReferenceManager().getReferencesTo(g)) {
            Instruction in = currentProgram.getListing().getInstructionAt(r.getFromAddress());
            if (!r.getReferenceType().isFlow() || in == null || !isJump(in)) return false;
        }
        return true;
    }

    /** By mnemonic, not flow type: Ghidra types a jump to a function entry as a tail call. */
    static boolean isJump(Instruction in) {
        return in.getMnemonicString().toLowerCase().matches("_?(jmp|bra|bt|bf)([./]s)?");
    }

    static boolean isJmp(Instruction in) {
        return in.getMnemonicString().equalsIgnoreCase("jmp");
    }

    /** The code from g is f's: it reaches f's body, or restores callee-saved registers f saves and it never saved.
     *  Walked by fall-through, delay slots and jumps (also those typed calls), not into calls. */
    boolean continues(Function f, Address g) {
        Listing listing = currentProgram.getListing();
        AddressSetView body = f.getBody();
        Set<Address> seen = new HashSet<>();
        Deque<Address> work = new ArrayDeque<>(List.of(g));
        Set<String> push = new HashSet<>(), pop = new HashSet<>();
        while (!work.isEmpty() && seen.size() < WALK) {
            Address a = work.poll();
            if (body.contains(a)) return true;
            Instruction in = listing.getInstructionAt(a);
            if (in == null || !seen.add(a)) continue;
            String s = text(in);
            java.util.regex.Matcher m = TcOrphans.PUSH.matcher(s);
            if (m.matches()) push.add(m.group(1));
            m = TcOrphans.POP.matcher(s);
            if (m.matches()) pop.add(m.group(1));
            work.addAll(TcTail.exits(currentProgram, in));
            if (!isJump(in)) continue;
            for (Reference r : in.getReferencesFrom()) if (r.getReferenceType().isFlow()) work.add(r.getToAddress());
        }
        pop.removeAll(push);
        pop.retainAll(TcOrphans.CALLEE_SAVED);
        return !pop.isEmpty() && saved(listing, f).containsAll(pop);
    }

    @Override
    public void run() throws Exception {
        Listing listing = currentProgram.getListing();
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        String[] args = getScriptArgs();
        // args: tsv, the game ranges, the overrides file. analyzeHeadless.bat (cmd.exe) splits the comma-joined ranges
        // into one argument each (Spawn ovl has three), so every range-shaped argument is a range and the last is the file.
        String over = null;
        for (int i = 1; i < args.length; i++) {
            if (!args[i].matches("[0-9a-fA-F]+-[0-9a-fA-F]+(,[0-9a-fA-F]+-[0-9a-fA-F]+)*")) {
                over = args[i];
                continue;
            }
            for (String r : args[i].split(",")) {
                String[] p = r.split("-");
                game.add(new long[] {Long.parseLong(p[0], 16), Long.parseLong(p[1], 16)});
            }
        }
        if (over != null && !over.isEmpty())
            for (String line : java.nio.file.Files.readAllLines(java.nio.file.Paths.get(over))) {
                String[] p = line.trim().split("\\s+");
                if (p.length == 2 && p[0].equals("entry")) keep.add(toAddr(Long.parseLong(p[1], 16)));
            }
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(args[0]), "UTF-8"))) {
            pw.println("# entry\tjoined\tjumps");
            int n = 0, round = 0;
            for (int k; (k = joinRound(listing, fm, rm, pw)) > 0; ) {
                n += k;
                round++;
            }
            println("TcJumpIn: " + n + " jump-in continuation(s) joined their function in " + round + " round(s)");
        }
    }

    /** One pass of the rule; returns how many entries joined. Repeated to a fixpoint. */
    int joinRound(Listing listing, FunctionManager fm, ReferenceManager rm, PrintWriter pw) throws Exception {
        Map<Address, Function> join = new LinkedHashMap<>();  // G's entry -> F
        Map<Address, Set<Function>> from = new LinkedHashMap<>();  // G's entry -> the functions that reach it
        for (Function g : fm.getFunctions(true)) {
            Address x = g.getEntryPoint();
            if (keep.contains(x) || !inGame(x.getOffset()) || !onlyJumps(x)) continue;
            Set<Function> fs = new LinkedHashSet<>();
            boolean jumped = false, tail = false;
            for (Reference r : rm.getReferencesTo(x)) {
                fs.add(fm.getFunctionContaining(r.getFromAddress()));
                jumped = true;
                tail |= tailCall(listing, listing.getInstructionAt(r.getFromAddress()));
            }
            if (tail || (jumped && !pushes(listing, x).isEmpty())) continue;  // a function of its own (rule 2)
            Instruction before = listing.getInstructionBefore(x);  // a delay slot's branch decides (8c944836 `bra`)
            Instruction owner = before != null && before.isInDelaySlot() ? before.getPrevious() : before;
            if (owner != null && owner.getFlowType().hasFallthrough() && x.equals(before.getMaxAddress().next()))
                fs.add(fm.getFunctionContaining(before.getAddress()));
            if (!fs.isEmpty() && !fs.contains(null) && !fs.contains(g)) from.put(x, fs);
        }
        Map<Function, Boolean> frames = new HashMap<>();
        for (Address x : from.keySet()) {
            Set<Function> fs = roots(x, from, frames, listing);
            if (fs != null && fs.size() == 1 && continues(fs.iterator().next(), x)) join.put(x, fs.iterator().next());
        }
        join.values().removeIf(f -> join.containsKey(f.getEntryPoint()));  // F leaves too: next round
        Map<Function, AddressSet> had = new HashMap<>();  // F's and its joining Gs' bodies, before
        for (Map.Entry<Address, Function> e : join.entrySet())
            had.computeIfAbsent(e.getValue(), f -> new AddressSet(f.getBody())).add(fm.getFunctionAt(e.getKey()).getBody());
        for (Map.Entry<Address, Function> e : join.entrySet()) {
            Address x = e.getKey();
            String js = plain(listing, rm, x);
            fm.removeFunction(x);
            pw.println(x + "\t" + e.getValue().getEntryPoint() + "\t" + js);
        }
        for (Function f : new LinkedHashSet<>(join.values())) {
            CreateFunctionCmd.fixupFunctionBody(currentProgram, f, monitor);
            for (Address x : join.keySet())
                if (join.get(x) == f && !f.getBody().contains(x))
                    pw.println("# " + x + " did not join " + f.getEntryPoint() + "'s body");
            AddressSet lost = had.get(f).subtract(f.getBody());
            List<Function> holders = new ArrayList<>();
            fm.getFunctionsOverlapping(lost).forEachRemaining(holders::add);
            for (Function o : holders) lost = lost.subtract(o.getBody());
            if (lost.isEmpty()) continue;
            f.setBody(f.getBody().union(lost));
            pw.println("# " + f.getEntryPoint() + " keeps " + lost + ": no other function holds it");
        }
        return join.size();
    }

    /** Makes the jumps to x plain: a `jmp`'s reference a computed jump, any flow override cleared. Returns the jumps
     *  changed ("" when none needed it). */
    String plain(Listing listing, ReferenceManager rm, Address x) {
        StringBuilder js = new StringBuilder();
        for (Reference r : rm.getReferencesTo(x)) {
            Instruction in = listing.getInstructionAt(r.getFromAddress());
            boolean jmp = isJmp(in) && r.getReferenceType() != RefType.COMPUTED_JUMP;
            if (!jmp && in.getFlowOverride() == FlowOverride.NONE) continue;
            js.append(js.length() == 0 ? "" : ",").append(r.getFromAddress());
            if (in.getFlowOverride() != FlowOverride.NONE) in.setFlowOverride(FlowOverride.NONE);
            if (!jmp) continue;  // a branch flows by itself once nothing overrides it
            rm.delete(r);
            rm.addMemoryReference(r.getFromAddress(), x, RefType.COMPUTED_JUMP, SourceType.ANALYSIS, r.getOperandIndex());
        }
        return js.toString();
    }
}

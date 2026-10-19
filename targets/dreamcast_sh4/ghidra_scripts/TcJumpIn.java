// Jump-in continuations: code a function reaches by `jmp @rN` to a constant address while its frame is live, which
// Ghidra made a function of its own. Spawn 8c0129c6 pushes r8..r14 and PR, then leaves its body by
// `mov.l 0x8c012a38,r2` (= 0x0c012bf0) / `jmp @r2` at three places (to 8c012b90, 8c012be0, 8c012bf0); that code calls
// 8c05a000, pops 8c0129c6's frame and returns for it. Ghidra typed each jump as a tail call and made functions at the
// three targets. Neither side could be tested: a target run alone pops a frame it never pushed and returns to a popped
// PR of 0 ("jumps outside its body to 00000000"), and the caller's mocked "tail call" returned to the PR of its last
// `jsr`, looping until the call cap (660 of 768 vectors).
//
// The rule, for every function G in the game ranges that is not a catalog_overrides "entry":
//  1. every reference to G's entry is a flow reference from a `jmp` (no call, no data pointer, no branch), and all of
//     them come from one other function F;
//  2. G cannot run alone: its extent (TcTail.extent, the body plus the shared tails it runs) restores callee-saved
//     registers it never saved (TcOrphans.popsUnpushed's test), and F saves every one of them.
// Then G stops being a function: its jump references become computed jumps (flow overrides cleared), G is removed and
// F's body is recomputed from flow, so G's code (in no other function) joins F. TcPrepare, the packet and the oracle
// then run it as F's own code. A real function placed after its caller saves what it restores and is kept, and so is
// a target any call, data pointer or second function refers to (a shared tail or a callee).
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
    final List<long[]> game = new ArrayList<>();
    final Set<Address> keep = new HashSet<>();

    boolean inGame(long a) {
        for (long[] r : game) if (a >= r[0] && a < r[1]) return true;
        return game.isEmpty();
    }

    /** The callee-saved registers code restores (`@r15+`) without saving them (`@-r15`) first. */
    static Set<String> frame(Listing listing, AddressSetView code, boolean popped) {
        Set<String> push = new HashSet<>(), pop = new HashSet<>();
        for (Instruction in : listing.getInstructions(code, true)) {
            String s = in.toString().toLowerCase().replaceFirst("^_", "");
            java.util.regex.Matcher m = TcOrphans.PUSH.matcher(s);
            if (m.matches()) push.add(m.group(1));
            m = TcOrphans.POP.matcher(s);
            if (m.matches()) pop.add(m.group(1));
        }
        Set<String> out = popped ? pop : push;
        if (popped) out.removeAll(push);
        out.retainAll(TcOrphans.CALLEE_SAVED);
        return out;
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
        Map<Address, Function> join = new LinkedHashMap<>();  // G's entry -> F
        Map<Address, List<Reference>> jumps = new HashMap<>();
        for (Function g : fm.getFunctions(true)) {
            Address x = g.getEntryPoint();
            if (keep.contains(x) || !inGame(x.getOffset())) continue;
            Function f = null;
            List<Reference> refs = new ArrayList<>();
            boolean ok = true;
            for (Reference r : rm.getReferencesTo(x)) {
                Instruction in = listing.getInstructionAt(r.getFromAddress());
                Function from = fm.getFunctionContaining(r.getFromAddress());
                if (!r.getReferenceType().isFlow() || in == null || !in.getMnemonicString().equalsIgnoreCase("jmp")
                    || from == null || from == g || (f != null && from != f)) {
                    ok = false;
                    break;
                }
                f = from;
                refs.add(r);
            }
            if (!ok || f == null) continue;
            Set<String> unpushed = frame(listing, TcTail.extent(currentProgram, g), true);
            if (unpushed.isEmpty() || !frame(listing, f.getBody(), false).containsAll(unpushed)) continue;
            join.put(x, f);
            jumps.put(x, refs);
        }
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(args[0]), "UTF-8"))) {
            pw.println("# entry\tjoined\tjumps");
            for (Map.Entry<Address, Function> e : join.entrySet()) {
                Address x = e.getKey();
                StringBuilder js = new StringBuilder();
                for (Reference r : jumps.get(x)) {
                    Instruction in = listing.getInstructionAt(r.getFromAddress());
                    rm.delete(r);
                    rm.addMemoryReference(r.getFromAddress(), x, RefType.COMPUTED_JUMP, SourceType.ANALYSIS,
                        r.getOperandIndex());
                    if (in.getFlowOverride() != FlowOverride.NONE) in.setFlowOverride(FlowOverride.NONE);
                    js.append(js.length() == 0 ? "" : ",").append(r.getFromAddress());
                }
                fm.removeFunction(x);
                pw.println(x + "\t" + e.getValue().getEntryPoint() + "\t" + js);
            }
            for (Function f : new LinkedHashSet<>(join.values())) {
                CreateFunctionCmd.fixupFunctionBody(currentProgram, f, monitor);
                for (Address x : join.keySet())
                    if (join.get(x) == f && !f.getBody().contains(x))
                        pw.println("# " + x + " did not join " + f.getEntryPoint() + "'s body");
            }
        }
        println("TcJumpIn: " + join.size() + " jump-in continuation(s) joined their function");
    }
}

// Data seeded as code: functions whose code cannot run. A function whose straight-line code from its entry (before
// any branch, call or return) runs into bytes that are no instruction is data that happened to decode for a word or
// two: a literal-pool constant (Toy Commander 8c058c40 holds 0x8c06d028, read by the mov.l at 8c058bfa) or a record
// whose 0x7fff decodes as `add #-1,r15`, the frame allocation TcSeed takes for a prologue (Spawn overlay 8c567ca8,
// pointed to by a table at 8c560e40). An entry something calls or jumps to is kept whatever follows it.
// The oracle refuses such a function (TcOracle.entersCallee: it executes the non-instruction); before that guard its
// run left the "body" as if calling the next bytes, and a two-line C function matched it.
//
// Script args: 0 "report" or "fix", 1 output file: one line per such function: entry, instructions, the first
// non-instruction address, the references to the entry, and the 16 words from the entry.
// "fix" removes those functions and clears their instructions, so the bytes are undefined again (TcSeed.create no
// longer seeds such code). Runs on the master before every TcCatalog (core/ghidra.py), like TcSwitch.
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class TcPrune extends GhidraScript {
    static final int MAX_STRAIGHT = 64;

    /** The first address the straight-line code from `entry` falls into that is no instruction, or null. `last`
     *  receives the last instruction of that code. */
    static Address runsIntoNonCode(Listing listing, Address entry, Instruction[] last) {
        Instruction in = listing.getInstructionAt(entry);
        for (int n = 0; in != null && n < MAX_STRAIGHT; n++) {
            last[0] = in;
            FlowType ft = in.getFlowType();
            if (ft.isJump() || ft.isCall() || ft.isTerminal() || in.getDelaySlotDepth() > 0) return null;
            Address next = in.getFallThrough();
            if (next == null) return null;
            in = listing.getInstructionAt(next);
            if (in == null) return next;
        }
        return null;
    }

    @Override
    public void run() throws Exception {
        boolean fix = getScriptArgs()[0].equals("fix");
        Listing listing = currentProgram.getListing();
        FunctionManager fm = currentProgram.getFunctionManager();
        List<Function> fakes = new ArrayList<>();
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(getScriptArgs()[1]), "UTF-8"))) {
            for (Function f : fm.getFunctions(true)) {
                Address e = f.getEntryPoint();
                StringBuilder refs = new StringBuilder();
                boolean flow = false;
                for (Reference r : getReferencesTo(e)) {
                    flow |= r.getReferenceType().isFlow();
                    refs.append(r.getReferenceType().getName()).append('@').append(r.getFromAddress()).append(' ');
                }
                Instruction[] last = new Instruction[1];
                Address bad = flow ? null : runsIntoNonCode(listing, e, last);
                if (bad == null) continue;
                StringBuilder words = new StringBuilder();
                for (int i = 0; i < 16; i++) {
                    Address a = e.add(2L * i);
                    words.append(currentProgram.getMemory().contains(a.add(1))
                        ? String.format("%04x ", currentProgram.getMemory().getShort(a) & 0xffff) : "---- ");
                }
                StringBuilder ins = new StringBuilder();
                for (Instruction in = listing.getInstructionAt(e); in != null && in.getAddress().compareTo(bad) < 0;
                     in = in.getNext()) ins.append(in).append("; ");
                pw.println(e + "\t" + ins + "\t" + bad + "\t" + (refs.length() == 0 ? "none" : refs.toString().trim())
                    + "\t" + words.toString().trim());
                fakes.add(f);
            }
        }
        if (fix) {
            for (Function f : fakes) {
                Address e = f.getEntryPoint();
                Instruction[] last = new Instruction[1];
                runsIntoNonCode(listing, e, last);
                fm.removeFunction(e);
                clearListing(e, last[0].getMaxAddress());
            }
        }
        println("TcPrune: " + fakes.size() + " function(s) whose code runs into non-instructions"
            + (fix ? " removed" : " found"));
    }
}

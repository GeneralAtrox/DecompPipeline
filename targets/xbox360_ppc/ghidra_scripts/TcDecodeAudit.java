// The decode audit (PROOF_PLAN.md step 2): Ghidra's reading of every instruction word of the listed functions, for
// tools/decode_audit.py to compare with the offline scanner (tools/ppcscan.classify). Read-only.
//
// Script args: 0 output path; 1.. function entries (hex); none = every function.
// Output, one line per word of each body: "addr<TAB>word<TAB>mnemonic", the mnemonic "??" where Ghidra decoded no
// instruction (a gap inside the body), lower case, as Ghidra prints it (simplified forms included: li, mr, slwi).
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.io.*;
import java.util.*;

public class TcDecodeAudit extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        FunctionManager fm = currentProgram.getFunctionManager();
        Listing listing = currentProgram.getListing();
        List<Function> funcs = new ArrayList<>();
        if (a.length > 1) {
            for (int i = 1; i < a.length; i++) {
                Function f = fm.getFunctionAt(toAddr(Long.parseLong(a[i], 16)));
                if (f == null) throw new IllegalArgumentException("no function at " + a[i]);
                funcs.add(f);
            }
        } else {
            for (Function f : fm.getFunctions(true)) funcs.add(f);
        }
        int words = 0, gaps = 0;
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(a[0]), "UTF-8"))) {
            for (Function f : funcs) {
                for (AddressRange r : f.getBody()) {
                    for (Address at = r.getMinAddress(); at.compareTo(r.getMaxAddress()) <= 0; at = at.add(4)) {
                        int w = currentProgram.getMemory().getInt(at);
                        Instruction in = listing.getInstructionAt(at);
                        String m = in == null ? "??" : in.getMnemonicString().toLowerCase();
                        if (in == null) gaps++;
                        words++;
                        pw.println(String.format("%08x\t%08x\t%s", at.getOffset(), w, m));
                        if (at.getOffset() + 4 > r.getMaxAddress().getOffset()) break;
                    }
                }
            }
        }
        println("TcDecodeAudit: " + funcs.size() + " functions, " + words + " words, " + gaps + " not decoded");
    }
}

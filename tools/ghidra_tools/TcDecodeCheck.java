// The decode check of tools/game_setup.py: Ghidra's reading of every instruction in the body of every function (all
// of them by default), for tools/decode_check.py to compare with the image's own bytes and with the target's own
// decoder (its translator's decode.py). Read-only; not an oracle script (it lives outside the targets'
// ghidra_scripts, so no harness stamp depends on it).
//
// Script args: 0 output path; 1.. function entries (hex); none = every function.
// Output, per function: "F<TAB>entry", then one line per instruction of its body, in body order:
// "addr<TAB>bytes<TAB>mnemonic" (bytes in hex, in memory order; mnemonic lower case, as Ghidra prints it). A body
// address where no instruction starts (a gap, or data inside the body) is one line per instruction-alignment unit
// with the mnemonic "??"; its bytes are "-" where memory holds none (uninitialized).
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import java.io.*;
import java.util.*;

public class TcDecodeCheck extends GhidraScript {
    private static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x & 0xff));
        return s.toString();
    }

    private String bytesAt(Memory mem, Address at, int n) {
        byte[] b = new byte[n];
        try {
            if (mem.getBytes(at, b) != n) return "-";
        } catch (MemoryAccessException e) {
            return "-";
        }
        return hex(b);
    }

    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        FunctionManager fm = currentProgram.getFunctionManager();
        Listing listing = currentProgram.getListing();
        Memory mem = currentProgram.getMemory();
        int align = Math.max(1, currentProgram.getLanguage().getInstructionAlignment());
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
        long insns = 0, gaps = 0;
        try (PrintWriter pw = new PrintWriter(new BufferedWriter(new OutputStreamWriter(new FileOutputStream(a[0]), "UTF-8")))) {
            for (Function f : funcs) {
                pw.println(String.format("F\t%08x", f.getEntryPoint().getOffset()));
                for (AddressRange r : f.getBody()) {
                    Address at = r.getMinAddress();
                    while (at != null && at.compareTo(r.getMaxAddress()) <= 0) {
                        Instruction in = listing.getInstructionAt(at);
                        int n = in == null ? align : in.getLength();
                        pw.print(String.format("%08x\t", at.getOffset()));
                        pw.print(in == null ? bytesAt(mem, at, n) : hex(in.getBytes()));
                        pw.print('\t');
                        pw.println(in == null ? "??" : in.getMnemonicString().toLowerCase());
                        if (in == null) gaps++; else insns++;
                        if (r.getMaxAddress().getOffset() - at.getOffset() < n) break;
                        at = at.add(n);
                    }
                }
            }
        }
        println("TcDecodeCheck: " + funcs.size() + " functions, " + insns + " instructions, " + gaps + " not decoded");
    }
}

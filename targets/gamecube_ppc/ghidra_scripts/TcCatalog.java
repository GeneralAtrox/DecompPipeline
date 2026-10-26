// Writes a JSON catalog of every function with difficulty features (script arg 0 = output path; arg 1 = instruction
// ranges; optional arg 2 = function body ranges). PowerPC (Gekko) version: fpu_special counts paired-single,
// quantized load/store and estimate instructions; fp_mode the FPSCR moves; sys_ops cache, MSR, SPR (other than
// LR/CTR/XER), sync and trap instructions; hw_refs the references into the hardware registers (0xcc000000..).
// "fpscr_loads" is kept (empty) for the tools that read it.
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.block.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class TcCatalog extends GhidraScript {
    static boolean fpuSpecial(String m) {
        return m.startsWith("ps_") || m.startsWith("psq_") || m.matches("fres\\.?|frsqrte\\.?");
    }

    static boolean fpMode(String m) {
        return m.matches("mtfsf\\.?|mtfsfi\\.?|mtfsb[01]\\.?|mffs\\.?");
    }

    static boolean sysOp(String m, String text) {
        if (m.matches("sc|rfi|mtmsr|mfmsr|mtsr|mfsr|mtsrin|mfsrin|tlbie|tlbsync|icbi|dcbi|dcbf|dcbst|dcbz|dcbz_l|dcbt"
            + "|dcbtst|eieio|sync|isync|mftb|tw|twi")) return true;
        if (m.matches("mtspr|mfspr")) return !text.matches(".*\\b(lr|ctr|xer)\\b.*") && !text.matches(".*gqr\\d.*");
        return false;
    }

    static String esc(String s) {
        StringBuilder b = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        return b.toString();
    }

    String readString(Memory mem, Address a) {
        try {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 70; i++) {
                int c = mem.getByte(a.add(i)) & 0xff;
                if (c == 0) break;
                if (c < 0x20 || c > 0x7e) return null;
                sb.append((char) c);
            }
            return sb.length() >= 4 ? sb.toString() : null;
        } catch (Exception e) {
            return null;
        }
    }

    @Override
    public void run() throws Exception {
        String out = getScriptArgs()[0];
        Memory mem = currentProgram.getMemory();
        Listing listing = currentProgram.getListing();
        FunctionManager fm = currentProgram.getFunctionManager();
        BasicBlockModel bbm = new BasicBlockModel(currentProgram);
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(out), "UTF-8"))) {
            pw.println("{\"functions\": [");
            boolean first = true;
            for (Function f : fm.getFunctions(true)) {
                if (monitor.isCancelled()) break;
                AddressSetView body = f.getBody();
                int insns = 0, fpu = 0, fpuS = 0, fpM = 0, hw = 0, sys = 0, glob = 0, fptr = 0, ind = 0, jt = 0;
                Set<String> strs = new LinkedHashSet<>();
                for (Instruction in : listing.getInstructions(body, true)) {
                    insns++;
                    String m = in.getMnemonicString().toLowerCase();
                    String text = in.toString().toLowerCase();
                    if ((m.startsWith("f") && !m.startsWith("fc") || m.startsWith("ps") || m.matches("l?stf.*|lf.*"))) fpu++;
                    if (fpuSpecial(m)) fpuS++;
                    if (fpMode(m)) fpM++;
                    if (sysOp(m, text)) sys++;
                    FlowType ft = in.getFlowType();
                    if (ft.isCall() && ft.isComputed()) {
                        boolean resolved = false;
                        for (Reference r : in.getReferencesFrom()) if (r.getReferenceType().isCall()) resolved = true;
                        if (!resolved) ind++;
                    }
                    if (ft.isJump() && ft.isComputed()) jt++;
                    for (Reference r : in.getReferencesFrom()) {
                        if (r.getReferenceType().isFlow()) continue;
                        Address to = r.getToAddress();
                        if (!to.isMemoryAddress()) continue;
                        long v = to.getOffset();
                        if (v >= 0xcc000000L && v < 0xcc010000L) {
                            hw++;
                        } else if (v >= 0x80000000L && v < 0x81800000L) {
                            if (fm.getFunctionAt(to) != null) {
                                fptr++;
                            } else {
                                MemoryBlock b = mem.getBlock(to);
                                String s = b != null && b.isInitialized() ? readString(mem, to) : null;
                                if (s != null) strs.add(s);
                                else glob++;
                            }
                        }
                    }
                }
                int blocks = 0, edges = 0, loops = 0;
                CodeBlockIterator it = bbm.getCodeBlocksContaining(body, monitor);
                while (it.hasNext()) {
                    CodeBlock b = it.next();
                    blocks++;
                    CodeBlockReferenceIterator d = b.getDestinations(monitor);
                    while (d.hasNext()) {
                        CodeBlockReference cr = d.next();
                        if (cr.getFlowType().isCall()) continue;
                        Address da = cr.getDestinationAddress();
                        if (!body.contains(da)) continue;
                        edges++;
                        if (da.compareTo(b.getMinAddress()) <= 0) loops++;
                    }
                }
                StringBuilder sl = new StringBuilder();
                for (String s : strs) {
                    if (sl.length() > 0) sl.append(',');
                    sl.append('"').append(esc(s)).append('"');
                }
                if (!first) pw.println(",");
                first = false;
                pw.print(String.format(
                    "{\"addr\":\"%s\",\"bytes\":%d,\"insns\":%d,\"blocks\":%d,\"cyclo\":%d,\"loops\":%d,\"callees\":%d,"
                        + "\"callers\":%d,\"indirect_calls\":%d,\"jump_tables\":%d,\"fpu\":%d,\"fpu_special\":%d,\"fp_mode\":%d,"
                        + "\"hw_refs\":%d,\"sys_ops\":%d,\"globals\":%d,\"func_ptrs\":%d,\"strings\":[%s]}",
                    String.format("%08x", f.getEntryPoint().getOffset()), body.getNumAddresses(), insns, blocks,
                    edges - blocks + 2, loops, f.getCalledFunctions(monitor).size(), f.getCallingFunctions(monitor).size(),
                    ind, jt, fpu, fpuS, fpM, hw, sys, glob, fptr, sl));
            }
            pw.println("],");
            pw.println("\"fpscr_loads\": []}");
        }
        // Instruction byte ranges: decompiled C must never read these (no machine-code interpreters).
        try (PrintWriter pw = new PrintWriter(new FileWriter(getScriptArgs()[1]))) {
            long lo = -1, hi = -1;
            for (Instruction in : listing.getInstructions(true)) {
                long a = in.getAddress().getOffset();
                long e = a + in.getLength();
                if (a == hi) {
                    hi = e;
                    continue;
                }
                if (lo >= 0) pw.println(String.format("%08x %08x", lo, hi));
                lo = a;
                hi = e;
            }
            if (lo >= 0) pw.println(String.format("%08x %08x", lo, hi));
        }
        if (getScriptArgs().length > 2) {
            try (PrintWriter pw = new PrintWriter(new FileWriter(getScriptArgs()[2]))) {
                for (Function f : fm.getFunctions(true)) {
                    for (AddressRange r : f.getBody()) {
                        pw.println(String.format("%08x %08x %08x", f.getEntryPoint().getOffset(), r.getMinAddress().getOffset(),
                            r.getMaxAddress().getOffset() + 1));
                    }
                }
            }
        }
        println("TcCatalog wrote " + fm.getFunctionCount() + " functions");
    }
}

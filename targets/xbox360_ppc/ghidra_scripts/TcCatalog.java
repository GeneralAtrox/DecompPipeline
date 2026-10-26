// Writes a JSON catalog of every function with difficulty features (script arg 0 = output path; arg 1 = instruction
// ranges; optional arg 2 = function body ranges). Xbox 360 (Xenon PowerPC) version: the instruction classes come from
// the instruction word (TcClass, the twin of tools/ppcscan.py), so the catalog and the offline scan count alike.
//  - fpu: floating-point instructions; fpu_special: multiply-adds, conversions and estimates (the corrected steps);
//    fp_mode: FPSCR moves; vmx: VMX/VMX128 instructions (phase 2);
//  - sys_ops: system and cache instructions (sc, rfid, MSR, SPRs other than LR/CTR/XER, dcbz, dcbf, icbi...);
//    atomics (lwarx/stwcx.) and traps are counted apart: they are game code, not hardware access;
//  - hw_refs: references into the hardware ranges (target.json memory "mmio");
//  - globals / func_ptrs / strings: references into the image (functions, strings, other data).
// "kind" is "frame_helper" for the register save/restore families, "import" for the import thunks (TcSetup names
// both), else "function"; "refuse" is the phase 1 refusal (TcClass), as in catalog_inputs.json.
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
    static final long[][] MMIO = {{0x7fc80000L, 0x7fd00000L}, {0xe0000000L, 0x100000000L}};
    static final long IMAGE_LO = 0x82000000L, IMAGE_HI = 0x90000000L;

    static boolean mmio(long v) {
        for (long[] r : MMIO) if (v >= r[0] && v < r[1]) return true;
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

    static String kind(Function f) {
        return TcBase.isHelper(f) ? "frame_helper" : TcBase.isImport(f) ? "import" : "function";
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
                int insns = 0, hw = 0, glob = 0, fptr = 0, ind = 0, jt = 0;
                Map<String, Integer> counts = new TreeMap<>();
                Set<String> strs = new LinkedHashSet<>();
                String refuse = null;
                for (Instruction in : listing.getInstructions(body, true)) {
                    insns++;
                    int w = TcFixups.word(in);
                    for (String c : TcClass.classify(w)) counts.merge(c, 1, Integer::sum);
                    if (refuse == null) refuse = TcClass.refusal(w);
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
                        long v = to.getOffset() & 0xffffffffL;
                        if (mmio(v)) {
                            hw++;
                        } else if (v >= IMAGE_LO && v < IMAGE_HI) {
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
                String k = kind(f);
                if (k.equals("frame_helper")) refuse = "frame helper: runs inline in its callers";
                int special = counts.getOrDefault("fma", 0) + counts.getOrDefault("fconv", 0)
                    + counts.getOrDefault("estimate", 0);
                int sys = counts.getOrDefault("sys", 0) + counts.getOrDefault("cache", 0);
                if (!first) pw.println(",");
                first = false;
                pw.print(String.format(
                    "{\"addr\":\"%s\",\"kind\":\"%s\",\"bytes\":%d,\"insns\":%d,\"blocks\":%d,\"cyclo\":%d,\"loops\":%d,"
                        + "\"callees\":%d,\"callers\":%d,\"indirect_calls\":%d,\"jump_tables\":%d,\"fpu\":%d,"
                        + "\"fpu_special\":%d,\"fp_mode\":%d,\"vmx\":%d,\"atomic\":%d,\"traps\":%d,\"hw_refs\":%d,"
                        + "\"sys_ops\":%d,\"globals\":%d,\"func_ptrs\":%d,\"strings\":[%s],\"refuse\":%s}",
                    String.format("%08x", f.getEntryPoint().getOffset()), k, body.getNumAddresses(), insns, blocks,
                    edges - blocks + 2, loops, f.getCalledFunctions(monitor).size(), f.getCallingFunctions(monitor).size(),
                    ind, jt, counts.getOrDefault("fpu", 0), special, counts.getOrDefault("fp_mode", 0),
                    counts.getOrDefault("vmx", 0), counts.getOrDefault("atomic", 0), counts.getOrDefault("trap", 0), hw,
                    sys, glob, fptr, sl, refuse == null ? "null" : "\"" + esc(refuse) + "\""));
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

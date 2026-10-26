// Exports the relationship graph between functions: direct calls, function pointers taken,
// globals referenced through literal-pool constants, and strings referenced.
//
// Script args: 0 output TSV path
// Line format: addr<TAB>calls<TAB>fptrs<TAB>globals<TAB>strings   (comma-separated hex; strings '|'-separated)
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class TcGraph extends GhidraScript {
    
    String readString(Memory mem, Address a) {
        try {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 60; i++) {
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
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(out), "UTF-8"))) {
            for (Function f : fm.getFunctions(true)) {
                Set<String> calls = new TreeSet<>(), fptrs = new TreeSet<>(), globs = new TreeSet<>();
                Set<String> strs = new LinkedHashSet<>();
                for (Instruction in : listing.getInstructions(f.getBody(), true)) {
                    for (Reference r : in.getReferencesFrom()) {
                        Address to = r.getToAddress();
                        if (!to.isMemoryAddress()) continue;
                        if (r.getReferenceType().isCall()) {
                            calls.add(String.format("%08x", to.getOffset()));
                            continue;
                        }
                        if (!r.getReferenceType().isRead()) continue;
                        MemoryBlock blk = mem.getBlock(to);
                        if (blk == null || !blk.isInitialized() || listing.getInstructionAt(to) != null) continue;
                        long v;
                        try {
                            v = mem.getInt(to) & 0xffffffffL;
                        } catch (Exception e) {
                            continue;
                        }
                        v = TcBase.fold(v); // the cache-through view of RAM
                        if (!TcBase.inRam(v, 1)) continue;
                        Address va = toAddr(v);
                        if (fm.getFunctionAt(va) != null) {
                            fptrs.add(String.format("%08x", v));
                            continue;
                        }
                        MemoryBlock vb = mem.getBlock(va);
                        String s = vb != null && vb.isInitialized() ? readString(mem, va) : null;
                        if (s != null) strs.add(s.replace('\t', ' ').replace('|', '/'));
                        else globs.add(String.format("%08x", v));
                    }
                }
                pw.println(String.format("%08x\t%s\t%s\t%s\t%s", f.getEntryPoint().getOffset(), String.join(",", calls),
                    String.join(",", fptrs), String.join(",", globs), String.join("|", strs)));
            }
        }
        println("TcGraph: wrote " + out);
    }
}

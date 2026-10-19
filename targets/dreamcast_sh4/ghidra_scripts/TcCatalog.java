// Writes a JSON catalog of every function with difficulty features (script arg 0 = output path; arg 1 = instruction
// ranges; optional arg 2 = function body ranges).
// Also lists every "lds rN,FPSCR" whose value comes from a literal-pool constant.
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
    static final Set<String> FPU_SPECIAL = new HashSet<>(Arrays.asList("fsca", "fsrra", "ftrv", "fipr"));
    static final long TABLE_SPAN = 0x100;
    static final Set<String> FP_MODE = new HashSet<>(Arrays.asList("frchg", "fschg", "fpchg"));
    // pref (cache prefetch hint) and movca.l (a plain store of r0 when emulated) are not excluded.
    static final Set<String> SYS = new HashSet<>(Arrays.asList(
        "ocbwb", "ocbi", "ocbp", "ldc", "ldc.l", "stc", "stc.l", "trapa", "sleep", "rte", "ldtlb"));

    static boolean isHw(long v) {
        return (v >= 0xA0000000L && v < 0xC0000000L) || v >= 0xE0000000L || (v >= 0x005F0000L && v < 0x00800000L);
    }

    // A hardware-looking constant only counts when its register is used as a memory address before being
    // overwritten (in address order, up to 64 instructions; copies followed). Colours (0xff11429e), masks (0xff000000)
    // and negative floats (0xbf800000) are hardware-looking values too.
    static boolean usedAsAddress(Listing listing, Instruction start, String reg, AddressSetView body) {
        Set<String> regs = new HashSet<>(Collections.singleton(reg));
        Instruction in = start.getNext();
        for (int n = 0; n < 64 && in != null && body.contains(in.getAddress()) && !regs.isEmpty(); n++, in = in.getNext()) {
            String t = in.toString().toLowerCase().replace("_", "");
            String[] ops = t.contains(" ") ? t.substring(t.indexOf(' ') + 1).split(",") : new String[0];
            for (String r : regs) {
                if (t.matches(".*@-?" + r + "\\b.*") || t.matches(".*@\\([^)]*," + r + "\\).*")
                    || (r.equals("r0") && t.matches(".*@\\(r0,r\\d+\\).*"))) return true;
            }
            if (ops.length == 0) continue;
            String d = ops[ops.length - 1].trim();
            String mn = t.split(" ")[0];
            if (mn.equals("mov") && ops.length == 2 && regs.contains(ops[0].trim())) {
                regs.add(d);  // a copy
            } else if ((mn.startsWith("add") || mn.equals("or")) && regs.contains(d)) {
                // base + offset, base | offset (batch 29: TC 8c0923e0 reads VRAM at 0xa5000000 | offset): an address
            } else if (regs.contains(d)) {
                regs.remove(d);  // overwritten
            }
            if (mn.equals("rts")) break;
            if (mn.equals("jsr") || mn.startsWith("bsr")) regs.removeIf(r -> r.matches("r[0-7]"));  // clobbered by a call
        }
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

    Long poolValue(Memory mem, Listing listing, Instruction in) {
        for (Reference r : in.getReferencesFrom()) {
            Address to = r.getToAddress();
            if (!r.getReferenceType().isRead() || !to.isMemoryAddress()) continue;
            MemoryBlock blk = mem.getBlock(to);
            if (blk == null || !blk.isInitialized() || listing.getInstructionAt(to) != null) continue;
            try {
                return mem.getInt(to) & 0xffffffffL;
            } catch (Exception e) {
                return null;
            }
        }
        return null;
    }

    @Override
    public void run() throws Exception {
        String out = getScriptArgs()[0];
        Memory mem = currentProgram.getMemory();
        Listing listing = currentProgram.getListing();
        FunctionManager fm = currentProgram.getFunctionManager();
        BasicBlockModel bbm = new BasicBlockModel(currentProgram);
        // Code entered through the uncached P2 mirror (a pool word 0xa... naming a function entry) runs uncached to
        // change the cache control register: Spawn 8c024c90 jumps to 0xac024cbe, which stores r3 at @r2 (CCR) and
        // jumps back through r4; 0xac024cf8 ors r2 into @r1. Its registers come from the hardware routine that jumps
        // there, not the C ABI, so it counts as a system operation: excluded (intent only) like that routine.
        Set<Address> uncached = new HashSet<>();
        long imgStart = 0x8c010000L, ramEnd = 0x8d000000L;
        TreeSet<Long> tables = new TreeSet<>();  // pool-named addresses holding no instruction: data
        for (Instruction in : listing.getInstructions(true)) {
            if (!in.getMnemonicString().toLowerCase().replaceFirst("^_", "").equals("mov.l")) continue;
            Long pv = poolValue(mem, listing, in);
            if (pv != null && pv >= imgStart && pv < ramEnd && listing.getInstructionAt(toAddr(pv)) == null) tables.add(pv);
            if (pv == null || pv < 0xA0000000L || pv >= 0xC0000000L) continue;
            Address e = toAddr((pv & 0x1fffffffL) | 0x80000000L);
            if (fm.getFunctionAt(e) != null) uncached.add(e);
        }
        List<String> fpscrLoads = new ArrayList<>();

        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(out), "UTF-8"))) {
            pw.println("{\"functions\": [");
            boolean first = true;
            for (Function f : fm.getFunctions(true)) {
                if (monitor.isCancelled()) break;
                AddressSetView body = f.getBody();
                int insns = 0, fpu = 0, fpuS = 0, fpM = 0, hw = 0, glob = 0, fptr = 0, ind = 0, jt = 0;
                int sys = uncached.contains(f.getEntryPoint()) ? 1 : 0;
                Set<String> strs = new LinkedHashSet<>();
                Map<Integer, Long> lastPool = new HashMap<>();
                for (Instruction in : listing.getInstructions(body, true)) {
                    insns++;
                    String m = in.getMnemonicString().toLowerCase();
                    if (m.startsWith("_")) m = m.substring(1);
                    String text = in.toString().toLowerCase();
                    if (m.startsWith("f")) fpu++;
                    if (FPU_SPECIAL.contains(m)) fpuS++;
                    if (FP_MODE.contains(m)) fpM++;
                    if ((m.startsWith("lds") || m.startsWith("sts")) && text.contains("fpscr")) fpM++;
                    if (SYS.contains(m)) sys++;
                    FlowType ft = in.getFlowType();
                    if (ft.isCall() && ft.isComputed()) {
                        boolean resolved = false;
                        for (Reference r : in.getReferencesFrom()) {
                            if (r.getReferenceType().isCall()) resolved = true;
                        }
                        if (!resolved) ind++;
                    }
                    if (ft.isJump() && ft.isComputed()) jt++;
                    if (m.equals("lds") && text.contains("fpscr")) {
                        String reg = text.replace("lds", "").trim().split(",")[0].trim();
                        Long v = reg.startsWith("r") ? lastPool.get(Integer.parseInt(reg.substring(1))) : null;
                        fpscrLoads.add(in.getAddress() + "=" + (v == null ? "?" : String.format("0x%08x", v)));
                    }
                    if (!m.equals("mov.l")) continue;
                    Long pv = poolValue(mem, listing, in);
                    if (pv == null) continue;
                    String[] ops = text.split(",");
                    String dst = ops[ops.length - 1].trim();
                    if (dst.startsWith("r")) {
                        try {
                            lastPool.put(Integer.parseInt(dst.substring(1)), pv);
                        } catch (NumberFormatException e) {
                            // not a general register
                        }
                    }
                    long v = (pv >= 0x0c000000L && pv < 0x0d000000L) ? (pv | 0x80000000L) : pv; // P0 mirror of RAM
                    if (isHw(v)) {
                        if (dst.startsWith("r") && usedAsAddress(listing, in, dst, body)) hw++;
                    } else if (v >= imgStart && v < ramEnd) {
                        Address va = toAddr(v);
                        if (fm.getFunctionAt(va) != null) {
                            fptr++;
                        } else {
                            String s = mem.getBlock(va).isInitialized() ? readString(mem, va) : null;
                            if (s != null) strs.add(s);
                            else glob++;
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
                    f.getEntryPoint().toString(), body.getNumAddresses(), insns, blocks, edges - blocks + 2, loops,
                    f.getCalledFunctions(monitor).size(), f.getCallingFunctions(monitor).size(), ind, jt, fpu, fpuS, fpM,
                    hw, sys, glob, fptr, sl));
            }
            pw.println("],");
            StringBuilder fl = new StringBuilder();
            for (String s : fpscrLoads) {
                if (fl.length() > 0) fl.append(',');
                fl.append('"').append(s).append('"');
            }
            pw.println("\"fpscr_loads\": [" + fl + "]}");
        }
        // Instruction byte ranges: decompiled C must never read these (no machine-code interpreters). A function never
        // called or jumped to, whose entry is read through a register (every reference), starting within TABLE_SPAN bytes
        // after a pool-named address that holds no instruction, is that table's data disassembled: TC's Duck ADPCM
        // decoder (8c09ae88) reads its nibble and step tables at 0x8c0b0240, named by a pool word, where "functions"
        // 8c0b0242 and 8c0b0280 have no caller; their only references are the decoder's table reads. A function with no
        // reference at all stays code (TC 8c0550dc, 8c061e54: real functions reached through runtime pointers).
        AddressSet tableData = new AddressSet();
        ReferenceManager rm = currentProgram.getReferenceManager();
        for (Function f : fm.getFunctions(true)) {
            Address e = f.getEntryPoint();
            Long t = tables.floor(e.getOffset());
            if (t == null || e.getOffset() - t >= TABLE_SPAN) continue;
            boolean data = !currentProgram.getSymbolTable().isExternalEntryPoint(e) && rm.getReferenceCountTo(e) > 0;
            for (Reference x : rm.getReferencesTo(e)) {  // a load through a register (not a mova: TC 8c0c43a4)
                Instruction by = listing.getInstructionAt(x.getFromAddress());
                data &= !x.getReferenceType().isFlow() && by != null && by.toString().matches("_?mov\\.[bwl] @\\(?r.*");
            }
            if (!data) continue;
            tableData.add(f.getBody());
            println("TcCatalog: table data, not code: " + f.getBody() + " read at " + rm.getReferenceCountTo(e) + " loads");
        }
        try (PrintWriter pw = new PrintWriter(new FileWriter(getScriptArgs()[1]))) {
            long lo = -1, hi = -1;
            for (Instruction in : listing.getInstructions(true)) {
                if (tableData.contains(in.getAddress())) continue;
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
        // Optional arg 2: function body ranges, "entry lo hi" per range (tools/completeness.py: code in no function).
        if (getScriptArgs().length > 2) {
            try (PrintWriter pw = new PrintWriter(new FileWriter(getScriptArgs()[2]))) {
                for (Function f : fm.getFunctions(true)) {
                    for (AddressRange r : f.getBody()) {
                        pw.println(String.format("%s %08x %08x", f.getEntryPoint(), r.getMinAddress().getOffset(),
                            r.getMaxAddress().getOffset() + 1));
                    }
                }
            }
        }
        println("TcCatalog wrote " + fm.getFunctionCount() + " functions");
    }
}

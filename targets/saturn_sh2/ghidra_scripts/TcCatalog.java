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
        "ldc", "ldc.l", "stc", "stc.l", "trapa", "sleep", "rte",
        // Ghidra's SH-2 mac.l is a stub (no multiply, no accumulate) and its mac.w gets the sign and carry wrong
        // (superh.sinc, "FIXME"): the oracle cannot run them, so a function using one is excluded like a system op.
        "mac.l", "mac.w"));

    // The store-queue window and QACR0/1: the oracle models them (TcSq, TcFold, TcBase.P4), so a function that only
    // uses them counts sq_refs, not hw_refs. `stc sr` / `ldc sr` (and .l): the oracle models SR (TcSr), sr_ops.
    // The SH-2 has neither: nothing is a store-queue reference.
    static boolean isSq(long v) {
        return false;
    }

    static boolean isSrOp(String m, String text) {
        String t = text.replace("_", "");
        String[] ops = t.contains(" ") ? t.substring(t.indexOf(' ') + 1).split(",") : new String[0];
        return ops.length == 2 && (m.matches("ldc(\\.l)?") && ops[1].trim().equals("sr")
            || m.matches("stc(\\.l)?") && ops[0].trim().equals("sr"));
    }

    // Saturn: SMPC and backup RAM (0x00100000..), the A-bus (cartridge, CD block), SCSP, VDP1, VDP2 and SCU
    // (0x02000000..0x05ffffff), both in either cache view, and the SH-2's own areas from 0x40000000 (associative
    // purge, cache arrays, on-chip registers at 0xfffffe00).
    static boolean isHw(long v) {
        return (v >= 0x00100000L && v < 0x00200000L) || (v >= 0x02000000L && v < 0x06000000L)
            || (v >= 0x20100000L && v < 0x20200000L) || (v >= 0x22000000L && v < 0x26000000L) || v >= 0x40000000L;
    }

    // A hardware-looking constant only counts when its register is used as a memory address before being
    // overwritten, on some path through the function from the load (copies followed). Colours (0xff11429e), masks
    // (0xff000000) and negative floats (0xbf800000) are hardware-looking values too.
    // The walk follows the flow, not address order: both arms of bt/bf (bt/s, bf/s after their delay slot), the target
    // of bra, and a call's return. It ends after the delay slot of rts, rte, jmp and braf, and outside the body. What
    // follows a bra in address order is another block or case: Spawn 8c0e7818 returns 0xffff0000 (-65536) through
    // `bra` to its epilogue, and the next case reads @(r0,r14) with another r0. A path stops after 64 instructions,
    // the walk after 256. A call (jsr, bsr, bsrf) clobbers r0-r7 after its delay slot, which runs first (Spawn
    // 8c03c964: `jsr; _mov r12,r4` passes the colour 0xff000000 to Fade_Out, and r4 is gone after the call). Real
    // uses reached only through a branch stay ones: Spawn 8c059b80 stores to the AICA's 0xa08000b0 in a loop entered
    // by bra, and 8c059c10 reads 0xa08000b4 on one arm of its switch.
    static final Set<String> DELAYED = new HashSet<>(Arrays.asList(
        "rts", "rte", "jmp", "braf", "bra", "bsr", "bsrf", "jsr", "bt/s", "bf/s"));
    static final Set<String> ENDS = new HashSet<>(Arrays.asList("rts", "rte", "jmp", "braf"));
    static final Set<String> CALLS = new HashSet<>(Arrays.asList("jsr", "bsr", "bsrf"));

    static String text(Instruction in) {
        return in.toString().toLowerCase().replace("_", "");
    }

    // One instruction on the walk: true if it uses a register of regs as an address; else applies its write to regs.
    static boolean step(String t, Set<String> regs) {
        for (String r : regs) {
            if (t.matches(".*@-?" + r + "\\b.*") || t.matches(".*@\\([^)]*," + r + "\\).*")
                || (r.equals("r0") && t.matches(".*@\\(r0,r\\d+\\).*"))) return true;
        }
        String[] ops = t.contains(" ") ? t.substring(t.indexOf(' ') + 1).split(",") : new String[0];
        if (ops.length == 0) return false;
        String mn = t.split(" ")[0], d = ops[ops.length - 1].trim();
        if (mn.equals("mov") && ops.length == 2 && regs.contains(ops[0].trim())) {
            regs.add(d);  // a copy
        } else if ((mn.startsWith("add") || mn.equals("or")) && regs.contains(d)) {
            // base + offset, base | offset (batch 29: TC 8c0923e0 reads VRAM at 0xa5000000 | offset): an address
        } else {
            regs.remove(d);  // overwritten
        }
        return false;
    }

    static boolean usedAsAddress(Listing listing, Instruction start, String reg, AddressSetView body) {
        Map<Address, Set<String>> seen = new HashMap<>();
        Deque<Object[]> work = new ArrayDeque<>();
        work.push(new Object[] {start.getNext(), new HashSet<>(Collections.singleton(reg)), 0});
        for (int budget = 256; !work.isEmpty() && budget > 0; ) {
            Object[] w = work.pop();
            Instruction in = (Instruction) w[0];
            @SuppressWarnings("unchecked")
            Set<String> regs = (Set<String>) w[1];
            int depth = (Integer) w[2];
            if (in == null || depth >= 64 || regs.isEmpty() || !body.contains(in.getAddress())) continue;
            Set<String> had = seen.computeIfAbsent(in.getAddress(), k -> new HashSet<>());
            if (had.containsAll(regs)) continue;  // this point was walked with these registers already
            had.addAll(regs);
            budget--;
            String t = text(in);
            if (step(t, regs)) return true;
            String mn = t.split(" ")[0];
            Instruction after = in.getNext();
            if (DELAYED.contains(mn)) {  // the delay slot runs before the transfer
                if (after == null || !body.contains(after.getAddress())) continue;
                if (step(text(after), regs)) return true;
                after = after.getNext();
                depth++;
            }
            if (ENDS.contains(mn)) continue;
            if (CALLS.contains(mn)) {
                regs.removeIf(r -> r.matches("r[0-7]"));  // clobbered by the call, after its delay slot
                work.push(new Object[] {after, regs, depth + 1});
                continue;
            }
            Instruction target = null;
            if (mn.equals("bra") || mn.equals("bt") || mn.equals("bf") || mn.equals("bt/s") || mn.equals("bf/s")) {
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("0x([0-9a-f]+)").matcher(t);
                if (m.find()) target = listing.getInstructionAt(in.getAddress().getNewAddress(Long.parseLong(m.group(1), 16)));
                if (target != null) work.push(new Object[] {target, new HashSet<>(regs), depth + 1});
                if (mn.equals("bra")) continue;
            }
            work.push(new Object[] {after, regs, depth + 1});
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
        TreeSet<Long> tables = new TreeSet<>();  // pool-named addresses holding no instruction: data
        for (Instruction in : listing.getInstructions(true)) {
            if (!in.getMnemonicString().toLowerCase().replaceFirst("^_", "").equals("mov.l")) continue;
            Long pv = poolValue(mem, listing, in);
            if (pv != null && TcBase.inRam(pv, 1) && listing.getInstructionAt(toAddr(pv)) == null) tables.add(pv);
            if (pv == null || pv == TcBase.fold(pv)) continue;  // the cache-through view of RAM
            Address e = toAddr(TcBase.fold(pv));
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
                int srOps = 0, sq = 0;  // isSrOp, isSq
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
                    if (isSrOp(m, text)) srOps++;
                    else if (SYS.contains(m)) sys++;
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
                    long v = TcBase.fold(pv); // the cache-through view of RAM
                    if (isHw(v)) {
                        if (dst.startsWith("r") && usedAsAddress(listing, in, dst, body)) {
                            if (isSq(v)) sq++;
                            else hw++;
                        }
                    } else if (TcBase.inRam(v, 1)) {
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
                        + "\"hw_refs\":%d,\"sys_ops\":%d,\"sr_ops\":%d,\"sq_refs\":%d,\"globals\":%d,"
                        + "\"func_ptrs\":%d,\"strings\":[%s]}",
                    f.getEntryPoint().toString(), body.getNumAddresses(), insns, blocks, edges - blocks + 2, loops,
                    f.getCalledFunctions(monitor).size(), f.getCallingFunctions(monitor).size(), ind, jt, fpu, fpuS, fpM,
                    hw, sys, srOps, sq, glob, fptr, sl));
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

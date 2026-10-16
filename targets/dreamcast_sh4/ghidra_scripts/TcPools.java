// Literal pools decoded as functions. A function whose entry is a word that PC-relative loads (mov.l / mov.w
// @(disp,PC)) of other functions read, that nothing branches to or calls and no image word points to, is a literal
// pool Ghidra decoded as code: Spawn 8c05281a holds the words read by 8c0527a4..8c052808 (`stc SSR,r12`, then
// nops). Its "body" often runs on through the padding into a real function nothing calls directly: Spawn 8c02eac4
// is a pool, 12 nops, then 8c02eae0, a computed-call target that was never its own function. The checker flags a
// read of such a pool as a read of machine code, so its real neighbours fail (Spawn 8c0527d0), and the real
// function inside is never queued.
// The pool is entry .. the end of the last word a load reads, scanning up to the first flow target, pointed-to
// address or terminal instruction past the words; nops after it are padding. "fix" removes the fake function,
// clears the pool and padding (their garbage instructions' references go too) and makes a function at the first
// instruction after them, if any. A pool span holding a flow target is reported as a conflict and left alone.
//
// Script args: 0 "report" or "fix"; 1 output file (one line per pool: entry, pool end, code after it or "-",
// loaders, conflict); 2 (optional) the catalog_overrides file ("entry" addresses are kept). Runs on the master
// after TcOverride, before TcOrphans (core/ghidra.py).
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class TcPools extends GhidraScript {
    final Map<Long, Integer> loadSize = new HashMap<>();  // word address -> widest PC-relative load of it
    final Map<Long, List<Address>> loaders = new HashMap<>();
    final Set<Long> pointed = new HashSet<>();  // P1 addresses an aligned image word points to (P0/P1/P2 form)
    final Set<Address> keep = new HashSet<>();

    int u16(Memory mem, Address a) throws Exception {
        return mem.getShort(a) & 0xffff;
    }

    /** Every PC-relative load in function code: mov.l @(disp,PC) reads 4 bytes at (pc & ~3) + 4 + disp*4, mov.w
     *  @(disp,PC) 2 bytes at pc + 4 + disp*2. Decoded from the opcode, not from Ghidra's references. */
    void indexLoads(Listing listing, FunctionManager fm, Memory mem) throws Exception {
        for (Instruction in : listing.getInstructions(true)) {
            Address a = in.getAddress();
            if (in.getLength() != 2 || fm.getFunctionContaining(a) == null) continue;
            int w = u16(mem, a);
            long t;
            int size;
            if ((w & 0xf000) == 0xd000) { t = (a.getOffset() & ~3L) + 4 + (w & 0xff) * 4L; size = 4; }
            else if ((w & 0xf000) == 0x9000) { t = a.getOffset() + 4 + (w & 0xff) * 2L; size = 2; }
            else if ((w & 0xff00) == 0xc700 && (size = movaLoad(in)) > 0) t = (a.getOffset() & ~3L) + 4 + (w & 0xff) * 4L;
            else continue;
            loadSize.merge(t, size, Math::max);
            loaders.computeIfAbsent(t, k -> new ArrayList<>()).add(a);
        }
    }

    static final java.util.regex.Pattern AT_R0 = java.util.regex.Pattern.compile("@r0\\b|@\\(r0,|,r0\\)");

    /** `mova @(disp,PC),r0` used to read data: the size of the first load or store through r0 (`fmov.s @r0,fr2`,
     *  `mov.w @(r0,r1),r0`, `mov.l @(4,r0),r1`) on the straight line after it, before r0 changes (an `add` keeps it a
     *  pointer, a call or an unconditional branch ends the line after its delay slot); else 0. `@-r0` touches bytes
     *  before the target (Toy Commander 8c0c41f6, an interrupt handler's save area) and changes r0: no load. A mova
     *  whose r0 is a code address (jmp, jsr, braf, bsrf, lds/ldc r0) is no load. Toy Commander 8c063c10 and 8c063c98 read the floats
     *  at 8c063d28 and 8c063d2c this way, 20 and 2 instructions later. */
    static int movaLoad(Instruction mova) {
        Instruction in = mova.getNext();
        int slots = -1;  // delay-slot instructions left before the straight line ends
        for (int i = 0; i < 32 && in != null && slots != 0; i++, in = in.getNext()) {
            String m = in.getMnemonicString().toLowerCase().replace("_", ""), s = in.toString().toLowerCase();
            if (m.startsWith("jmp") || m.startsWith("jsr") || m.startsWith("braf") || m.startsWith("bsrf")
                    || (m.startsWith("lds") || m.startsWith("ldc")) && s.matches("\\S+ r0,.*") || s.contains("@-r0")) return 0;
            if (AT_R0.matcher(s).find()) return m.endsWith(".b") ? 1 : m.endsWith(".w") ? 2 : 4;
            for (Object o : in.getResultObjects())
                if (o instanceof ghidra.program.model.lang.Register && ((ghidra.program.model.lang.Register) o).getName()
                        .equalsIgnoreCase("r0") && !m.equals("add")) return 0;
            FlowType ft = in.getFlowType();
            if (slots > 0) slots--;
            else if (ft.isCall() || ft.isTerminal() || (ft.isJump() && !ft.isConditional())) slots = in.getDelaySlotDepth();
        }
        return 0;
    }

    void indexPointers(Memory mem) throws Exception {
        for (MemoryBlock b : mem.getBlocks()) {
            if (!b.isInitialized() || b.getSize() > (64L << 20)) continue;
            byte[] buf = new byte[(int) b.getSize()];
            b.getBytes(b.getStart(), buf);
            long base = b.getStart().getOffset();
            for (int i = (int) ((4 - (base & 3)) & 3); i + 4 <= buf.length; i += 4) {
                long w = (buf[i] & 0xffL) | (buf[i + 1] & 0xffL) << 8 | (buf[i + 2] & 0xffL) << 16 | (buf[i + 3] & 0xffL) << 24;
                long seg = w >>> 29;
                if (seg == 0 || seg == 4 || seg == 5) pointed.add((w & 0x1fffffffL) | 0x80000000L);
            }
        }
    }

    boolean flowTarget(Address a) {
        for (Reference r : getReferencesTo(a)) if (r.getReferenceType().isFlow()) return true;
        return false;
    }

    /** A load of `e` from a function other than f. */
    boolean loadedFromOutside(Address e, Function f, FunctionManager fm, Set<Address> fakes) {
        for (Address from : loaders.getOrDefault(e.getOffset(), List.of())) {
            Function g = fm.getFunctionContaining(from);
            if (g != null && !g.equals(f) && (fakes == null || !fakes.contains(g.getEntryPoint()))) return true;
        }
        return false;
    }

    @Override
    public void run() throws Exception {
        boolean fix = getScriptArgs()[0].equals("fix");
        Listing listing = currentProgram.getListing();
        FunctionManager fm = currentProgram.getFunctionManager();
        Memory mem = currentProgram.getMemory();
        if (getScriptArgs().length > 2) {
            for (String line : java.nio.file.Files.readAllLines(java.nio.file.Paths.get(getScriptArgs()[2]))) {
                String[] p = line.trim().split("\\s+");
                if (p.length == 2 && p[0].equals("entry")) keep.add(toAddr(Long.parseLong(p[1], 16)));
            }
        }
        indexLoads(listing, fm, mem);
        indexPointers(mem);
        Set<Address> fakes = new LinkedHashSet<>();
        for (Function f : fm.getFunctions(true)) {
            Address e = f.getEntryPoint();
            if (!keep.contains(e) && !flowTarget(e) && !pointed.contains(e.getOffset())
                    && loadedFromOutside(e, f, fm, null)) fakes.add(e);
        }
        int n = 0;
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(getScriptArgs()[1]), "UTF-8"))) {
            for (Address e : fakes) {
                Function f = fm.getFunctionAt(e);
                if (f == null || !loadedFromOutside(e, f, fm, fakes)) continue;  // read only by other pools
                Address max = f.getBody().getRangeContaining(e).getMaxAddress();
                long end = e.getOffset(), stop = max.getOffset() + 1;
                String conflict = "";
                for (long p = e.getOffset(); p <= max.getOffset(); p += 2) {
                    Address a = toAddr(p);
                    if (p > e.getOffset() && (flowTarget(a) || pointed.contains(p))) {
                        if (p < end) conflict = "conflict: flow target or pointer at " + a;
                        stop = p;
                        break;
                    }
                    Integer s = loadSize.get(p);
                    if (s != null) end = Math.max(end, p + s);
                    Instruction in = listing.getInstructionAt(a);
                    if (p >= end && in != null && in.getFlowType().isTerminal()) { stop = p; break; }
                }
                end = Math.min(end, max.getOffset() + 1);  // the pool's last word may lie past the decoded body
                long code = end;
                while (code < stop && code <= max.getOffset() && u16(mem, toAddr(code)) == 0x0009) code += 2;
                Address after = code <= max.getOffset() && listing.getInstructionAt(toAddr(code)) != null ? toAddr(code) : null;
                StringBuilder who = new StringBuilder();
                for (Address from : loaders.get(e.getOffset())) who.append(from).append(' ');
                pw.println(e + "\t" + toAddr(end) + "\t" + (after == null ? "-" : after.toString()) + "\t"
                    + who.toString().trim() + "\t" + conflict);
                if (!conflict.isEmpty()) continue;
                n++;
                if (!fix) continue;
                fm.removeFunction(e);
                clearListing(e, toAddr(code - 1));
                if (after != null && fm.getFunctionContaining(after) == null) createFunction(after, null);
            }
        }
        println("TcPools: " + n + " literal pool(s) decoded as functions" + (fix ? " removed" : " found"));
    }
}

// Post-analysis: find functions that auto-analysis missed because nothing calls them directly
// (callbacks, function tables, code reached only through pointers). Run after auto-analysis, before TcCatalog.
//  1. pointer seeds: every aligned word (outside instructions) that points into the image, directly or through the
//     0x0c... P0 mirror, at what looks like a function start with a prologue
//  2. gap seeds: the first word after a known function's end, its literal pool and nop padding, if it looks like a
//     function start: a prologue within the first few setup instructions, or a leaf that reaches rts cleanly
//  0. mirror references: calls and pointers that auto-analysis recorded against 0x0c... (unmapped) are moved to the
//     0x8c... address, and constant calls through a register holding a mirror address get a call reference
// Each round creates the new functions, re-runs analysis, and repeats until nothing is added.
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.symbol.*;
import ghidra.program.util.ContextEvaluatorAdapter;
import ghidra.program.util.SymbolicPropogator;
import java.util.*;

public class TcSeed extends GhidraScript {
    private static final int GAP_REACH = 0x400;
    private static final int SETUP_WORDS = 8;
    private static final int LEAF_WORDS = 128;

    private Memory mem;
    private Listing listing;
    private long lo;
    private long hi;
    private AddressSet img; // initialized bytes of the image (a combined image has uninitialized gaps)

    private boolean inImg(long a, int n) {
        return a >= lo && a + n <= hi && img.contains(toAddr(a)) && img.contains(toAddr(a + n - 1));
    }

    /** The 16-bit word at a, or 0 (padding) outside the image's initialized bytes. */
    private int u16(long a) throws Exception {
        return inImg(a, 2) ? mem.getShort(toAddr(a)) & 0xffff : 0;
    }

    /** A word that starts a compiled C function: a register push, sts.l pr, an fmov push or a frame allocation. */
    private static boolean isPrologue(int w) {
        if ((w & 0xff0f) == 0x2f06 && ((w >> 4) & 0xf) >= 8) return true; // mov.l r8..r14,@-r15
        if (w == 0x4f22) return true;                                     // sts.l pr,@-r15
        if ((w & 0xff0f) == 0xff0b) return true;                           // fmov.s frN,@-r15
        return (w & 0xff80) == 0x7f80;                                     // add #-n,r15
    }

    /** Argument/constant setup a compiler may schedule before the prologue: mov #imm, PC-relative loads, mov Rm,Rn. */
    private static boolean isSetup(int w) {
        int top = w >> 12;
        return top == 0xE || top == 0xD || top == 0x9 || (w & 0xff00) == 0xC700 || (w & 0xf00f) == 0x6003;
    }

    private static boolean endsFlow(int w) {
        int top = w >> 12;
        return w == 0x000b || top == 0xA || top == 0xB || (w & 0xf0ff) == 0x402b || (w & 0xf0ff) == 0x400b
            || (w & 0xff00) == 0x8900 || (w & 0xff00) == 0x8b00 || (w & 0xff00) == 0x8d00 || (w & 0xff00) == 0x8f00;
    }

    private boolean known(long a) {
        Address at = toAddr(a);
        return listing.getInstructionContaining(at) != null || listing.getDefinedDataContaining(at) != null
            || getFunctionContaining(at) != null;
    }

    private boolean prologueStart(long a) throws Exception {
        for (int i = 0; i < SETUP_WORDS && a + 2 * i + 2 <= hi; i++) {
            int w = u16(a + 2 * i);
            if (isPrologue(w)) return true;
            if (!isSetup(w)) return false;
        }
        return false;
    }

    /** A leaf: reaches rts within LEAF_WORDS without running into known code/data or impossible words. */
    private boolean leafStart(long a) throws Exception {
        for (int i = 0; i < LEAF_WORDS && a + 2 * i + 2 <= hi; i++) {
            long p = a + 2 * i;
            if (i > 0 && known(p)) return false;
            int w = u16(p);
            if (w == 0x0000 || w == 0xffff || w == 0xfffd) return false;
            if (w == 0x000b) return true;
        }
        return false;
    }

    private boolean create(long a) throws Exception {
        Address at = toAddr(a);
        disassemble(at);
        Instruction in = listing.getInstructionAt(at);
        for (int i = 0; i < 8 && in != null; i++) {
            if (in.getMnemonicString().startsWith("?")) {
                clearListing(at, in.getMaxAddress());
                return false;
            }
            if (endsFlow(u16(in.getAddress().getOffset()))) break;
            in = in.getNext();
        }
        Instruction[] last = new Instruction[1];
        if (TcPrune.runsIntoNonCode(listing, at, last) != null) { // data that decodes for a word or two (TcPrune)
            clearListing(at, last[0].getMaxAddress());
            return false;
        }
        return listing.getInstructionAt(at) != null && createFunction(at, null) != null;
    }

    /** An image address seen through the 0x0c... (P0) mirror as its 0x8c... form; else unchanged. */
    private long fold(long v) {
        if (v >= (lo & 0x1fffffffL) && v < (hi & 0x1fffffffL)) return v | 0x80000000L;
        return v;
    }

    /**
     * Auto-analysis already found most calls and pointers, but recorded them against unmapped mirror addresses
     * (0x0c...), where no function or global exists. Moves every such reference to the 0x8c... address, keeping its
     * type, operand and source; a call target with no function gets one. Returns the number of references moved.
     */
    private int retargetMirrorRefs() throws Exception {
        ReferenceManager rm = currentProgram.getReferenceManager();
        List<Reference> refs = new ArrayList<>();
        Address from = toAddr(lo & 0x1fffffffL), to = toAddr((hi - 1) & 0x1fffffffL);
        AddressIterator it = rm.getReferenceDestinationIterator(new AddressSet(from, to), true);
        while (it.hasNext()) for (Reference r : rm.getReferencesTo(it.next())) refs.add(r);
        int moved = 0;
        for (Reference r : refs) {
            if (mem.contains(r.getToAddress())) continue;
            long t = fold(r.getToAddress().getOffset());
            Address ta = toAddr(t);
            RefType type = r.getReferenceType();
            if (type.isCall() && getFunctionAt(ta) == null && (t & 1) == 0
                    && listing.getInstructionContaining(ta) == null) create(t);
            rm.delete(r);
            Reference nr = rm.addMemoryReference(r.getFromAddress(), ta, type, r.getSource(), r.getOperandIndex());
            if (r.isPrimary()) rm.setPrimary(nr, true);
            moved++;
        }
        return moved;
    }

    /** The register a `jsr @rN` / `jmp @rN` goes through, or -1. */
    private static int indirectReg(int w) {
        return ((w & 0xf0ff) == 0x400b || (w & 0xf0ff) == 0x402b) ? (w >> 8) & 0xf : -1;
    }

    /**
     * Calls and tail jumps through a register holding a 0x0c... (P0 mirror) address: Ghidra's constant propagation
     * knows the value but makes no reference, because nothing is mapped there. Adds the call reference to the 0x8c...
     * function (the same RAM), creating that function if needed. Returns the number of references added.
     */
    private int mirrorCalls() throws Exception {
        int added = 0;
        ReferenceManager rm = currentProgram.getReferenceManager();
        List<Function> funcs = new ArrayList<>();
        for (Function f : currentProgram.getFunctionManager().getFunctions(true)) funcs.add(f);
        for (Function f : funcs) {
            List<Instruction> open = new ArrayList<>();
            for (Instruction in : listing.getInstructions(f.getBody(), true)) {
                if (indirectReg(u16(in.getAddress().getOffset())) < 0) continue;
                boolean hasCall = false;
                for (Reference r : in.getReferencesFrom()) hasCall |= r.getReferenceType().isCall();
                if (!hasCall) open.add(in);
            }
            if (open.isEmpty()) continue;
            SymbolicPropogator sp = new SymbolicPropogator(currentProgram);
            sp.flowConstants(f.getEntryPoint(), f.getBody(), new ContextEvaluatorAdapter(), false, monitor);
            for (Instruction in : open) {
                int w = u16(in.getAddress().getOffset());
                Register reg = currentProgram.getRegister("r" + indirectReg(w));
                SymbolicPropogator.Value v = sp.getRegisterValue(in.getAddress(), reg);
                if (v == null || v.isRegisterRelativeValue()) continue;
                long raw = v.getValue() & 0xffffffffL;
                long t = fold(raw);
                if (t == raw || !inImg(t, 2) || (t & 1) != 0) continue;
                Address ta = toAddr(t);
                if (getFunctionAt(ta) == null && listing.getInstructionContaining(ta) == null) create(t);
                if (getFunctionAt(ta) == null) continue;
                rm.addMemoryReference(in.getAddress(), ta, (w & 0xf0ff) == 0x400b ? RefType.COMPUTED_CALL
                    : RefType.COMPUTED_CALL_TERMINATOR, SourceType.ANALYSIS, 0);
                added++;
            }
        }
        return added;
    }

    @Override
    public void run() throws Exception {
        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        img = new AddressSet();
        for (MemoryBlock b : mem.getBlocks())
            if (b.isInitialized() && b.getStart().getOffset() >= 0x8c010000L && b.getEnd().getOffset() < 0x8d000000L)
                img.add(b.getStart(), b.getEnd());
        lo = img.getMinAddress().getOffset();
        hi = img.getMaxAddress().getOffset() + 1;
        int total = 0;
        for (int round = 0; round < 20; round++) {
            int moved = retargetMirrorRefs();
            if (moved > 0) println("TcSeed round " + round + ": " + moved + " reference(s) moved off the mirror");
            int added = mirrorCalls();
            if (added > 0) println("TcSeed round " + round + ": " + added + " call(s) through the P0 mirror resolved");
            for (long off = lo; off + 4 <= hi; off += 4) {
                if (!inImg(off, 4) || listing.getInstructionContaining(toAddr(off)) != null) continue;
                long v = fold(mem.getInt(toAddr(off)) & 0xffffffffL);
                if (!inImg(v, 2) || (v & 1) != 0 || known(v)) continue;
                if (prologueStart(v) && create(v)) added++;
            }
            Deque<Long> ends = new ArrayDeque<>();
            for (Function f : currentProgram.getFunctionManager().getFunctions(true))
                ends.add(f.getBody().getMaxAddress().getOffset() + 1);
            while (!ends.isEmpty()) {
                long end = ends.poll();
                long a = (end + 1) & ~1L;
                while (a + 2 <= hi && a < end + GAP_REACH && listing.getInstructionContaining(toAddr(a)) == null
                       && (listing.getDefinedDataContaining(toAddr(a)) != null || u16(a) == 0x0009 || u16(a) == 0))
                    a += 2;
                if (a + 2 > hi || a >= end + GAP_REACH || !inImg(a, 2) || known(a)) continue;
                if ((prologueStart(a) || leafStart(a)) && create(a)) {
                    added++;
                    Function nf = getFunctionAt(toAddr(a));
                    if (nf != null) ends.add(nf.getBody().getMaxAddress().getOffset() + 1);
                }
            }
            if (added > 0) analyzeChanges(currentProgram);
            total += added;
            println("TcSeed round " + round + ": " + added + " function(s) added");
            if (added == 0) break;
        }
        println("TcSeed: " + total + " function(s) added in total");
    }
}

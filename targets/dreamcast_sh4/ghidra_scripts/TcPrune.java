// Data seeded as code: functions whose code cannot run. A function whose straight-line code from its entry (before
// any branch, call or return) runs into bytes that are no instruction is data that happened to decode for a word or
// two: a literal-pool constant (Toy Commander 8c058c40 holds 0x8c06d028, read by the mov.l at 8c058bfa) or a record
// whose 0x7fff decodes as `add #-1,r15`, the frame allocation TcSeed takes for a prologue (Spawn overlay 8c567ca8,
// pointed to by a table at 8c560e40). An entry something calls or jumps to is kept whatever follows it.
// The oracle refuses such a function (TcOracle.entersCallee: it executes the non-instruction); before that guard its
// run left the "body" as if calling the next bytes, and a two-line C function matched it.
// Pool words can also decode cleanly up to an rts: Spawn 8c056594 holds 0x0c361fd8 0x8c05fec0 ..., the literal pool
// of 8c056540 (its `mova` points 0x14 bytes before it and @(disp,r14) loads read it), and kept as a function it made
// every oracle run of 8c056540 fault on reading machine code (R8). So an entry nothing branches to whose first two
// words are RAM pointers is data too when it lies in another function's literal pool: within 0x40 bytes after a
// `mova` target past that function's body, or read by a PC-relative mov.l/mov.w (poolWord). A branch to such an
// entry from its own body is garbage too (Power Stone 2 8c17d898: its words decode into code reaching the pool at
// 8c17d7bc, whose `bf 8c17d898` jumps back), so only a branch from outside the body keeps a pool word.
//
// Script args: 0 "report" or "fix", 1 output file: one line per such function: entry, instructions, the first
// non-instruction address (or "pool: <why>"), the references to the entry, and the 16 words from the entry.
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

    int word(Address a) throws Exception {
        return currentProgram.getMemory().getShort(a) & 0xffff;
    }

    /** A word that folds into main RAM (P0..P2 mirrors): a pointer, as pool words are. */
    boolean ramPointer(Address a) throws Exception {
        if (!currentProgram.getMemory().contains(a.add(3))) return false;
        long v = currentProgram.getMemory().getInt(a) & 0x1fffffffL;
        return v >= 0x0c010000L && v < 0x0d000000L;
    }

    /** {mova target: the mova} for every mova whose target lies past its own function's body (a literal pool). */
    Map<Long, Address> movaPools() throws Exception {
        Map<Long, Address> out = new HashMap<>();
        FunctionManager fm = currentProgram.getFunctionManager();
        for (Instruction in : currentProgram.getListing().getInstructions(true)) {
            int op = word(in.getAddress());
            if (op >> 8 != 0xc7) continue;
            long t = ((in.getAddress().getOffset() & ~3L) + 4) + (op & 0xff) * 4L;
            Function own = fm.getFunctionContaining(in.getAddress());
            if (own != null && !own.getBody().contains(in.getAddress().getNewAddress(t))) out.put(t, in.getAddress());
        }
        return out;
    }

    /** Why `f` is a literal-pool word seeded as code, or null (see the header). */
    String poolWord(Function f, Map<Long, Address> pools) throws Exception {
        Address e = f.getEntryPoint();
        if (e.getOffset() % 4 != 0 || !ramPointer(e) || !ramPointer(e.add(4))) return null;
        for (Reference r : getReferencesTo(e)) {
            Address from = r.getFromAddress();
            if (!currentProgram.getMemory().contains(from.add(1))) continue;
            int op = word(from);
            long t = op >> 12 == 0xd ? ((from.getOffset() & ~3L) + 4) + (op & 0xff) * 4L
                : op >> 12 == 0x9 ? from.getOffset() + 4 + (op & 0xff) * 2L : -1;
            if (t == e.getOffset()) return "read by the PC-relative load at " + from;
        }
        FunctionManager fm = currentProgram.getFunctionManager();
        for (long t = e.getOffset() - 0x3c; t <= e.getOffset(); t += 4) {
            Address mova = pools.get(t);
            if (mova == null) continue;
            Function owner = fm.getFunctionContaining(mova);
            boolean between = false;  // another function starting inside the pool before e: e is past the pool
            for (long x = t + 2; x < e.getOffset(); x += 2) between |= fm.getFunctionAt(e.getNewAddress(x)) != null;
            if (owner != null && !owner.equals(f) && !between)
                return "in the literal pool at " + e.getNewAddress(t) + " of the mova at " + mova;
        }
        return null;
    }

    @Override
    public void run() throws Exception {
        boolean fix = getScriptArgs()[0].equals("fix");
        Listing listing = currentProgram.getListing();
        FunctionManager fm = currentProgram.getFunctionManager();
        List<Function> fakes = new ArrayList<>();
        Map<Function, AddressSetView> pooled = new HashMap<>();  // pool words: their whole body is cleared
        Map<Long, Address> pools = movaPools();
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(getScriptArgs()[1]), "UTF-8"))) {
            for (Function f : fm.getFunctions(true)) {
                Address e = f.getEntryPoint();
                StringBuilder refs = new StringBuilder();
                boolean flow = false, flowIn = false;  // flowIn: a branch or call from outside f's body
                for (Reference r : getReferencesTo(e)) {
                    flow |= r.getReferenceType().isFlow();
                    flowIn |= r.getReferenceType().isFlow() && !f.getBody().contains(r.getFromAddress());
                    refs.append(r.getReferenceType().getName()).append('@').append(r.getFromAddress()).append(' ');
                }
                Instruction[] last = new Instruction[1];
                Address bad = flow ? null : runsIntoNonCode(listing, e, last);
                String pool = flowIn || bad != null ? null : poolWord(f, pools);
                if (bad == null && pool == null) continue;
                StringBuilder words = new StringBuilder();
                for (int i = 0; i < 16; i++) {
                    Address a = e.add(2L * i);
                    words.append(currentProgram.getMemory().contains(a.add(1))
                        ? String.format("%04x ", currentProgram.getMemory().getShort(a) & 0xffff) : "---- ");
                }
                StringBuilder ins = new StringBuilder();
                Address end = bad != null ? bad : f.getBody().getMaxAddress().add(1);
                for (Instruction in = listing.getInstructionAt(e); in != null && in.getAddress().compareTo(end) < 0;
                     in = in.getNext()) ins.append(in).append("; ");
                pw.println(e + "\t" + ins + "\t" + (bad != null ? bad.toString() : "pool: " + pool) + "\t"
                    + (refs.length() == 0 ? "none" : refs.toString().trim()) + "\t" + words.toString().trim());
                fakes.add(f);
                if (bad == null) pooled.put(f, new AddressSet(f.getBody()));
            }
        }
        if (fix) {
            for (Function f : fakes) {
                Address e = f.getEntryPoint();
                AddressSetView body = pooled.get(f);
                Instruction[] last = new Instruction[1];
                if (body == null) runsIntoNonCode(listing, e, last);
                fm.removeFunction(e);
                if (body != null) clearListing(body);
                else clearListing(e, last[0].getMaxAddress());
            }
        }
        println("TcPrune: " + (fakes.size() - pooled.size()) + " function(s) whose code runs into non-instructions, "
            + pooled.size() + " literal-pool word(s) seeded as code" + (fix ? ", removed" : ", found"));
    }
}

// PR continuations: the code a function's callee returns into because the function set PR to it before a tail jump
// (the return-address trick). Toy Commander 8c061ec4 loads the pool word 8c061ed4 = 8c061ee4, does `lds r0,PR` and
// `bra 8c061db0`: 8c061db0's rts returns to 8c061ee4, which runs a call (bsr 8c061a7c), a transform and a branch into
// the shared tail of 8c061e54 that pops the frame and returns for 8c061ec4. No flow reaches 8c061ee4, so Ghidra left
// it as undefined bytes; TcTail.prTarget then saw no instruction there, the function's extent stopped at the `bra`,
// and its packet and harness modelled a plain tail call.
//
// The rule, for every function in the game ranges and every `lds rN,PR` in its body whose value is a constant
// (TcTail.prValue: a literal-pool word or mova on the straight line) and that redirects an unconditional jump
// (TcTail.redirectedJump: the jump whose delay slot it is, or the next one on the straight line):
//  1. the continuation C (the constant, P0/P1/P2 folded) must be in the game ranges, on initialized bytes, at an even
//     address, and not inside defined data or another instruction;
//  2. when C is undefined bytes, Ghidra disassembles it following its flows, restricted to the undefined span that
//     starts at C (up to the next instruction, defined data or function). The decode is kept only when it is clean:
//     every instruction legal, every flow (a branch, a fall-through, the line after a conditional branch's delay
//     slot) lands on the new code or on an existing instruction, and every call on a function entry. Otherwise it is
//     undone and reported (the oracle then refuses the function: "executes code outside its body");
//  3. the continuation's code in no function (followed from C through fall-throughs, delay slots and jump
//     references as TcTail.exits does, not into calls, stopping at any function's body) becomes part of the
//     function's body: the catalog, function_bodies.txt and tools/completeness.py count it as that function's code,
//     and TcPrepare lists, blocks, covers and runs it (TcTail.extent, as before for a continuation already decoded).
// A continuation inside another function's body (TC 8c061aac -> 8c061aa0 in 8c061a98, 8c061eac -> 8c061e60 in
// 8c061e54, 8c0616f8 -> 8c0616ee in 8c0616ec, 8c061698 -> 8c06163e in 8c061630) stays there: TcTail.extent runs it
// as a shared tail. Idempotent: a continuation already in the function's body is reported "present".
// Not modelled (none in Toy Commander or any Spawn workspace, see STAGE_REPORT.md): PR popped by `lds.l @rN+,PR`
// from a pushed constant or a table. The oracle refuses such a function when a run returns to code outside its body.
//
// The second kind (ds-flow): a branch into another branch's delay slot (TcTail.slotEntry; PS2 8c189204 `bf
// 0x8c189208` into the slot of `8c189206 bra`) runs the slot as a plain instruction and then the next one, the
// fall-on, which Ghidra's flow never reaches: left undefined (PS2 8c18920a) or in no function (8c19725a). Every such
// slot in the image (not only the game ranges: PS2's are SDK code in scope through extra_functions) gets the same
// steps 2 and 3 for its fall-on, for the function holding the branch. A branch in no function (a not_entry piece)
// keeps the decoded code free: the extent of the function calling the piece reaches it (TcTail.exits). A fall-on in another function's body stays there ("shared", a shared tail), and one at a
// function entry stays a mocked tail call ("entry": Spawn main 8c0703ba, a catalog_overrides entry made for this gap).
//
// Script args: 0 output file (one line per redirect: function, lds address, continuation, jump, status, bytes added;
// then one per slot fall-on: function, branch, fall-on, slot, status, bytes added);
// 1 the game ranges, "lo-hi,lo-hi" in hex. Runs on the master after TcSwitch, before TcCatalog (core/ghidra.py), so a
// body TcSwitch recomputes from flow keeps its continuation.
//@category DecompPipeline
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import java.io.*;
import java.util.*;

public class TcCont extends GhidraScript {
    static final int MAX_SPAN = 0x1000, MAX_CODE = 4096, MAX_ROUNDS = 8;

    final List<long[]> game = new ArrayList<>();
    Memory mem;
    Listing listing;
    FunctionManager fm;

    boolean inGame(long a) {
        for (long[] r : game) if (a >= r[0] && a < r[1]) return true;
        return game.isEmpty() || wide;
    }

    // Slot fall-ons: anywhere in the image's initialized bytes, not only the game ranges (PS2's 8c188422 and 8c197240,
    // Spawn main's 8c05fec2..8c0703a0 are SDK or late code past game_end, in scope through extra_functions).
    boolean wide = false;

    boolean inited(Address a) {
        MemoryBlock b = mem.getBlock(a);
        return b != null && b.isInitialized();
    }

    /** The undefined bytes from c up to the next instruction, defined data, function or uninitialized byte. */
    AddressSet span(Address c) {
        Address a = c;
        for (int n = 0; n < MAX_SPAN && inited(a) && inGame(a.getOffset()); n++, a = a.add(1)) {
            if (listing.getInstructionContaining(a) != null || listing.getDefinedDataContaining(a) != null
                    || fm.getFunctionContaining(a) != null) break;
        }
        return a.equals(c) ? new AddressSet() : new AddressSet(c, a.previous());
    }

    /** Slot fall-ons: the bytes of c's memory block in no function. Ghidra decodes the fall-on following its flows over
     *  them and keeps every instruction already there (8c18c486's fall-on branches to 8c18c626, further on). */
    AddressSet outside(Address c) {
        MemoryBlock b = mem.getBlock(c);
        if (b == null || !b.isInitialized()) return new AddressSet();
        if (bodies == null) {
            bodies = new AddressSet();
            for (Function f : fm.getFunctions(true)) bodies.add(f.getBody());
        }
        return new AddressSet(b.getStart(), b.getEnd()).subtract(bodies);
    }

    AddressSet bodies = null;  // every function's body, for outside(); reset when a body grows

    /** Why the freshly decoded code `got` is not a clean continuation, or null when it is. */
    String unclean(AddressSetView got) {
        for (Instruction in : listing.getInstructions(got, true)) {
            for (Address f : in.getFlows()) {
                if (in.getFlowType().isCall()) {
                    // a slot fall-on may also call code in no function, a local subroutine its extent runs
                    // (TcTail.exits; PS2 8c18bf20's not_entry piece 8c18ca0c)
                    boolean local = wide && (got.contains(f) || listing.getInstructionAt(f) != null);
                    if (fm.getFunctionAt(f) == null && !local) return in.getAddress() + " calls " + f + ", no function entry";
                } else if (!got.contains(f) && listing.getInstructionAt(f) == null) {
                    return in.getAddress() + " branches to " + f + ", no instruction";
                }
            }
            Address next = null;
            if (in.isInDelaySlot()) {  // the line after a conditional branch's slot
                Instruction own = in.getPrevious();
                if (own != null && own.getFlowType().hasFallthrough()) next = in.getMaxAddress().next();
            } else if (in.getDelaySlotDepth() == 0 && in.getFlowType().hasFallthrough()) {
                next = in.getFallThrough();
            }
            if (next != null && !got.contains(next) && listing.getInstructionAt(next) == null)
                return in.getAddress() + " runs on into " + next + ", no instruction";
        }
        return null;
    }

    /** Decode the continuation at c when it is undefined bytes; null when done (or nothing to do), else why not. */
    String decode(Address c) throws Exception {
        if (listing.getInstructionAt(c) != null) return null;
        AddressSet room = wide ? outside(c) : span(c);
        if (room.isEmpty()) return "no undefined bytes at " + c;
        DisassembleCommand cmd = new DisassembleCommand(c, room, true);
        cmd.applyTo(currentProgram, monitor);
        AddressSetView got = cmd.getDisassembledAddressSet();
        String why = got == null || got.isEmpty() ? "no instruction decodes at " + c : unclean(got);
        if (why != null && got != null) for (AddressRange r : got) clearListing(r.getMinAddress(), r.getMaxAddress());
        return why;
    }

    /** The continuation's instructions in no function, from c (TcTail.exits); stops at any function's body. */
    AddressSet freeCode(Address c) {
        AddressSet out = new AddressSet();
        Deque<Address> work = new ArrayDeque<>(List.of(c));
        while (!work.isEmpty() && out.getNumAddresses() < MAX_CODE) {
            Address a = work.poll();
            if (out.contains(a) || fm.getFunctionContaining(a) != null) continue;
            Instruction in = listing.getInstructionAt(a);
            if (in == null) continue;
            out.add(in.getMinAddress(), in.getMaxAddress());
            work.addAll(TcTail.exits(currentProgram, in));
        }
        return out;
    }

    @Override
    public void run() throws Exception {
        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        fm = currentProgram.getFunctionManager();
        String[] args = getScriptArgs();
        // the game ranges: cmd.exe (analyzeHeadless.bat) splits the comma-joined list into one argument each
        for (int i = 1; i < args.length; i++)
            for (String r : args[i].split(",")) {
                if (r.isEmpty()) continue;
                String[] p = r.split("-");
                game.add(new long[] {Long.parseLong(p[0], 16), Long.parseLong(p[1], 16)});
            }
        List<Object[]> found = new ArrayList<>();  // {function, lds, continuation, jump}
        for (Function f : fm.getFunctions(true)) {
            if (!inGame(f.getEntryPoint().getOffset())) continue;
            for (Instruction in : listing.getInstructions(f.getBody(), true)) {
                Address c = TcTail.prValue(currentProgram, in);
                Address j = c == null ? null : TcTail.redirectedJump(in, true);
                if (j != null) found.add(new Object[] {f, in.getAddress(), c, j});
            }
        }
        int added = 0;
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(args[0]), "UTF-8"))) {
            pw.println("# function\tlds\tcontinuation\tjump\tstatus\tbytes added");
            for (Object[] r : found) {
                Function f = (Function) r[0];
                Address c = (Address) r[2];
                String status;
                long bytes = 0;
                Function owner = fm.getFunctionContaining(c);
                if (c.getOffset() % 2 != 0 || !inited(c) || !inGame(c.getOffset())) {
                    status = "not code: outside the game's bytes";
                } else if (owner == f) {
                    status = "present";
                } else if (owner != null) {
                    status = "shared: in " + owner.getEntryPoint();
                } else if (listing.getDefinedDataContaining(c) != null
                        || (listing.getInstructionContaining(c) != null && listing.getInstructionAt(c) == null)) {
                    status = "not code: inside data or an instruction";
                } else {
                    String why = decode(c);
                    AddressSet code = why == null ? freeCode(c) : new AddressSet();
                    if (why != null) status = "not decoded: " + why;
                    else if (code.isEmpty()) status = "no free code";
                    else {
                        f.setBody(f.getBody().union(code));
                        bytes = code.getNumAddresses();
                        status = "own";
                        added++;
                    }
                }
                pw.println(f.getEntryPoint() + "\t" + r[1] + "\t" + c + "\t" + r[3] + "\t" + status + "\t" + bytes);
            }
            pw.println("# slot fall-ons: function of the branch (- in none)\tbranch\tfall-on\tslot\tstatus\tbytes added");
            added += slotFallOns(pw);
        }
        println("TcCont: " + found.size() + " PR redirect(s), " + slots + " branch(es) into a delay slot, " + added
            + " continuation(s) added to a body; " + game.size() + " game range(s)");
    }

    int slots = 0;

    /** The slot fall-ons, in rounds until one decodes and adds nothing: code a fall-on decodes can branch into a slot
     *  itself (PS2 8c18bf20's 8c18c416 after 8c18c414). One row per slot, its last status ("own" once it added bytes);
     *  returns the slots whose code joined a body. */
    int slotFallOns(PrintWriter pw) throws Exception {
        wide = true;
        Map<Address, Object[]> rows = new TreeMap<>(); // slot -> {function, branch, fall-on, status, bytes}
        for (int round = 0; round < MAX_ROUNDS; round++) {
            Map<Instruction, Address> entered = new LinkedHashMap<>(); // slot -> branch, found before any decode
            for (Instruction s : listing.getInstructions(true)) {
                Address b = inited(s.getAddress()) ? TcTail.slotEntry(s) : null;
                if (b != null) entered.put(s, b);
            }
            long before = listing.getNumInstructions(), grown = 0;
            for (Map.Entry<Instruction, Address> e : entered.entrySet()) {
                Address s = e.getKey().getAddress(), b = e.getValue(), n = e.getKey().getMaxAddress().next();
                Function f = fm.getFunctionContaining(b);
                long[] got = {0};
                String status = fallOn(f, n, got);
                Object[] old = rows.get(s);
                long bytes = (old == null ? 0 : (Long) old[4]) + got[0];
                if (bytes > 0 && status.equals("present")) status = "own";
                rows.put(s, new Object[] {f == null ? "-" : f.getEntryPoint(), b, n, status, bytes});
                grown += got[0];
                if (got[0] > 0) bodies = null;
            }
            if (grown == 0 && listing.getNumInstructions() == before) break;
        }
        int added = 0;
        for (Map.Entry<Address, Object[]> e : rows.entrySet()) {
            Object[] r = e.getValue();
            if ((Long) r[4] > 0) added++;
            pw.println(r[0] + "\t" + r[1] + "\t" + r[2] + "\t" + e.getKey() + "\t" + r[3] + "\t" + r[4]);
        }
        slots = rows.size();
        return added;
    }

    /** A slot fall-on n (the header's second kind) for the branch's function f (null: the branch is in no function,
     *  a not_entry piece's code, which its caller's extent reaches): its status; got[0] the bytes it adds. */
    String fallOn(Function f, Address n, long[] got) throws Exception {
        Function owner = fm.getFunctionContaining(n);
        if (n.getOffset() % 2 != 0 || !inited(n) || !inGame(n.getOffset())) return "not code: outside the game's bytes";
        if (owner != null && owner == f) return "present";
        if (owner != null) return (owner.getEntryPoint().equals(n) ? "entry: " : "shared: in ") + owner.getEntryPoint();
        if (listing.getDefinedDataContaining(n) != null
                || (listing.getInstructionContaining(n) != null && listing.getInstructionAt(n) == null))
            return "not code: inside data or an instruction";
        String why = decode(n);
        if (why != null) return "not decoded: " + why;
        AddressSet code = freeCode(n);
        if (code.isEmpty()) return "no free code";
        if (f == null) return "free: decoded, in no function";
        f.setBody(f.getBody().union(code));
        got[0] = code.getNumAddresses();
        return "own";
    }
}

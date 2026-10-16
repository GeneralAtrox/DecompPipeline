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
// Script args: 0 output file (one line per redirect: function, lds address, continuation, jump, status, bytes added);
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
    static final int MAX_SPAN = 0x1000, MAX_CODE = 4096;

    final List<long[]> game = new ArrayList<>();
    Memory mem;
    Listing listing;
    FunctionManager fm;

    boolean inGame(long a) {
        for (long[] r : game) if (a >= r[0] && a < r[1]) return true;
        return game.isEmpty();
    }

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

    /** Why the freshly decoded code `got` is not a clean continuation, or null when it is. */
    String unclean(AddressSetView got) {
        for (Instruction in : listing.getInstructions(got, true)) {
            for (Address f : in.getFlows()) {
                if (in.getFlowType().isCall()) {
                    if (fm.getFunctionAt(f) == null) return in.getAddress() + " calls " + f + ", no function entry";
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
        AddressSet room = span(c);
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
        if (args.length > 1 && !args[1].isEmpty())
            for (String r : args[1].split(",")) {
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
        }
        println("TcCont: " + found.size() + " PR redirect(s), " + added + " continuation(s) added to a body");
    }
}

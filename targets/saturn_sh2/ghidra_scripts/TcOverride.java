// Hand-checked catalog corrections from project.json "catalog_overrides", for what TcPrune / TcSwitch cannot see.
// Toy Commander: 8c023224 is data whose words decode as `add #-0x74,r15; addv r7,r15; bsr 8c022f18`; its fake call
// seeded a function at 8c022f18, the middle of the real function 8c022e04 (a full prologue, reached through the
// handler table at 8c027470), and an internal `bra` there seeded 8c023030 (that function's epilogue).
//
// Script args: 0 an overrides file, one "KIND ADDR" per line:
//   data ADDR       remove the function at ADDR and clear its instructions (data seeded as code)
//   not_entry ADDR  remove the function at ADDR, keep its instructions (an address inside another function)
//   entry ADDR      create a function at ADDR (disassembling it first if needed)
//   table ADDR N    an entry for every 4-byte code pointer in the N-entry table at ADDR (zero / non-code entries
//                   are skipped). Toy Commander's script interpreter (dispatchers 8c01c0e8, 8c01ca2c: `jmp @r0` through
//                   8c0a5598, 305 entries) has 282 handlers; Ghidra had made functions of only 31.
// Applied in that order, so a real entry's body can take over the code of the removed false entries. Idempotent.
// Runs on the master before TcPrune, TcSwitch and every TcCatalog (core/ghidra.py).
//@category DecompPipeline
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.nio.file.*;
import java.util.*;

public class TcOverride extends GhidraScript {
    @Override
    public void run() throws Exception {
        Map<String, List<Address>> by = new HashMap<>();
        Memory mem = currentProgram.getMemory();
        for (String line : Files.readAllLines(Paths.get(getScriptArgs()[0]))) {
            String[] p = line.trim().split("\\s+");
            if (p.length == 3 && p[0].equals("table")) {
                Address t = toAddr(Long.parseLong(p[1], 16));
                for (int i = 0; i < Integer.parseInt(p[2]); i++) {
                    Address e = toAddr(mem.getInt(t.add(4L * i)) & 0xffffffffL);
                    MemoryBlock b = mem.getBlock(e);
                    if (b != null && b.isInitialized() && e.getOffset() % 2 == 0)
                        by.computeIfAbsent("entry", k -> new ArrayList<>()).add(e);
                }
                continue;
            }
            if (p.length != 2) continue;
            by.computeIfAbsent(p[0], k -> new ArrayList<>()).add(toAddr(Long.parseLong(p[1], 16)));
        }
        FunctionManager fm = currentProgram.getFunctionManager();
        int n = 0;
        for (Address a : by.getOrDefault("data", List.of())) {
            Function f = fm.getFunctionAt(a);
            if (f == null) continue;
            Address end = f.getBody().getMaxAddress();
            fm.removeFunction(a);
            clearListing(a, end);
            n++;
        }
        Set<Function> refit = new HashSet<>();
        for (Address a : by.getOrDefault("not_entry", List.of())) {
            if (fm.getFunctionAt(a) != null && fm.removeFunction(a)) n++;
            // A branch to a false entry was marked a call (tail call) while the function existed; as a plain branch
            // its target joins the caller's body again (8c022e04's `bra 8c023030` at 8c022eba).
            for (Reference r : getReferencesTo(a)) {
                Instruction in = getInstructionAt(r.getFromAddress());
                if (in == null || in.getFlowOverride() == FlowOverride.NONE) continue;
                in.setFlowOverride(FlowOverride.NONE);
                Function f = fm.getFunctionContaining(in.getAddress());
                if (f != null) refit.add(f);
                n++;
            }
        }
        for (Function f : refit) CreateFunctionCmd.fixupFunctionBody(currentProgram, f, monitor);
        for (Address a : by.getOrDefault("entry", List.of())) {
            if (fm.getFunctionAt(a) != null) continue;
            if (getInstructionAt(a) == null) disassemble(a);
            if (createFunction(a, null) == null) throw new RuntimeException("TcOverride: cannot create a function at " + a);
            n++;
        }
        println("TcOverride: " + n + " change(s)");
    }
}

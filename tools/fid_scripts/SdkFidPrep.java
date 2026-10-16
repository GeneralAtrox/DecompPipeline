// Post-import step for one SDK library object (ELF converted from SYSROF): makes sure every named code symbol
// starts a function, so Function ID ingest sees every routine the object defines. Ghidra's ELF loader and
// analysis usually create them already; symbols they missed (a routine reached only through a literal-pool
// pointer, an assembler label) are disassembled and made functions here.
// Prints one line: FIDPREP <program path> functions=<n> created=<m>
//@category DecompPipeline
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.util.*;

public class SdkFidPrep extends GhidraScript {
    boolean isCode(Address a) {
        MemoryBlock b = currentProgram.getMemory().getBlock(a);
        return b != null && b.isExecute() && b.isInitialized() && !b.isExternalBlock();
    }

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        Listing listing = currentProgram.getListing();
        List<Address> starts = new ArrayList<>();
        for (Symbol s : currentProgram.getSymbolTable().getAllSymbols(false)) {
            if (s.getSource() == SourceType.DEFAULT || s.isExternal()) continue;
            if (s.getSymbolType() != SymbolType.LABEL && s.getSymbolType() != SymbolType.FUNCTION) continue;
            Address a = s.getAddress();
            if (!a.isMemoryAddress() || !isCode(a) || (a.getOffset() & 1) != 0) continue;
            String n = s.getName();
            if (n.startsWith("$") || n.startsWith(".") || n.startsWith("L") && n.matches("L\\d+")) continue;
            if (fm.getFunctionAt(a) == null) starts.add(a);
        }
        int created = 0;
        for (Address a : starts) {
            if (fm.getFunctionAt(a) != null) continue;
            if (listing.getInstructionAt(a) == null) {
                if (listing.getDefinedDataContaining(a) != null) continue;
                new DisassembleCommand(a, null, true).applyTo(currentProgram, monitor);
            }
            if (listing.getInstructionAt(a) == null) continue;
            if (new CreateFunctionCmd(a).applyTo(currentProgram, monitor)) created++;
        }
        println("FIDPREP " + currentProgram.getDomainFile().getPathname() + " functions=" + fm.getFunctionCount()
            + " created=" + created);
    }
}

// Exports the disassembly and Ghidra's pseudo-C of any functions (library ones included) for reading:
// OUTDIR/<addr>/asm.txt and ghidra.c, in the packet format. No oracle, no harness, read-only.
//
// Script args: 0 output folder, 1.. function addresses (hex)
//@category DecompPipeline
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.nio.file.*;

public class TcExport extends TcBase {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Path out = Paths.get(args[0]);
        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        fm = currentProgram.getFunctionManager();
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        for (int k = 1; k < args.length; k++) {
            fn = fm.getFunctionAt(toAddr(Long.parseLong(args[k], 16)));
            if (fn == null) {
                println("TcExport: no function at " + args[k]);
                continue;
            }
            Path dir = out.resolve(args[k].toLowerCase());
            Files.createDirectories(dir);
            body = fn.getBody();
            StringBuilder asm = new StringBuilder();
            asm.append("; ").append(fn.getName()).append("  entry ").append(fn.getEntryPoint()).append("  ")
                .append(body.getNumAddresses()).append(" bytes\n");
            asm.append("; SH-2 big endian. A mnemonic starting with '_' executes in the delay slot of the branch above it.\n");
            for (Instruction in : listing.getInstructions(body, true)) {
                long ia = in.getAddress().getOffset();
                String note = "";
                Long pv = poolValue(in);
                if (pv != null) note = "   ; = 0x" + hex(pv) + "  (" + describeConst(pv) + ")";
                for (Reference r : in.getReferencesFrom()) {
                    if (r.getReferenceType().isCall()) note += "   ; call " + r.getToAddress();
                }
                if (in.getAddress().equals(fn.getEntryPoint()) || in.getReferenceIteratorTo().hasNext()) {
                    if (ia != fn.getEntryPoint().getOffset()) asm.append(String.format("LAB_%s:\n", hex(ia)));
                }
                asm.append(String.format("  %s  %-34s%s\n", hex(ia), in.toString(), note));
            }
            Files.write(dir.resolve("asm.txt"), asm.toString().getBytes("UTF-8"));
            DecompileResults dres = di.decompileFunction(fn, 60, monitor);
            String c = dres.decompileCompleted() ? dres.getDecompiledFunction().getC() : "/* decompile failed */";
            Files.write(dir.resolve("ghidra.c"), c.getBytes("UTF-8"));
        }
        di.dispose();
    }
}

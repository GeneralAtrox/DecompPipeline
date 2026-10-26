// Exports the disassembly and Ghidra's pseudo-C of any functions for reading: OUTDIR/<addr>/asm.txt and ghidra.c,
// in the packet format (TcPrepare.asmText). No oracle, no harness, read-only.
//
// Script args: 0 output folder, 1.. function addresses (hex)
//@category DecompPipeline
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
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
            Files.write(dir.resolve("asm.txt"), TcAsm.text(this, fn, body, null).getBytes("UTF-8"));
            DecompileResults dres = di.decompileFunction(fn, 90, monitor);
            String c = dres.decompileCompleted() ? dres.getDecompiledFunction().getC() : "/* decompile failed */";
            Files.write(dir.resolve("ghidra.c"), c.getBytes("UTF-8"));
        }
        di.dispose();
    }
}

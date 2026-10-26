// Pre-analysis setup for a GameCube main.dol imported by the GameCubeLoader extension (run once, before analysis).
//  - maps every gap of MEM1 (0x80000000..0x81800000) that no DOL section covers as uninitialized RAM: the arena,
//    the OS globals page, the scratch window and the oracle's stack read as zero, as in the C runner
//  - script arguments NAME:HEX (project.json "fixed_regs"): register values pinned at every address of the
//    executable sections (r2 and r13, the SDA bases the startup code sets; GQRn), so the decompiler resolves
//    r2/r13-relative globals and the oracle starts every run with them
//  - script argument @FILE (project.json "seed_entries", written by tools/gcrel.py for REL modules linked into the
//    image): one hex address per line, each made an entry point and a function, so analysis starts there too
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.ProgramContext;
import ghidra.program.model.mem.*;
import java.math.BigInteger;
import java.util.*;

public class TcSetup extends GhidraScript {
    @Override
    public void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        long lo = TcBase.RAM_LO, hi = TcBase.RAM_HI;
        List<long[]> gaps = new ArrayList<>();
        long at = lo;
        List<MemoryBlock> blocks = new ArrayList<>(Arrays.asList(mem.getBlocks()));
        blocks.sort(Comparator.comparing(MemoryBlock::getStart));
        for (MemoryBlock b : blocks) {
            long s = b.getStart().getOffset(), e = b.getEnd().getOffset() + 1;
            if (e <= lo || s >= hi) continue;
            if (s > at) gaps.add(new long[] {at, s});
            at = Math.max(at, e);
        }
        if (at < hi) gaps.add(new long[] {at, hi});
        int n = 0;
        for (long[] g : gaps) {
            MemoryBlock b = mem.createUninitializedBlock("ram_" + Long.toHexString(g[0]), toAddr(g[0]), g[1] - g[0], false);
            b.setRead(true);
            b.setWrite(true);
            n++;
        }
        ProgramContext ctx = currentProgram.getProgramContext();
        int pins = 0, seeds = 0;
        for (String arg : getScriptArgs()) {
            if (arg.startsWith("@")) {
                for (String line : java.nio.file.Files.readAllLines(java.nio.file.Paths.get(arg.substring(1)))) {
                    if (line.isBlank()) continue;
                    Address a = toAddr(Long.parseLong(line.trim(), 16));
                    currentProgram.getSymbolTable().addExternalEntryPoint(a);
                    disassemble(a);
                    if (getFunctionAt(a) == null) createFunction(a, null);
                    seeds++;
                }
                continue;
            }
            pins++;
            String[] kv = arg.split(":");
            Register reg = currentProgram.getRegister(kv[0]);
            if (reg == null) throw new IllegalArgumentException("no register " + kv[0]);
            BigInteger v = new BigInteger(kv[1].replaceFirst("^0x", ""), 16);
            for (MemoryBlock b : mem.getBlocks()) {
                if (b.isExecute()) ctx.setValue(reg, b.getStart(), b.getEnd(), v);
            }
        }
        println("TcSetup: " + n + " RAM gap block(s), " + pins + " fixed register(s), " + seeds + " seeded entr(ies)");
    }
}

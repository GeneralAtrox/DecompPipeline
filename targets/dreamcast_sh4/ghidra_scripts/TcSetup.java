// Pre-analysis setup for a Dreamcast main binary loaded at 0x8c010000 (run once, before auto-analysis).
//  - maps the rest of Dreamcast main RAM (to 0x8d000000) as uninitialized memory
//  - pins FPSCR_PR / FPSCR_SZ / FPSCR_FR to 0 at every code address, the mode the
//    game's compiled C runs in, so fmov/fadd decode as single precision 32-bit moves
//  - seeds disassembly at the load address
//  - script arguments LO-HI (hex, end exclusive): ranges of a combined image (main binary + overlays at their load
//    addresses) that are only padding. They become uninitialized like the RAM past the image, so reading them is an
//    oracle input, exactly as for a single binary.
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.ProgramContext;
import ghidra.program.model.mem.*;
import java.math.BigInteger;

public class TcSetup extends GhidraScript {
    @Override
    public void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        MemoryBlock image = mem.getBlocks()[0];
        Address end = image.getEnd().add(1);
        long len = toAddr(0x8d000000L).subtract(end);
        MemoryBlock bss = mem.createUninitializedBlock("ram_bss", end, len, false);
        bss.setRead(true);
        bss.setWrite(true);

        ProgramContext ctx = currentProgram.getProgramContext();
        for (String name : new String[] {"FPSCR_PR", "FPSCR_SZ", "FPSCR_FR"}) {
            Register reg = currentProgram.getRegister(name);
            ctx.setValue(reg, image.getStart(), image.getEnd(), BigInteger.ZERO);
        }

        for (String gap : getScriptArgs()) {
            String[] lh = gap.split("-");
            Address lo = toAddr(Long.parseUnsignedLong(lh[0], 16)), hi = toAddr(Long.parseUnsignedLong(lh[1], 16));
            MemoryBlock blk = mem.getBlock(lo);
            if (!blk.getStart().equals(lo)) {
                mem.split(blk, lo);
                blk = mem.getBlock(lo);
            }
            if (blk.getEnd().compareTo(hi) >= 0) mem.split(blk, hi);
            mem.convertToUninitialized(mem.getBlock(lo));
        }

        Address entry = toAddr(0x8c010000L);
        disassemble(entry);
        createFunction(entry, "entry");
        currentProgram.getSymbolTable().addExternalEntryPoint(entry);
    }
}

// Pre-analysis setup for a Saturn image (a program or a combined image of a loader plus overlays) loaded at its load
// address in work RAM high (run once, before auto-analysis).
//  - maps the rest of work RAM high (0x06000000..0x060fffff, below and above the image) and all of work RAM low
//    (0x00200000..0x002fffff) as uninitialized memory: reading it is an oracle input
//  - seeds disassembly at the first loaded byte (the load address, or a combined image's first file)
//  - script arguments LO-HI (hex, end exclusive): ranges of a combined image (main binary + overlays at their load
//    addresses) that are only padding. They become uninitialized like the RAM past the image, so reading them is an
//    oracle input, exactly as for a single binary.
// The SH-2 has no FPU: no FPSCR mode to pin (the Dreamcast setup's FPSCR_PR/SZ/FR context).
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.*;

public class TcSetup extends GhidraScript {
    void uninitialized(String name, long lo, long hi) throws Exception {
        if (hi <= lo) return;
        MemoryBlock b = currentProgram.getMemory().createUninitializedBlock(name, toAddr(lo), hi - lo, false);
        b.setRead(true);
        b.setWrite(true);
    }

    @Override
    public void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        MemoryBlock image = mem.getBlocks()[0];
        long start = image.getStart().getOffset(), end = image.getEnd().getOffset() + 1;
        if (start < TcBase.RAM_LO || end > TcBase.RAM_HI) {
            throw new IllegalStateException("image " + Long.toHexString(start) + "-" + Long.toHexString(end)
                + " is not inside work RAM high");
        }
        uninitialized("ram_lo", TcBase.RAM_LO, start);
        uninitialized("ram_bss", end, TcBase.RAM_HI);
        uninitialized("ram_low", TcBase.LOW_LO, TcBase.LOW_HI);

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

        // the first loaded byte: a combined image starts at 0x06000000 with padding (a gap) up to its first file
        Address entry = null;
        for (MemoryBlock blk : mem.getBlocks()) {
            if (blk.isInitialized() && (entry == null || blk.getStart().compareTo(entry) < 0)) entry = blk.getStart();
        }
        disassemble(entry);
        createFunction(entry, "entry");
        currentProgram.getSymbolTable().addExternalEntryPoint(entry);
    }
}

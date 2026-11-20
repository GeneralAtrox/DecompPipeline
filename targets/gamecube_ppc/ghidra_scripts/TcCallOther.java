// The GameCube oracle's callbacks for the SLEIGH's user-defined p-code ops (CALLOTHER), which Ghidra's emulator
// does not implement. TcOracle installs them; TcFixups.refuse refuses an instruction with an op not in HANDLED.
//  - cache and ordering instructions with no register or memory effect a single-threaded program can see (dcbf, dcbst,
//    dcbt, dcbtst, icbi, sync, isync, eieio) are no-ops: the SLEIGH's CALLOTHERs get empty callbacks (install). dcbz
//    zeroes the 32-byte block holding the effective address.
//  - cntlzw: countLeadingZeros. psq_l / psq_st: ldexpf (2^n). sc: syscall (TcSystem.sc).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.pcode.memstate.MemoryState;
import ghidra.pcode.pcoderaw.PcodeOpRaw;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.pcode.Varnode;

public class TcCallOther {
    // cntlzw / cntlzw.: the SLEIGH is A = countLeadingZeros(S:4), a CALLOTHER the emulator does not implement.
    // The cache and ordering CALLOTHERs below do nothing a program can see (see the header); dataCacheBlockClearToZero
    // zeroes the 32-byte block.
    static final java.util.Set<String> NOP_OPS = java.util.Set.of("sync", "instructionSynchronize",
        "enforceInOrderExecutionIO", "dataCacheBlockFlush", "dataCacheBlockStore", "dataCacheBlockTouch",
        "dataCacheBlockTouchForStore", "instructionCacheBlockInvalidate");
    static final java.util.Set<String> HANDLED;

    static {
        java.util.Set<String> h = new java.util.HashSet<>(NOP_OPS);
        h.add("countLeadingZeros");
        h.add("dataCacheBlockClearToZero");
        h.add("ldexpf");
        h.add("syscall");
        h.add("dataCacheBlockInvalidate");
        HANDLED = java.util.Set.copyOf(h);
    }

    static void install(EmulatorHelper emu, AddressSpace ram) {
        for (String name : NOP_OPS) {
            emu.registerCallOtherCallback(name, new BreakCallBack() {
                @Override
                public boolean pcodeCallback(PcodeOpRaw op) {
                    return true;
                }
            });
        }
        emu.registerCallOtherCallback("dataCacheBlockClearToZero", new BreakCallBack() {
            @Override
            public boolean pcodeCallback(PcodeOpRaw op) {
                MemoryState ms = emu.getEmulator().getMemState();
                long ea = ms.getValue(op.getInput(1)) & 0xffffffe0L;
                ms.setChunk(new byte[32], ram, ea, 32);
                return true;
            }
        });
        emu.registerCallOtherCallback("syscall", new BreakCallBack() {  // sc: the OS handler (TcSystem)
            @Override
            public boolean pcodeCallback(PcodeOpRaw op) {
                TcSystem.sc(emu);
                return true;
            }
        });
        // dcbi: no memory effect; TcFixups checks the block was not written during the run (TcSystem.dcbi).
        emu.registerCallOtherCallback("dataCacheBlockInvalidate", new BreakCallBack() {
            @Override
            public boolean pcodeCallback(PcodeOpRaw op) {
                return true;
            }
        });
        // psq_l / psq_st scale = ldexpf(n): 2^n, so the step runs. The fixup rewrites what the step loaded or stored.
        emu.registerCallOtherCallback("ldexpf", new BreakCallBack() {
            @Override
            public boolean pcodeCallback(PcodeOpRaw op) {
                MemoryState ms = emu.getEmulator().getMemState();
                Varnode out = op.getOutput();
                if (out == null) return true;
                int n = (int) (byte) ms.getValue(op.getInput(1));
                ms.setValue(out, out.getSize() == 4 ? Float.floatToRawIntBits(Math.scalb(1.0f, n)) & 0xffffffffL
                    : Double.doubleToRawLongBits(Math.scalb(1.0, n)));
                return true;
            }
        });
        emu.registerCallOtherCallback("countLeadingZeros", new BreakCallBack() {
            @Override
            public boolean pcodeCallback(PcodeOpRaw op) {
                MemoryState ms = emu.getEmulator().getMemState();
                Varnode in = op.getInput(1);
                int bits = 8 * in.getSize();
                long v = ms.getValue(in) & (bits >= 64 ? -1L : (1L << bits) - 1);
                ms.setValue(op.getOutput(), v == 0 ? bits : Long.numberOfLeadingZeros(v) - (64 - bits));
                return true;
            }
        });
    }
}

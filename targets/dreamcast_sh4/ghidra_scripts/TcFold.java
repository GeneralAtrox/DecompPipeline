// The oracle's RAM bank: main RAM through its P0 mirror (0x0c...) reads and writes the 0x8c... bytes (TcBase.fold).
// Not a script itself; TcOracle installs it over the emulator's RAM space.
import ghidra.pcode.memstate.MemoryBank;
import ghidra.pcode.memstate.MemoryPage;

public class TcFold extends MemoryBank {
    final MemoryBank in;
    // The first address in the P4 operand-cache address array (TcBase.P4_OCA_LO..HI) that was read, or -1. A store
    // there is logged (TcOracle.collect), but a load returns cache state (tag, U and V bits), which nothing models:
    // the emulator would hand back the bytes stored, and the C runner (tc_p4.c) faults. TcOracle fails the vector on
    // such a read, as on any read outside RAM (Toy Commander 8c05be7c, 8c0542c4: a random r4 in P4, read back).
    long p4Read = -1;

    TcFold(MemoryBank in) {
        super(in.getSpace(), in.isBigEndian(), in.getPageSize(), in.getMemoryFaultHandler());
        this.in = in;
    }

    @Override
    public void setChunk(long offset, int size, byte[] val) {
        in.setChunk(TcBase.fold(offset), size, val);
    }

    @Override
    public void setInitialized(long offset, int size, boolean initialized) {
        in.setInitialized(TcBase.fold(offset), size, initialized);
    }

    @Override
    public int getChunk(long offset, int size, byte[] res, boolean stopOnUninitialized) {
        long f = TcBase.fold(offset);
        if (p4Read < 0 && f < TcBase.P4_OCA_HI && f + size > TcBase.P4_OCA_LO) p4Read = Math.max(f, TcBase.P4_OCA_LO);
        return in.getChunk(f, size, res, stopOnUninitialized);
    }

    @Override
    protected MemoryPage getPage(long addr) {
        throw new UnsupportedOperationException("TcFold delegates whole chunks");
    }

    @Override
    protected void setPage(long addr, byte[] val, int skip, int size, int bufOffset) {
        throw new UnsupportedOperationException("TcFold delegates whole chunks");
    }

    @Override
    protected void setPageInitialized(long addr, boolean initialized, int skip, int size, int bufOffset) {
        throw new UnsupportedOperationException("TcFold delegates whole chunks");
    }
}

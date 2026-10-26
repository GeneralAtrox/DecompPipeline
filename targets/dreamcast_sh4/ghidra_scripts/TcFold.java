// The oracle's RAM bank: main RAM through its P0 mirror (0x0c...) reads and writes the 0x8c... bytes (TcBase.fold),
// and so does the store-queue window for a TcSq function, whose stores there also fill the queues (TcSq). A load
// from a hardware register the project lists returns the target's value for it (TcHw).
// Not a script itself; TcOracle installs one per run over the emulator's RAM space.
import ghidra.pcode.memstate.MemoryBank;
import ghidra.pcode.memstate.MemoryPage;

public class TcFold extends MemoryBank {
    final MemoryBank in;
    // The first address in the P4 operand-cache address array (TcBase.P4_OCA_LO..HI) that was read, or -1. A store
    // there is logged (TcOracle.collect), but a load returns cache state (tag, U and V bits), which nothing models:
    // the emulator would hand back the bytes stored, and the C runner (tc_p4.c) faults. TcOracle fails the vector on
    // such a read, as on any read outside RAM (Toy Commander 8c05be7c, 8c0542c4: a random r4 in P4, read back).
    long p4Read = -1;
    final TcSq sq;  // the function's store queues (TcSq), or null
    // An SH-4 address error (a 2-byte access at an odd address, a longer one off a multiple of 4): the first such
    // access of an instruction TcOracle steps (stepping), or -1. The hardware never completes one, so no run of the
    // original makes one; Ghidra completes it, and a NaN or any value stored there lands at bytes the C runner need
    // not reproduce (Power Stone 2 8c052cbe, Spawn 8c07cfe0: a pointer from the inputs). Recorded only: the run goes
    // on as before, and TcAlign leaves a valid vector that made one out of the harness (Result.misaligned).
    long misaligned = -1;
    boolean misWrite, stepping;

    // gi: r8..r14 at entry (TcOracle.entryGi), where the queues' state at entry comes from (TcSq.begin).
    TcFold(MemoryBank in, TcSq sq, long[] gi) {
        super(in.getSpace(), in.isBigEndian(), in.getPageSize(), in.getMemoryFaultHandler());
        this.in = in;
        this.sq = sq;
        TcBase.sqWindow = sq != null;  // TcBase.fold maps the window onto RAM in this function's runs only
        if (sq != null) sq.begin(gi);
    }

    @Override
    public void setChunk(long offset, int size, byte[] val) {
        align(offset, size, true);
        if (sq != null) sq.store(offset, size, val);
        in.setChunk(TcBase.fold(offset), size, val);
    }

    @Override
    public void setInitialized(long offset, int size, boolean initialized) {
        in.setInitialized(TcBase.fold(offset), size, initialized);
    }

    @Override
    public int getChunk(long offset, int size, byte[] res, boolean stopOnUninitialized) {
        if (!stopOnUninitialized) align(offset, size, false);  // a load (getValue); true: an instruction fetch
        byte[] hw = TcHw.on == null ? null : TcHw.on.read(offset, size);  // a listed hardware register (TcHw)
        if (hw != null) {
            System.arraycopy(hw, 0, res, 0, size);
            return size;
        }
        long f = TcBase.fold(offset);
        if (p4Read < 0 && f < TcBase.P4_OCA_HI && f + size > TcBase.P4_OCA_LO) p4Read = Math.max(f, TcBase.P4_OCA_LO);
        return in.getChunk(f, size, res, stopOnUninitialized);
    }

    void align(long offset, int size, boolean write) {
        // a data access only: a load or store is 2, 4 or 8 bytes (the fetch reads its cache from a 2-aligned pc)
        if (stepping && misaligned < 0 && (size == 2 || size == 4 || size == 8)
                && (offset & (size == 2 ? 1 : 3)) != 0) {
            misaligned = offset;
            misWrite = write;
        }
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

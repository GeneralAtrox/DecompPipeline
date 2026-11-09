// The oracle's RAM bank (not a script itself; TcOracle installs one per run over the emulator's RAM space): the
// uncached MEM1 view 0xc0000000.. reads and writes the 0x80000000.. bytes (TcBase.fold), and every data access of
// the current step is logged for the oracle:
//  - reads: {pc, address, size, value} for the load log (pointer repair, TcInputs.repairVec) and rule R8 (the C
//    runner fails any data read whose first byte is an instruction byte: state/code_ranges.txt);
//  - the step's last store {address, size} (TcFixups: stfs), all its stores (TcTrack: float stores), and every byte
//    stored once `track` is set (after the vector's patches are in): the run's writes, compared by the checker.
// The emulator's instruction fetches go through the bank too; reads starting in [pc, pc + 16) are taken as fetches.
// A store into a hardware register (facts mmio: the GX FIFO at 0xcc008000, the video and DMA registers) is not
// memory: it is logged in order (io: {address, size, value, 0}) and goes no further, so nothing reads it back. A read
// of one takes the vector's next scripted word (hw, vectors.txt H: a device's answer is an input) and is logged in the
// same list ({address, size, value, 1}): the order of reads and writes is part of the result. Without hw it faults, as
// it always did. The same step storing the same address and size again replaces that entry (TcFixups: stfs rewrites
// the stored word).
import ghidra.pcode.memstate.MemoryBank;
import ghidra.pcode.memstate.MemoryPage;
import ghidra.program.model.address.AddressSet;
import java.util.*;

public class TcBank extends MemoryBank {
    final MemoryBank in;
    final TcFacts facts;
    long pc = -1;                                  // the instruction being stepped
    final List<long[]> reads = new ArrayList<>();  // this step's data reads {pc, address, size, value}
    long[] stored;                                 // this step's last store {address, size, io index or -1}
    final List<long[]> stores = new ArrayList<>(); // this step's stores {address, size, io index or -1}
    long codeRead = -1;                            // the first data read of an instruction byte (R8), or -1
    boolean track;                                 // record stores in written
    static final int IO_LIMIT = 4096;              // tc_rt.c's MAX_IO must equal it
    final List<long[]> io = new ArrayList<>();     // stores into hardware registers, in order {address, size, value}
    boolean ioOverflow;                            // more than IO_LIMIT of them
    int ioStep = -1;                               // the io index of this step's last entry, or -1
    long[] hw;                                     // the vector's scripted hardware words (16, then round again), or null
    int hwNext;                                    // reads of them so far
    final AddressSet written = new AddressSet();

    TcBank(MemoryBank in, TcFacts facts) {
        super(in.getSpace(), in.isBigEndian(), in.getPageSize(), in.getMemoryFaultHandler());
        this.in = in;
        this.facts = facts;
    }

    void beginStep(long at) {
        pc = at;
        reads.clear();
        stored = null;
        stores.clear();
        ioStep = -1;
    }

    @Override
    public void setChunk(long offset, int size, byte[] val) {
        long f = TcBase.fold(offset);
        stored = new long[] {f, size, -1};
        stores.add(stored);
        if (track && size > 0 && size <= 8 && facts.isMmio(f)) {
            long v = 0;
            for (int i = 0; i < size; i++) v = v << 8 | (val[i] & 0xffL);
            if (ioStep >= 0 && io.get(ioStep)[0] == f && io.get(ioStep)[1] == size) io.get(ioStep)[2] = v;
            else if (io.size() < IO_LIMIT) {
                io.add(new long[] {f, size, v, 0});
                ioStep = io.size() - 1;
            } else ioOverflow = true;
            if (ioStep >= 0 && io.get(ioStep)[0] == f) stored[2] = ioStep;
            return;
        }
        if (track && size > 0) written.add(getSpace().getAddress(f), getSpace().getAddress(f + size - 1));
        in.setChunk(f, size, val);
    }

    @Override
    public void setInitialized(long offset, int size, boolean initialized) {
        in.setInitialized(TcBase.fold(offset), size, initialized);
    }

    @Override
    public int getChunk(long offset, int size, byte[] res, boolean stopOnUninitialized) {
        long f = TcBase.fold(offset);
        if (track && hw != null && pc >= 0 && size > 0 && size <= 8 && facts.isMmio(f)) {
            long w = hw[hwNext++ & 15] & 0xffffffffL;
            long v = size == 8 ? w << 32 | w : size == 4 ? w : w & ((1L << (8 * size)) - 1);
            for (int i = 0; i < size; i++) res[i] = (byte) (v >>> (8 * (size - 1 - i)));
            if (io.size() < IO_LIMIT) io.add(new long[] {f, size, v, 1});
            else ioOverflow = true;
            reads.add(new long[] {pc, f, size, v});
            return size;
        }
        int got = keepWritten(f, size, res, stopOnUninitialized);
        boolean fetch = pc < 0 || (f >= pc && f < pc + 16);
        if (!fetch) {
            if (codeRead < 0 && facts.isCode(f)) codeRead = f;
            long v = 0;
            for (int i = 0; i < Math.min(size, 8); i++) v = v << 8 | (res[i] & 0xffL);
            reads.add(new long[] {pc, f, size, v});
        }
        return got;
    }

    // Ghidra's MemoryBank.getChunk hands the fault handler the whole rest of a read from its first byte never written,
    // as the page's own data, and TcOracle's handler zeroes that range and accepts it (so stopOnUninitialized never
    // stops it): a word whose first bytes were never written lost the bytes after them that were. Skies 80211298: the
    // call record's read of r1+0x18 wiped what sth r0,0x1a(r1) had stored, so the oracle copied 0 where the original
    // copies the halfword; 800e6e58: lwz of a word whose low half sthx had just stored read 0. Read each run the page
    // marks initialized as it is (no handler) and each byte never written alone, so the handler only fills bytes that
    // hold nothing.
    static final java.lang.reflect.Method PAGE;
    static {
        try {
            PAGE = MemoryBank.class.getDeclaredMethod("getPage", long.class);
            PAGE.setAccessible(true);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    // The bytes from f (at most n, within f's page) that the page marks initialized.
    int initRun(long f, int n) {
        long ps = in.getPageSize(), al = f & ~(ps - 1);
        int skip = (int) (f - al), m = (int) Math.min(n, ps - skip);
        try {
            return ((MemoryPage) PAGE.invoke(in, al)).getInitializedByteCount(skip, m);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    int keepWritten(long f, int size, byte[] res, boolean stopOnUninitialized) {
        int n = 0;
        while (n < size) {
            int k = Math.max(initRun(f + n, size - n), 1);
            byte[] b = new byte[k];
            int got = in.getChunk(f + n, k, b, stopOnUninitialized);
            System.arraycopy(b, 0, res, n, Math.max(got, 0));
            n += Math.max(got, 0);
            if (got < k) break;
        }
        return n;
    }

    @Override
    protected MemoryPage getPage(long addr) {
        throw new UnsupportedOperationException("TcBank delegates whole chunks");
    }

    @Override
    protected void setPage(long addr, byte[] val, int skip, int size, int bufOffset) {
        throw new UnsupportedOperationException("TcBank delegates whole chunks");
    }

    @Override
    protected void setPageInitialized(long addr, boolean initialized, int skip, int size, int bufOffset) {
        throw new UnsupportedOperationException("TcBank delegates whole chunks");
    }
}

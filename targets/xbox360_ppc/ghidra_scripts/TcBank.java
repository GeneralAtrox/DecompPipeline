// The oracle's RAM bank (not a script itself; TcOracle installs one per run over the emulator's RAM space): every
// data access of the current step is logged for the oracle:
//  - reads: {pc, address, size, value} for the load log (pointer repair, TcInputs.repairVec) and rule R8 (the C
//    runner fails any data read whose first byte is an instruction byte: state/code_ranges.txt);
//  - the step's last store {address, size} (TcFixups: stfs), all its stores (TcTrack: float stores), and every byte
//    stored once `track` is set (after the vector's patches are in): the run's writes, compared by the checker.
// The emulator's instruction fetches go through the bank too; reads starting in [pc, pc + 16) are taken as fetches.
// Addresses are 32 bits (64-32addr): there are no mirrors to fold, unlike the GameCube's.
import ghidra.pcode.memstate.MemoryBank;
import ghidra.pcode.memstate.MemoryPage;
import ghidra.program.model.address.AddressSet;
import java.util.*;

public class TcBank extends MemoryBank {
    final MemoryBank in;
    final TcFacts facts;
    long pc = -1;                                  // the instruction being stepped
    final List<long[]> reads = new ArrayList<>();  // this step's data reads {pc, address, size, value}
    long[] stored;                                 // this step's last store {address, size}
    final List<long[]> stores = new ArrayList<>(); // this step's stores {address, size}
    long codeRead = -1;                            // the first data read of an instruction byte (R8), or -1
    boolean track;                                 // record stores in written
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
    }

    @Override
    public void setChunk(long offset, int size, byte[] val) {
        long f = TcBase.fold(offset);
        stored = new long[] {f, size};
        stores.add(stored);
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
        int got = stopOnUninitialized || size == 1 ? in.getChunk(f, size, res, stopOnUninitialized) : whole(f, size, res);
        boolean fetch = pc < 0 || (f >= pc && f < pc + 16);
        if (!fetch) {
            if (codeRead < 0 && facts.isCode(f)) codeRead = f;
            long v = 0;
            for (int i = 0; i < Math.min(size, 8); i++) v = v << 8 | (res[i] & 0xffL);
            reads.add(new long[] {pc, f, size, v});
        }
        return got;
    }

    // A read that may go on through unwritten bytes. MemoryBank.getChunk hands the fault handler all that is left of a
    // read from its first unwritten byte, and the zeros the handler fills in (the regions' unwritten memory) land on the
    // page over any written bytes further along. So a doubleword read whose first word was never written wiped its
    // second word: TcOracle.callRecord reads the stack doublewords at every call, and 821bd160 lost its
    // `stw r30,0x64(r31)` to the mocked call after it and returned 0 (only on the paths with no earlier call to have
    // written the first word). Here the handler gets each unwritten byte alone, so what was stored stays. While
    // `probing` the handler declines (fills nothing, reports nothing) and getChunk stops at the first unwritten byte.
    static boolean probing;

    int whole(long f, int size, byte[] res) {
        byte[] rest = res;
        int done = 0;
        byte[] one = new byte[1];
        while (true) {
            probing = true;
            int n;
            try {
                n = in.getChunk(f + done, size - done, rest, true);
            } finally {
                probing = false;
            }
            if (rest != res) System.arraycopy(rest, 0, res, done, n);
            done += n;
            if (done >= size) return size;
            in.getChunk(f + done, 1, one, false);
            res[done++] = one[0];
            if (done >= size) return size;
            rest = new byte[size - done];
        }
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

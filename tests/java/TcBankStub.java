// The oracle bank's hardware-register log (tests/test_gc_io.py): TcBank over a plain page bank, no Ghidra program.
//
// usage: java TcBankStub   prints "key value" lines:
//   io          the log after the script below: "address size value" joined by ';'
//   written     the bytes the bank recorded as the run's RAM writes: "lo-hi" joined by ';'
//   readback    the word at 0xcc008000 read through the bank (0: a hardware register is not memory)
//   ram         the word at 0x80002000 read through the bank after a RAM store (the store reached memory)
//   patch       the word at 0xcc000010 after a store made before `track` was set (an input patch passes through)
//   overflow    ioOverflow after IO_LIMIT + 1 stores
//   count       the log's size after them (IO_LIMIT)
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryPageBank;
import ghidra.program.model.address.*;
import java.util.*;

public class TcBankStub {
    static byte[] w(long v, int n) {
        byte[] b = new byte[n];
        for (int i = 0; i < n; i++) b[i] = (byte) (v >>> (8 * (n - 1 - i)));
        return b;
    }

    static long word(TcBank b, long a) {
        byte[] r = new byte[4];
        b.in.getChunk(a, 4, r, false);
        return (r[0] & 0xffL) << 24 | (r[1] & 0xffL) << 16 | (r[2] & 0xffL) << 8 | (r[3] & 0xffL);
    }

    static String io(TcBank b) {
        StringBuilder s = new StringBuilder();
        for (long[] e : b.io) s.append(s.length() > 0 ? ";" : "").append(Long.toHexString(e[0])).append(' ').append(e[1]).append(' ').append(Long.toHexString(e[2]));
        return s.toString();
    }

    public static void main(String[] args) {
        AddressSpace sp = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 0);
        MemoryFaultHandler h = new MemoryFaultHandler() {
            public boolean uninitializedRead(Address a, int size, byte[] buf, int off) {
                for (int i = 0; i < size; i++) buf[off + i] = 0;
                return true;
            }

            public boolean unknownAddress(Address a, boolean write) {
                return false;
            }
        };
        TcFacts f = TcFacts.defaults();
        f.mmio.add(new long[] {0xcc000000L, 0xcc010000L});
        f.mmio.add(new long[] {0xe0000000L, 0xe0004000L});
        TcBank b = new TcBank(new MemoryPageBank(sp, true, 4096, h), f);
        b.setChunk(0xcc000010L, 4, w(0x11223344L, 4));  // an input patch, before track
        System.out.println("patch " + Long.toHexString(word(b, 0xcc000010L)));
        b.track = true;
        b.beginStep(0x80001000L);
        b.setChunk(0xcc008000L, 4, w(0x3f800000L, 4));
        b.setChunk(0xcc008000L, 4, w(0x40000000L, 4));  // the same step, address and size: replaces
        b.beginStep(0x80001004L);
        b.setChunk(0xcc008000L, 4, w(0x40400000L, 4));  // the next step: a new entry
        b.beginStep(0x80001008L);
        b.setChunk(0xcc00001cL, 2, w(0xbeefL, 2));
        b.setChunk(0xe0000020L, 8, w(0x0102030405060708L, 8));
        b.beginStep(0x8000100cL);
        b.setChunk(0x80002000L, 4, w(0xdeadbeefL, 4));  // RAM
        b.setChunk(0xd0000000L, 4, w(1, 4));  // neither: stays a RAM-window violation
        b.setChunk(0xcc010000L, 4, w(2, 4));  // one past the window
        System.out.println("io " + io(b));
        StringBuilder s = new StringBuilder();
        for (AddressRange r : b.written) s.append(s.length() > 0 ? ";" : "").append(Long.toHexString(r.getMinAddress().getOffset())).append('-').append(Long.toHexString(r.getMaxAddress().getOffset()));
        System.out.println("written " + s);
        System.out.println("readback " + Long.toHexString(word(b, 0xcc008000L)));
        System.out.println("ram " + Long.toHexString(word(b, 0x80002000L)));
        TcBank o = new TcBank(new MemoryPageBank(sp, true, 4096, h), f);
        o.track = true;
        for (int i = 0; i <= TcBank.IO_LIMIT; i++) {
            o.beginStep(0x80001000L + 4 * i);
            o.setChunk(0xcc008000L, 4, w(i, 4));
        }
        System.out.println("overflow " + o.ioOverflow);
        System.out.println("count " + o.io.size());
    }
}

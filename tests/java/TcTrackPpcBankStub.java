// tests/test_track.py: the GameCube TcTrack reads a word of RAM for a step past the bank's log (TcTrack.word with
// the bank), so the step's loads (TcBank.reads: the load log, rule R8) stay what the instruction read and step's loop
// over them is not changed under it (Skies of Arcadia 80100bf8: ConcurrentModificationException on every vector).
// Prints the word read, the bank's reads before and after, and a read that folds the uncached view.
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryPageBank;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.address.GenericAddressSpace;

public class TcTrackPpcBankStub {
    public static void main(String[] args) {
        AddressSpace ram = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 0);
        MemoryFaultHandler none = new MemoryFaultHandler() {
            public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) { return false; }
            public boolean unknownAddress(Address address, boolean write) { return false; }
        };
        MemoryPageBank in = new MemoryPageBank(ram, true, 4096, none);
        in.setChunk(0x80001000L, 4, new byte[] {(byte) 0x7f, (byte) 0xc0, 0, 1});
        TcBank bank = new TcBank(in, null);
        bank.beginStep(0x80000100L);
        bank.reads.add(new long[] {0x80000100L, 0x80001000L, 4, 0x7fc00001L});  // the lwz's own load
        int before = bank.reads.size();
        long w = TcTrack.word(bank, 0x80001000L);
        long u = TcTrack.word(bank, 0xc0001000L);
        System.out.printf("word %08x reads %d -> %d uncached %08x%n", w, before, bank.reads.size(), u);
    }
}

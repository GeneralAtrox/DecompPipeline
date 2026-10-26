// SH-4 address errors in the oracle's RAM bank (tests/test_sh4_align.py): TcFold.align over a plain Ghidra page bank,
// no Ghidra application and no program.
//
// usage: java TcAlignStub CASES      CASES lines (hex; # starts a comment), one run (a fresh TcFold) per line:
//   NAME ACCESS...   ACCESS: [s]{r|w|f}SIZE@ADDR, an access of SIZE bytes (s: while TcOracle steps an instruction;
//   f: an instruction fetch, a read with stopOnUninitialized as EmulateMemoryStateBuffer.getBytes makes it)
//   prints "NAME: MIS P4" (MIS: TcFold.misaligned and " write|read", or "-"; P4: TcFold.p4Read, or "-")
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryPageBank;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.address.GenericAddressSpace;
import java.nio.file.*;

public class TcAlignStub {
    public static void main(String[] args) throws Exception {
        AddressSpace ram = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 0);
        MemoryFaultHandler none = new MemoryFaultHandler() {
            public boolean uninitializedRead(Address a, int n, byte[] buf, int off) { return true; }
            public boolean unknownAddress(Address a, boolean write) { return false; }
        };
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.trim().split("\\s+");
            if (t[0].isEmpty() || t[0].startsWith("#")) continue;
            TcFold bank = new TcFold(new MemoryPageBank(ram, false, 4096, none), null, new long[7]);
            for (int i = 1; i < t.length; i++) {
                String a = t[i];
                bank.stepping = a.startsWith("s");
                if (bank.stepping) a = a.substring(1);
                int size = Integer.parseInt(a.substring(1, a.indexOf('@')));
                long addr = Long.parseLong(a.substring(a.indexOf('@') + 1), 16);
                byte[] buf = new byte[size];
                if (a.charAt(0) == 'w') bank.setChunk(addr, size, buf);
                else bank.getChunk(addr, size, buf, a.charAt(0) == 'f');
                bank.stepping = false;
            }
            System.out.println(t[0] + ": " + (bank.misaligned < 0 ? "-" : Long.toHexString(bank.misaligned)
                + (bank.misWrite ? " write" : " read")) + " " + (bank.p4Read < 0 ? "-" : Long.toHexString(bank.p4Read)));
        }
    }
}

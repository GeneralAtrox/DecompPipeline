// tests/test_x360_judge_gaps.py: the Xbox 360 TcTrack without Ghidra. One case per line of the file in args[0], one
// output line "N: result" per case that prints:
//   T CALLEES PARAMS     a new function (comma lists of hex entries and registers, "-" for none) and run
//   D REGS               the run sets these registers (a step's outputs)
//   C TARGET ZEROED      a mocked call that zeroes ZEROED (TcOracle.clobbered): TcTrack.call ("\n" shown as "|"),
//                        then "; defined regs; fbits regs"
//   N HEX                TcTrack.nan
//   B REGS               these registers hold float bits (an lwz or ld of a float store's word: TcTrack.step)
//   F MNEM D S B OUTS    TcTrack.flow of an instruction (operand registers D, S and B, "-" for none) writing OUTS:
//                        "stores regs"
//   W                    TcTrack.word through a bank: the word, and the bank's reads before and after
//   Z HEX                TcFlow.literalZero of an instruction word: the registers its p-code reads and the
//                        processor does not
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryPageBank;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.address.GenericAddressSpace;
import java.nio.file.*;
import java.util.*;

public class X360TrackStub {
    static List<String> list(String s) {
        return s.equals("-") ? List.of() : Arrays.asList(s.split(","));
    }

    static String sorted(Set<String> s) {
        List<String> l = new ArrayList<>(s);
        l.sort(Comparator.comparing((String x) -> x.charAt(0)).thenComparing(x -> Integer.parseInt(x.substring(1))));
        return String.join(",", l);
    }

    static String word() {
        AddressSpace ram = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 0);
        MemoryFaultHandler none = new MemoryFaultHandler() {
            public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) { return false; }
            public boolean unknownAddress(Address address, boolean write) { return false; }
        };
        MemoryPageBank in = new MemoryPageBank(ram, true, 4096, none);
        in.setChunk(0x40001000L, 4, new byte[] {(byte) 0x7f, (byte) 0xc0, 0, 1});
        TcBank bank = new TcBank(in, null);
        bank.beginStep(0x82000100L);
        bank.reads.add(new long[] {0x82000100L, 0x40001000L, 4, 0x7fc00001L});  // the lwz's own load
        int before = bank.reads.size();
        long w = TcTrack.word(bank, 0x40001000L);
        return String.format("word %08x reads %d -> %d", w, before, bank.reads.size());
    }

    public static void main(String[] args) throws Exception {
        int n = 0;
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.replaceAll("#.*", "").trim().split("\\s+");
            if (t[0].isEmpty()) continue;
            String out = null;
            switch (t[0]) {
                case "T":
                    Set<Long> c = new HashSet<>();
                    for (String a : list(t[1])) c.add(Long.parseLong(a, 16));
                    TcTrack.on = new TcTrack(c, new HashSet<>(list(t[2])), true, true, null);
                    TcTrack.start();
                    break;
                case "D":
                    TcTrack.on.cur.defined.addAll(list(t[1]));
                    break;
                case "C":
                    out = TcTrack.call(Long.parseLong(t[1], 16), new HashSet<>(list(t[2]))).replace("\n", "|")
                        + "; " + sorted(TcTrack.on.cur.defined) + "; " + sorted(TcTrack.on.cur.fbits);
                    break;
                case "N":
                    out = String.valueOf(TcTrack.nan(Long.parseLong(t[1], 16)));
                    break;
                case "B":
                    TcTrack.on.cur.fbits.addAll(list(t[1]));
                    break;
                case "F":
                    boolean st = TcTrack.flow(TcTrack.on.cur.fbits, t[1], t[2].replace("-", ""),
                        t[3].replace("-", ""), t[4].replace("-", ""), new HashSet<>(list(t[5])));
                    out = st + " " + sorted(TcTrack.on.cur.fbits);
                    break;
                case "W":
                    out = word();
                    break;
                case "Z":
                    out = sorted(TcFlow.literalZero(Long.parseLong(t[1], 16)));
                    break;
                default:
                    throw new IllegalArgumentException(line);
            }
            if (out != null) System.out.println(n++ + ": " + out);
        }
    }
}

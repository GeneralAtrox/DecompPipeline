// tests/test_x360_oracle_runs.py's view of the Xbox 360 oracle's parts that need no program: Ghidra's real memory bank
// under TcBank, and TcFlow's and TcRun's decisions.
//
// usage: java X360OracleStub CASES   CASE lines (numbers in hex):
//   "b"                   a fresh bank of the oracle's kind (the fault handler zero-fills, as TcOracle's does)
//   "s addr bytes"        store through it
//   "r addr size"         prints "r addr bytes": a read through it, which may start on unwritten bytes
//   "i word"              prints "i word 0|1": TcFlow.identityMove
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryPageBank;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.FlowType;
import ghidra.program.model.symbol.RefType;
import java.nio.file.*;

public class X360OracleStub {
    static final AddressSpace RAM = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 3);

    static long hex(String s) {
        return Long.parseUnsignedLong(s, 16);
    }

    static byte[] bytes(String s) {
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return b;
    }

    static TcBank bank() {
        MemoryFaultHandler zero = new MemoryFaultHandler() {
            @Override
            public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) {
                if (TcBank.probing) return false;
                for (int i = 0; i < size; i++) buf[bufOffset + i] = 0;
                return true;
            }

            @Override
            public boolean unknownAddress(Address address, boolean write) {
                return false;
            }
        };
        return new TcBank(new MemoryPageBank(RAM, true, 4096, zero), TcFacts.defaults());
    }

    public static void main(String[] args) throws Exception {
        TcBank bank = null;
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = line.trim().split("\\s+");
            switch (t[0]) {
                case "b":
                    bank = bank();
                    break;
                case "s":
                    byte[] d = bytes(t[2]);
                    bank.setChunk(hex(t[1]), d.length, d);
                    bank.setInitialized(hex(t[1]), d.length, true);
                    break;
                case "r":
                    byte[] got = new byte[Integer.parseInt(t[2], 16)];
                    bank.getChunk(hex(t[1]), got.length, got, false);
                    System.out.println("r " + t[1] + " " + TcBase.bytesHex(got));
                    break;
                case "i":
                    System.out.println("i " + t[1] + " " + (TcFlow.identityMove(hex(t[1])) ? 1 : 0));
                    break;
                case "t":  // "t flowtype flowsIntoBody targetInFunction": prints TcRun.tailJump
                    FlowType f = (FlowType) RefType.class.getField(t[1]).get(null);
                    System.out.println("t " + t[1] + " " + t[2] + " " + t[3] + " "
                        + (TcRun.tailJump(f, t[2].equals("1"), t[3].equals("1")) ? 1 : 0));
                    break;
                case "p":  // "p prev calls wordBeforeInBody": prints TcRun.pastEnd
                    System.out.println("p " + t[1] + " " + t[2] + " " + t[3] + " "
                        + (TcRun.pastEnd(Long.parseLong(t[1]), Integer.parseInt(t[2]), t[3].equals("1")) ? 1 : 0));
                    break;
                case "l":  // "l inHelper": prints TcRun.logsLoads
                    System.out.println("l " + t[1] + " " + (TcRun.logsLoads(t[1].equals("1")) ? 1 : 0));
                    break;
                case "n":  // "n full run...": a run is steps, or "L" for a run that hit the limit, or "x" invalid
                    TcRun.StepBudget bud = new TcRun.StepBudget(Integer.parseInt(t[1]));
                    StringBuilder lim = new StringBuilder("n");
                    for (int k = 2; k < t.length; k++) {
                        bud.note(!t[k].equals("L") && !t[k].equals("x"), t[k].equals("L"),
                            t[k].equals("L") || t[k].equals("x") ? 0 : Integer.parseInt(t[k]));
                        lim.append(' ').append(bud.limit);
                    }
                    System.out.println(lim);
                    break;
                default:
                    throw new IllegalArgumentException(line);
            }
        }
    }
}

// Leg B of the instruction vote (tools/x360_vote.py leg-b; targets/xbox360_ppc/tools/vote_leg_b.py): runs every
// instruction test case's words in Ghidra's p-code emulator with the oracle's Xenon corrections (TcFixups: fcti*
// saturation, stfs bits, fused multiply-add, single rounding, NaN payloads) and its vector unit (TcVmx: VMX and VMX128,
// which Ghidra cannot decode), through the oracle's memory bank (TcBank), and prints the registers each case names in
// the same out.txt format as leg A's vote_rt.c, so vote_leg_a.parse_out reads both.
//
// Not a judge script: it sits outside ghidra_scripts/ (tests/test_xbox360_scripts.py compiles those) and is copied
// next to them by vote_leg_b.py for one headless run. The program is vote_code.bin, every case's words at its own
// address, imported raw (BinaryLoader, PowerPC:BE:64:64-32addr, base 0x82000000). Each case starts a fresh emulator:
// r0..r31 and f0..f31 from its R/F lines (others 0), r1 = TcBase.SP only when the case gives no r1, LR = the sentinel,
// CR, XER and CTR 0, vector registers from its V lines; memory from its M lines (already moved into the heap region by
// leg A's rebase). A case ends at the sentinel; a refusal (TcFixups.refuse: FPSCR moves, estimates, ...), a fault, a
// trap or the step limit prints "U <reason>" instead of registers.
//
// usage (analyzeHeadless ... -postScript TcVote.java <cases.txt> <out.txt>)
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.script.GhidraScript;
import ghidra.pcode.memstate.DefaultMemoryState;
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.Instruction;
import java.io.*;
import java.math.BigInteger;
import java.nio.file.*;
import java.util.*;

public class TcVote extends GhidraScript {
    static final int STEP_LIMIT = 100000;

    static BigInteger u64(long v) {
        return new BigInteger(Long.toUnsignedString(v));
    }

    static final class Case {
        int id;
        long at;
        final Map<Integer, Long> r = new HashMap<>(), f = new HashMap<>();
        final Map<Integer, int[]> v = new HashMap<>();
        final List<long[]> memAt = new ArrayList<>();
        final List<byte[]> mem = new ArrayList<>();
        final List<String> outs = new ArrayList<>();
        final List<long[]> q = new ArrayList<>();
    }

    @Override
    protected void run() throws Exception {
        String[] a = getScriptArgs();
        List<String> lines = Files.readAllLines(Paths.get(a[0]));
        TcFacts facts = TcFacts.load("-");
        try (PrintWriter out = new PrintWriter(new BufferedWriter(new FileWriter(a[1])))) {
            Case c = null;
            for (String ln : lines) {
                String[] t = ln.trim().split("\\s+");
                if (t[0].isEmpty()) continue;
                switch (t[0]) {
                    case "C":  // C id address (leg B writes the function's address where leg A has its index)
                        c = new Case();
                        c.id = Integer.parseInt(t[1]);
                        c.at = Long.parseLong(t[2], 16);
                        break;
                    case "R": c.r.put(Integer.parseInt(t[1]), Long.parseUnsignedLong(t[2], 16)); break;
                    case "F": c.f.put(Integer.parseInt(t[1]), Long.parseUnsignedLong(t[2], 16)); break;
                    case "V": {
                        int[] w = new int[4];
                        for (int k = 0; k < 4; k++) w[k] = (int) Long.parseLong(t[2 + k], 16);
                        c.v.put(Integer.parseInt(t[1]), w);
                        break;
                    }
                    case "M": {
                        byte[] b = new byte[t.length > 2 ? t[2].length() / 2 : 0];
                        for (int k = 0; k < b.length; k++) b[k] = (byte) Integer.parseInt(t[2].substring(2 * k, 2 * k + 2), 16);
                        c.memAt.add(new long[] {Long.parseLong(t[1], 16)});
                        c.mem.add(b);
                        break;
                    }
                    case "O": c.outs.add(t[1]); break;
                    case "Q": c.q.add(new long[] {Long.parseLong(t[1], 16), Long.parseLong(t[2])}); break;
                    case "E":
                        out.print(runCase(c, facts));
                        break;
                    default:
                        break;
                }
            }
        }
    }

    Instruction insnAt(Address at) {
        Instruction in = currentProgram.getListing().getInstructionAt(at);
        if (in == null) {
            new DisassembleCommand(at, new AddressSet(at, at.add(3)), false).applyTo(currentProgram, monitor);
            in = currentProgram.getListing().getInstructionAt(at);
        }
        return in;
    }

    String runCase(Case c, TcFacts facts) {
        StringBuilder o = new StringBuilder("C " + c.id + "\n");
        EmulatorHelper emu = new EmulatorHelper(currentProgram);
        TcFixups fix = new TcFixups();
        final String[] fault = {null};
        try {
            fix.install(emu);
            emu.setMemoryFaultHandler(new MemoryFaultHandler() {
                @Override
                public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) {
                    if (TcBank.probing) return false;
                    if (address.isMemoryAddress() && !(TcBase.inRegions(address.getOffset()) && TcBase.inRegions(address.getOffset() + size - 1)) && fault[0] == null) {
                        fault[0] = "read " + address;
                    }
                    for (int i = 0; i < size; i++) buf[bufOffset + i] = 0;
                    return true;
                }

                @Override
                public boolean unknownAddress(Address address, boolean write) {
                    if (fault[0] == null) fault[0] = (write ? "write " : "read ") + address;
                    return false;
                }
            });
            DefaultMemoryState dms = (DefaultMemoryState) emu.getEmulator().getMemState();
            AddressSpace ram = toAddr(c.at).getAddressSpace();
            TcBank bank = new TcBank(dms.getMemoryBank(ram), facts);
            dms.setMemoryBank(bank);
            for (int i = 0; i < 32; i++) {
                emu.writeRegister("r" + i, u64(c.r.getOrDefault(i, 0L)));
                emu.writeRegister("f" + i, u64(c.f.getOrDefault(i, 0L)));
            }
            if (!c.r.containsKey(1)) emu.writeRegister("r1", u64(TcBase.SP));
            for (int k = 0; k < 8; k++) emu.writeRegister("cr" + k, BigInteger.ZERO);
            for (String x : new String[] {"xer_ca", "xer_so", "xer_ov", "CTR"}) emu.writeRegister(x, BigInteger.ZERO);
            emu.writeRegister("LR", u64(TcBase.SENTINEL));
            for (int i = 0; i < c.mem.size(); i++) emu.writeMemory(toAddr(c.memAt.get(i)[0]), c.mem.get(i));
            TcVmx vm = new TcVmx();
            for (Map.Entry<Integer, int[]> e : c.v.entrySet()) vm.v[e.getKey()] = e.getValue().clone();
            emu.writeRegister(emu.getPCRegister(), u64(c.at));
            int steps = 0;
            while (true) {
                long pc = emu.getExecutionAddress().getOffset() & 0xffffffffL;
                if (pc == TcBase.SENTINEL) break;
                if (++steps > STEP_LIMIT) return o.append("U step limit\nE\n").toString();
                byte[] wb = emu.readMemory(toAddr(pc), 4);
                int word = (wb[0] & 0xff) << 24 | (wb[1] & 0xff) << 16 | (wb[2] & 0xff) << 8 | (wb[3] & 0xff);
                TcVmx.Op vop = TcVmx.decode(word);
                if (vop != null) {
                    long[] gpr = new long[32];
                    for (int g = 0; g < 32; g++) gpr[g] = emu.readRegister("r" + g).longValue();
                    bank.beginStep(pc);
                    int cr = vm.exec(vop, gpr, new TcVmx.Mem() {
                        @Override
                        public byte[] read(long ea, int n) {
                            return emu.readMemory(toAddr(ea & 0xffffffffL), n);
                        }

                        @Override
                        public void write(long ea, byte[] data) {
                            emu.writeMemory(toAddr(ea & 0xffffffffL), data);
                        }
                    });
                    if (cr >= 0) emu.writeRegister("cr6", BigInteger.valueOf(cr));
                    emu.writeRegister(emu.getPCRegister(), u64(pc + 4));
                } else {
                    Instruction in = insnAt(toAddr(pc));
                    String why = in == null ? "no instruction at " + Long.toHexString(pc) : TcFixups.refuse(in);
                    if (why != null) return o.append("U unsupported: ").append(why).append("\nE\n").toString();
                    fix.trapped = false;
                    fix.before(emu, in);
                    bank.beginStep(pc);
                    boolean ok = emu.step(monitor);
                    if (ok && fault[0] == null) fix.after(emu, ram, bank.stored, bank.reads);
                    if (fix.trapped) return o.append("U trap taken\nE\n").toString();
                    if (!ok || fault[0] != null) {
                        return o.append("U fault: ").append(fault[0] != null ? fault[0] : emu.getLastError()).append("\nE\n")
                            .toString();
                    }
                }
            }
            long cr = 0;
            for (int k = 0; k < 8; k++) cr |= (emu.readRegister("cr" + k).longValue() & 15) << (28 - 4 * k);
            for (String n : c.outs) {
                if (n.equals("cr")) {
                    o.append(String.format("O cr %08x%n", cr));
                } else if (n.charAt(0) == 'v') {
                    int[] w = vm.v[Integer.parseInt(n.substring(1))];
                    o.append(String.format("O %s %08x %08x %08x %08x%n", n, w[0], w[1], w[2], w[3]));
                } else {
                    o.append(String.format("O %s %016x%n", n, emu.readRegister(n).longValue()));
                }
            }
            for (long[] q : c.q) {
                byte[] b = emu.readMemory(toAddr(q[0]), (int) q[1]);
                o.append(String.format("Q %08x ", q[0]));
                for (byte x : b) o.append(String.format("%02x", x & 0xff));
                o.append('\n');
            }
            return o.append("E\n").toString();
        } catch (Exception e) {
            return o.append("U exception: ").append(e.toString().replace('\n', ' ')).append("\nE\n").toString();
        } finally {
            emu.dispose();
        }
    }
}

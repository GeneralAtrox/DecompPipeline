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
//   Q INSN...            TcFlow's entry CR rule (TcCrFirst.effect, TcFlow.crStep) over one block of these
//                        instructions, p-code in the shapes of Ghidra's PowerPC SLEIGH (cr0flags, setCrBit, getCrBit):
//                        "reads FIELDS; sets FIELDS". INSN: rlwinm. (rlwinm. r10,r7,2,0,29), addic. (addic. r10,r10,-1),
//                        beq (bc 12,2), mfcr (mfcr r3), fcmpu6 (fcmpu cr6,f1,f2), mtcrf80 (mtcrf 0x80,r12: cr0 only),
//                        crxor26 (crxor 4*cr6+eq x3), mcrf60 (mcrf cr6,cr0), stwcx. (stwcx. r3,0,r4)
//   J PC ADDR SIZE      rule R8 for a read of SIZE bytes at ADDR in the step at PC (TcBank.getChunk), the function
//                        82000000..82000050 all code, with its switch table resolved (3 words at 82000020, the shape
//                        of tests/test_x360_table_in_code.py): "data" or "R8 ADDR"; K the same with no table resolved
//   S HEX                TcSwitchHits.isBcctr of an instruction word: true or false
//   H PC CTR...          TcSwitchHits.add of one kept run that executed the bcctr at PC with these CTR values
//   P                    TcSwitchHits.json of every H so far
//   G AT SIZE NUM       TcJumpTables.add of that table for the function 82000000..82000050: the tables kept
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryPageBank;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.address.GenericAddressSpace;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.SequenceNumber;
import ghidra.program.model.pcode.Varnode;
import java.nio.file.*;
import java.util.*;

public class X360TrackStub {
    static final TcSwitchHits SWITCHES = new TcSwitchHits();

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

    // ---------- J, K, G: the switch tables R8 reads as data (TcJumpTables) ----------
    static String tableRead(String[] t, boolean resolved) {
        TcFacts f = TcFacts.defaults();
        f.code.set(0, 0x50);
        f.haveCode = true;
        if (resolved) f.tables.add(0x82000020L, 4, 3, 0x82000000L, 0x82000050L);
        AddressSpace ram = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 0);
        MemoryFaultHandler none = new MemoryFaultHandler() {
            public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) { return false; }
            public boolean unknownAddress(Address address, boolean write) { return false; }
        };
        MemoryPageBank in = new MemoryPageBank(ram, true, 4096, none);
        in.setChunk(0x82000000L, 0x60, new byte[0x60]);
        in.setChunk(0x40001000L, 8, new byte[8]);
        TcBank bank = new TcBank(in, f);
        bank.beginStep(Long.parseLong(t[1], 16));
        int size = Integer.parseInt(t[3]);
        bank.getChunk(Long.parseLong(t[2], 16), size, new byte[size], false);
        return bank.codeRead < 0 ? "data" : "R8 " + Long.toHexString(bank.codeRead);
    }

    static String tableAdd(String[] t) {
        TcJumpTables j = new TcJumpTables();
        j.add(Long.parseLong(t[1], 16), Long.parseLong(t[2]), Long.parseLong(t[3]), 0x82000000L, 0x82000050L);
        return j.toString();
    }

    // ---------- Q: the CR fields a block reads before it sets them ----------
    static final AddressSpace CONST = new GenericAddressSpace("const", 64, AddressSpace.TYPE_CONSTANT, 0);
    static final AddressSpace UNIQ = new GenericAddressSpace("unique", 32, AddressSpace.TYPE_UNIQUE, 1);
    static final AddressSpace REG = new GenericAddressSpace("register", 32, AddressSpace.TYPE_REGISTER, 2);
    static final AddressSpace RAM = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 3);
    static final TreeMap<Long, String> CRS = new TreeMap<>();
    static List<PcodeOp> ops;
    static int uniq;

    static {
        for (int k = 0; k < 8; k++) CRS.put(0x900L + k, "cr" + k);
    }

    static Varnode c(long v, int size) { return new Varnode(CONST.getAddress(v), size); }
    static Varnode u(int size) { return new Varnode(UNIQ.getAddress(0x100L * ++uniq), size); }
    static Varnode cr(int k) { return new Varnode(REG.getAddress(0x900L + k), 1); }
    static Varnode gpr(int n) { return new Varnode(REG.getAddress(8L * n), 8); }
    static Varnode fpr(int n) { return new Varnode(REG.getAddress(0x1000L + 16L * n), 8); }
    static final Varnode XER_SO = new Varnode(REG.getAddress(0x400L), 1), RESERVE = new Varnode(REG.getAddress(0x480L), 1);

    static Varnode op(int code, Varnode out, Varnode... in) {
        ops.add(new PcodeOp(new SequenceNumber(RAM.getAddress(0x82a800e8L), ops.size()), code, in, out));
        return out;
    }

    static Varnode getCrBit(int k, int bit) {   // tmp:1 = crReg >> (3-bitIndex); result = tmp & 1
        Varnode sh = op(PcodeOp.INT_SUB, u(1), c(3, 1), c(bit, 1));
        return op(PcodeOp.INT_AND, u(1), op(PcodeOp.INT_RIGHT, u(1), cr(k), sh), c(1, 1));
    }

    static void setCrBit(int k, int bit, Varnode val) {   // crReg = crReg & ~(1 << shift); crReg = crReg | val << shift
        Varnode sh = op(PcodeOp.INT_SUB, u(1), c(3, 1), c(bit, 1));
        Varnode mask = op(PcodeOp.INT_NEGATE, u(1), op(PcodeOp.INT_LEFT, u(1), c(1, 1), sh));
        Varnode t = op(PcodeOp.INT_LEFT, u(1), val, sh);
        op(PcodeOp.INT_AND, cr(k), cr(k), mask);
        op(PcodeOp.INT_OR, cr(k), cr(k), t);
    }

    static void cr0flags(Varnode res) {
        setCrBit(0, 0, op(PcodeOp.INT_SLESS, u(1), res, c(0, 8)));
        setCrBit(0, 1, op(PcodeOp.INT_SLESS, u(1), c(0, 8), res));
        setCrBit(0, 2, op(PcodeOp.INT_EQUAL, u(1), res, c(0, 8)));
        setCrBit(0, 3, op(PcodeOp.INT_AND, u(1), XER_SO, c(1, 1)));
    }

    static void insn(String m) {
        switch (m) {
            case "rlwinm.": cr0flags(op(PcodeOp.INT_AND, gpr(10), op(PcodeOp.INT_LEFT, u(8), gpr(7), c(2, 4)), c(0xfffffffcL, 8))); break;
            case "addic.": cr0flags(op(PcodeOp.INT_SUB, gpr(10), gpr(10), c(1, 8))); break;
            case "beq": op(PcodeOp.CBRANCH, null, new Varnode(RAM.getAddress(0x82a8010cL), 4), getCrBit(0, 2)); break;
            case "mfcr": {
                Varnode acc = c(0, 4);
                for (int k = 0; k < 8; k++) {
                    Varnode z = op(PcodeOp.INT_ZEXT, u(4), op(PcodeOp.INT_AND, u(1), cr(k), c(0xf, 1)));
                    acc = op(PcodeOp.INT_OR, u(4), acc, op(PcodeOp.INT_LEFT, u(4), z, c(28 - 4 * k, 4)));
                }
                op(PcodeOp.INT_ZEXT, gpr(3), acc);
                break;
            }
            case "fcmpu6": {
                Varnode lt = op(PcodeOp.FLOAT_LESS, u(1), fpr(1), fpr(2)), eq = op(PcodeOp.FLOAT_EQUAL, u(1), fpr(1), fpr(2));
                op(PcodeOp.INT_OR, cr(6), op(PcodeOp.INT_LEFT, u(1), lt, c(3, 1)), op(PcodeOp.INT_LEFT, u(1), eq, c(1, 1)));
                break;
            }
            case "mtcrf80":   // crK = (crK * (CRMK == 0)) | (tmp:1 * (CRMK == 1)), CRM = 0x80
                for (int k = 0; k < 8; k++) {
                    Varnode t = op(PcodeOp.INT_AND, u(8), op(PcodeOp.INT_RIGHT, u(8), gpr(12), c(28 - 4 * k, 4)), c(0xf, 8));
                    Varnode t1 = op(PcodeOp.SUBPIECE, u(1), t, c(0, 4)), crm = c(k == 0 ? 1 : 0, 1);
                    Varnode keep = op(PcodeOp.INT_MULT, u(1), cr(k), op(PcodeOp.INT_EQUAL, u(1), crm, c(0, 1)));
                    Varnode put = op(PcodeOp.INT_MULT, u(1), t1, op(PcodeOp.INT_EQUAL, u(1), crm, c(1, 1)));
                    op(PcodeOp.INT_OR, cr(k), keep, put);
                }
                break;
            case "crxor26": setCrBit(6, 2, op(PcodeOp.INT_XOR, u(1), getCrBit(6, 2), getCrBit(6, 2))); break;
            case "mcrf60": op(PcodeOp.COPY, cr(6), cr(0)); break;
            case "stwcx.": {   // if (RESERVE == 0) goto inst_next; *EA = ..; cr0flags(0)
                Varnode no = op(PcodeOp.INT_EQUAL, u(1), RESERVE, c(0, 1));
                op(PcodeOp.CBRANCH, null, new Varnode(RAM.getAddress(0x82a800ecL), 4), no);
                op(PcodeOp.STORE, null, c(0x1a0, 8), gpr(4), gpr(3));
                cr0flags(c(0, 8));
                break;
            }
            default: throw new IllegalArgumentException(m);
        }
    }

    static String crFirst(String[] t) {
        TcFlow.Blk b = new TcFlow.Blk();
        for (int i = 1; i < t.length; i++) {
            ops = new ArrayList<>();
            insn(t[i]);
            TcFlow.crStep(b, TcCrFirst.effect(ops, CRS, 1));
        }
        return "reads " + String.join(",", new TreeSet<>(b.use)) + "; sets " + String.join(",", new TreeSet<>(b.def));
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
                case "Q":
                    out = crFirst(t);
                    break;
                case "J":
                case "K":
                    out = tableRead(t, t[0].equals("J"));
                    break;
                case "G":
                    out = tableAdd(t);
                    break;
                case "S":
                    out = String.valueOf(TcSwitchHits.isBcctr(Long.parseLong(t[1], 16)));
                    break;
                case "H": {
                    List<long[]> run = new ArrayList<>();
                    for (int i = 2; i < t.length; i++) run.add(new long[] {Long.parseLong(t[1], 16), Long.parseLong(t[i], 16)});
                    SWITCHES.add(run);
                    break;
                }
                case "P":
                    out = SWITCHES.json();
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

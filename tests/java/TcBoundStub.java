// tests/test_targeted_vectors.py's view of TcBound without a program: its harvest on hand-built p-code and its draws.
//
// usage: java TcBoundStub CASE   CASE lines (numbers in hex):
//   "O OPCODE out in..."  one p-code op (a mnemonic); a varnode is "c:value:size" (constant), "r:offset:size"
//                         (register), "u:offset:size" (unique) or "-" (no output)
//   "M addr size value"   the program image holds value there (a literal pool)
//   "X c" / "Y bits"      an extra integer constant / f32 constant (the target's pattern harvest)
//   "S seed n"            n draws of each kind with Random(seed)
// prints "I" ints, "F" floats, "D" doubles, "C" fcmp, then per S: "V1", "V2", "V4" intValue draws at
// 1, 2 and 4 bytes, "G" floatBits(f32), "H" floatBits(f64)
import ghidra.program.model.address.*;
import ghidra.program.model.pcode.*;
import java.util.*;

public class TcBoundStub {
    static final AddressSpace CONST = new GenericAddressSpace("const", 64, AddressSpace.TYPE_CONSTANT, 0);
    static final AddressSpace REG = new GenericAddressSpace("register", 32, AddressSpace.TYPE_REGISTER, 1);
    static final AddressSpace UNIQ = new GenericAddressSpace("unique", 32, AddressSpace.TYPE_UNIQUE, 2);
    static final AddressSpace RAM = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 3);

    static long hex(String s) {
        return Long.parseUnsignedLong(s, 16);
    }

    static Varnode vn(String s) {
        if (s.equals("-")) return null;
        String[] p = s.split(":");
        AddressSpace sp = p[0].equals("c") ? CONST : p[0].equals("r") ? REG : UNIQ;
        return new Varnode(sp.getAddress(hex(p[1])), Integer.parseInt(p[2]));
    }

    static String join(Collection<Long> xs) {
        StringBuilder b = new StringBuilder();
        for (long x : xs) b.append(' ').append(Long.toHexString(x));
        return b.toString();
    }

    public static void main(String[] args) throws Exception {
        List<String> lines = java.nio.file.Files.readAllLines(java.nio.file.Path.of(args[0]));
        List<PcodeOp> ops = new ArrayList<>();
        Map<String, Long> image = new HashMap<>();
        List<Long> xi = new ArrayList<>(), xf = new ArrayList<>();
        int seq = 0;
        for (String line : lines) {
            String[] t = line.trim().split("\\s+");
            if (t[0].equals("O")) {
                Varnode[] in = new Varnode[t.length - 3];
                for (int i = 0; i < in.length; i++) in[i] = vn(t[3 + i]);
                ops.add(new PcodeOp(RAM.getAddress(0x1000 + seq), seq++, PcodeOp.getOpcode(t[1]), in, vn(t[2])));
            } else if (t[0].equals("M")) {
                image.put(hex(t[1]) + ":" + Integer.parseInt(t[2]), hex(t[3]));
            } else if (t[0].equals("X")) {
                xi.add(hex(t[1]));
            } else if (t[0].equals("Y")) {
                xf.add(hex(t[1]));
            }
        }
        TcBound b = TcBound.of(ops, (a, n) -> image.get(a + ":" + n), xi, xf);
        StringBuilder out = new StringBuilder();
        out.append("I").append(join(b.ints)).append("\nF").append(join(b.floats)).append("\nD").append(join(b.doubles))
            .append("\nC ").append(b.fcmp).append('\n');
        for (String line : lines) {
            String[] t = line.trim().split("\\s+");
            if (!t[0].equals("S")) continue;
            int n = Integer.parseInt(t[2]);
            for (int size : new int[] {1, 2, 4}) {
                Random h = new Random(hex(t[1]));
                List<Long> v = new ArrayList<>();
                for (int i = 0; i < n; i++) v.add(b.intValue(h, size));
                out.append("V").append(size).append(join(v)).append('\n');
            }
            for (boolean dbl : new boolean[] {false, true}) {
                Random h = new Random(hex(t[1]));
                List<Long> v = new ArrayList<>();
                for (int i = 0; i < n; i++) v.add(b.floatBits(h, dbl));
                out.append(dbl ? "H" : "G").append(join(v)).append('\n');
            }
        }
        System.out.print(out);
    }
}

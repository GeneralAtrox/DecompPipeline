// The oracle's fmac (tests/test_fmac.py), as TcFpu computes it under FPSCR.DN=1: denormal sources counted as signed
// zeros (TcFpu.before), an exact value just below the smallest normal rejected (TcRound.nearMinNormal), the result
// rounded once in the given mode (TcRound.apply with once: TcRound.fma), a denormal result flushed (TcFpu.after).
//
// usage: java TcFmacStub CASES RM   CASES: lines "A B C" (fr0, frm, frn bits in hex); prints one line per case: the
//                                   result bits in hex, "N" for a NaN, "B" for a rejected vector
public class TcFmacStub {
    static long dn(long b) {
        return (b & 0x7f800000L) == 0 ? b & 0x80000000L : b;
    }

    public static void main(String[] args) throws Exception {
        int rm = Integer.parseInt(args[1]);
        StringBuilder out = new StringBuilder();
        for (String line : java.nio.file.Files.readAllLines(java.nio.file.Path.of(args[0]))) {
            String[] t = line.trim().split("\\s+");
            if (t.length < 3) continue;
            java.util.Map<String, Long> regs = new java.util.HashMap<>();
            regs.put("fr0", dn(Long.parseLong(t[0], 16)));
            regs.put("fr1", dn(Long.parseLong(t[1], 16)));
            regs.put("fr2", dn(Long.parseLong(t[2], 16)));
            TcFpu.Op op = new TcFpu.Op();
            op.kind = "fmac";
            op.a = new String[] {"fr0", "fr1", "fr2"};
            op.m = 1;
            if (TcRound.nearMinNormal(TcRound.f(regs, "fr0"), TcRound.f(regs, "fr1"), TcRound.f(regs, "fr2"))) {
                out.append("B\n");
                continue;
            }
            TcRound.apply(op, regs, rm, false, true);
            long r = regs.get("fr2");
            out.append(TcRound.isNaN("fr2", r) ? "N" : Long.toHexString(dn(r))).append('\n');
        }
        System.out.print(out);
    }
}

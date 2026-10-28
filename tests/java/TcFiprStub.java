// The oracle's fipr and ftrv (tests/test_fipr.py), as TcFpu computes them under FPSCR.DN=1: denormal sources counted
// as signed zeros (TcFpu.before), a double sum just below the smallest normal rejected (TcFpu.intermediate:
// TcRound.nearMinNormal), the result rounded once in the given mode (TcRound.apply with once), a denormal result
// flushed (TcFpu.after). ONCE=0 prints the p-code's seven roundings instead (the C the judge must tell apart).
//
// usage: java TcFiprStub CASES RM ONCE   CASES: lines of 8 hex words "a0 a1 a2 a3 b0 b1 b2 b3" (fv0, fv4); prints one
//                                        line per case: fipr fv0,fv4's result bits, "N" for a NaN, "B" for a rejected
//                                        vector; then, as ftrv xmtrx,fv4 with XMTRX row 0 = a, row 1 = -a, rows 2, 3 = 0,
//                                        its four results (the same tokens)
public class TcFiprStub {
    static long dn(long b) {
        return (b & 0x7f800000L) == 0 ? b & 0x80000000L : b;
    }

    static String show(java.util.Map<String, Long> regs, String r) {
        long b = regs.get(r);
        return TcRound.isNaN(r, b) ? "N" : Long.toHexString(dn(b));
    }

    public static void main(String[] args) throws Exception {
        int rm = Integer.parseInt(args[1]);
        boolean once = !args[2].equals("0");
        StringBuilder out = new StringBuilder();
        for (String line : java.nio.file.Files.readAllLines(java.nio.file.Path.of(args[0]))) {
            String[] t = line.trim().split("\\s+");
            if (t.length < 8) continue;
            java.util.Map<String, Long> regs = new java.util.HashMap<>();
            float[] a = new float[4], b = new float[4];
            for (int i = 0; i < 4; i++) {
                regs.put("fr" + i, dn(Long.parseLong(t[i], 16)));
                regs.put("fr" + (4 + i), dn(Long.parseLong(t[4 + i], 16)));
                a[i] = TcRound.f(regs, "fr" + i);
                b[i] = TcRound.f(regs, "fr" + (4 + i));
            }
            TcFpu.Op op = new TcFpu.Op();
            op.kind = "fipr";
            op.a = new String[] {"fv0", "fv4"};
            op.m = 0;
            op.n = 4;
            if (TcRound.nearMinNormal(TcRound.dot(a, b, rm))) out.append("B");
            else {
                java.util.Map<String, Long> r = new java.util.HashMap<>(regs);
                TcRound.apply(op, r, rm, false, once);
                out.append(show(r, "fr7"));
            }
            for (int row = 0; row < 4; row++) {
                for (int k = 0; k < 4; k++) {
                    float x = row == 0 ? a[k] : row == 1 ? -a[k] : 0f;
                    regs.put("xf" + (row + 4 * k), TcRound.fb(x));
                }
            }
            op.kind = "ftrv";
            op.a = new String[] {"xmtrx", "fv4"};
            boolean near = TcRound.nearMinNormal(TcRound.dot(a, b, rm));
            TcRound.apply(op, regs, rm, false, once);
            for (int k = 0; k < 4; k++) out.append(' ').append(near && k < 2 ? "B" : show(regs, "fr" + (4 + k)));
            out.append('\n');
        }
        System.out.print(out);
    }
}

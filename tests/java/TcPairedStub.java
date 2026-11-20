// Prints TcPaired's results on fixed cases as "case <name> <hex>" (tests/test_gc_paired.py checks them).
public class TcPairedStub {
    static long d(double x) {
        return Double.doubleToRawLongBits(x);
    }

    static void out(String name, long v) {
        System.out.println("case " + name + " " + String.format("%016x", v));
    }

    static void ps(String name, String op, long[]... s) {
        long[] r = TcPaired.result(op, s);
        out(name + "_0", r[0]);
        out(name + "_1", r[1]);
    }

    public static void main(String[] args) {
        // The store-single narrowing psq_st and stfs use, the value written into the hardware-register log too (Skies
        // 8019dba8's GX FIFO words): a NaN keeps its sign and top 23 fraction bits and is not quieted.
        out("narrow_qnan", TcFixups.toSingle(0x7ff8123456789abcL));
        out("narrow_snan", TcFixups.toSingle(0x7ff0000020000000L));
        out("narrow_neg_qnan", TcFixups.toSingle(0xfffc000000000000L));
        out("narrow_inf", TcFixups.toSingle(0x7ff0000000000000L));
        out("dq_u8_ff", TcPaired.dequant(4, 0, 0xff));
        out("dq_s8_ff", TcPaired.dequant(6, 0, 0xff));
        out("dq_u16_8000_sc8", TcPaired.dequant(5, 8, 0x8000));
        out("dq_s16_3_scm1", TcPaired.dequant(7, 0x3f, 3));
        out("dq_s16_8000", TcPaired.dequant(7, 0, 0x8000));
        out("q_u8_300", TcPaired.quant(4, 0, d(300.0)));
        out("q_u8_neg", TcPaired.quant(4, 0, d(-5.0)));
        out("q_u8_nan", TcPaired.quant(4, 0, 0x7ff8000000000000L));
        out("q_u8_single_first", TcPaired.quant(4, 0, d(0.99999999999)));
        out("q_s8_m200", TcPaired.quant(6, 0, d(-200.0)));
        out("q_s16_sc8", TcPaired.quant(7, 8, d(1.5)));
        out("q_u16_scm1", TcPaired.quant(5, 0x3f, d(7.0)));
        out("q_s16_trunc", TcPaired.quant(7, 0, d(-2.7)));
        ps("add_rounds", "ps_add", new long[] {d(1.0), d(1.0)}, new long[] {d(Math.scalb(1.0, -30)), d(Math.scalb(1.0, -23))});
        ps("add_subnormal", "ps_add", new long[] {d(1.5 * Math.scalb(1.0, -150)), 0}, new long[] {0, 0});
        ps("mul_nan", "ps_mul", new long[] {0x7ff8123456789abcL, 0x7ff0000000000000L}, new long[] {d(1.0), 0});
        ps("nmadd", "ps_nmadd", new long[] {0, d(1.0)}, new long[] {0x7ff0000000000000L, d(2.0)}, new long[] {d(1.0), d(1.0)});
        ps("neg_snan", "ps_neg", new long[] {0x7ff0000000000001L, d(1.0)});
        ps("merge10", "ps_merge10", new long[] {d(1.0), d(2.0)}, new long[] {d(3.0), d(4.0)});
        ps("sum1", "ps_sum1", new long[] {d(1.0), d(2.0)}, new long[] {d(7.0), d(6.0)}, new long[] {d(3.0), d(4.0)});
        ps("madds1", "ps_madds1", new long[] {d(2.0), d(3.0)}, new long[] {d(10.0), d(0.5)}, new long[] {d(1.0), d(1.0)});
        ps("sel", "ps_sel", new long[] {d(-0.0), d(-1.0)}, new long[] {d(1.0), d(1.0)}, new long[] {d(2.0), d(2.0)});
        ps("div", "ps_div", new long[] {0, d(1.0)}, new long[] {0, d(4.0)});
        ps("res", "ps_res", new long[] {d(3.0), 0});
        ps("rsqrte", "ps_rsqrte", new long[] {d(3.0), 0x7ff0000000000000L});
    }
}

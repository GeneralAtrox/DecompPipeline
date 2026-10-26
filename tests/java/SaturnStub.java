// tests/test_saturn_sh2.py: the Saturn target's address and byte-order helpers (TcBase.fold, canon, inRam, getBE,
// putBE, TcInputs.genPut, TcCatalog.isHw), compiled against targets/saturn_sh2 with no Ghidra application.
// Prints one "name value" line per case.
public class SaturnStub {
    static void p(String name, long v) {
        System.out.println(name + " " + Long.toHexString(v));
    }

    public static void main(String[] args) throws Exception {
        if (args.length > 0) {  // a facts file core/ghidra.facts_body wrote from target.json: TcFacts.verify agrees
            TcFacts f = TcFacts.load(args[0]);
            p("facts_ram", f.ramLo);
            p("facts_sp", f.entrySp);
        }
        long[] fold = {0x26001234L, 0x060ffffcL, 0x2610_0000L, 0x20200010L, 0x002fffffL, 0x25e00000L, 0x05e00000L};
        for (long v : fold) p("fold_" + Long.toHexString(v), TcBase.fold(v));
        long[] canon = {0x26001234L, 0x25e00000L, 0x40000000L, 0xfffffe10L};
        for (long v : canon) p("canon_" + Long.toHexString(v), TcBase.canon(v));
        p("inRam_high", TcBase.inRam(0x060ffffcL, 4) ? 1 : 0);
        p("inRam_high_end", TcBase.inRam(0x060ffffeL, 4) ? 1 : 0);
        p("inRam_low", TcBase.inRam(0x00200000L, 4) ? 1 : 0);
        p("inRam_gap", TcBase.inRam(0x00300000L, 1) ? 1 : 0);
        p("inRam_mirror", TcBase.inRam(0x26000000L, 4) ? 1 : 0);
        byte[] b = {0x12, 0x34, 0x56, 0x78};
        p("getBE4", TcBase.getBE(b, 0, 4));
        p("getBE2", TcBase.getBE(b, 2, 2));
        byte[] w = new byte[4];
        TcBase.putBE(w, 0, 0xcafef00dL, 4);
        p("putBE", TcBase.getBE(w, 0, 4));
        byte[] g = new byte[6];
        TcInputs.genPut(g, 1, 0xa1b2c3d4L, 4);
        TcInputs.genPut(g, 0, 0xffeeL, 1);
        p("genPut", TcBase.getBE(g, 0, 4));
        p("genPut2", TcBase.getBE(g, 4, 2));
        long[] hw = {0x25e00000L, 0x05f80000L, 0x060a0000L, 0x00200000L, 0x00100001L, 0xfffffe92L, 0x3f800000L};
        for (long v : hw) p("hw_" + Long.toHexString(v), TcCatalog.isHw(v) ? 1 : 0);
        p("sq", TcCatalog.isSq(0xe0000000L) ? 1 : 0);
        p("mac", TcCatalog.SYS.contains("mac.l") && TcCatalog.SYS.contains("mac.w") ? 1 : 0);
        p("heap", TcInputs.GEN_HEAP_LO >= TcBase.SCR + TcBase.SCR_SIZE && TcInputs.GEN_HEAP_HI <= TcBase.STACK_LO ? 1 : 0);
    }
}

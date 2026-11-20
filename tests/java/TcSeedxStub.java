// The op-aware extra vectors' classes and values (TcSeedx) and the cases TcVmx reports (tests/test_x360_seedx.py).
// No Ghidra imports. usage: java TcSeedxStub   prints one line per check, "ok NAME" or "WRONG NAME got ...".
import java.util.*;

public class TcSeedxStub {
    static int bad = 0;

    static void check(String name, Object got, Object want) {
        boolean same = got instanceof int[] ? Arrays.equals((int[]) got, (int[]) want) : Objects.equals(got, want);
        System.out.println(same ? "ok " + name : "WRONG " + name + " got " + (got instanceof int[]
            ? Arrays.toString((int[]) got) : String.valueOf(got)));
        if (!same) bad++;
    }

    static int vx(int vd, int va, int vb, int xo) {
        return (4 << 26) | (vd << 21) | (va << 16) | (vb << 11) | xo;
    }

    static int va(int vd, int a, int b, int c, int xo) {
        return (4 << 26) | (vd << 21) | (a << 16) | (b << 11) | (c << 6) | xo;
    }

    // Run one op on fresh registers; the event it reports.
    static String run(int word, String mn, int[] a, int[] b, int[] c) {
        TcVmx x = new TcVmx();
        TcVmx.Op o = TcVmx.decode(word);
        if (!o.mn.equals(mn)) throw new IllegalStateException("decoded " + o.mn + ", want " + mn);
        if (a != null) x.v[o.va] = a.clone();
        if (b != null) x.v[o.vb] = b.clone();
        if (c != null && o.vc != TcVmx.NONE) x.v[o.vc] = c.clone();
        x.exec(o, new long[32], null);
        return x.event;
    }

    static int[] four(long[] w, int at) {
        return new int[] {(int) w[at], (int) w[at + 1], (int) w[at + 2], (int) w[at + 3]};
    }

    // 82a1d008's first 32 words (82a1d008..82a1d084): its gate is the rlwinm at 82a1d070, word 26
    static final int[] W82A1D008 = {
        0x7d8802a6, 0x9181fff8, 0xfbe1fff0, 0x9421ff80, 0x8163001c, 0x81430018, 0x81230008, 0x5566103a,
        0x8903000d, 0x7cab5050, 0x80830004, 0x7d4849d6, 0x81630000, 0x80e30014, 0x7cbf0e70, 0x5548103a,
        0x7d492050, 0x7d3f0194, 0x7d685a14, 0x7d063a14, 0x7f0a4800, 0x40980008, 0x7d495378, 0x552a07be,
        0x7d474378, 0x7ce65b78, 0x54c4073e, 0x2f040000, 0x419a001c, 0x4bffb565, 0x38210080, 0x8181fff8};

    static void put4(byte[] m, int at, long x) {
        for (int k = 0; k < 4; k++) m[at + k] = (byte) (x >>> (24 - 8 * k));
    }

    static long get4(byte[] m, int at) {
        long x = 0;
        for (int k = 0; k < 4; k++) x = (x << 8) | (m[at + k] & 0xff);
        return x;
    }

    // 82a1d008's entry (82a1d018..82a1d078), its loop bounds and the trap at 82a1d200, with r3 at scratch offset s:
    // "exit" (the gate's call), "noloop" (r9 <= 0), "trap" (byte +0xd is 0), "out" (the loop leaves the 1 MB heap or
    // runs over 0x4000 times), "loop" (the vector loop runs: the planted vmrghw is reached).
    static String entry82a1d008(byte[] m, int s) {
        long A = get4(m, s + 0x1c), B = get4(m, s + 0x18), C = get4(m, s + 8), E = get4(m, s + 4), F = get4(m, s),
            G = get4(m, s + 0x14), D = m[s + 0xd] & 0xff;
        int r5 = (int) (B - A), r9 = r5 / 2, r10 = (int) (E - C);
        if (r10 < r9) r9 = r10;
        long r11 = (F + 4 * ((D * C) & 0xffffffffL)) & 0xffffffffL, r8 = (4 * A + G) & 0xffffffffL;
        if ((((long) (r9 & 3)) | r11 | r8) % 16 != 0) return "exit";
        if (r9 <= 0) return "noloop";
        long n = ((r9 - 1) >> 2) + 1;
        if (n > 0x4000 || r11 < 0x40000000L || r11 + 16 * n > 0x40100000L || r8 < 0x40000000L || r8 + 32 * n > 0x40100000L)
            return "out";
        return D == 0 ? "trap" : "loop";
    }

    public static void main(String[] args) {
        // classes per function, by its vector op mnemonics
        check("classes_82cbc588", TcSeedx.classes(Arrays.asList("lvx", "vaddshs", "stvx")),
            new int[] {TcSeedx.SAT | TcSeedx.ALIGN, TcSeedx.W_HALF});
        check("classes_vmaddfp", TcSeedx.classes(Arrays.asList("vmaddfp", "vspltw"))[0], TcSeedx.TINY);
        check("classes_128_fma", TcSeedx.classes(Arrays.asList("vmaddcfp128", "vmsum4fp128"))[0], TcSeedx.TINY);
        check("classes_vmrghw", TcSeedx.classes(Arrays.asList("lvx128", "vmrghw", "stvx128"))[0], TcSeedx.ALIGN);
        check("classes_none", TcSeedx.classes(Arrays.asList("vperm", "lvsl", "vxor", "vspltisw"))[0], 0);
        check("widths_pack_hb", TcSeedx.widths("vpkshus"), TcSeedx.W_PK_HB);
        check("widths_pack_wh_128", TcSeedx.widths("vpkswss128"), TcSeedx.W_PK_WH);
        check("widths_bytes", TcSeedx.widths("vaddubs"), TcSeedx.W_BYTE);
        check("widths_words", TcSeedx.widths("vsubsws"), TcSeedx.W_WORD);
        check("widths_ctsxs", TcSeedx.widths("vctsxs"), TcSeedx.W_F2I);
        check("widths_modulo", TcSeedx.widths("vadduhm"), 0);
        check("focus_order", TcSeedx.focus(TcSeedx.SAT | TcSeedx.TINY | TcSeedx.ALIGN),
            new int[] {TcSeedx.SAT, TcSeedx.TINY, TcSeedx.ALIGN});
        check("names", TcSeedx.names(TcSeedx.SAT | TcSeedx.ALIGN), "[\"sat\",\"align\"]");

        // determinism: the same generator gives the same words
        Random r1 = new Random(7), r2 = new Random(7);
        boolean same = true;
        for (int i = 0; i < 100; i++) same &= TcSeedx.satWord(r1, TcSeedx.W_HALF) == TcSeedx.satWord(r2, TcSeedx.W_HALF);
        check("deterministic", same, true);

        // SAT: halfword extremes make vaddshs saturate (82cbc588); small values do not
        Random r = new Random(1);
        long[] w = new long[400];
        int ext = 0;
        for (int i = 0; i < w.length; i++) {
            w[i] = TcSeedx.satWord(r, TcSeedx.W_HALF);
            for (int h : new int[] {(int) (w[i] >>> 16), (int) (w[i] & 0xffff)}) if (h >= 0x7ff0 && h <= 0x800f) ext++;
        }
        check("half_extremes_share", ext * 100 / (2 * w.length) >= 70, true);
        int vaddshs = vx(1, 2, 3, 0x340), sats = 0;
        for (int i = 0; i + 8 <= w.length; i += 8) if ("sat".equals(run(vaddshs, "vaddshs", four(w, i), four(w, i + 4), null))) sats++;
        check("vaddshs_saturates", sats >= 25, true);
        check("vaddshs_small_no_case", run(vaddshs, "vaddshs", new int[] {1, 2, 3, 4}, new int[] {5, 6, 7, 8}, null), null);
        check("byte_words", (TcSeedx.satWord(new Random(3), TcSeedx.W_BYTE) >>> 32) == 0, true);

        // TINY: floats near 2^-64 give denormal products in vmaddfp (82de7510, 82de7878)
        Random t = new Random(2);
        int near = 0, den = 0, n = 400;
        for (int i = 0; i < n; i++) {
            int f = TcSeedx.tinyFloat(t), e = (f >>> 23) & 0xff;
            if (e >= 53 && e <= 64) near++;
        }
        check("tiny_near_2^-68_share", near * 100 / n >= 35, true);
        int vmaddfp = va(1, 2, 3, 4, 46);
        for (int i = 0; i < 100; i++) {
            int[] a = new int[4], c = new int[4];
            for (int k = 0; k < 4; k++) {
                a[k] = TcSeedx.tinyFloat(t);
                c[k] = TcSeedx.tinyFloat(t);
            }
            String ev = run(vmaddfp, "vmaddfp", a, new int[4], c);
            if (ev != null && ev.contains("den_out")) den++;
        }
        check("vmaddfp_denormal_results", den >= 20, true);
        int one = 0x3f800000;
        check("vmaddfp_normal_no_case", run(vmaddfp, "vmaddfp", new int[] {one, one, one, one}, new int[4],
            new int[] {one, one, one, one}), null);
        check("vmaddfp_den_in", run(vmaddfp, "vmaddfp", new int[] {1, one, one, one}, new int[4],
            new int[] {one, one, one, one}), "den_in");
        check("vmaddfp_2^-64_squared", run(vmaddfp, "vmaddfp", new int[] {0x1f800000, 0, 0, 0}, new int[4],
            new int[] {0x1f800000, 0, 0, 0}), "den_out");
        // an exact result below 2^-149 rounds to a zero: no den_out (82de7510 vectors 98 and 106 of the base run)
        check("vmaddfp_below_denormal_rounds_to_zero", run(vmaddfp, "vmaddfp", new int[] {0xa9531d6b, 0, 0, 0},
            new int[] {0x00240d1e, 0, 0, 0}, new int[] {0x00800000, 0, 0, 0}), "den_in");
        check("vmaddfp_below_denormal_no_case", run(vmaddfp, "vmaddfp", new int[] {0, 0x1fc52fbc, 0, 0}, new int[4],
            new int[] {0, 0x012b94fd, 0, 0}), null);
        check("vmaddfp_smallest_denormal_case", run(vmaddfp, "vmaddfp", new int[] {0x1a800000, 0, 0, 0}, new int[4],
            new int[] {0x1a800000, 0, 0, 0}), "den_out");
        check("vaddfp_den_in", run(vx(1, 2, 3, 10), "vaddfp", new int[] {0x80000001, 0, 0, 0}, new int[4], null), "den_in");
        check("vcsxwfp_int_input_not_den", TcSeedx.floatIn("vcsxwfp128"), false);

        // ALIGN: pointers 16-aligned, counts multiples of 4 (82a1d008)
        check("align", TcSeedx.align(0x40000124L), 0x40000120L);
        Random q = new Random(4);
        boolean c4 = true;
        for (int i = 0; i < 100; i++) {
            long k = TcSeedx.count4(q);
            c4 &= k % 4 == 0 && k >= 4 && k <= 32;
        }
        check("count4", c4, true);

        // ALIGN byte fields (82a1d008: the byte at +0xd is a multiplier, and the function traps when it is 0)
        boolean b4 = true;
        int fields = 0, ptrs = 0;
        for (int i = 0; i < 200; i++) {
            long x = TcSeedx.alignWord(q, 0x40000120L);
            b4 &= x % 4 == 0 && (x >>> 32) == 0;
            if (x == 0x40000120L) ptrs++;
            else if (((x >>> 16) & 0xff) != 0) fields++;
        }
        check("align_ptr_share", ptrs >= 70 && ptrs <= 130, true);
        check("align_words_multiple_of_4", b4, true);
        check("align_words_byte1_nonzero_share", fields >= 30 && fields <= 70, true);
        long bw = TcSeedx.bytes4(new Random(5));
        boolean each = true;
        for (int i = 0; i < 4; i++) {
            long b = (bw >>> (8 * i)) & 0xff;
            each &= b == 4 || b == 8 || b == 12 || b == 16;
        }
        check("bytes4_fields", each, true);

        // which extra vectors are kept: a novel one always, a plain one only while under half the quota
        Set<Integer> seen = new TreeSet<>(Arrays.asList(1, 2, 3));
        Set<String> oldC = new TreeSet<>(Arrays.asList("82cbc5e4:sat")), seedC = new TreeSet<>(Arrays.asList("82de753c:den_out"));
        Set<String> none = new TreeSet<>();
        check("novel_block", TcSeedx.novel(new TreeSet<>(Arrays.asList(2, 9)), none, seen, oldC, seedC), true);
        check("novel_case", TcSeedx.novel(new TreeSet<>(Arrays.asList(1)), new TreeSet<>(Arrays.asList("82de752c:den_out")),
            seen, oldC, seedC), true);
        check("not_novel", TcSeedx.novel(new TreeSet<>(Arrays.asList(1, 3)),
            new TreeSet<>(Arrays.asList("82cbc5e4:sat", "82de753c:den_out")), seen, oldC, seedC), false);
        check("keep_plain_under_half", TcSeedx.keepExtra(false, 7, 16), true);
        check("skip_plain_at_half", TcSeedx.keepExtra(false, 8, 16), false);
        check("keep_novel_always", TcSeedx.keepExtra(true, 8, 16), true);


        // GATE (82a1d008): the alignment gate and the argument fields it reads, from the function's own words
        check("gate_at_82a1d008", TcSeedx.gateAt(W82A1D008), 26);
        int[][] gf = TcSeedx.gateFields(W82A1D008);
        StringBuilder fs = new StringBuilder();
        for (int[] f : gf) fs.append(String.format("r%d+%x:%d ", f[0], f[1], f[2]));
        check("gate_fields_82a1d008", fs.toString().trim(), "r3+1c:4 r3+18:4 r3+8:4 r3+d:1 r3+4:4 r3+0:4 r3+14:4");
        check("gate_replaces_align", TcSeedx.withGate(TcSeedx.TINY | TcSeedx.ALIGN, gf), TcSeedx.TINY | TcSeedx.GATE);
        check("gate_needs_align", TcSeedx.withGate(TcSeedx.TINY, gf), TcSeedx.TINY);
        check("no_gate_no_fields", TcSeedx.gateFields(new int[] {0x7d8802a6, 0x8163001c, 0x4e800020}).length, 0);
        check("no_gate_keeps_align", TcSeedx.withGate(TcSeedx.ALIGN, new int[0][]), TcSeedx.ALIGN);
        int lwz = (32 << 26) | (11 << 21) | (3 << 16) | 4, andi = (28 << 26) | (11 << 21) | 15, bne = (16 << 26) | (4 << 21) | (2 << 16) | 8;
        check("gate_andi_record", TcSeedx.gateFields(new int[] {lwz, andi, bne}).length, 1);
        int addi = (14 << 26) | (3 << 21) | (3 << 16) | 16;   // r3 moved before the load: not an argument field
        check("gate_arg_written", TcSeedx.gateFields(new int[] {addi, lwz, andi, bne}).length, 0);
        check("gate_mask_unused", TcSeedx.gateAt(new int[] {lwz, andi}), -1);
        Map<Integer, Integer> hits = new TreeMap<>();
        hits.put(1, 50); hits.put(7, 3);
        check("gate_rare_block", TcSeedx.rare(new TreeSet<>(Arrays.asList(1, 7)), hits), true);
        hits.put(7, 4);
        check("gate_block_kept_enough", TcSeedx.rare(new TreeSet<>(Arrays.asList(1, 7)), hits), false);
        check("names_gate", TcSeedx.names(TcSeedx.GATE | TcSeedx.TINY), "[\"tiny\",\"gate\"]");

        // GATE vectors always pass 82a1d008's gate and never trap on its byte; ALIGN words alone rarely reach the loop
        Random g = new Random(9);
        int pass = 0, trap = 0, loopG = 0, loopA = 0, n8 = 4000;
        for (int i = 0; i < n8; i++) {
            byte[] m = new byte[0x1000];
            long[] rg = new long[8];
            for (int k = 0; k < m.length; k += 4) put4(m, k, TcSeedx.alignWord(g, 0x40000000L + (4L * g.nextInt(0x400) & ~15L)));
            List<Long> pa = new ArrayList<>(Arrays.asList(0x40000000L));
            List<byte[]> pd = new ArrayList<>(Arrays.asList(m));
            TcSeedx.gateFill(gf, rg, pa, pd, 0x40000000L, 0x40001000L, g, 0x40000000L + (4L * g.nextInt(0x400) & ~15L));
            if (i == 0) check("gate_heap_patch", pa.get(1) == 0x40001000L && pd.get(1).length == TcSeedx.GATE_HEAP, true);
            String e = entry82a1d008(m, (int) (rg[0] - 0x40000000L));
            if (!e.equals("exit")) pass++;
            if (e.equals("trap")) trap++;
            if (e.equals("loop")) loopG++;
            byte[] a = new byte[0x1000];
            for (int k = 0; k < a.length; k += 4) put4(a, k, TcSeedx.alignWord(g, 0x40000000L + (4L * g.nextInt(0x400) & ~15L)));
            if (entry82a1d008(a, (4 * g.nextInt(0x3f0)) & ~15).equals("loop")) loopA++;
        }
        check("gate_always_passed", pass, n8);
        check("gate_byte_never_zero", trap, 0);
        System.out.println("82a1d008 fast path: GATE " + loopG + "/" + n8 + ", ALIGN " + loopA + "/" + n8);
        check("gate_loop_share", loopG * 100 >= 3 * n8, true);
        check("align_loop_rare", loopA * 100 < n8, true);

        System.out.println(bad == 0 ? "ALL OK" : bad + " WRONG");
        System.exit(bad == 0 ? 0 : 1);
    }
}

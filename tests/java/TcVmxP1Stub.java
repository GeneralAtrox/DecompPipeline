// VMX_JUDGE_PLAN.md P1 (tests/test_x360_vmx_p1.py): TcVmx decode and lanes, no Ghidra imports.
// usage: java TcVmxP1Stub   prints one line per check, "ok NAME" or "WRONG NAME got ...".
import java.util.*;

public class TcVmxP1Stub {
    static int bad = 0;

    static void check(String name, Object got, Object want) {
        boolean same = got instanceof int[] ? Arrays.equals((int[]) got, (int[]) want) : Objects.equals(got, want);
        String g = got instanceof int[] ? hex((int[]) got) : String.valueOf(got);
        System.out.println(same ? "ok " + name : "WRONG " + name + " got " + g);
        if (!same) bad++;
    }

    static String hex(int[] r) {
        StringBuilder s = new StringBuilder();
        for (int x : r) s.append(String.format("%08x ", x));
        return s.toString().trim();
    }

    static int vx(int vd, int va, int vb, int xo) {
        return (4 << 26) | (vd << 21) | (va << 16) | (vb << 11) | xo;
    }

    static int[] run(TcVmx x, int word, int[] a, int[] b, int[] c) {
        TcVmx.Op o = TcVmx.decode(word);
        if (a != null) x.v[o.va] = a.clone();
        if (b != null) x.v[o.vb] = b.clone();
        if (c != null) x.v[o.vc] = c.clone();
        x.exec(o, new long[32], null);
        return x.v[o.vd].clone();
    }

    public static void main(String[] args) {
        // vupkhsb128 / vupklsb128 with VA = 0x60 are the halfword-to-word unpacks (Xenia ppc_emit_altivec.cc:2024-2051);
        // the game's word at 82a131bc, 0x1bc0ffa5, feeds vcsxwfp128 (word lanes).
        int game = 0x1bc0ffa5;
        TcVmx.Op o = TcVmx.decode(game);
        check("decode_82a131bc", o.mn, "vupkhsh128");
        check("decode_82a131bc_reads_vb_only", o.va, TcVmx.NONE);
        check("decode_va0_is_byte_form", TcVmx.decode(game & ~0x420).mn, "vupkhsb128");
        check("decode_lo_va60", TcVmx.decode((game & ~0x3f0) | 0x3e0).mn, "vupklsh128");
        check("decode_lo_va0", TcVmx.decode(((game & ~0x3f0) | 0x3e0) & ~0x420).mn, "vupklsb128");
        int[] hb = {0x8001_7fff, 0xffff_0002, 0x01020304, 0x05060708};
        TcVmx x = new TcVmx();
        check("vupkhsh128_lanes", run(x, game, null, hb, null), new int[] {0xffff8001, 0x00007fff, 0xffffffff, 0x2});
        check("vupklsh128_lanes", run(x, (game & ~0x3f0) | 0x3e0, null, hb, null),
              new int[] {0x0102, 0x0304, 0x0506, 0x0708});
        check("vupkhsb128_lanes", run(x, game & ~0x420, null, hb, null),
              new int[] {0xff800001, 0x007fffff, 0xffffffff, 0x00000002});
        // lanes: lane 0 is the most significant word
        int[] A = {0x10, 0x11, 0x12, 0x13}, B = {0x20, 0x21, 0x22, 0x23};
        check("vspltw_lane1", run(x, vx(1, 1, 3, 652), null, B, null), new int[] {0x21, 0x21, 0x21, 0x21});
        check("vmrghw", run(x, vx(1, 2, 3, 140), A, B, null), new int[] {0x10, 0x20, 0x11, 0x21});
        check("vmrglw", run(x, vx(1, 2, 3, 396), A, B, null), new int[] {0x12, 0x22, 0x13, 0x23});
        // saturation and SAT
        x.vscr = 0;
        check("vaddshs_sat", run(x, vx(1, 2, 3, 832), new int[] {0x7fff8000, 0x00010001, 0, 0},
              new int[] {0x00010001, 0xffff8000, 0, 0}, null), new int[] {0x7fff8001, 0x00008001, 0, 0});
        x.vscr = 0;
        check("vaddshs_sat_hi", run(x, vx(1, 2, 3, 832), new int[] {0x7fff8000, 0, 0, 0},
              new int[] {0x0001ffff, 0, 0, 0}, null), new int[] {0x7fff8000, 0, 0, 0});
        check("vaddshs_sets_sat", x.vscr & 1, 1);
        x.vscr = 0;
        check("vpkshus", run(x, vx(1, 2, 3, 270), new int[] {0x7fff_ff80, 0x00ff_0100, 0, 0x0001_0002},
              new int[] {0, 0, 0, 0x0003_8000}, null), new int[] {0xff00ffff, 0x00000102, 0, 0x00000300});
        check("vpkshus_sets_sat", x.vscr & 1, 1);
        // NJ: denormal inputs read as signed zero, denormal results written as signed zero
        int den = 0x00000001, nden = 0x80000001, one = 0x3f800000;
        check("vaddfp_den_in", run(x, vx(1, 2, 3, 10), new int[] {den, nden, one, 0x00400000},
              new int[] {0, 0x80000000, den, 0}, null), new int[] {0, 0x80000000, one, 0});
        int tiny = 0x00800000, half = 0x3f000000;     // 2^-126 * 0.5 is denormal: flushed to +0
        check("vmaddfp_den_out", run(x, (4 << 26) | (1 << 21) | (2 << 16) | (3 << 11) | (4 << 6) | 46,
              new int[] {tiny, 0x80800000, tiny, one}, new int[] {0x80000000, 0x80000000, 0, one},
              new int[] {half, half, one, one}), new int[] {0, 0x80000000, tiny, 0x40000000});
        check("vaddfp_nan_first_operand", run(x, vx(1, 2, 3, 10), new int[] {0x7f800001, 0, 0, 0x7f800000},
              new int[] {0x7fc00002, 0x7fa00000, 0, 0xff800000}, null), new int[] {0x7fc00001, 0x7fe00000, 0, 0x7fc00000});
        System.out.println(bad == 0 ? "ALL OK" : "WRONG " + bad);
    }
}

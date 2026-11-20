// VMX_JUDGE_PLAN.md P2 (tests/test_x360_vmx_p2.py): vpermwi128 and vrlimi128 on the game's own words and lanes, no
// Ghidra imports. Each case replays a confirmation site (TcVmxConfirmed.java) with the lanes its loads give.
// usage: java TcVmxP2Stub   prints one line per check, "ok NAME" or "WRONG NAME got ...", then "ALL OK".
import java.util.*;

public class TcVmxP2Stub {
    static int bad = 0;

    static void check(String name, int[] got, int[] want) {
        boolean same = Arrays.equals(got, want);
        System.out.println(same ? "ok " + name : "WRONG " + name + " got " + hex(got));
        if (!same) bad++;
    }

    static String hex(int[] r) {
        StringBuilder s = new StringBuilder();
        for (int x : r) s.append(String.format("%08x ", x));
        return s.toString().trim();
    }

    static void exec(TcVmx x, int word) {
        x.exec(TcVmx.decode(word), new long[32], null);
    }

    static int f(float v) {
        return Float.floatToRawIntBits(v);
    }

    static int[] fv(float a, float b, float c, float d) {
        return new int[] {f(a), f(b), f(c), f(d)};
    }

    public static void main(String[] args) {
        TcVmx x = new TcVmx();
        // vrlimi128, 827be458: 0x50..0x5c(r1) hold arg3, arg2, arg4, 0.0; lvlx at a word address loads up to the
        // 16-byte boundary and zero-fills. 827be660..68 read lanes 0, 1, 2 back as the x, y, z increments.
        int a2 = 0x22222222, a3 = 0x33333333, a4 = 0x44444444, z = 0x5a5a5a5a;
        x.v[0] = new int[] {a3, a2, a4, z};                 // lvlx 0x50
        x.v[13] = new int[] {a2, a4, z, 0};                 // lvlx 0x54
        exec(x, 0x19a407d0);                                // 827be644 vrlimi128 v13,v0,4,3
        check("vrlimi_827be644", x.v[13], new int[] {a2, a3, z, 0});
        x.v[0] = new int[] {a4, z, 0, 0};                   // lvlx 0x58
        x.v[12] = new int[] {z, 0, 0, 0};                   // lvlx 0x5c
        exec(x, 0x180467d0);                                // 827be650 vrlimi128 v0,v12,4,3
        check("vrlimi_827be650", x.v[0], new int[] {a4, z, 0, 0});
        exec(x, 0x19a30790);                                // 827be654 vrlimi128 v13,v0,3,2
        check("vrlimi_827be654_args_in_order", x.v[13], new int[] {a2, a3, a4, z});

        // vrlimi128, 827c5530: v0 = lvlx *r5, v13 = lvlx *r4 (the rest of each line is other memory), v0 = lvlx *r6,
        // v11 = lvlx -0x50(r1) = 0.0 and more; read back by lfs -0x40, -0x3c, -0x38 as the scale of rows 0, 1, 2.
        int sx = f(2f), sy = f(3f), sz = f(5f), j = 0x7e7e7e7e;
        x.v[0] = new int[] {sy, j + 1, j + 2, j + 3};
        x.v[13] = new int[] {sx, j + 4, j + 5, j + 6};
        exec(x, 0x19a407d0);                                // 827c5548 vrlimi128 v13,v0,4,3
        x.v[0] = new int[] {sz, j + 7, j + 8, j + 9};
        x.v[11] = new int[] {0, j + 10, j + 11, 0};
        exec(x, 0x18045fd0);                                // 827c5570 vrlimi128 v0,v11,4,3
        exec(x, 0x19a30790);                                // 827c5574 vrlimi128 v13,v0,3,2
        check("vrlimi_827c5574_scales_in_order", x.v[13], new int[] {sx, sy, sz, 0});

        // vpermwi128, 82e90d48: v0 = point j, v13 = point i (lvx128); the plane normal is cross(i, j) with w = 0.
        x.v[0] = fv(4f, 5f, 6f, 9f);
        x.v[13] = fv(1f, 2f, 3f, 7f);
        exec(x, 0x19870310);                                // 82e90d6c vpermwi128 v12,v0,135
        check("vpermwi_82e90d6c_zxyw", x.v[12], fv(6f, 4f, 5f, 9f));
        exec(x, 0x19636ad0);                                // 82e90d70 vpermwi128 v11,v13,99
        check("vpermwi_82e90d70_yzxw", x.v[11], fv(2f, 3f, 1f, 7f));
        exec(x, 0x19a76b10);                                // 82e90d74 vpermwi128 v13,v13,135
        exec(x, 0x180302d0);                                // 82e90d78 vpermwi128 v0,v0,99
        exec(x, 0x140d0090);                                // 82e90d7c vmulfp128 v0,v13,v0
        exec(x, 0x15ab6090);                                // 82e90d80 vmulfp128 v13,v11,v12
        exec(x, 0x100d004a);                                // 82e90d84 vsubfp v0,v13,v0
        check("vpermwi_82e90d48_cross_w0", x.v[0], fv(-3f, 6f, -3f, 0f));

        // vpermwi128, 82e74950: v13 = edge p2 - p1, v12 = edge p0 - p2; cross(v13, v12) is stored at 0x80(r1) and read
        // back lane by lane (lfs 0x80, 0x84, 0x88).
        x.v[13] = fv(1f, 0f, 0f, 0f);
        x.v[12] = fv(0f, 1f, 0f, 0f);
        exec(x, 0x19676b10);                                // 82e749d4 vpermwi128 v11,v13,135
        exec(x, 0x194362d0);                                // 82e749d8 vpermwi128 v10,v12,99
        exec(x, 0x19076310);                                // 82e749dc vpermwi128 v8,v12,135
        exec(x, 0x18e36ad0);                                // 82e749e0 vpermwi128 v7,v13,99
        exec(x, 0x156b5090);                                // 82e749e4 vmulfp128 v11,v11,v10
        exec(x, 0x15474090);                                // 82e749e8 vmulfp128 v10,v7,v8
        exec(x, 0x116a584a);                                // 82e749ec vsubfp v11,v10,v11
        check("vpermwi_82e749ec_x_cross_y_is_z", x.v[11], fv(0f, 0f, 1f, 0f));
        x.v[13] = fv(2f, 3f, 4f, 0f);
        x.v[12] = fv(5f, 6f, 7f, 0f);
        for (int w : new int[] {0x19676b10, 0x194362d0, 0x19076310, 0x18e36ad0, 0x156b5090, 0x15474090, 0x116a584a})
            exec(x, w);
        check("vpermwi_82e749ec_cross", x.v[11], fv(-3f, 6f, -3f, 0f));
        System.out.println(bad == 0 ? "ALL OK" : "WRONG " + bad);
    }
}

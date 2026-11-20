import java.util.Random;

/** Prints TcVmxFloat.vpkd3d (the oracle's Direct3D pack) for edge and random inputs: t mask z d0..d3 b0..b3 r0..r3, hex. */
class PkCheck {
    public static void main(String[] a) {
        int[] sp = {0x7FC00000, 0xFFC00001, 0x7F800001, 0x7F800000, 0xFF800000, 0, 0x80000000, 0x00000001, 0x807FFFFF,
            0x40400000, 0x40400001, 0x404000FF, 0x40400100, 0x403FFFFF, 0x40407FFF, 0x40408000, 0x403F8001, 0x403F8000,
            0x404001FF, 0x40400200, 0x403FFE01, 0x403FFE00, 0x3F800000, 0x3F800001, 0x3F800003, 0x3F800004, 0x3FFFFFFF,
            0x477FE000, 0x477FEFFF, 0x477FF000, 0x477FFFFF, 0x47800000, 0xC77FE000, 0xC7800000,
            0x3F801000, 0x3F803000, 0x3F801001, 0x3F802FFF, 0x38800000, 0x387FF000, 0x387FE000, 0x33800000, 0x33000000,
            0x33000001, 0x33C00000, 0x34000000, 0x34400000, 0x34A00000, 0x2F800000, 0xB3000001, 0xB8800000, 0x3EAAAAAB, 0xC2F6E979};
        Random r = new Random(20261103L);
        int[] d = {0x11111111, 0x22222222, 0x33333333, 0x44444444};
        StringBuilder sb = new StringBuilder();
        for (int t = 0; t < 6; t++) {
            for (int m = 0; m < 4; m++) {
                for (int z = 0; z < 4; z++) {
                    for (int k = 0; k < sp.length + 400; k++) {
                        int[] b = new int[4];
                        for (int l = 0; l < 4; l++) {
                            b[l] = k < sp.length ? sp[(k + 7 * l) % sp.length]
                                : (r.nextInt(3) == 0 ? r.nextInt() : (r.nextBoolean() ? 0x40400000 : 0x3F800000) + r.nextInt(0x1000) - 0x800);
                        }
                        int[] o = TcVmxFloat.vpkd3d(d, b, t, m, z);
                        sb.append(String.format("%d %d %d %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x%n", t, m, z,
                            d[0], d[1], d[2], d[3], b[0], b[1], b[2], b[3], o[0], o[1], o[2], o[3]));
                    }
                }
            }
        }
        System.out.print(sb);
    }
}

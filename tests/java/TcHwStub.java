// Saturn TcHw's listed registers by width (tests/test_saturn_hw.py): the bytes TcFold serves for loads in and around a
// 1-, 2- and 4-byte register, its meta value and its vectors.txt Y line.
//
// usage: java TcHwStub ARG   prints "read OFFSET SIZE BYTES|null" per load, then "meta ..." and "header ..." lines
public class TcHwStub {
    public static void main(String[] args) {
        TcHw.on = TcHw.parse(args[0]);
        for (java.util.Map.Entry<Long, Long> e : TcHw.on.reads.entrySet()) {
            long r = e.getKey();
            for (long off = r - 2; off <= r + 4; off++) {
                for (int size : new int[] {1, 2, 4}) {
                    byte[] b = TcHw.on.read(off, size);
                    StringBuilder s = new StringBuilder();
                    if (b == null) s.append("null");
                    else for (byte x : b) s.append(String.format("%02x", x & 0xff));
                    System.out.println("read " + Long.toHexString(off) + " " + size + " " + s);
                }
            }
        }
        System.out.println("meta " + TcHw.on.meta());
        System.out.print("header " + TcHw.header().replace("\n", "|") + "\n");
    }
}

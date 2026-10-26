// TcFpu.fscaTable on a table of cases (tests/test_fsca_rm.py): which fsca table the oracle takes, and whether it
// rejects the vector, for fpscr_model on or off, the live FPSCR.RM and the entry FPSCR.
//
// usage: java TcFscaStub      prints "MODEL RM ENTRY -> TABLE REJECT" per case (REJECT: null or the reason)
public class TcFscaStub {
    public static void main(String[] args) {
        long[][] cases = {{0, 0, 0x40001}, {0, 1, 0x40001}, {1, 1, 0x40001}, {1, 0, 0x40001}, {1, 0, 0x40000},
                          {1, 1, 0x40000}, {1, 2, 0x40001}};
        for (long[] c : cases) {
            String[] reject = {null};
            int t = TcFpu.fscaTable(c[0] != 0, c[1], c[2], reject);
            System.out.println(c[0] + " " + c[1] + " " + Long.toHexString(c[2]) + " -> " + t + " " + reject[0]);
        }
    }
}

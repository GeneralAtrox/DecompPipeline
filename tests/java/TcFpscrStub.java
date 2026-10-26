// TcFpscr on tables (tests/test_dc_fpscr.py): no Ghidra application, no program.
//
// usage: java TcFpscrStub CASES      CASES lines (# starts a comment):
//   K TEXT...                          prints "K: kind" (TcFpscr.kind of an instruction's text)
//   A TEXT...                          prints "A: access" (TcFpscr.access: does it make FPSCR state, TcFpscr.of)
//   E MODEL PROJECT R4 R5 R6 R7 FR4    prints "E: fpscr" (TcFpscr.entry of a vector with these inputs; MODEL 0 or 1)
//   O PROJECT                          prints "O: fpscr" (the entry with TcFpscr.on null: the project's, unchanged)
//   X ENTRY EXIT [CALL...]             prints "X: N line|FS line|FC line" (TcFpscr.line and exit of such a run)
import java.nio.file.*;

public class TcFpscrStub {
    static TcInputs.Vec vec(String[] x, int at) {
        TcInputs.Vec v = new TcInputs.Vec();
        for (int i = 0; i < 4; i++) v.r[i] = Long.parseLong(x[at + i], 16);
        v.fr[0] = (int) Long.parseLong(x[at + 4], 16);
        return v;
    }

    public static void main(String[] args) throws Exception {
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String l = line.replaceFirst("#.*", "").trim();
            if (l.isEmpty()) continue;
            String[] x = l.split("\\s+");
            if (x[0].equals("K")) {
                System.out.println("K: " + TcFpscr.kind(l.substring(2)));
            } else if (x[0].equals("A")) {
                System.out.println("A: " + TcFpscr.access(l.substring(2)));
            } else if (x[0].equals("E")) {
                TcFpscr.on = new TcFpscr(x[1].equals("1"));
                TcFpscr.Run r = TcFpscr.start();
                long fp = TcFpscr.entry(Long.parseLong(x[2], 16), vec(x, 3));
                if (r.entry != fp) throw new AssertionError("the run's entry is not the vector's");
                System.out.println("E: " + TcBase.hex(fp));
            } else if (x[0].equals("O")) {
                TcFpscr.on = null;
                if (TcFpscr.start() != null) throw new AssertionError("a run without TcFpscr.on");
                System.out.println("O: " + TcBase.hex(TcFpscr.entry(Long.parseLong(x[1], 16), new TcInputs.Vec())));
            } else if (x[0].equals("X")) {
                TcFpscr.Run r = new TcFpscr.Run();
                r.entry = Long.parseLong(x[1], 16);
                r.exit = Long.parseLong(x[2], 16);
                for (int i = 3; i < x.length; i++) r.calls.add(Long.parseLong(x[i], 16));
                String s = TcFpscr.line(r) + "|" + TcFpscr.exit(r) + "|" + TcFpscr.line(null) + TcFpscr.exit((TcFpscr.Run) null);
                System.out.println("X: " + s.replace("\n", "/"));
            }
        }
    }
}

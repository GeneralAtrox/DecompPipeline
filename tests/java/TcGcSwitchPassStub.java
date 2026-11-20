// TcSwitchPass's slicer (TcSwitchSel; tests/test_gc_switch_pass.py), no Ghidra program: reads "addr word" lines (hex)
// from args[0]; then for each "fn bctr i.." line of args[1] prints "bctr: kind reg at callee size | plan(i).. | targets"
// (a plan is its roots' values joined by '+', or null), and for each "G fn branch taken" line prints
// "branch: why | kind reg | values" (TcSwitchSel.guard), and for each "W fn end bctr" line the frontier on the way to the
// bctr from fn..end, "W: branch:t|f .." (TcSwitchPass.frontier), and for an "O flag.." line the order of the bases
// (flag 1: ran the switch) the case loop tries, "O: index.." (TcSwitchPass.ranFirst).
import java.nio.file.*;
import java.util.*;

public class TcGcSwitchPassStub {
    public static void main(String[] args) throws Exception {
        Map<Long, Long> img = new HashMap<>();
        for (String ln : Files.readAllLines(Paths.get(args[0]))) {
            String[] t = ln.trim().split("\\s+");
            if (t.length == 2) img.put(Long.parseLong(t[0], 16), Long.parseLong(t[1], 16));
        }
        TcSwitchBody.Words w = img::get;
        for (String ln : Files.readAllLines(Paths.get(args[1]))) {
            String[] t = ln.trim().split("\\s+");
            if (t.length < 2) continue;
            if (t[0].equals("O")) {
                List<Integer> pool = new ArrayList<>();
                for (int i = 1; i < t.length; i++) pool.add(i - 1);
                StringBuilder b = new StringBuilder("O:");
                for (int i : TcSwitchPass.ranFirst(pool, k -> t[k + 1].equals("1"))) b.append(' ').append(i);
                System.out.println(b);
                continue;
            }
            if (t[0].equals("W")) {
                List<Long> body = new ArrayList<>();
                for (long p = Long.parseLong(t[1], 16); p <= Long.parseLong(t[2], 16); p += 4) body.add(p);
                StringBuilder b = new StringBuilder("W:");
                for (long[] f : TcSwitchPass.frontier(w, body, Map.of(), Long.parseLong(t[3], 16)))
                    b.append(' ').append(Long.toHexString(f[0])).append(f[1] == 1 ? ":t" : ":f");
                System.out.println(b);
                continue;
            }
            if (t[0].equals("G")) {
                long fn = Long.parseLong(t[1], 16), br = Long.parseLong(t[2], 16);
                TcSwitchSel.Guard g = TcSwitchSel.guard(w, fn, br, t[3].equals("1"));
                StringBuilder b = new StringBuilder(String.format("%08x: %s| %s %d |", br, g.why == null ? "-" : g.why,
                    g.sel == null ? "-" : TcSwitchSel.kind(g.sel), g.sel == null ? -1 : g.sel.reg));
                for (long x : g.want) b.append(' ').append(Long.toHexString(x));
                System.out.println(b);
                continue;
            }
            long fn = Long.parseLong(t[0], 16), bctr = Long.parseLong(t[1], 16);
            TcSwitchSel.Sel s = TcSwitchSel.of(w, fn, bctr);
            StringBuilder b = new StringBuilder(String.format("%08x: %s %d %x %x %d |", bctr, TcSwitchSel.kind(s), s.reg,
                s.at, s.callee, s.size));
            for (int i = 2; i < t.length; i++) {
                Map<TcSwitchSel.Sel, Long> p = TcSwitchSel.plan(s, Integer.parseInt(t[i]));
                StringBuilder v = new StringBuilder();
                if (p != null) for (long x : p.values()) v.append(v.length() > 0 ? "+" : "").append(Long.toHexString(x));
                b.append(' ').append(p == null ? "null" : v);
            }
            b.append(" | ").append(s.targets == null ? "null" : String.valueOf(s.targets.length));
            System.out.println(b);
        }
    }
}

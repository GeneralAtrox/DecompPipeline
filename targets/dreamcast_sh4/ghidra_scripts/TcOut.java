// A callee's register outputs in r4..r7 (TcLive's table entries 7..10). Not a script itself; TcPrepare calls prepare()
// and TcLive.of calls outs() per callee, TcVectors writes header() and exp().
//
// Hand-written library leaves hand a result back in an argument register: Spawn's vertex leaves 8c0602a0 and 8c060ca0
// return with r4 advanced past the vertex stream, and Lib_RenderModelAlpha (8c05fec2) reads the next mesh header
// through that r4. A mocked callee used to leave r4..r7 as they were (the ABI: they are dead after a call), so C that
// kept its own r4 matched the judge and faulted in the port. Now, per callee, the value of r4..r7 at each of its
// returns against its entry value (TcLive.Deltas, following its own calls: follow()) is
//   NONE (kept on every path), {d..} (rK + a constant), UNKNOWN (from rK, offset unknown) or OTHER (not from rK);
// a caller that reads rK after a call to it before writing it (TcStrict.walk from the return address) gets the same
// model TcLive gives r8..r14: rK += d, one of the set, or STEP * (1..3), drawn per vector and call (header lines K
// "K target 4..7 n d..", line A). The C runners hand it over through sh4_r / sh4_set_r. OTHER is never guessed: such a
// (callee, register) a caller reads is reported (meta "r_out_left") and left as before.
// The callee's own side (exported): a function whose rK is not NONE and that some caller reads after calling it has
// rK compared at exit (vectors.txt "O mask", expected.txt "RO v4 v5 v6 v7"), and counts rK as read at its returns
// (TcStrict.walk), so the strict judge's junk never lands there. Without any of this every harness byte is as before.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.Reference;
import java.util.*;

final class TcOut {
    static int exported;                                        // TcFlow masks of r4..r7 compared at exit
    static TcLive.Deltas follow;                                // the outs, the callee's own calls followed
    static final Map<Long, String> left = new TreeMap<>(), out = new TreeMap<>();  // callee -> "r4,r6" (meta)

    static int bit(int k) {
        return TcFlow.maskOf("r" + (4 + k));
    }

    // TcPrepare, before TcLive.of: the function's own outputs some caller reads (exported).
    static void prepare(TcBase s, boolean on) {
        Program prog = s.getCurrentProgram();
        exported = TcStrict.exitLive = 0;
        left.clear();
        out.clear();
        if (!on) return;
        follow = new TcLive.Deltas(prog);
        follow.follow = true;
        long[][] o = follow.outs(s.fn);
        int want = 0;
        for (int k = 0; k < 4; k++) if (o[k] != TcLive.NONE) want |= bit(k);
        if (want == 0) return;
        int ex = 0;
        FunctionManager fm = prog.getFunctionManager();
        for (Reference r : prog.getReferenceManager().getReferencesTo(s.fn.getEntryPoint())) {
            Function c = r.getReferenceType().isCall() ? fm.getFunctionContaining(r.getFromAddress()) : null;
            if (c != null) ex |= want & TcStrict.walk(prog, TcTail.extent(prog, c), r.getFromAddress().add(4));
        }
        exported = TcStrict.exitLive = ex;
    }

    // TcLive.of, per callee g of the function: e[7 + k] for each rK g leaves changed that the function reads after a
    // call to g. Whether any was set.
    static boolean outs(TcBase s, Function g, long[][] e) {
        Program prog = s.getCurrentProgram();
        long[][] o = follow.outs(g);
        int read = 0;
        AddressSetView ext = TcTail.extent(prog, s.fn);
        for (Instruction in : prog.getListing().getInstructions(ext, true)) {
            for (Reference r : in.getReferencesFrom()) {
                if (r.getReferenceType().isCall() && r.getToAddress().equals(g.getEntryPoint())) {
                    read |= TcStrict.walk(prog, ext, in.getAddress().add(4));
                }
            }
        }
        boolean any = false;
        long at = g.getEntryPoint().getOffset();
        for (int k = 0; k < 4; k++) {
            if ((read & bit(k)) == 0 || o[k] == TcLive.NONE) continue;
            Map<Long, String> m = o[k] == TcLive.OTHER ? left : out;
            m.merge(at, "r" + (4 + k), (x, y) -> x + "," + y);
            if (o[k] == TcLive.OTHER) continue;
            e[7 + k] = o[k];
            any = true;
        }
        return any;
    }

    // Deltas.solve at a return: rK against its entry value.
    static void exit(TcLive.Deltas.St s, List<Set<Long>> ox, boolean[] unk, boolean[] oth) {
        for (int k = 0; k < 4; k++) {
            if (s.base[4 + k] == 4 + k) ox.get(k).add(s.off[4 + k]);
            else if (s.base[4 + k] == TcLive.Deltas.ARG + k) unk[k] = true;
            else oth[k] = true;
        }
    }

    // Deltas.solve's end: per rK, NONE, {d..}, UNKNOWN or OTHER.
    static long[][] classify(List<Set<Long>> ox, boolean[] unk, boolean[] oth) {
        long[][] ou = new long[4][];
        for (int k = 0; k < 4; k++) {
            Set<Long> e = ox.get(k);
            ou[k] = oth[k] ? TcLive.OTHER : unk[k] || e.size() > TcLive.MAX_SET ? TcLive.UNKNOWN
                : e.isEmpty() || e.equals(Set.of(0L)) ? TcLive.NONE : e.stream().mapToLong(Long::longValue).toArray();
        }
        return ou;
    }

    // Deltas.call in follow mode, after r0..r7 were clobbered: the callee's r4..r7 outs, of the value at the call.
    static void follow(TcLive.Deltas.St s, long[][] o, TcLive.Deltas.St was) {
        for (int k = 0; k < 4; k++) {
            int b = was.base[4 + k], a = TcLive.Deltas.arg(b);
            long[] x = o[k];
            if (x == TcLive.OTHER || b == TcLive.Deltas.TOP || x != TcLive.NONE && x.length != 1 && a < 0) continue;
            s.base[4 + k] = x == TcLive.NONE || x.length == 1 ? b : TcLive.Deltas.ARG + a;
            s.off[4 + k] = x == TcLive.NONE ? was.off[4 + k] : x.length == 1 ? was.off[4 + k] + x[0] : 0;
        }
    }

    // vectors.txt header line "O mask" (bit k: r(4+k) compared at exit), only with exports.
    static String header() {
        int m = 0;
        for (int k = 0; k < 4; k++) if ((exported & bit(k)) != 0) m |= 1 << k;
        return m == 0 ? "" : "O " + m + "\n";
    }

    // expected.txt "RO v4 v5 v6 v7" at exit (TcExit.collect: Result.ro), only with exports.
    static String exp(TcOracle.Result r) {
        if (exported == 0 || r.halted) return "";
        return String.format("\nRO %08x %08x %08x %08x", r.ro[0], r.ro[1], r.ro[2], r.ro[3]);
    }

    // meta.json: "r_out" {"callee": "r4,r6"} modelled, "r_out_left" the same not modelled (OTHER), "r_exports".
    static String meta() {
        StringBuilder b = new StringBuilder();
        if (!out.isEmpty()) b.append(",\n  \"r_out\": ").append(json(out));
        if (!left.isEmpty()) b.append(",\n  \"r_out_left\": ").append(json(left));
        if (exported != 0) {
            StringBuilder x = new StringBuilder();
            for (int k = 0; k < 4; k++) if ((exported & bit(k)) != 0) x.append(x.length() > 0 ? "," : "").append("\"r" + (4 + k) + "\"");
            b.append(",\n  \"r_exports\": [").append(x).append(']');
        }
        return b.toString();
    }

    static String json(Map<Long, String> m) {
        StringBuilder b = new StringBuilder();
        for (Map.Entry<Long, String> e : m.entrySet()) {
            b.append(b.length() > 0 ? "," : "").append(String.format("\"%08x\":\"%s\"", e.getKey(), e.getValue()));
        }
        return "{" + b + "}";
    }
}

// The signature-dependent meta fields of many functions, computed exactly as TcPrepare computes them, one JSON line
// per function: signature, ret_reg, r_state, fr_state, unsupported and the registers compared at each callee. For
// tools/sigdrift.py, which compares them with the stored harnesses (a deploy that changes prototypes, or a callee
// whose own prototype changed, leaves matched functions judged on an older signature). Read-only.
// args: OUTFILE FACTSFILE ADDRFILE (one hex address per line)
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.io.*;
import java.nio.file.*;

public class TcSigs extends TcPrepare {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        facts = TcFacts.load(a[1]);
        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        fm = currentProgram.getFunctionManager();
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        try (PrintWriter w = new PrintWriter(new OutputStreamWriter(new FileOutputStream(a[0]), "UTF-8"))) {
            for (String line : Files.readAllLines(Paths.get(a[2]))) {
                String addr = line.trim();
                if (addr.isEmpty()) continue;
                if (monitor.isCancelled()) break;
                w.println(record(di, addr));
                w.flush();
            }
        } finally {
            di.dispose();
        }
    }

    String record(DecompInterface di, String addr) {
        StringBuilder o = new StringBuilder("{\"addr\":\"" + addr + "\"");
        try {
            Function f = fm.getFunctionAt(toAddr(Long.parseLong(addr, 16)));
            if (f == null) return o.append(",\"error\":\"no function\"}").toString();
            AddressSetView ext = TcTail.extent(currentProgram, f);
            Proto p = prototypeOf(di, f);
            o.append(",\"signature\":\"").append(esc(cSignature("fn_" + addr, p))).append('"');
            o.append(",\"ret_reg\":").append(p.retReg == null ? "null" : "\"" + p.retReg + "\"");
            o.append(",\"r_state\":[").append(quoted(p.giState)).append(']');
            o.append(",\"fr_state\":[").append(quoted(p.frState & TcFlow.STATE)).append(']');
            o.append(",\"unsupported\":").append(p.unsupported == null ? "null" : "\"" + esc(p.unsupported) + "\"");
            o.append(",\"callees\":{");
            boolean first = true;
            for (Function c : calledBy(f, ext)) {
                Proto cp = prototypeOf(di, c, PASS_DEPTH - 1);
                o.append(first ? "" : ",").append('"').append(hex(c.getEntryPoint().getOffset())).append("\":[")
                    .append(String.join(",", calleeRegs(cp, c).stream().map(r -> "\"" + r + "\"").toList())).append(']');
                first = false;
            }
            return o.append("}}").toString();
        } catch (Exception e) {
            return o.append(",\"error\":\"").append(esc(String.valueOf(e))).append("\"}").toString();
        }
    }

    static String quoted(int mask) {
        return mask == 0 ? "" : "\"" + TcSig.names(mask).replace(", ", "\",\"") + "\"";
    }
}

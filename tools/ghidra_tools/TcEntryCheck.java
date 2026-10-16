// Read-only check of candidate function entries (tools/data_funcs.py, batch 27): does each address disassemble as a
// subroutine? Not an oracle script (it lives outside the target's ghidra_scripts, so no harness stamp depends on it).
//
// Script args: 0 input file (one hex address per line), 1 output file, one line per address:
//   ADDR <TAB> verdict <TAB> detail
// verdict: function (already a function entry), inside (inside function F), code (decoded, in no function), data
// (typed data there), valid (PseudoDisassembler: a subroutine that decodes and terminates), invalid (it does not).
// detail: the first instruction, and "prologue" when it pushes PR or a register (sts.l PR,@-r15 / mov.l rN,@-r15).
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.app.util.PseudoDisassembler;
import ghidra.app.util.PseudoInstruction;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import java.io.*;
import java.nio.file.*;

public class TcEntryCheck extends GhidraScript {
    private static String mark(String insn) {
        String t = insn.toLowerCase();
        return insn + (t.matches("(sts\\.l pr|mov\\.l r\\d+),@-r15") ? " (prologue)" : "");
    }

    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        FunctionManager fm = currentProgram.getFunctionManager();
        Listing listing = currentProgram.getListing();
        PseudoDisassembler pd = new PseudoDisassembler(currentProgram);
        try (PrintWriter pw = new PrintWriter(new OutputStreamWriter(new FileOutputStream(a[1]), "UTF-8"))) {
            for (String line : Files.readAllLines(Paths.get(a[0]))) {
                String s = line.trim();
                if (s.isEmpty() || s.startsWith("#")) continue;
                Address x = toAddr(Long.parseLong(s, 16));
                String verdict, detail = "";
                Function f = fm.getFunctionContaining(x);
                Data d = listing.getDefinedDataContaining(x);
                if (f != null) {
                    verdict = f.getEntryPoint().equals(x) ? "function" : "inside";
                    detail = "fn_" + f.getEntryPoint();
                } else if (listing.getInstructionAt(x) != null) {
                    verdict = "code";
                    detail = mark(listing.getInstructionAt(x).toString());
                } else if (d != null && !ghidra.program.model.data.Undefined.isUndefined(d.getDataType())) {
                    verdict = "data";
                    detail = d.getDataType().getName();
                } else {
                    PseudoInstruction first = null;
                    try {
                        first = pd.disassemble(x);
                    } catch (Exception e) {
                        first = null;
                    }
                    boolean ok = first != null && pd.isValidSubroutine(x, true);
                    verdict = ok ? "valid" : "invalid";
                    if (first != null) detail = mark(first.toString());
                }
                pw.println(s + "\t" + verdict + "\t" + detail);
            }
        }
    }
}

// The functions the GATE extra vectors reach: those Seeds, the decision TcVectors.makeVectors itself takes, puts in
// the GATE class (TcSeedx.withGate). One entry address per line (8 hex digits), after a "# functions N gate M" line,
// for the stamp scope "gate" (target.json "stamp_scopes", x360_scope.py). Read-only. LOCKED.
//
// Script args: 0 output file. Run by targets/xbox360_ppc/tools/gate_scan.py, which adds the header that binds the
// list to the scripts and the catalog it was made from.
//@category DecompPipeline
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.nio.file.*;
import java.util.*;

public class TcGateScan extends GhidraScript {
    /**
     * What decides a body's extra vector classes (seedExtras), read off its instructions in address order: its words
     * (TcSeedx.gateFields), its vector ops (TcSeedx.classes), whether it has a multiply-add, and from them cw =
     * {classes, saturating widths} with ALIGN turned GATE in a function with gate fields (TcSeedx.withGate). The one
     * GATE decision: TcVectors.makeVectors takes it, and TcGateScan lists the functions it puts in GATE for the stamp
     * scope "gate" (target.json).
     */
    static final class Seeds {
        final Set<String> vmxOps = new TreeSet<>();
        final int[] cw;
        final int[][] gateFields;
        boolean fma = false;

        Seeds(Program p, AddressSetView body) {
            List<Integer> words = new ArrayList<>();
            for (Instruction in : p.getListing().getInstructions(body, true)) {
                words.add(TcFixups.word(in));
                if (TcClass.classify(TcFixups.word(in)).contains("fma")) fma = true;
                TcVmx.Op vop = TcBase.vmxOpAt(p, in.getAddress());
                if (vop != null) vmxOps.add(vop.mn);
            }
            gateFields = TcSeedx.gateFields(words.stream().mapToInt(Integer::intValue).toArray());
            cw = TcSeedx.classes(vmxOps);
            cw[0] = TcSeedx.withGate(cw[0], gateFields);
        }

        boolean gate() {
            return (cw[0] & TcSeedx.GATE) != 0;
        }
    }

    @Override
    public void run() throws Exception {
        List<String> gate = new ArrayList<>();
        int n = 0;
        for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
            if (f.isExternal()) continue;
            n++;
            if (new Seeds(currentProgram, f.getBody()).gate()) gate.add(String.format("%08x", f.getEntryPoint().getOffset()));
        }
        StringBuilder b = new StringBuilder("# functions " + n + " gate " + gate.size() + "\n");
        for (String a : gate) b.append(a).append('\n');
        Files.write(Paths.get(getScriptArgs()[0]), b.toString().getBytes("UTF-8"));
        println("TcGateScan: " + gate.size() + " of " + n + " functions in GATE");
    }
}

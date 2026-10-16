// Seeds a fresh raw-binary import with the project's own functions before analysis, and (run again as a
// post-script with "enforce") makes the function set exactly the project's: functions whose entry is not listed are
// removed and every listed function gets the listed body. So FID hashes the boundaries the project uses.
// Script args: 0 bodies file ("entry lo hi" per line, hex, hi exclusive; several lines per entry for a body in
//   several ranges), 1 optional "enforce".
// Prints FIDSEED listed=<n> functions=<m> removed=<r> failed=<f>
//@category DecompPipeline
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import java.nio.file.*;
import java.util.*;

public class SdkFidSeed extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        boolean enforce = a.length > 1 && a[1].equals("enforce");
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        Map<Address, AddressSet> bodies = new TreeMap<>();
        for (String line : Files.readAllLines(Paths.get(a[0]))) {
            String[] p = line.trim().split("\\s+");
            if (p.length < 3 || p[0].startsWith("#")) continue;
            Address e = sp.getAddress(Long.parseLong(p[0], 16));
            long lo = Long.parseLong(p[1], 16), hi = Long.parseLong(p[2], 16);
            AddressSet s = bodies.computeIfAbsent(e, k -> new AddressSet());
            if (hi > lo) s.add(sp.getAddress(lo), sp.getAddress(hi - 1));
        }
        FunctionManager fm = currentProgram.getFunctionManager();
        Listing listing = currentProgram.getListing();
        int removed = 0, failed = 0;
        if (enforce) {
            List<Function> drop = new ArrayList<>();
            for (Function f : fm.getFunctions(true)) {
                if (!bodies.containsKey(f.getEntryPoint())) drop.add(f);
            }
            for (Function f : drop) {
                fm.removeFunction(f.getEntryPoint());
                removed++;
            }
            for (Function f : fm.getFunctions(true)) {  // shrink first, so no listed body overlaps a stale one
                Address e = f.getEntryPoint();
                f.setBody(new AddressSet(e, e));
            }
        }
        for (Map.Entry<Address, AddressSet> en : bodies.entrySet()) {
            Address e = en.getKey();
            AddressSet body = en.getValue();
            if (body.isEmpty()) body.add(e);
            if (!body.contains(e)) body.add(e);
            if (listing.getInstructionAt(e) == null) {
                new DisassembleCommand(e, body, true).applyTo(currentProgram, monitor);
            }
            Function f = fm.getFunctionAt(e);
            try {
                if (f == null) {
                    List<Address> stale = new ArrayList<>();
                    for (Iterator<Function> it = fm.getFunctionsOverlapping(body); it.hasNext();) {
                        Address g = it.next().getEntryPoint();
                        if (!bodies.containsKey(g)) stale.add(g);
                    }
                    for (Address g : stale) fm.removeFunction(g);
                    fm.createFunction(null, e, body, SourceType.DEFAULT);
                }
                else if (enforce && !f.getBody().equals(body)) {
                    f.setBody(body);
                }
            }
            catch (Exception ex) {
                failed++;
                if (failed <= 20) println("FIDSEED failed " + e + ": " + ex.getMessage());
            }
        }
        println("FIDSEED listed=" + bodies.size() + " functions=" + fm.getFunctionCount() + " removed=" + removed
            + " failed=" + failed);
    }
}

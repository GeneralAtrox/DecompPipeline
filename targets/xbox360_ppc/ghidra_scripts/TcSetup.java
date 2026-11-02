// Pre-analysis setup for an Xbox 360 title's loaded image (image.bin from tools/catalog_inputs.py, imported raw at
// 0x82000000 as PowerPC:BE:64:64-32addr; run once, before analysis).
//  - maps the heap, stack and kernel regions (TcBase) as uninitialized read/write blocks: they read as zero, as in
//    the C runner;
//  - script arguments NAME:HEX (project.json "fixed_regs"): register values pinned at every address of the
//    executable blocks (r13, the processor block), so the decompiler resolves r13-relative accesses and the oracle
//    starts every run with them;
//  - turns every auto-analyzer off but the constant reference analyzers (lis/addi pairs name the globals): the
//    functions come from the catalog, not from Ghidra's searches;
//  - disassembles and creates the functions of ghidra_setup.txt beside the binary (tools/ghidra_setup.py): exact
//    bodies, the save/restore helper families with a label per entry, and the import thunks.
//@category DecompPipeline
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.io.File;
import java.math.BigInteger;
import java.nio.file.*;
import java.util.*;

public class TcSetup extends GhidraScript {
    static final String LIST = "ghidra_setup.txt";

    static long num(String s) {
        return Long.parseLong(s, 16);
    }

    // The setup list beside the imported file (getExecutablePath is "/C:/..." on Windows).
    Path listPath() {
        String exe = currentProgram.getExecutablePath();
        if (exe.matches("^/[A-Za-z]:.*")) exe = exe.substring(1);
        return new File(exe).toPath().resolveSibling(LIST);
    }

    void mapRegions() throws Exception {
        Memory mem = currentProgram.getMemory();
        long[][] regions = {{TcBase.HEAP_LO, TcBase.HEAP_HI}, {TcBase.STACK_LO, TcBase.STACK_HI},
            {TcBase.KERNEL_LO, TcBase.KERNEL_HI}};
        String[] names = {"heap", "stack", "kernel"};
        for (int i = 0; i < regions.length; i++) {
            MemoryBlock b = mem.createUninitializedBlock(names[i], toAddr(regions[i][0]), regions[i][1] - regions[i][0], false);
            b.setRead(true);
            b.setWrite(true);
            b.setExecute(false);
        }
    }

    void pinRegisters() throws Exception {
        ProgramContext ctx = currentProgram.getProgramContext();
        for (String arg : getScriptArgs()) {
            String[] kv = arg.split(":");
            Register reg = currentProgram.getRegister(kv[0]);
            if (reg == null) throw new IllegalArgumentException("no register " + kv[0]);
            BigInteger v = new BigInteger(kv[1].replaceFirst("^0x", ""), 16);
            for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
                if (b.isExecute()) ctx.setValue(reg, b.getStart(), b.getEnd(), v);
            }
        }
    }

    // Every boolean analyzer option off, but the constant reference analyzers. Returns the names left on.
    List<String> quietAnalysis() {
        Map<String, String> opts = getCurrentAnalysisOptionsAndValues(currentProgram);
        Map<String, String> set = new HashMap<>();
        List<String> on = new ArrayList<>();
        for (Map.Entry<String, String> e : opts.entrySet()) {
            String v = e.getValue();
            if (v == null || !(v.equals("true") || v.equals("false")) || e.getKey().contains(".")) continue;
            boolean keep = e.getKey().toLowerCase().contains("constant reference");
            set.put(e.getKey(), keep ? "true" : "false");
            if (keep) on.add(e.getKey());
        }
        setAnalysisOptions(currentProgram, set);
        return on;
    }

    // The stand-in for a vector instruction word: a compare of the general registers the instruction reads (cmplw cr7
    // for two, or one twice), crxor 6,6,6 for none. Ghidra decodes both, and the register dataflow sees the same reads.
    static int standIn(TcVmx.Op op) {
        if (op.gWrite.length > 0) throw new IllegalStateException("vector instruction writing a GPR: " + op);
        if (op.gRead.length == 0) return 0x4CC63182;
        int a = op.gRead[0], b = op.gRead.length > 1 ? op.gRead[1] : a;
        return 0x7F800040 | (a << 16) | (b << 11);
    }

    // Replace every vector word of the code (TcBase.VMX_MAP keeps the original). Returns the number replaced.
    int patchVector(Collection<long[]> ranges) throws Exception {
        Memory mem = currentProgram.getMemory();
        ghidra.program.model.util.IntPropertyMap map = currentProgram.getUsrPropertyManager().getIntPropertyMap(TcBase.VMX_MAP);
        if (map == null) map = currentProgram.getUsrPropertyManager().createIntPropertyMap(TcBase.VMX_MAP);
        int n = 0, mismatch = 0;
        for (long[] r : ranges) {
            for (long a = r[0]; a + 4 <= r[1]; a += 4) {
                Address at = toAddr(a);
                int w = mem.getInt(at);
                TcVmx.Op op = TcVmx.decode(w);
                if (op == null) {
                    if (TcClass.isVector(w) && (w >>> 26) != 31 && n + mismatch < 20) println("TcSetup: vector word not decoded: " + at);
                    if (TcClass.isVector(w) && (w >>> 26) != 31) mismatch++;
                    continue;
                }
                if (!TcClass.isVector(w) && n + mismatch < 20) println("TcSetup: TcVmx decodes " + op + " at " + at + ", TcClass does not name it a vector word");
                map.add(at, w);
                mem.setInt(at, standIn(op));
                n++;
            }
        }
        if (mismatch > 0) throw new IllegalStateException(mismatch + " vector word(s) TcClass names but TcVmx does not decode");
        return n;
    }

    AddressSet range(long lo, long hi) {
        return new AddressSet(toAddr(lo), toAddr(hi - 1));
    }

    int freed = 0;

    // An entry the first disassembly did not reach: disassemble from there, so createFunction finds an instruction.
    void freeEntry(Address at, AddressSetView body) {
        Listing listing = currentProgram.getListing();
        if (listing.getInstructionAt(at) != null) return;
        CodeUnit cu = listing.getCodeUnitAt(at);
        if (cu instanceof Data) listing.clearCodeUnits(at, at.add(cu.getLength() - 1), false);
        new DisassembleCommand(at, body, true).applyTo(currentProgram, monitor);
        freed++;
        if (freed <= 20) println("TcSetup: " + at + " was not reached by the disassembly; disassembled from there");
    }

    @Override
    public void run() throws Exception {
        mapRegions();
        pinRegisters();
        List<String> on = quietAnalysis();
        Path list = listPath();
        if (!Files.exists(list)) {
            throw new IllegalStateException("no " + list + ": run targets/xbox360_ppc/tools/ghidra_setup.py first");
        }
        List<String[]> items = new ArrayList<>();
        List<long[]> spans = new ArrayList<>();
        AddressSet code = new AddressSet();
        for (String line : Files.readAllLines(list)) {
            String[] t = line.trim().split("\\s+");
            if (t[0].isEmpty() || t[0].startsWith("#")) continue;
            items.add(t);
            if (t[0].equals("F")) {
                code.add(range(num(t[1]), num(t[1]) + num(t[2])));
                spans.add(new long[] {num(t[1]), num(t[1]) + num(t[2])});
            } else if (t[0].equals("H")) {
                code.add(range(num(t[2]), num(t[3])));
                spans.add(new long[] {num(t[2]), num(t[3])});
            }
            else if (t[0].equals("I")) code.add(range(num(t[1]), num(t[1]) + 16));
        }
        int vectors = patchVector(spans);
        println("TcSetup: " + vectors + " vector instruction word(s) replaced by stand-ins");
        // Everything in the bodies starts clear, and the disassembly runs from every entry: a body only reached by a
        // function pointer is not reached by following the flow from the first range (Mega Man 10 packs its
        // functions, so their ranges merge).
        Listing lst = currentProgram.getListing();
        for (AddressRange rg : code) lst.clearCodeUnits(rg.getMinAddress(), rg.getMaxAddress(), false);
        DisassembleCommand dis = new DisassembleCommand(code, code, true);
        dis.applyTo(currentProgram, monitor);
        for (String[] t : items) {
            if (t[0].equals("F")) freeEntry(toAddr(num(t[1])), range(num(t[1]), num(t[1]) + num(t[2])));
            else if (t[0].equals("H")) freeEntry(toAddr(num(t[2])), range(num(t[2]), num(t[3])));
            else if (t[0].equals("I")) freeEntry(toAddr(num(t[1])), range(num(t[1]), num(t[1]) + 16));
        }
        FunctionManager fm = currentProgram.getFunctionManager();
        SymbolTable st = currentProgram.getSymbolTable();
        int funcs = 0, labels = 0, failed = 0;
        for (String[] t : items) {
            try {
                switch (t[0]) {
                    case "F":
                        fm.createFunction("fn_" + t[1], toAddr(num(t[1])), range(num(t[1]), num(t[1]) + num(t[2])),
                            SourceType.USER_DEFINED);
                        funcs++;
                        break;
                    case "H":
                        fm.createFunction("__" + t[1], toAddr(num(t[2])), range(num(t[2]), num(t[3])), SourceType.USER_DEFINED);
                        funcs++;
                        break;
                    case "L":
                        st.createLabel(toAddr(num(t[2])), t[1], SourceType.USER_DEFINED);
                        labels++;
                        break;
                    case "I":
                        fm.createFunction(TcBase.IMPORT_PREFIX + t[2] + "_" + t[3], toAddr(num(t[1])),
                            range(num(t[1]), num(t[1]) + 16), SourceType.USER_DEFINED);
                        funcs++;
                        break;
                    default:
                        throw new IllegalArgumentException("bad setup line: " + String.join(" ", t));
                }
            } catch (IllegalArgumentException e) {
                Address at = toAddr(num(t[t[0].equals("H") ? 2 : 1]));
                ghidra.program.model.listing.CodeUnit cu = currentProgram.getListing().getCodeUnitContaining(at);
                throw new IllegalArgumentException(String.join(" ", t) + ": " + e.getMessage() + " (code unit there: "
                    + (cu == null ? "none" : cu.getMinAddress() + " " + cu.getMnemonicString() + " len " + cu.getLength()
                        + " " + cu.getClass().getSimpleName()) + ")", e);
            } catch (Exception e) {
                failed++;
                if (failed <= 20) println("TcSetup: " + String.join(" ", t) + ": " + e.getMessage());
            }
        }
        if (freed > 0) println("TcSetup: " + freed + " function entr(ies) had not been reached by the first disassembly");
        println("TcSetup: " + funcs + " functions, " + labels + " helper labels, " + failed + " failed; "
            + getScriptArgs().length + " fixed register(s); analyzers left on: " + on);
        if (failed > 0) throw new IllegalStateException(failed + " setup item(s) failed (see above)");
    }
}

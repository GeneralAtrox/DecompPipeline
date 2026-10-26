// Prepares one PowerPC function for decompilation: work packet, test vectors and oracle results.
//
// Script args (as core/ghidra.prepare passes them for every target):
//   0 function address (hex)      1 packet dir       2 harness dir
//   3 dev vector count            4 hidden vector count
//   5 seed                        6 (unused here: the SH-4 fsca table)
//   7 marks file ("-" for none): lines "addr<TAB>status<TAB>note" written back as bookmarks
//   8 hints file ("-" for none): oracle/hints/<addr>.txt, input hints passed to TcInputs.applyHints
//   9 target facts file ("-" for none): the memory map, fixed registers and global ranges (TcFacts)
//
// LOCKED: the watcher agent may not change this file (it may only change the project's TcInputs.java and hints).
// The oracle (TcOracle) runs the ORIGINAL machine code in Ghidra's p-code emulator; calls leaving the function are
// not executed: the target and argument registers are recorded and scripted values are returned in r3 and f1.
//@category DecompPipeline
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.block.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.nio.file.*;
import java.util.*;

public class TcPrepare extends TcVectors {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        long entry = Long.parseLong(a[0], 16);
        Path packet = Paths.get(a[1]), harness = Paths.get(a[2]);
        int ndev = Integer.parseInt(a[3]), nhidden = Integer.parseInt(a[4]);
        long seed = TcStrict.start(Long.parseLong(a[5]));  // TcStrict: the junk's key
        String marks = a.length > 7 ? a[7] : "-";
        String hintsFile = a.length > 8 ? a[8] : "-";
        facts = TcFacts.load(a.length > 9 ? a[9] : "-");
        List<String> hints = hintsFile.equals("-") ? new ArrayList<>() : Files.readAllLines(Paths.get(hintsFile));
        Files.createDirectories(packet);
        Files.createDirectories(harness);
        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        fm = currentProgram.getFunctionManager();
        if (!marks.equals("-") && Files.exists(Paths.get(marks))) applyMarks(Paths.get(marks));
        fn = fm.getFunctionAt(toAddr(entry));
        if (fn == null) throw new IllegalArgumentException("no function at " + a[0]);
        body = fn.getBody();
        String name = "fn_" + hex(entry);
        findInline();

        BasicBlockModel bbm = new BasicBlockModel(currentProgram);
        CodeBlockIterator it = bbm.getCodeBlocksContaining(body, monitor);
        List<long[]> blocks = new ArrayList<>();
        while (it.hasNext()) {
            CodeBlock b = it.next();
            blocks.add(new long[] {b.getMinAddress().getOffset(), b.getMaxAddress().getOffset()});
        }
        blocks.sort(Comparator.comparingLong(x -> x[0]));
        Set<Long> labels = new HashSet<>();
        for (int i = 0; i < blocks.size(); i++) {
            blockIndex.put(blocks.get(i)[0], i);
            labels.add(blocks.get(i)[0]);
        }
        if (facts.branchCoverage || facts.targetedVectors) dirs = TcDirs.of(listing, body);  // TcDirs, TcBound
        Files.write(packet.resolve("asm.txt"), TcAsm.text(this, fn, body, labels).getBytes("UTF-8"));

        // the writable globals the code names (randomised by the vectors), the instructions the oracle refuses
        Set<Long> globals = new LinkedHashSet<>();
        Set<String> special = new TreeSet<>();
        String refuse = null;
        for (Instruction in : listing.getInstructions(body, true)) {
            String m = in.getMnemonicString().toLowerCase();
            if (m.matches("fctiwz?\\.?|fmadds?\\.?|fmsubs?\\.?|fnmadds?\\.?|fnmsubs?\\.?|frsp\\.?|fsel\\.?|cntlzw\\.?"
                + "|rlwimi\\.?|stfiwx|fres\\.?|frsqrte\\.?")) special.add(m.replace(".", ""));
            if (refuse == null) refuse = TcFixups.refuse(in, facts.fixed);
            for (long g : TcAsm.named(this, in)) {
                g = fold(g);
                if (facts.randomGlobal(g) && fm.getFunctionAt(toAddr(g)) == null && !codePointer(g)) globals.add(g);
            }
        }

        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        Proto proto = prototypeOf(di, fn);
        TcTrack.on = TcTrack.of(listing, body, calledBy(), proto);  // pointer calls' arguments, float NaN words
        DecompileResults dres = di.decompileFunction(fn, 60, monitor);
        String cText = dres.decompileCompleted() ? dres.getDecompiledFunction().getC()
            : "/* Ghidra's decompiler failed on this function (" + esc(String.valueOf(dres.getErrorMessage()).trim())
                + "). There is no decompiler output: translate asm.txt directly. The signature comes from the\n"
                + "   registers the code reads before writing them. */\n";
        Files.write(packet.resolve("ghidra.c"), cText.getBytes("UTF-8"));
        StringBuilder callees = new StringBuilder();
        Map<Long, List<String>> regsOf = new HashMap<>();  // TcStrict: each callee's compared registers
        for (Function c : calledBy()) {
            Proto cp = prototypeOf(di, c);
            StringBuilder regs = new StringBuilder();
            List<String> cr = calleeRegs(cp);
            regsOf.put(c.getEntryPoint().getOffset(), cr);
            for (String r : cr) {
                if (regs.length() > 0) regs.append(',');
                regs.append('"').append(r).append('"');
            }
            if (callees.length() > 0) callees.append(",\n    ");
            callees.append(String.format("{\"addr\":\"%s\",\"regs\":[%s],\"signature\":\"%s\"}",
                hex(c.getEntryPoint().getOffset()), regs, esc(cSignature("fn_" + hex(c.getEntryPoint().getOffset()), cp))));
        }
        TcStrict.callees(regsOf);  // mock_junk: where a CJ line can matter (TcStrict)
        di.dispose();
        if (proto.unsupported == null && refuse != null) proto.unsupported = refuse;

        StringBuilder params = new StringBuilder();
        for (Param q : proto.params) {
            if (params.length() > 0) params.append(',');
            params.append(String.format("{\"name\":\"%s\",\"kind\":\"%s\",\"storage\":\"%s\",\"stack_offset\":%d}",
                q.name, q.kind, q.storage, q.stackOffset));
        }
        StringBuilder gl = new StringBuilder();
        for (long g : globals) gl.append(gl.length() > 0 ? "," : "").append('"').append(hex(g)).append('"');
        StringBuilder bl = new StringBuilder();
        for (long[] b : blocks) bl.append(bl.length() > 0 ? "," : "").append(String.format("[\"%s\",\"%s\"]", hex(b[0]), hex(b[1])));
        StringBuilder sp = new StringBuilder();
        for (String s : special) sp.append(sp.length() > 0 ? "," : "").append('"').append(s).append('"');
        StringBuilder fx = new StringBuilder();
        for (Map.Entry<String, Long> e : facts.fixed.entrySet()) {
            fx.append(fx.length() > 0 ? "," : "").append(String.format("\"%s\":\"%s\"", e.getKey(), hex(e.getValue())));
        }
        String unsupported = proto.unsupported == null ? "null" : "\"" + esc(proto.unsupported) + "\"";
        String meta = String.format("{\n  \"addr\": \"%s\",\n  \"name\": \"%s\",\n  \"bytes\": %d,\n  \"signature\": \"%s\",\n"
                + "  \"params\": [%s],\n  \"ret\": \"%s\",\n  \"unsupported\": %s,\n  \"callees\": [%s],\n"
                + "  \"globals\": [%s],\n  \"blocks\": [%s],\n  \"ppc_special\": [%s],\n  \"fixed_regs\": {%s},\n"
                + "  \"cpu\": \"ppc\"%s\n}\n",
            hex(entry), name, body.getNumAddresses(), esc(cSignature(name, proto)), params, proto.ret, unsupported,
            callees, gl, bl, sp, fx, TcStrict.meta(facts));
        writeMeta(packet, harness, meta);
        if (proto.unsupported != null) {
            println("TcPrepare: unsupported: " + proto.unsupported);
            return;
        }

        makeVectors(proto, new ArrayList<>(globals), hints, seed, ndev, nhidden);
        String why = outside >= 0 ? "executes code outside its body at " + hex(outside)
            : refused != null ? refused
            : outOfTime ? String.format("oracle: vector generation did not finish in time (dev %d of %d tried valid,"
                + " hidden %d of %d)", valid[0], tried[0], valid[1], tried[1]) : null;
        if (why != null) {
            vout.setLength(0);
            eout.setLength(0);
            valid[0] = valid[1] = 0;
            covDev.clear();
            covAll.clear();
            dirsDev.clear();
            dirsAll.clear();
            blockHits.clear();
            meta = meta.replace("\"unsupported\": null,", "\"unsupported\": \"" + esc(why) + "\",");
            writeMeta(packet, harness, meta);
            println("TcPrepare: unsupported: " + why);
        }
        bound = why == null ? TcBoundPass.run(this, proto, seed, new ArrayList<>(globals)) : null;  // TcBound
        for (int s = 0; s < 2; s++) {  // chosen vectors whose strict replay failed: not written (TcStrict.replay)
            valid[s] -= TcStrict.dropped[s];
            if (TcStrict.dropped[s] > 0) invalid.merge("strict flags: the replay failed", TcStrict.dropped[s], Integer::sum);
        }
        Files.write(harness.resolve("vectors.txt"), (TcStrict.header(facts) + vout).getBytes("UTF-8"));  // J, U
        Files.write(harness.resolve("expected.txt"), eout.toString().getBytes("UTF-8"));
        Files.write(harness.resolve("oracle_summary.json"), summary(blocks, hints.size()).getBytes("UTF-8"));
        println("TcPrepare: " + summary(blocks, hints.size()).trim());
    }

    void writeMeta(Path packet, Path harness, String meta) throws Exception {
        Files.write(packet.resolve("meta.json"), meta.getBytes("UTF-8"));
        Files.write(harness.resolve("meta.json"), meta.getBytes("UTF-8"));
    }

    String bound;  // the targeted-vector pass's summary fragment (TcBoundPass), or null

    String summary(List<long[]> blocks, int nhints) {
        StringBuilder inv = new StringBuilder();
        for (Map.Entry<String, Integer> e : invalid.entrySet()) {
            inv.append(inv.length() > 0 ? "," : "").append('"').append(esc(e.getKey())).append("\":").append(e.getValue());
        }
        StringBuilder ex = new StringBuilder();
        for (String e : examples) ex.append(ex.length() > 0 ? "," : "").append('"').append(esc(e)).append('"');
        StringBuilder sites = new StringBuilder();
        for (Map.Entry<Long, Integer> e : siteHits.entrySet()) {
            sites.append(sites.length() > 0 ? "," : "").append('"').append(hex(e.getKey())).append("\":").append(e.getValue());
        }
        StringBuilder unhit = new StringBuilder();
        for (int i = 0; valid[0] + valid[1] > 0 && i < blocks.size(); i++) {
            if (!covAll.contains(i)) unhit.append(unhit.length() > 0 ? "," : "").append('"').append(hex(blocks.get(i)[0])).append('"');
        }
        return String.format("{\"dev_valid\":%d,\"dev_tried\":%d,\"hidden_valid\":%d,\"hidden_tried\":%d,"
                + "\"blocks_total\":%d,\"blocks_hit_dev\":%d,\"blocks_hit_all\":%d,\"min_block_hits\":%d,"
                + "\"repaired\":%d,\"fixups\":%d,%s\"hints\":%d,\"call_sites\":{%s},\"invalid\":{%s},\"examples\":[%s]}\n",
            valid[0], tried[0], valid[1], tried[1], blocks.size(), covDev.size(), covAll.size(),
            blockHits.isEmpty() ? 0 : Collections.min(blockHits.values()), repaired, fix.fixups,
            (unhit.length() > 0 ? "\"blocks_unhit\":[" + unhit + "]," : "") + (bound != null ? bound + "," : "")
            + (dirs != null && valid[0] + valid[1] > 0 ? dirs.summary(dirsDev, dirsAll) : ""), nhints, sites, inv, ex);
    }

    // The functions fn calls or tail-branches to (save helpers excepted: they run inline), sorted by entry.
    List<Function> calledBy() {
        List<Function> called = new ArrayList<>();
        for (Function c : fn.getCalledFunctions(monitor)) {
            if (!TcFlow.isSaveHelper(listing, c.getEntryPoint())) called.add(c);
        }
        for (Instruction in : listing.getInstructions(body, true)) {
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isJump()) continue;
                Function t = fm.getFunctionAt(r.getToAddress());
                if (t != null && t != fn && !called.contains(t) && !body.contains(r.getToAddress())) called.add(t);
            }
        }
        called.sort(Comparator.comparing(Function::getEntryPoint));
        return called;
    }

    // The registers compared at a call to a callee with prototype cp: the argument registers it reads (TcFlow, so
    // a parameter Ghidra left out still counts), and sN for a stack word at r1+8+4N.
    List<String> calleeRegs(Proto cp) {
        List<String> out = new ArrayList<>();
        Set<String> regs = new TreeSet<>(Comparator.comparing((String r) -> r.charAt(0)).thenComparingInt(r -> Integer.parseInt(r.substring(1))));
        for (Param q : cp.params) if (q.storage.matches(ARG_REG)) regs.add(q.storage);
        regs.addAll(cp.inputs);
        out.addAll(regs);
        for (Param q : cp.params) {
            if (q.storage.equals("stack") && q.stackOffset >= 8 && q.stackOffset < 40 && q.stackOffset % 4 == 0) {
                out.add("s" + (q.stackOffset - 8) / 4);
            }
        }
        return out;
    }

    void applyMarks(Path marks) throws Exception {
        BookmarkManager bm = currentProgram.getBookmarkManager();
        for (String line : Files.readAllLines(marks)) {
            String[] p = line.split("\t", 3);
            if (p.length < 3) continue;
            Address ad = toAddr(Long.parseLong(p[0], 16));
            for (Bookmark b : bm.getBookmarks(ad)) {
                if (b.getCategory().startsWith("TC ")) bm.removeBookmark(b);
            }
            bm.setBookmark(ad, BookmarkType.NOTE, "TC " + p[1], p[2]);
            listing.setComment(ad, CodeUnit.PLATE_COMMENT, "TC decomp: " + p[1] + "\n" + p[2]);
        }
    }

    // A word that holds a code address (a switch table, a function-pointer table): never randomised, or the code
    // would jump to random addresses the original never reaches.
    boolean codePointer(long g) {
        try {
            return facts.isCode(fold(currentProgram.getMemory().getInt(toAddr(g)) & 0xffffffffL));
        } catch (Exception e) {
            return false;
        }
    }
}

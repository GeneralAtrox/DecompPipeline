// Prepares one Xbox 360 function for decompilation: work packet, test vectors and oracle results.
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
// After the vectors, the width probes (TcVectors) may widen 32-bit parameters and the result to 64 bits; meta.json
// is written again with the final signature.
//@category DecompPipeline
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.block.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.symbol.*;
import java.nio.file.*;
import java.util.*;

public class TcPrepare extends TcVectors {
    static final String SPECIAL = "addc|adde|subfc|subfe|cntlz[wd]|div[wd]u?|mulh[wd]u?|fcfid|fcti[wd]z?|f(n?)m(add|sub)s?"
        + "|frsp|fsel|fsqrts?|rlwimi|rldimi|s[lr][wd]|sra[wd]i?|stfsu?x?|stfiwx";

    Map<String, Object> metaFields = new LinkedHashMap<>();

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
        if (facts.branchCoverage || facts.targetedVectors) dirs = TcDirs.of(listing, body);  // TcDirs, TcBound

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
        Files.write(packet.resolve("asm.txt"), TcAsm.text(this, fn, body, labels).getBytes("UTF-8"));

        // the writable globals the code names (randomised by the vectors), the instructions the oracle refuses
        Set<Long> globals = new LinkedHashSet<>();
        Set<String> special = new TreeSet<>(), helpers = new TreeSet<>();
        String refuse = null;
        Map<Long, Long> pairs = TcAsm.lisPairs(this, body, labels);
        for (Instruction in : listing.getInstructions(body, true)) {
            String m = in.getMnemonicString().toLowerCase().replace(".", "");
            if (m.matches(SPECIAL)) special.add(m.matches("stfsu?x?") ? "stfs" : m);
            if (refuse == null) refuse = TcFixups.refuse(in);
            for (Address to : in.getFlows()) {
                if (isHelper(fm.getFunctionContaining(to))) helpers.add(currentProgram.getSymbolTable().getPrimarySymbol(to).getName());
            }
            for (long g : TcAsm.named(this, in, pairs)) {
                if (facts.randomGlobal(g) && fm.getFunctionAt(toAddr(g)) == null && !codePointer(g)) globals.add(g);
            }
        }

        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        Proto proto = prototypeOf(di, fn);
        DecompileResults dres = di.decompileFunction(fn, 60, monitor);
        String cText = dres.decompileCompleted() ? dres.getDecompiledFunction().getC()
            : "/* Ghidra's decompiler failed on this function (" + esc(String.valueOf(dres.getErrorMessage()).trim())
                + "). There is no decompiler output: translate asm.txt directly. The signature comes from the\n"
                + "   registers the code reads before writing them. */\n";
        Files.write(packet.resolve("ghidra.c"), cText.getBytes("UTF-8"));
        Map<Function, Proto> called = new LinkedHashMap<>();
        for (Function c : calledBy()) called.put(c, c == fn ? proto : isImport(c) ? importProto(c) : prototypeOf(di, c));
        passThrough(proto, called);
        TcTrack.on = TcTrack.of(listing, body, calledBy(), proto, flow);  // pointer calls' arguments, float NaN words
        StringBuilder callees = new StringBuilder();
        Map<Long, List<String>> regsOf = new HashMap<>();  // TcStrict: each callee's compared registers
        for (Function c : called.keySet()) {
            Proto cp = called.get(c);
            StringBuilder regs = new StringBuilder();
            regsOf.put(c.getEntryPoint().getOffset(), calleeRegs(cp));
            for (String r : calleeRegs(cp)) regs.append(regs.length() > 0 ? "," : "").append('"').append(r).append('"');
            if (callees.length() > 0) callees.append(",\n    ");
            String cname = isImport(c) ? c.getName() : "fn_" + hex(c.getEntryPoint().getOffset());
            List<String> keeps = keeps(c);
            callees.append(String.format("{\"addr\":\"%s\",\"regs\":[%s],\"signature\":\"%s\"%s%s}",
                hex(c.getEntryPoint().getOffset()), regs, esc(cSignature(cname, cp)),
                isImport(c) ? ",\"import\":\"" + esc(c.getName().substring(IMPORT_PREFIX.length())) + "\"" : "",
                keeps.isEmpty() ? "" : ",\"keeps\":" + jsonList(keeps)));
        }
        di.dispose();
        TcStrict.callees(regsOf);  // mock_junk: where a CJ line can matter (TcStrict)
        if (proto.unsupported == null && refuse != null) proto.unsupported = refuse;

        StringBuilder gl = new StringBuilder();
        for (long g : globals) gl.append(gl.length() > 0 ? "," : "").append('"').append(hex(g)).append('"');
        StringBuilder bl = new StringBuilder();
        for (long[] b : blocks) bl.append(bl.length() > 0 ? "," : "").append(String.format("[\"%s\",\"%s\"]", hex(b[0]), hex(b[1])));
        StringBuilder fx = new StringBuilder();
        for (Map.Entry<String, Long> e : facts.fixed.entrySet()) {
            fx.append(fx.length() > 0 ? "," : "").append(String.format("\"%s\":\"%s\"", e.getKey(), hex(e.getValue())));
        }
        metaFields.put("addr", hex(entry));
        metaFields.put("name", name);
        metaFields.put("bytes", body.getNumAddresses());
        metaFields.put("callees", "[" + callees + "]");
        metaFields.put("globals", "[" + gl + "]");
        metaFields.put("blocks", "[" + bl + "]");
        metaFields.put("ppc_special", jsonList(special));
        metaFields.put("frame_helpers", jsonList(helpers));
        metaFields.put("fixed_regs", "{" + fx + "}");
        writeMeta(packet, harness, proto, null);
        if (proto.unsupported != null) {
            println("TcPrepare: unsupported: " + proto.unsupported);
            return;
        }

        makeVectors(proto, new ArrayList<>(globals), hints, seed, ndev, nhidden);
        String why = outside >= 0 ? "executes code outside its body at " + hex(outside)
            : refused != null ? "unsupported: " + refused
            : outOfTime ? String.format("oracle: vector generation did not finish in time (dev %d of %d tried valid,"
                + " hidden %d of %d)", valid[0], tried[0], valid[1], tried[1]) : null;
        List<String> widened = new ArrayList<>();
        bound = why == null ? TcBoundPass.run(this, proto, seed, new ArrayList<>(globals)) : null;  // before the widths
        if (why != null) {
            vout.setLength(0);
            eout.setLength(0);
            valid[0] = valid[1] = 0;
            covDev.clear();
            covAll.clear();
            dirsDev.clear();
            dirsAll.clear();
            blockHits.clear();
            println("TcPrepare: unsupported: " + why);
        } else {
            for (Param q : probeWidths(proto)) {
                widened.add(q.name + " " + q.kind + "->u64");
                q.kind = "u64";
            }
            String rk = retKind(proto.ret);
            if (!rk.equals(proto.ret)) widened.add("return " + proto.ret + "->" + rk);
            proto.ret = rk;
        }
        for (int s = 0; s < 2; s++) {  // chosen vectors whose strict replay failed: not written (TcStrict.replay)
            valid[s] -= TcStrict.dropped[s];
            if (TcStrict.dropped[s] > 0) invalid.merge("strict flags: the replay failed", TcStrict.dropped[s], Integer::sum);
        }
        writeMeta(packet, harness, proto, why);
        Files.write(harness.resolve("vectors.txt"), (TcStrict.header(facts) + vout).getBytes("UTF-8"));  // J, U
        Files.write(harness.resolve("expected.txt"), eout.toString().getBytes("UTF-8"));
        String sum = summary(blocks, hints.size(), widened);
        Files.write(harness.resolve("oracle_summary.json"), sum.getBytes("UTF-8"));
        println("TcPrepare: " + sum.trim());
    }

    static String jsonList(Collection<String> xs) {
        StringBuilder b = new StringBuilder("[");
        for (String x : xs) b.append(b.length() > 1 ? "," : "").append('"').append(esc(x)).append('"');
        return b.append(']').toString();
    }

    void writeMeta(Path packet, Path harness, Proto proto, String why) throws Exception {
        StringBuilder params = new StringBuilder();
        for (Param q : proto.params) {
            if (params.length() > 0) params.append(',');
            params.append(String.format("{\"name\":\"%s\",\"kind\":\"%s\",\"storage\":\"%s\",\"stack_offset\":%d}",
                q.name, q.kind, q.storage, q.stackOffset));
        }
        String unsup = why != null ? why : proto.unsupported;
        StringBuilder m = new StringBuilder("{\n");
        m.append(String.format("  \"addr\": \"%s\",\n  \"name\": \"%s\",\n  \"bytes\": %s,\n  \"signature\": \"%s\",\n",
            metaFields.get("addr"), metaFields.get("name"), metaFields.get("bytes"),
            esc(cSignature((String) metaFields.get("name"), proto))));
        m.append("  \"params\": [").append(params).append("],\n  \"ret\": \"").append(proto.ret).append("\",\n");
        m.append("  \"unsupported\": ").append(unsup == null ? "null" : "\"" + esc(unsup) + "\"").append(",\n");
        for (String k : new String[] {"callees", "globals", "blocks", "ppc_special", "frame_helpers", "fixed_regs"}) {
            m.append("  \"").append(k).append("\": ").append(metaFields.get(k)).append(",\n");
        }
        m.append("  \"cpu\": \"ppc64\"").append(TcStrict.meta(facts)).append("\n}\n");
        Files.write(packet.resolve("meta.json"), m.toString().getBytes("UTF-8"));
        Files.write(harness.resolve("meta.json"), m.toString().getBytes("UTF-8"));
    }

    String bound;  // the targeted-vector pass's summary fragment (TcBoundPass), or null

    String summary(List<long[]> blocks, int nhints, List<String> widened) {
        StringBuilder inv = new StringBuilder();
        for (Map.Entry<String, Integer> e : invalid.entrySet()) {
            inv.append(inv.length() > 0 ? "," : "").append('"').append(esc(e.getKey())).append("\":").append(e.getValue());
        }
        StringBuilder sites = new StringBuilder();
        for (Map.Entry<Long, Integer> e : siteHits.entrySet()) {
            sites.append(sites.length() > 0 ? "," : "").append('"').append(hex(e.getKey())).append("\":").append(e.getValue());
        }
        List<String> unhit = new ArrayList<>();
        for (int i = 0; valid[0] + valid[1] > 0 && i < blocks.size(); i++) if (!covAll.contains(i)) unhit.add(hex(blocks.get(i)[0]));
        return String.format("{\"dev_valid\":%d,\"dev_tried\":%d,\"hidden_valid\":%d,\"hidden_tried\":%d,"
                + "\"blocks_total\":%d,\"blocks_hit_dev\":%d,\"blocks_hit_all\":%d,\"min_block_hits\":%d,"
                + "\"repaired\":%d,\"fixups\":%d,%s\"fma_checked\":%d,\"fma_sensitive\":%d,\"widened\":%s,"
                + "\"hints\":%d,\"call_sites\":{%s},\"invalid\":{%s},\"examples\":%s}\n",
            valid[0], tried[0], valid[1], tried[1], blocks.size(), covDev.size(), covAll.size(),
            blockHits.isEmpty() ? 0 : Collections.min(blockHits.values()), repaired, fix.fixups,
            (unhit.isEmpty() ? "" : "\"blocks_unhit\":" + jsonList(unhit) + ",") + (bound != null ? bound + "," : "")
            + (dirs != null && valid[0] + valid[1] > 0 ? dirs.summary(dirsDev, dirsAll) : ""), fmaChecked, fmaSensitive,
            jsonList(widened), nhints, sites, inv, jsonList(examples));
    }

    // The functions fn calls or tail-branches to (helper families excepted: they run inline), sorted by entry; fn
    // itself when it calls its own entry (TcOracle mocks that call like any other).
    List<Function> calledBy() {
        Set<Function> called = new TreeSet<>(Comparator.comparing(Function::getEntryPoint));
        for (Instruction in : listing.getInstructions(body, true)) {
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isFlow() || body.contains(r.getToAddress()) && !selfCall(r)) continue;
                Function t = fm.getFunctionAt(r.getToAddress());
                if (t != null && !isHelper(t)) called.add(t);
            }
        }
        return new ArrayList<>(called);
    }

    // The volatile registers a bl to c leaves as they were (TcFlow.clobbers), its results r3 and f1 aside; meta.json
    // "keeps" (context.md lists them). The oracle's mock keeps them too (TcOracle.clobber).
    static final List<String> KEEP_ORDER = new ArrayList<>();

    static {
        KEEP_ORDER.add("r0");
        for (int i = 4; i <= 12; i++) KEEP_ORDER.add("r" + i);
        KEEP_ORDER.add("f0");
        for (int i = 2; i <= 13; i++) KEEP_ORDER.add("f" + i);
        KEEP_ORDER.add("ctr");
    }

    List<String> keeps(Function c) {
        if (flow == null) flow = new TcFlow(currentProgram, monitor);
        Set<String> w = flow.clobbers(c.getEntryPoint());
        List<String> out = new ArrayList<>();
        for (String r : KEEP_ORDER) if (!w.contains(r)) out.add(r);
        return out;
    }

    // The registers fn passes on untouched to a call that compares them (the shared rule, TcPassThrough) are inputs as
    // well, added as param_rN like the other registers Ghidra's signature misses. fn's own entry, when it calls
    // itself, compares the registers fn takes so far.
    void passThrough(Proto proto, Map<Function, Proto> called) throws Exception {
        TcPassThrough.take(fn, flow, ARG_REG, () -> {
            Map<Address, Set<String>> at = new HashMap<>();
            for (Function c : called.keySet()) at.put(c.getEntryPoint(), new HashSet<>(calleeRegs(called.get(c))));
            return at;
        }, r -> {
            for (Param q : proto.params) if (q.storage.equals(r)) return false;
            if (!proto.inputs.add(r)) return false;
            Param q = new Param();
            q.name = "param_" + r;
            q.kind = r.startsWith("f") ? "f64" : "u64";
            q.storage = r;
            proto.params.add(q);
            proto.paramIsPointer.add(false);
            return true;
        });
    }

    // An import's compared registers: the argument registers the caller writes between the previous call (or the
    // entry) and each call to the thunk. The thunk itself reads nothing (TcFlow), so the C passes exactly these.
    Proto importProto(Function thunk) {
        if (flow == null) flow = new TcFlow(currentProgram, monitor);
        Proto p = new Proto();
        p.ret = "u64";
        for (Instruction in : listing.getInstructions(body, true)) {
            boolean site = false;
            for (Address to : in.getFlows()) site |= to.equals(thunk.getEntryPoint());
            if (!site || !in.getFlowType().isCall()) continue;
            Instruction b = listing.getInstructionBefore(in.getAddress());
            for (int n = 0; b != null && body.contains(b.getAddress()) && n < 64; n++) {
                if (b.getFlowType().isCall()) break;
                if (!TcFlow.identityMove(TcFlow.word(b))) {
                    for (PcodeOp op : b.getPcode()) {
                        String r = flow.nameOf(op.getOutput());
                        if (r != null && r.matches(ARG_REG)) p.inputs.add(r);
                    }
                }
                if (b.getAddress().equals(fn.getEntryPoint())) break;
                b = listing.getInstructionBefore(b.getAddress());
            }
        }
        for (String r : p.inputs) {
            Param q = new Param();
            q.name = r;
            q.kind = r.startsWith("f") ? "f64" : "u64";
            q.storage = r;
            p.params.add(q);
            p.paramIsPointer.add(false);
        }
        return p;
    }

    // The registers compared at a call to a callee with prototype cp: the argument registers it reads (TcFlow, so
    // a parameter Ghidra left out still counts), and sN for a stack doubleword at r1+0x50+8N.
    List<String> calleeRegs(Proto cp) {
        Set<String> regs = new TreeSet<>(Comparator.comparing((String r) -> r.charAt(0)).thenComparingInt(r -> Integer.parseInt(r.substring(1))));
        for (Param q : cp.params) if (q.storage.matches(ARG_REG)) regs.add(q.storage);
        regs.addAll(cp.inputs);
        List<String> out = new ArrayList<>(regs);
        for (Param q : cp.params) if (q.storage.equals("stack") && q.stackOffset >= 0) out.add("s" + (q.stackOffset - STACK_ARGS) / 8);
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
            return facts.isCode(currentProgram.getMemory().getInt(toAddr(g)) & 0xffffffffL);
        } catch (Exception e) {
            return false;
        }
    }
}

// Prepares one function for decompilation: work packet, test vectors and oracle results.
//
// Script args:
//   0 function address (hex)      1 packet dir       2 harness dir
//   3 dev vector count            4 hidden vector count
//   5 seed                        6 fsca table (fsca_table.bin)
//   7 marks file ("-" for none): lines "addr<TAB>status<TAB>note" written back as bookmarks
//   8 hints file ("-" for none): oracle/hints/<addr>.txt, input hints passed to TcInputs.applyHints
//   9 target facts file ("-" for none): the calling convention and memory map (TcFacts, written by core/ghidra.py)
//  10 "spin" when project.json "spin_waits" lists the function (TcSpin applies), "spin:w_..." with the words its
//     evidence names (compare.spin_arg), else "-" or absent
//  11 "hw:..." when project.json "hw_ops" lists the function: its listed hardware ops (TcHw, core/hw_ops.arg), else
//     "-" or absent
//
// LOCKED: the watcher agent may not change this file (it may only change the project's TcInputs.java and hints).
//
// The oracle runs the ORIGINAL machine code in Ghidra's p-code emulator (patched
// SuperH4:LE:32:tcfix language). Calls leaving the function are not executed: the
// target and argument registers are recorded and a scripted value is returned in r0/fr0.
//@category DecompPipeline
import ghidra.app.decompiler.*;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.script.GhidraScript;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryState;
import ghidra.pcode.pcoderaw.PcodeOpRaw;
import ghidra.program.model.address.*;
import ghidra.program.model.block.*;
import ghidra.program.model.data.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.pcode.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.math.BigInteger;
import java.nio.file.*;
import java.util.*;

public class TcPrepare extends TcVectors {
    // ---------- main ----------
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        long entry = Long.parseLong(a[0], 16);
        Path packet = Paths.get(a[1]), harness = Paths.get(a[2]);
        int ndev = Integer.parseInt(a[3]), nhidden = Integer.parseInt(a[4]);
        long seed = TcStrict.seed = Long.parseLong(a[5]);  // TcStrict: the junk's key
        Path fsca = Paths.get(a[6]);
        String marks = a.length > 7 ? a[7] : "-";
        String hintsFile = a.length > 8 ? a[8] : "-";
        facts = TcFacts.load(a.length > 9 ? a[9] : "-");
        frameLo = facts.frameLo;
        spinWait = a.length > 10 && a[10].startsWith("spin");
        if (spinWait && a[10].startsWith("spin:")) for (String w : a[10].substring(5).split("_")) spinWords.add(Long.parseLong(w, 16));
        TcHw.on = TcHw.parse(a.length > 11 ? a[11] : "-");  // listed hardware ops (TcHw), null: none (every prepare)
        List<String> hints = hintsFile.equals("-") ? new ArrayList<>() : Files.readAllLines(Paths.get(hintsFile));
        Files.createDirectories(packet);
        Files.createDirectories(harness);

        mem = currentProgram.getMemory();
        listing = currentProgram.getListing();
        fm = currentProgram.getFunctionManager();

        if (!marks.equals("-") && Files.exists(Paths.get(marks))) applyMarks(Paths.get(marks));

        fn = fm.getFunctionAt(toAddr(entry));
        if (fn == null) throw new IllegalArgumentException("no function at " + a[0]);
        body = TcTail.extent(currentProgram, fn);  // its body and the shared tails it runs: listed, blocked, covered, run
        TcTrack.on = null;  // set once the prototype is known
        String name = "fn_" + a[0].toLowerCase();

        // blocks
        BasicBlockModel bbm = new BasicBlockModel(currentProgram);
        CodeBlockIterator it = bbm.getCodeBlocksContaining(body, monitor);
        List<long[]> blocks = new ArrayList<>();
        while (it.hasNext()) {
            CodeBlock b = it.next();
            blocks.add(new long[] {b.getMinAddress().getOffset(), b.getMaxAddress().getOffset()});
        }
        blocks.sort(Comparator.comparingLong(x -> x[0]));
        for (int i = 0; i < blocks.size(); i++) blockIndex.put(blocks.get(i)[0], i);
        if (facts.branchCoverage || facts.targetedVectors) dirs = TcDirs.of(listing, body);  // TcDirs, TcBound

        // fsca table (core/fsca.py): 2 x 65536 x (sin bits, cos bits), little endian; round to nearest, toward zero
        byte[] tb = Files.readAllBytes(fsca);
        if (tb.length != 2 * 65536 * 8)
            throw new IllegalStateException("fsca table " + fsca + ": " + tb.length + " bytes, not " + 2 * 65536 * 8
                + " (both tables: python -m core.fsca)");
        java.nio.ByteBuffer bb = java.nio.ByteBuffer.wrap(tb).order(java.nio.ByteOrder.LITTLE_ENDIAN);
        for (int t = 0; t < 2; t++) {
            for (int i = 0; i < 65536; i++) {
                fscaSin[t][i] = bb.getInt();
                fscaCos[t][i] = bb.getInt();
            }
        }
        for (int i = 0; i < 65536; i++) {
            float angle = (Float.intBitsToFloat(0x40c90fdb) * (float) i) / 65536f;
            angleToFraction.put(Float.floatToIntBits(angle), i);
        }

        // disassembly + constants + globals
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        StringBuilder asm = new StringBuilder();
        asm.append("; ").append(name).append("  entry ").append(fn.getEntryPoint()).append("  ")
            .append(fn.getBody().getNumAddresses()).append(" bytes\n");
        asm.append("; SH-2 big endian. A mnemonic starting with '_' executes in the delay slot of the branch above it.\n");
        if (!body.equals(fn.getBody())) {
            asm.append("; It also runs shared tails: code outside its own body that it branches into, marked below.\n")
                .append("; That code is part of this function: implement it here, not as a call.\n");
        }
        Set<Long> globals = new LinkedHashSet<>();
        Set<String> sh4Special = new TreeSet<>();
        boolean usesXf = false;
        Function region = fn;
        for (Instruction in : listing.getInstructions(body, true)) {
            long ia = in.getAddress().getOffset();
            Function owner = fn.getBody().contains(in.getAddress()) ? fn : getFunctionContaining(in.getAddress());
            if (owner != region) {  // TcTail
                asm.append(owner == fn ? "; own code\n" : "; shared tail: code of "
                    + (owner == null ? "no function" : "fn_" + hex(owner.getEntryPoint().getOffset())) + "\n");
                region = owner;
            }
            if (blockIndex.containsKey(ia) && ia != entry) asm.append(String.format("LAB_%s:\n", hex(ia)));
            String text = in.toString();
            String m = in.getMnemonicString().toLowerCase().replace("_", "");
            if (m.matches("fsca|fsrra|fsqrt|fmac|ftrv|fipr|frchg|fschg|fpchg")) sh4Special.add(m); // agent.py: helpers
            if (TcSr.op(in) != null) sh4Special.add(TcSr.op(in));  // stc sr, ldc sr: sh4_stc_sr, sh4_ldc_sr
            if (TcHw.on != null && TcHw.on.special(in) != null) sh4Special.add(TcHw.on.special(in));  // gbr, cache: TcHw
            if (m.matches("ftrv|frchg") || text.toLowerCase().contains("xd") || text.toLowerCase().contains("xf")) usesXf = true;
            String note = "";
            Long pv = poolValue(in);
            if (pv != null) {
                note = "   ; = 0x" + hex(pv) + "  (" + describeConst(pv) + ")";
                long v = fold(pv);
                if (inRam(v, 1) && fm.getFunctionAt(toAddr(v)) == null
                    && (!mem.getBlock(toAddr(v)).isInitialized() || readString(toAddr(v)) == null)
                    && listing.getInstructionAt(toAddr(v)) == null) {
                    globals.add(v);
                }
            }
            for (Reference r : in.getReferencesFrom()) {
                if (r.getReferenceType().isCall()) note += "   ; call " + r.getToAddress();
            }
            Address prTo = TcTail.prTarget(currentProgram, in);
            if (prTo != null) note += "   ; PR = " + prTo + ": the next jump's callee returns there (code of this function)";
            asm.append(String.format("  %s  %-34s%s\n", hex(ia), text, note));
        }
        Files.write(packet.resolve("asm.txt"), asm.toString().getBytes("UTF-8"));

        Proto proto = prototypeOf(di, fn);
        String hwWhy = TcHw.on == null || proto.unsupported != null ? null : TcHw.on.check(this, body);
        if (hwWhy != null) {  // a hardware op the listing does not name: as unsupported as before (TcHw)
            proto = copyOf(proto);
            proto.unsupported = hwWhy;
        }
        callWide |= proto.fpuState;  // TcOracle: the 57-field call record (r0..r3, r8..r14, then the FPU)
        DecompileResults dres = di.decompileFunction(fn, 60, monitor);
        String cText = dres.decompileCompleted() ? dres.getDecompiledFunction().getC()
            : "/* Ghidra's decompiler failed on this function (" + esc(String.valueOf(dres.getErrorMessage()).trim())
                + "). There is no decompiler output: translate asm.txt directly. The signature comes from the\n"
                + "   registers the code reads before writing them. */\n";
        Files.write(packet.resolve("ghidra.c"), cText.getBytes("UTF-8"));

        // callees: registers that carry their parameters (compared at each call)
        StringBuilder callees = new StringBuilder();
        Map<Long, List<String>> regsOf = new HashMap<>();  // TcStrict: each callee's compared registers
        for (Function c : calledBy(fn, body)) {
            Proto cp = prototypeOf(di, c, PASS_DEPTH - 1);  // what fn's pass-through analysis assumed
            if (cp.ret.equals("f32")) floatRet.add(c.getEntryPoint().getOffset());  // TcFret
            StringBuilder regs = new StringBuilder();
            List<String> cr = calleeRegs(cp, c);
            regsOf.put(c.getEntryPoint().getOffset(), cr);
            for (String r : cr) {
                if (regs.length() > 0) regs.append(',');
                regs.append('"').append(r).append('"');
                callWide |= r.matches("r([0-3]|[89]|1[0-4])");  // TcOracle: the 36-field call record
            }
            if (callees.length() > 0) callees.append(",\n    ");
            callees.append(String.format("{\"addr\":\"%s\",\"regs\":[%s],\"signature\":\"%s\"}",
                hex(c.getEntryPoint().getOffset()), regs, esc(cSignature("fn_" + hex(c.getEntryPoint().getOffset()), cp))));
        }
        live = facts.paramSummaries ? TcLive.of(this, calledBy(fn, body)) : null;  // callees changing r8..r14
        TcTrack.on = TcTrack.of(listing, body, calledBy(fn, body), proto);  // pointer calls' arguments
        TcStrict.callees(this, calledBy(fn, body), regsOf);  // mock_junk: what each callee may change (TcStrict)
        sq = TcSq.of(listing, body, proto.giState);  // a state pointer whose store queue the function sends
        sr = TcSr.of(listing, body);  // SR read or written (TcSr)
        di.dispose();

        StringBuilder params = new StringBuilder();
        for (Param q : proto.params) {
            if (params.length() > 0) params.append(',');
            params.append(String.format("{\"name\":\"%s\",\"kind\":\"%s\",\"storage\":\"%s\",\"stack_offset\":%d}",
                q.name, q.kind, q.storage, q.stackOffset));
        }
        StringBuilder gl = new StringBuilder();
        for (long g : globals) {
            if (gl.length() > 0) gl.append(',');
            gl.append('"').append(hex(g)).append('"');
        }
        StringBuilder bl = new StringBuilder();
        for (long[] b : blocks) {
            if (bl.length() > 0) bl.append(',');
            bl.append(String.format("[\"%s\",\"%s\"]", hex(b[0]), hex(b[1])));
        }
        StringBuilder sp = new StringBuilder();
        for (String s : sh4Special) {
            if (sp.length() > 0) sp.append(',');
            sp.append('"').append(s).append('"');
        }
        StringBuilder fs = new StringBuilder();  // fr12..fr15 runtime state: set at entry, compared at exit
        for (int b = 12; b < 16; b++) {
            if ((proto.frState & (1 << b)) == 0) continue;
            if (fs.length() > 0) fs.append(',');
            fs.append("\"fr").append(b).append('"');
        }
        String unsupported = proto.unsupported == null ? "null" : "\"" + esc(proto.unsupported) + "\"";
        StringBuilder extra = new StringBuilder();  // keys only functions that need them get (other metas unchanged)
        if (proto.retReg != null) extra.append(",\n  \"ret_reg\": \"").append(proto.retReg).append('"');
        if (proto.giState != 0) {
            extra.append(",\n  \"r_state\": [\"").append(TcSig.names(proto.giState).replace(", ", "\",\"")).append("\"]");
        }
        if (live != null) extra.append(",\n  \"r_live\": ").append(live.meta());  // TcLive
        if (sq != null) extra.append(",\n  \"sq_window\": ").append(sq.meta());  // TcSq
        if (proto.fpuState) extra.append(",\n  \"fpu_state\": true");  // TcSig.fpuProto: the whole FPU is state
        if (TcHw.on != null) extra.append(",\n  \"hw_ops\": ").append(TcHw.on.meta());  // listed hardware ops (TcHw)
        extra.append(TcStrict.meta(facts));  // the facts mock_writes wrote from (TcStrict)
        String meta = String.format("{\n  \"addr\": \"%s\",\n  \"name\": \"%s\",\n  \"bytes\": %d,\n  \"signature\": \"%s\",\n"
                + "  \"params\": [%s],\n  \"ret\": \"%s\",\n  \"unsupported\": %s,\n  \"callees\": [%s],\n"
                + "  \"globals\": [%s],\n  \"blocks\": [%s],\n  \"sh4_special\": [%s],\n  \"uses_xf\": %s,\n"
                + "  \"fr_state\": [%s]%s\n}\n",
            hex(entry), name, fn.getBody().getNumAddresses(), esc(cSignature(name, proto)), params, proto.ret, unsupported,
            callees, gl, bl, sp, usesXf, fs, extra);
        Files.write(packet.resolve("meta.json"), meta.getBytes("UTF-8"));
        Files.write(harness.resolve("meta.json"), meta.getBytes("UTF-8"));
        if (proto.unsupported != null) {
            println("TcPrepare: unsupported: " + proto.unsupported);
            return;
        }

        // vectors + oracle (TcVectors)
        for (long g : globals) namedWords.add(g & ~3L);
        makeVectors(proto, new ArrayList<>(globals), hints, seed, ndev, nhidden);
        String why = outside >= 0 ? "executes code outside its body at " + hex(outside) : outOfTime ? String.format(
            "oracle: vector generation did not finish in time (dev %d of %d tried valid, hidden %d of %d; step limit x%d:"
            + " a loop count from the inputs?)%s", valid[0], tried[0], valid[1], tried[1],
            invalid.getOrDefault("step limit (possible infinite loop)", 0), overtimeNote)
            : capNote != null && (valid[0] < Math.min(facts.minValid, ndev)
                || valid[1] < Math.min(facts.minValid, nhidden)) ? capNote : null;
        if (why != null) {  // refused (code outside its body, out of time, or the call-cap fail-fast): no vector left
            vout.setLength(0);
            eout.setLength(0);
            valid[0] = valid[1] = 0;
            covDev.clear();
            covAll.clear();
            dirsDev.clear();
            dirsAll.clear();
            blockHits.clear();
            siteHits.clear();
            meta = meta.replace("\"unsupported\": null,", "\"unsupported\": \"" + why + "\",");
            Files.write(packet.resolve("meta.json"), meta.getBytes("UTF-8"));
            Files.write(harness.resolve("meta.json"), meta.getBytes("UTF-8"));
            println("TcPrepare: unsupported: " + why);
        }
        String reach = why == null ? TcReach.run(this, proto, seed, seeds) : null;  // vectors reaching unreached calls
        String bound = why == null ? TcBoundPass.run(this, proto, seed, new ArrayList<>(globals)) : null;  // TcBound
        String head = fpscrLine() + TcHw.header() + (live != null ? live.header() : "")
            + (sq != null ? sq.header() : "");
        Files.write(harness.resolve("vectors.txt"), (head + TcStrict.header(facts) + vout).getBytes("UTF-8"));  // J, U
        Files.write(harness.resolve("expected.txt"), eout.toString().getBytes("UTF-8"));
        for (int s = 0; s < 2; s++) {  // chosen vectors whose strict replay failed: not written (TcStrict.replay)
            valid[s] -= TcStrict.dropped[s];
            if (TcStrict.dropped[s] > 0) invalid.merge("strict flags: the replay failed", TcStrict.dropped[s], Integer::sum);
        }
        StringBuilder inv = new StringBuilder();
        for (Map.Entry<String, Integer> e : invalid.entrySet()) {
            if (inv.length() > 0) inv.append(',');
            inv.append('"').append(esc(e.getKey())).append("\":").append(e.getValue());
        }
        StringBuilder ex = new StringBuilder();
        for (String e : examples) {
            if (ex.length() > 0) ex.append(',');
            ex.append('"').append(esc(e)).append('"');
        }
        StringBuilder sites = new StringBuilder(); // per call site, the valid vectors reaching it (report only)
        for (Map.Entry<Long, Integer> e : siteHits.entrySet()) {
            if (sites.length() > 0) sites.append(',');
            sites.append('"').append(hex(e.getKey())).append("\":").append(e.getValue());
        }
        StringBuilder bal = new StringBuilder(); // report only (TcBalance): unpushed pops, r15 off at exit
        for (String b : TcBalance.scan(currentProgram, body, fn.getEntryPoint())) {
            if (bal.length() > 0) bal.append(',');
            bal.append('"').append(esc(b)).append('"');
        }
        StringBuilder unhit = new StringBuilder(); // report only (core/proof): starts of blocks no valid vector reached
        for (int i = 0; why == null && i < blocks.size(); i++) {
            if (covAll.contains(i)) continue;
            if (unhit.length() > 0) unhit.append(',');
            unhit.append('"').append(hex(blocks.get(i)[0])).append('"');
        }
        String summary = String.format("{\"dev_valid\":%d,\"dev_tried\":%d,\"hidden_valid\":%d,\"hidden_tried\":%d,"
                + "\"blocks_total\":%d,\"blocks_hit_dev\":%d,\"blocks_hit_all\":%d,\"min_block_hits\":%d,"
                + "\"repaired\":%d,\"work\":[%d,%d],%s\"hints\":%d,\"call_sites\":{%s},\"invalid\":{%s},"
                + "\"examples\":[%s]}\n",
            valid[0], tried[0], valid[1], tried[1], blocks.size(), covDev.size(), covAll.size(),
            blockHits.isEmpty() ? 0 : Collections.min(blockHits.values()), repaired, workSteps, workRuns,
            (rescued ? "\"rescued\":1," : "")
            + (textRestored > 0 ? "\"text_restored\":" + textRestored + "," : "")
            + (bal.length() > 0 ? "\"stack_balance\":[" + bal + "]," : "") + (reach != null ? reach + "," : "")
            + (unhit.length() > 0 ? "\"blocks_unhit\":[" + unhit + "]," : "")
            + (bound != null ? bound + "," : "")
            + (dirs != null && why == null ? dirs.summary(dirsDev, dirsAll) : ""),
            hints.size(), sites, inv, ex);
        Files.write(harness.resolve("oracle_summary.json"), summary.getBytes("UTF-8"));
        println("TcPrepare: " + summary.trim());
    }

    // The functions fn calls or tail-jumps to, from its body and its shared tails (TcTail), sorted by entry.
    List<Function> calledBy(Function fn, AddressSetView body) {
        List<Function> called = new ArrayList<>(fn.getCalledFunctions(monitor));  // a HashSet: sort for stable packets
        for (Instruction in : listing.getInstructions(body, true)) {  // tail branches (bra/bt/bf) into another function
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isJump()) continue;
                Function t = fm.getFunctionAt(r.getToAddress());
                if (t != null && t != fn && !called.contains(t)) called.add(t);
            }
        }
        for (Address t : TcTail.tailCallees(currentProgram, fn, body)) {  // calls made in shared tails (TcTail)
            Function g = fm.getFunctionAt(t);
            if (g != null && g != fn && !body.contains(t) && !called.contains(g)) called.add(g);
        }
        called.sort(Comparator.comparing(Function::getEntryPoint));
        return called;
    }

    // The registers compared at a call to callee c with prototype cp: its argument registers, sN (the stack word
    // @(4N,r15)), and the runtime state it reads at entry (fr12..fr15, r8..r14). With callee summaries the registers
    // are those c reads (TcSig.callMask); the stack words still come from cp.
    List<String> calleeRegs(Proto cp, Function c) {
        List<String> out = new ArrayList<>();
        int sm = facts.paramSummaries ? sig.callMask(c) : 0;
        if (facts.paramSummaries) {
            for (int b : new int[] {16, 17, 18, 19, 20, 21, 22, 23, 4, 5, 6, 7, 8, 9, 10, 11}) {
                if ((sm & (1 << b)) != 0) out.add(TcFlow.nameOf(b));
            }
        }
        for (Param q : cp.params) {
            if (sig.isArg(q.storage) && !facts.paramSummaries) out.add(q.storage);
            else if (q.storage.equals("stack") && q.stackOffset >= 0 && q.stackOffset < 32 && q.stackOffset % 4 == 0) {
                out.add("s" + q.stackOffset / 4);
            }
        }
        int fr = facts.paramSummaries ? sm : cp.frIn, gi = facts.paramSummaries ? sm : cp.giIn;
        for (int b = 12; b < 16; b++) if ((fr & (1 << b)) != 0) out.add("fr" + b);
        for (int b = 24; b < 31; b++) if ((gi & (1 << b)) != 0) out.add(TcFlow.nameOf(b));
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
}

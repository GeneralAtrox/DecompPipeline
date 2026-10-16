// Shared state and helpers for the Dreamcast SH-4 oracle scripts: constants, listing helpers and the
// decompiler-derived C signature (repaired by TcSig). Extended by TcInputs, TcOracle, TcVectors, then TcPrepare.

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
import java.util.function.ToIntFunction;

public abstract class TcBase extends GhidraScript {
    static final long RAM_LO = 0x8c010000L, RAM_HI = 0x8d000000L;
    static final long SCR = 0x8cc00000L, SCR_SIZE = 0x1000L;
    static final long SP = 0x8cffe000L, SENTINEL = 0x8cfff000L;
    static final long STACK_LO = 0x8cff0000L;
    // P4 operand-cache address array: a store there is no RAM write and no fault; it is logged as a W line and compared
    // (TcOracle.collect, runtime tc_p4.c). Toy Commander 8c04c230 invalidates the cache this way. A load there is a
    // fault in both (TcFold.p4Read): it returns cache state, never the bytes stored.
    static final long P4_OCA_LO = 0xf4000000L, P4_OCA_HI = 0xf5000000L;
    static final int STEP_LIMIT = 500000;

    Memory mem;
    Listing listing;
    FunctionManager fm;
    Function fn;
    AddressSetView body;
    int[] fscaSin = new int[65536], fscaCos = new int[65536];
    Map<Integer, Integer> angleToFraction = new HashMap<>();
    Random rnd; // seeded by TcPrepare per vector set; TcInputs draws from it but must never reassign it

    // ---------- small helpers ----------
    // Main RAM is also visible at 0x0c... (P0); with the MMU off it is the same bytes. Some games are linked to run
    // there: every address is compared in its 0x8c... form. Only P0 folds: the other segments (0x2c..., 0xac...) are
    // what random input pointers look like, and must keep faulting as before (harnesses of other games unchanged).
    static long fold(long v) {
        v &= 0xffffffffL;
        return (v >= 0x0c000000L && v < 0x0d000000L) ? (v | 0x80000000L) : v;
    }

    static String hex(long v) {
        return String.format("%08x", v & 0xffffffffL);
    }

    static String esc(String s) {
        StringBuilder b = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c == '\n') b.append("\\n");
            else if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        return b.toString();
    }

    static String bytesHex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x & 0xff));
        return s.toString();
    }

    String readString(Address a) {
        try {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 70; i++) {
                int c = mem.getByte(a.add(i)) & 0xff;
                if (c == 0) break;
                if (c < 0x20 || c > 0x7e) return null;
                sb.append((char) c);
            }
            return sb.length() >= 4 ? sb.toString() : null;
        } catch (Exception e) {
            return null;
        }
    }

    // Literal-pool value loaded by a PC-relative mov.l / mov.w, or null.
    Long poolValue(Instruction in) {
        String m = in.getMnemonicString().toLowerCase().replace("_", "");
        if (!m.equals("mov.l") && !m.equals("mov.w")) return null;
        for (Reference r : in.getReferencesFrom()) {
            Address to = r.getToAddress();
            if (!r.getReferenceType().isRead() || !to.isMemoryAddress()) continue;
            MemoryBlock blk = mem.getBlock(to);
            if (blk == null || !blk.isInitialized() || listing.getInstructionAt(to) != null) continue;
            if (in.toString().contains("@(") || in.toString().contains("@r")) continue; // register-relative, not a pool
            try {
                return m.equals("mov.l") ? (mem.getInt(to) & 0xffffffffL) : (long) mem.getShort(to);
            } catch (Exception e) {
                return null;
            }
        }
        return null;
    }

    String describeConst(long v) {
        v = fold(v);
        if (v >= RAM_LO && v < RAM_HI) {
            Address a = toAddr(v);
            if (fm.getFunctionAt(a) != null) return "function " + hex(v);
            String s = mem.getBlock(a).isInitialized() ? readString(a) : null;
            if (s != null) return "string \"" + s + "\"";
            return "global " + hex(v);
        }
        if ((v >= 0xA0000000L && v < 0xC0000000L) || v >= 0xE0000000L) return "hardware/uncached address";
        float f = Float.intBitsToFloat((int) v);
        int exp = (int) ((v >> 23) & 0xff);
        if (v != 0 && exp > 100 && exp < 160) return String.format("%d / f32 %s", (int) v, Float.toString(f));
        return Long.toString((int) v);
    }

    // ---------- decompiler-derived signature ----------
    static class Param {
        String name, kind, storage;
        int stackOffset = -1;
    }

    String kindOf(DataType dt) {
        if (dt == null) return "u32";
        String n = dt.getName().toLowerCase();
        if (dt instanceof AbstractFloatDataType || n.equals("float")) return dt.getLength() == 4 ? "f32" : "UNSUPPORTED";
        if (dt instanceof VoidDataType || n.equals("void")) return "void";
        if (dt.getLength() > 4) return "UNSUPPORTED";
        if (dt instanceof Pointer) return "u32";
        if (dt instanceof AbstractIntegerDataType && ((AbstractIntegerDataType) dt).isSigned()) return "s32";
        return "u32";
    }

    static class Proto {
        List<Param> params = new ArrayList<>();
        String ret = "void";
        List<Boolean> paramIsPointer = new ArrayList<>();
        String unsupported = null;
        boolean pairs = false; // Ghidra typed a float pair (drN parameter, dr0/fr1 result): rebuilt as singles
        // fr12..fr15 (TcFlow bits 12..15) the code reads at entry or passes through to a callee unwritten (frIn), or
        // leaves changed at exit: runtime state, not parameters (the vector library keeps a vector in fv12). The
        // harness sets them at entry and compares them at exit; callers are compared on frIn at the call.
        int frIn = 0, frState = 0;
        // r8..r14 (TcFlow bits 24..30) likewise: hand-written display-list helpers keep a pointer in r14 (TcSig).
        int giIn = 0, giState = 0;
        // Ghidra's result storage when it is not r0/fr0 (retStorage), or a result wider than 32 bits (retWide):
        // resolved by TcSig against the dataflow into r0, a u64 in r0:r1, or a nonstandard result register (retReg).
        String retStorage = null, retReg = null;
        boolean retWide = false;
        // A mode-switching function under TcFacts.fpuState (TcSig.fpuProto): no float parameters or result; fr0..fr15,
        // xf0..xf15 and the FPSCR mode bits are state, set at entry (line Q) and compared at exit and at every call.
        boolean fpuState = false;
    }

    // The registers a callee's harness compares at each call, by name (TcSig.isArg decides from the target's ABI).
    static final String ARG_REGS = "r[0-7]|fr([4-9]|1[01])";
    static final int PASS_DEPTH = TcSig.PASS_DEPTH;
    TcFacts facts = TcFacts.defaults(); // TcPrepare loads the target's (script arg 9)
    TcSig sig;

    Proto prototypeOf(DecompInterface di, Function f) {
        return prototypeOf(di, f, PASS_DEPTH);
    }

    // The tested prototype of f, its callees looked through `depth` levels deep (TcSig).
    Proto prototypeOf(DecompInterface di, Function f, int depth) {
        if (sig == null || sig.di != di) sig = new TcSig(this, di);
        return sig.of(f, depth);
    }

    // Ghidra's own signature (memoized: every depth starts from it). Never modified after it is built.
    final Map<Address, Proto> ghidraMemo = new HashMap<>();

    Proto ghidraProto(DecompInterface di, Function f) {
        Proto p = ghidraMemo.get(f.getEntryPoint());
        if (p != null) return p;
        p = new Proto();
        ghidraMemo.put(f.getEntryPoint(), p);
        DecompileResults res = di.decompileFunction(f, 60, monitor);
        if (!res.decompileCompleted()) {
            p.unsupported = "decompile failed: " + res.getErrorMessage();
            return p;
        }
        HighFunction hf = res.getHighFunction();
        FunctionPrototype fp = hf.getFunctionPrototype();
        LocalSymbolMap lsm = hf.getLocalSymbolMap();
        for (int i = 0; i < lsm.getNumParams(); i++) {
            HighSymbol s = lsm.getParamSymbol(i);
            Param q = new Param();
            q.name = "param_" + (i + 1);
            q.kind = kindOf(s.getDataType());
            VariableStorage vs = s.getStorage();
            if (vs.isRegisterStorage()) {
                q.storage = vs.getRegister().getName().toLowerCase();
            } else if (vs.isStackStorage()) {
                q.storage = "stack";
                q.stackOffset = vs.getStackOffset();
            } else {
                q.storage = "?";
                p.unsupported = "parameter " + q.name + " has unsupported storage " + vs;
            }
            if (q.storage.matches("dr([468]|10)")) {
                p.pairs = true;
            } else if (q.kind.equals("UNSUPPORTED")) {
                p.unsupported = "64-bit parameter " + q.name;
            }
            p.params.add(q);
            p.paramIsPointer.add(s.getDataType() instanceof Pointer);
        }
        if (p.unsupported == null) {
            // stack words the code reads that Ghidra's prototype misses (TcStack)
            Set<Integer> have = new HashSet<>();
            for (Param q : p.params) if (q.storage.equals("stack")) have.add(q.stackOffset & ~3);
            for (int off : TcStack.paramReads(listing, f, this::poolValue)) {
                if (have.contains(off)) continue;
                Param q = new Param();
                q.name = "param_s" + off / 4;
                q.kind = "u32";
                q.storage = "stack";
                q.stackOffset = off;
                p.params.add(q);
                p.paramIsPointer.add(false);
            }
        }
        DataType rt = fp.getReturnType();
        p.ret = kindOf(rt);
        if (rt != null && !p.ret.equals("void")) {
            VariableStorage rs = fp.getReturnStorage();
            String rn = rs != null && rs.isRegisterStorage() ? rs.getRegister().getName().toLowerCase() : "";
            if (rn.matches("dr\\d+|fr([1-9]|1[0-5])")) {
                p.ret = "f32";
                p.pairs = true;
            } else if (p.ret.equals("UNSUPPORTED")) {
                p.retWide = true; // TcSig: u64 in r0:r1, or u32
            } else if (!rn.isEmpty() && !rn.equals("r0") && !rn.equals("fr0")) {
                p.retStorage = rn; // TcSig: r0, or a nonstandard result register
            }
        }
        return p;
    }

    static Proto copyOf(Proto p) {
        Proto q = new Proto();
        q.ret = p.ret;
        q.params.addAll(p.params);
        q.paramIsPointer.addAll(p.paramIsPointer);
        q.unsupported = p.unsupported;
        q.frIn = p.frIn;
        q.frState = p.frState;
        q.giIn = p.giIn;
        q.giState = p.giState;
        q.retStorage = p.retStorage;
        q.retReg = p.retReg;
        q.retWide = p.retWide;
        q.fpuState = p.fpuState;
        return q;
    }

    String cSignature(String name, Proto p) {
        StringBuilder s = new StringBuilder(p.ret + " " + name + "(");
        if (p.params.isEmpty()) s.append("void");
        for (int i = 0; i < p.params.size(); i++) {
            if (i > 0) s.append(", ");
            s.append(p.params.get(i).kind).append(' ').append(p.params.get(i).name);
        }
        return s.append(")").toString();
    }

}

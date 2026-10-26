// Shared state and helpers for the GameCube PowerPC (Gekko) oracle scripts: the memory map, listing helpers and the
// tested prototype (Ghidra's decompiler signature, repaired by the register dataflow of TcFlow). Extended by
// TcInputs, TcOracle, TcVectors, then TcPrepare. TcExport uses it too.

import ghidra.app.decompiler.*;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.data.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.pcode.*;
import ghidra.program.model.symbol.*;
import java.util.*;

public abstract class TcBase extends GhidraScript {
    // MEM1 (24 MB, cached view). The uncached view 0xc0000000.. folds onto it (fold).
    static final long RAM_LO = 0x80000000L, RAM_HI = 0x81800000L;
    static final long SCR = 0x81000000L, SCR_SIZE = 0x1000L;
    static final long SP = 0x817fe000L, SENTINEL = 0x817ff000L;
    static final long STACK_LO = 0x817f0000L;
    static final int STEP_LIMIT = 500000;

    Memory mem;
    Listing listing;
    FunctionManager fm;
    Function fn;
    AddressSetView body;
    Random rnd; // seeded by TcPrepare per vector set; TcInputs draws from it but must never reassign it

    // ---------- small helpers ----------
    Program prog() {
        return currentProgram;
    }

    static long fold(long v) {
        v &= 0xffffffffL;
        return (v >= 0xc0000000L && v < 0xc1800000L) ? v - 0x40000000L : v;
    }

    static String hex(long v) {
        return String.format("%08x", v & 0xffffffffL);
    }

    static String hex64(long v) {
        return String.format("%016x", v);
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

    // PowerPC has no literal pools: constants are built with lis/addi or read through r2/r13. Kept for TcInputs.
    Long poolValue(Instruction in) {
        return null;
    }

    // The RAM addresses an instruction reads or writes as data (Ghidra's references: lis/addi pairs, r2/r13 SDA).
    List<Long> dataRefs(Instruction in) {
        List<Long> out = new ArrayList<>();
        for (Reference r : in.getReferencesFrom()) {
            if (r.getReferenceType().isFlow() || !r.getToAddress().isMemoryAddress()) continue;
            long v = fold(r.getToAddress().getOffset());
            if (v >= RAM_LO && v < RAM_HI) out.add(v);
        }
        return out;
    }

    String describeConst(long v) {
        v = fold(v);
        if (v >= RAM_LO && v < RAM_HI) {
            Address a = toAddr(v);
            if (fm.getFunctionAt(a) != null) return "function " + hex(v);
            MemoryBlock b = mem.getBlock(a);
            String s = b != null && b.isInitialized() ? readString(a) : null;
            if (s != null) return "string \"" + s + "\"";
            String blk = b == null ? "ram" : b.getName();
            try {
                if (b != null && b.isInitialized()) {
                    int w = mem.getInt(a);
                    return String.format("global in %s, word 0x%08x (f32 %s)", blk, w, Float.toString(Float.intBitsToFloat(w)));
                }
            } catch (Exception e) {
                // unreadable
            }
            return "global in " + blk;
        }
        if (v >= 0xcc000000L && v < 0xcc010000L) return "hardware register";
        return Long.toString((int) v);
    }

    // ---------- the tested prototype ----------
    static class Param {
        String name, kind, storage;
        int stackOffset = -1;
    }

    static String kindOf(DataType dt) {
        if (dt == null) return "u32";
        if (dt instanceof TypeDef) dt = ((TypeDef) dt).getBaseDataType();
        String n = dt.getName().toLowerCase();
        if (dt instanceof AbstractFloatDataType || n.equals("float") || n.equals("double")) {
            return dt.getLength() == 4 ? "f32" : dt.getLength() == 8 ? "f64" : "UNSUPPORTED";
        }
        if (dt instanceof VoidDataType || n.equals("void")) return "void";
        if (dt.getLength() == 8) return "u64";
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
        boolean decompiled = true;
        Set<String> inputs = new TreeSet<>(); // TcFlow: argument registers read before written (with pass-through)
    }

    TcFacts facts = TcFacts.defaults(); // TcPrepare loads the target's (script arg 9)
    TcFlow flow;

    static final String ARG_REG = "r([3-9]|10)|f[1-8]";

    // The tested prototype of f: Ghidra's parameters and result, plus every argument register the code reads before
    // writing it (TcFlow) that Ghidra left out (u32 for rN, f64 for fN: an FPR always holds a double).
    Proto prototypeOf(DecompInterface di, Function f) {
        if (flow == null) flow = new TcFlow(currentProgram, monitor);
        Proto p = new Proto();
        for (String r : flow.inputs(f, 3)) if (r.matches(ARG_REG)) p.inputs.add(r);
        DecompileResults res = di.decompileFunction(f, 90, monitor);
        Set<String> have = new HashSet<>();
        if (res.decompileCompleted()) {
            HighFunction hf = res.getHighFunction();
            LocalSymbolMap lsm = hf.getLocalSymbolMap();
            for (int i = 0; i < lsm.getNumParams(); i++) {
                HighSymbol s = lsm.getParamSymbol(i);
                Param q = new Param();
                q.name = "param_" + (i + 1);
                q.kind = kindOf(s.getDataType());
                VariableStorage vs = s.getStorage();
                if (vs.isRegisterStorage()) {
                    q.storage = vs.getRegister().getName().toLowerCase();
                    if (q.storage.matches("f[1-8]")) q.kind = "f64"; // an FPR always holds a double
                    if (!q.storage.matches(ARG_REG)) p.unsupported = "parameter " + q.name + " in " + q.storage;
                } else if (vs.isStackStorage()) {
                    q.storage = "stack";
                    q.stackOffset = vs.getStackOffset();
                    if (q.stackOffset < 8 || q.stackOffset >= 40 || q.stackOffset % 4 != 0) {
                        p.unsupported = "stack parameter " + q.name + " at offset " + q.stackOffset;
                    }
                } else {
                    q.storage = "?";
                    p.unsupported = "parameter " + q.name + " has unsupported storage " + vs;
                }
                if (q.kind.equals("u64") || q.kind.equals("UNSUPPORTED")) p.unsupported = "64-bit parameter " + q.name;
                have.add(q.storage);
                p.params.add(q);
                p.paramIsPointer.add(s.getDataType() instanceof Pointer);
            }
            FunctionPrototype fp = hf.getFunctionPrototype();
            p.ret = kindOf(fp.getReturnType());
            VariableStorage rs = fp.getReturnStorage();
            String rn = rs != null && rs.isRegisterStorage() ? rs.getRegister().getName().toLowerCase() : "";
            if (rn.equals("f1") || p.ret.equals("f32")) p.ret = "f64";
            if (p.ret.equals("UNSUPPORTED")) p.unsupported = "unsupported result type " + fp.getReturnType();
        } else {
            p.decompiled = false;
            p.ret = "u32";
        }
        for (String r : p.inputs) {  // registers the code reads that Ghidra's signature misses
            if (have.contains(r)) continue;
            Param q = new Param();
            q.name = "param_" + r;
            q.kind = r.startsWith("f") ? "f64" : "u32";
            q.storage = r;
            p.params.add(q);
            p.paramIsPointer.add(false);
        }
        return p;
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

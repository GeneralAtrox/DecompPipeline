// Shared state and helpers for the Xbox 360 (Xenon PowerPC) oracle scripts: the memory map, listing helpers and the
// tested prototype (Ghidra's decompiler signature, repaired by the register dataflow of TcFlow). Extended by
// TcInputs, TcOracle, TcVectors, then TcPrepare. TcExport uses it too.
//
// Registers are 64 bits, addresses 32 (PowerPC:BE:64:64-32addr). The guest memory is four regions (target.json
// "memory", runtime/tc_rt_internal.h): the image at 0x82000000, the heap (scratch at its start), the stack and the
// kernel page (r13's processor block, the imported kernel variables). TcSetup maps the last three as uninitialized
// blocks; any address outside the four faults, in the oracle as in the C runner.

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
    // RAM_LO..RAM_HI spans every region (target.json memory "ram"); mapped() says which addresses exist.
    static final long RAM_LO = 0x40000000L, RAM_HI = 0x90000000L;
    static final long IMAGE_LO = 0x82000000L;
    static final long HEAP_LO = 0x40000000L, HEAP_HI = 0x40100000L;
    static final long SCR = 0x40000000L, SCR_SIZE = 0x1000L;
    static final long STACK_LO = 0x70000000L, STACK_HI = 0x70100000L;
    static final long SP = 0x700fe000L, SENTINEL = 0x700ff000L;
    static final long KERNEL_LO = 0x7fff0000L, KERNEL_HI = 0x80000000L;
    static final int STACK_ARGS = 0x50;  // the Nth stack doubleword argument is at r1 + 0x50 + 8N at the call
    static final int STEP_LIMIT = 500000;
    static final String[] HELPER_PREFIXES = {"__savegprlr", "__restgprlr", "__savefpr", "__restfpr", "__savevmx",
        "__restvmx"};
    static final String IMPORT_PREFIX = "__imp_";

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
        return v & 0xffffffffL;  // no mirrors: the title sees one view of memory
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

    // The heap, stack and kernel regions; the image is every initialized block from IMAGE_LO (TcSetup).
    static boolean inRegions(long a) {
        return (a >= HEAP_LO && a < HEAP_HI) || (a >= STACK_LO && a < STACK_HI) || (a >= KERNEL_LO && a < KERNEL_HI);
    }

    boolean mapped(long a) {
        a = fold(a);
        if (inRegions(a)) return true;
        if (a < IMAGE_LO) return false;
        MemoryBlock b = mem.getBlock(toAddr(a));
        return b != null && b.isInitialized();
    }

    boolean mapped(long a, long size) {
        return size <= 0 || (mapped(a) && mapped(a + size - 1));
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

    // PowerPC has no literal pools: constants are built with lis/addi or read through r13. Kept for TcInputs.
    Long poolValue(Instruction in) {
        return null;
    }

    // The mapped addresses an instruction reads or writes as data (Ghidra's references).
    List<Long> dataRefs(Instruction in) {
        List<Long> out = new ArrayList<>();
        for (Reference r : in.getReferencesFrom()) {
            if (r.getReferenceType().isFlow() || !r.getToAddress().isMemoryAddress()) continue;
            long v = fold(r.getToAddress().getOffset());
            if (v >= RAM_LO && v < RAM_HI && mapped(v)) out.add(v);
        }
        return out;
    }

    static boolean isHelper(Function f) {
        if (f == null) return false;
        for (String p : HELPER_PREFIXES) if (f.getName().startsWith(p)) return true;
        return false;
    }

    static boolean isImport(Function f) {
        return f != null && f.getName().startsWith(IMPORT_PREFIX);
    }

    // ---------- vector instructions (TcVmx) ----------
    // Ghidra decodes no VMX128 and mis-decodes some of it, so TcSetup replaced every vector instruction word of the
    // image by a stand-in that Ghidra decodes (same address, same size; it reads the same general registers, so the
    // register dataflow and the decompiler still see the base and index registers) and recorded the original word in
    // this program property. The oracle never steps a stand-in: it runs the original word in TcVmx.
    static final String VMX_MAP = "tc_vmx_word";

    // The vector instruction the original image holds at a, or null. Memoised per program: the oracle asks for every
    // instruction it executes, and the stand-ins do not change once TcSetup has written them.
    static final Map<Program, Map<Long, Optional<TcVmx.Op>>> VMX_OPS = new WeakHashMap<>();

    static synchronized TcVmx.Op vmxOpAt(Program p, Address a) {
        Map<Long, Optional<TcVmx.Op>> m = VMX_OPS.computeIfAbsent(p, k -> new HashMap<>());
        Optional<TcVmx.Op> o = m.get(a.getOffset());
        if (o == null) {
            o = Optional.ofNullable(vmxOpUncached(p, a));
            m.put(a.getOffset(), o);
        }
        return o.orElse(null);
    }

    static TcVmx.Op vmxOpUncached(Program p, Address a) {
        try {
            ghidra.program.model.util.IntPropertyMap map = p.getUsrPropertyManager().getIntPropertyMap(VMX_MAP);
            return map != null && map.hasProperty(a) ? TcVmx.decode(map.getInt(a)) : null;
        } catch (Exception e) {
            return null;
        }
    }

    final Map<Long, TcVmx.Op> vmxCache = new HashMap<>();

    TcVmx.Op vmxOp(long pc) {
        if (vmxCache.containsKey(pc)) return vmxCache.get(pc);
        TcVmx.Op op = vmxOpAt(currentProgram, toAddr(pc));
        vmxCache.put(pc, op);
        return op;
    }

    // A call instruction's reference to fn's own entry: a self-call, mocked like any other call (TcOracle), so the
    // recursion is a logged call as in the C, whose fixed stack addresses cannot hold a second frame (82929e18).
    boolean selfCall(Reference r) {
        return r.getReferenceType().isCall() && r.getToAddress().equals(fn.getEntryPoint());
    }

    String describeConst(long v) {
        v = fold(v);
        if (v >= KERNEL_LO && v < KERNEL_HI) return v < 0x7fff8000L ? "processor block (r13)" : "kernel variable";
        if (v >= HEAP_LO && v < HEAP_HI) return "heap";
        if (v >= IMAGE_LO && mapped(v)) {
            Address a = toAddr(v);
            Function f = fm.getFunctionAt(a);
            if (f != null) return (isImport(f) ? "import " : "function ") + f.getName();
            MemoryBlock b = mem.getBlock(a);
            String s = readString(a);
            if (s != null) return "string \"" + s + "\"";
            try {
                int w = mem.getInt(a);
                return String.format("global in %s, word 0x%08x (f32 %s)", b.getName(), w,
                    Float.toString(Float.intBitsToFloat(w)));
            } catch (Exception e) {
                return "global in " + b.getName();
            }
        }
        return Long.toString((int) v);
    }

    // ---------- the tested prototype ----------
    // The listed special floats: infinities, quiet and signalling NaNs, zeros, denormals, the extremes, and values next
    // to a rounding tie or a carry.
    static final int[] SPECIAL_FLOAT = {
        0x7f800000, 0xff800000, 0x7fc00000, 0xffc00000, 0x7fa00001, 0xffbfffff, 0x7fc12345, 0x80000000, 0x00000001,
        0x807fffff, 0x00800000, 0x80800000, 0x7f7fffff, 0xff7fffff, 0x3f7fffff, 0x3fffffff, 0x3f800001, 0x3fc00000,
        0x4b7fffff, 0x4b800001, 0x4b000001, 0x3f000001, 0xbf800001, 0x7e800000, 0x01000000};
    // The listed special integer words: signed and unsigned limits, halfword limits, byte patterns, shift counts.
    static final int[] SPECIAL_WORD = {
        0x80000000, 0x7fffffff, 0xffff8000, 0x00008000, 0x00007fff, 0x7f7f7f7f, 0x80808080, 0x00ff00ff, 0xff00ff00,
        0x0000ffff, 0xffff0000, 0x01010101, 0xfefefefe, 1, 0xffffffff, 0x1f, 0x20, 0x3f};
    static class Param {
        String name, kind, storage;
        int stackOffset = -1;  // stack: r1 + STACK_ARGS + 8N at entry, the doubleword holding the argument
    }

    // Integer kinds: an 8-byte type is u64/s64, a pointer u32 (4-byte addresses), anything up to 4 bytes u32/s32.
    static String kindOf(DataType dt) {
        if (dt == null) return "u64";
        if (dt instanceof TypeDef) dt = ((TypeDef) dt).getBaseDataType();
        String n = dt.getName().toLowerCase();
        if (dt instanceof AbstractFloatDataType || n.equals("float") || n.equals("double")) {
            return dt.getLength() == 4 ? "f32" : dt.getLength() == 8 ? "f64" : "UNSUPPORTED";
        }
        if (dt instanceof VoidDataType || n.equals("void")) return "void";
        boolean signed = dt instanceof AbstractIntegerDataType && ((AbstractIntegerDataType) dt).isSigned();
        if (dt instanceof Pointer) return "u32";
        if (dt.getLength() == 8) return signed ? "s64" : "u64";
        if (dt.getLength() > 8 || dt.getLength() <= 0) return "UNSUPPORTED";
        if (dt.getLength() > 4) return "u64";
        return signed ? "s32" : "u32";
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

    static final String ARG_REG = "r([3-9]|10)|f([1-9]|1[0-3])";
    static final String ENTRY_SCRATCH = "r(0|11|12)";  // volatile, never an argument
    static final String INT_KIND = "u32|s32|u64|s64";

    // A stack parameter's doubleword offset (STACK_ARGS + 8N, N < 8) from Ghidra's offset (+4 for a word read from
    // the low half of its big-endian doubleword), or -1.
    static int stackSlot(int off) {
        int d = off - STACK_ARGS;
        if (d < 0 || d >= 64 || (d % 8 != 0 && d % 8 != 4)) return -1;
        return STACK_ARGS + d - d % 8;
    }

    // The tested prototype of f: Ghidra's parameters and result, plus every argument register the code reads before
    // writing it (TcFlow) that Ghidra left out (u64 for rN: the whole register; f64 for fN: an FPR holds a double).
    Proto prototypeOf(DecompInterface di, Function f) {
        if (flow == null) flow = new TcFlow(currentProgram, monitor);
        Proto p = new Proto();
        for (String r : flow.inputs(f, 3)) {
            if (r.matches(ARG_REG)) p.inputs.add(r);
            // a volatile register no caller sets as an argument, read on entry: an exception funclet's frame base in
            // r12 (82a513d0), or a non-standard call; no vector can stand for it
            else if (r.matches(ENTRY_SCRATCH) && p.unsupported == null) {
                p.unsupported = "reads " + r + " on entry (not an argument register)";
            }
        }
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
                    q.storage = String.valueOf(flow.nameOf(vs.getFirstVarnode()));  // r3, also for its low word
                    if (q.storage.matches("f([1-9]|1[0-3])")) q.kind = "f64";
                    if (!q.storage.matches(ARG_REG)) p.unsupported = "parameter " + q.name + " in " + q.storage;
                } else if (vs.isStackStorage()) {
                    q.storage = "stack";
                    q.stackOffset = stackSlot(vs.getStackOffset());
                    if (q.stackOffset < 0) {
                        p.unsupported = "stack parameter " + q.name + " at offset " + vs.getStackOffset();
                    }
                } else {
                    q.storage = "?";
                    p.unsupported = "parameter " + q.name + " has unsupported storage " + vs;
                }
                if (q.kind.equals("UNSUPPORTED") || q.kind.equals("void")) {
                    p.unsupported = "parameter " + q.name + " of type " + s.getDataType();
                }
                have.add(q.storage);
                p.params.add(q);
                p.paramIsPointer.add(s.getDataType() instanceof Pointer);
            }
            FunctionPrototype fp = hf.getFunctionPrototype();
            p.ret = kindOf(fp.getReturnType());
            VariableStorage rs = fp.getReturnStorage();
            String rn = rs != null && rs.isRegisterStorage() ? String.valueOf(flow.nameOf(rs.getFirstVarnode())) : "";
            if (rn.equals("f1") || p.ret.equals("f32")) p.ret = "f64";
            if (p.ret.equals("UNSUPPORTED")) p.unsupported = "unsupported result type " + fp.getReturnType();
        } else {
            p.decompiled = false;
            p.ret = "u64";
        }
        for (String r : p.inputs) {  // registers the code reads that Ghidra's signature misses
            if (have.contains(r)) continue;
            Param q = new Param();
            q.name = "param_" + r;
            q.kind = r.startsWith("f") ? "f64" : "u64";
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

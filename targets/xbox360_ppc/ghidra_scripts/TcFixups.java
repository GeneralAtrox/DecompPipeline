// Where Ghidra's PowerPC p-code differs from the Xenon, the oracle corrects the step (not a script itself; TcOracle
// calls before() and after() around every emulated instruction). The C runtime (runtime/tc_ppc.c) implements the
// same results, so oracle and C agree bit for bit (DESIGN.md, "Emulation audit").
//  - fctiw / fctiwz: saturate to s32 (NaN and values below -2^31 give 0x80000000, values from 2^31 give
//    0x7fffffff), round fctiw to nearest even, and leave the word sign-extended in the register: ppc_fctiw_fpr. The
//    hardware's upper word is undefined; DP fixes it to the sign extension in both places.
//  - fctid / fctidz: saturate to s64 the same way.
//  - stfs / stfsu / stfsx / stfsux: the architecture's store conversion selects bits without rounding (a value below
//    the single range is denormalized by shifting): ppc_single_bits. The SLEIGH rounds (float2float).
//  - multiply-add (fmadd, fmsub, fnmadd, fnmsub and their single forms): the SLEIGH rounds the product, then the
//    sum, as tc_ppc.c does with TC_FMA_FUSED=0. FMA_FUSED below must equal target.json "fma" (a test checks it);
//    when true, the step's result is replaced by the once-rounded one, as TC_FMA_FUSED=1. TcVectors runs every
//    vector of a function with multiply-adds in the other mode too and reports how many differ ("fma_sensitive").
//  - single-precision results (SINGLE: fadds, fsubs, fmuls, fdivs, fsqrts, frsp, and the single multiply-adds while
//    they are not fused): Ghidra's float2float to 4 bytes gives +-0 for a value between 0.5 and 1 denormal unit
//    (2^-150 < |x| < 2^-149), where IEEE rounding to nearest, the Xenon and tc_ppc.c give +-2^-149 (found by
//    xenon-translate on Too Human 828c9a10). The rule is the PowerPC targets' shared TcSingle
//    (core/ghidra_shared/ppc); a result is replaced only when it is the one Ghidra's rounding makes and IEEE differs.
//  - NaN results (lfs lfsu lfsx lfsux, fadd fsub fmul fdiv fmadd fmsub fnmadd fnmsub fsqrt and their single forms,
//    frsp, fneg fabs): Ghidra makes every NaN the canonical one (an invalid operation's negative); the Xenon keeps the
//    payload (Too Human 82635508: lfs of 0xffffffff, fmuls, fadds). Where the step's result is a NaN, the fixup writes
//    the hardware's: the PowerPC targets' shared TcNaN (core/ghidra_shared/ppc).
//  - refuse: TcClass's phase 1 classes (VMX/VMX128, FPSCR moves, estimates, atomics, cache and system instructions),
//    decided by the instruction word, and any CALLOTHER without a callback here.
//  - traps (tw, twi, td, tdi): a trap whose condition holds ends the run; the vector is invalid (TcOracle). Barriers
//    (sync, lwsync, eieio, isync) and cache touch hints (dcbt, dcbtst) do nothing.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.pcode.memstate.MemoryState;
import ghidra.pcode.pcoderaw.PcodeOpRaw;
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

public class TcFixups {
    static final boolean FMA_FUSED = true;  // target.json "fma": "fused"
    static final Set<String> NO_OPS = Set.of("sync", "instructionSynchronize", "enforceInOrderExecutionIO",
        "dataCacheBlockTouch", "dataCacheBlockTouchForStore", "DataCacheBlockTouch2", "DataCacheBlockTouchByExternalPID");
    static final Set<String> TRAPS = Set.of("trapWord", "trapDoubleWord", "trapDoubleWordImmediate");

    static int word(Instruction in) {
        try {
            byte[] b = in.getBytes();
            return (b[0] & 0xff) << 24 | (b[1] & 0xff) << 16 | (b[2] & 0xff) << 8 | (b[3] & 0xff);
        } catch (Exception e) {
            return 0;
        }
    }

    // Why the oracle cannot run this instruction faithfully, or null (TcPrepare reports "unsupported: " + this).
    static String refuse(Instruction in) {
        TcVmx.Op vop = TcBase.vmxOpAt(in.getProgram(), in.getAddress());
        if (vop != null) return refuseVector(vop, in.getAddress());
        String m = in.getMnemonicString().toLowerCase();
        String why = TcClass.refusal(word(in));
        if (why != null) return why + " instruction " + m + " at " + in.getAddress();
        for (PcodeOp op : in.getPcode()) {
            if (op.getOpcode() != PcodeOp.CALLOTHER) continue;
            String name = in.getProgram().getLanguage().getUserDefinedOpName((int) op.getInput(0).getOffset());
            if (!NO_OPS.contains(name) && !TRAPS.contains(name)) {
                return "CALLOTHER " + name + " in " + m + " (no emulator callback)";
            }
        }
        return null;
    }

    // The vector instructions TcVmx cannot execute faithfully: the estimate ops need the hardware's tables, and the
    // Direct3D pack/unpack semantics were written from memory of other emulators, not from the architecture.
    static String refuseVector(TcVmx.Op op, Address at) {
        if (!TcVmx.supported(op.mn)) return "vector estimate instruction " + op.mn + " at " + at + " (hardware table not modelled)";
        if (op.mn.equals("vpkd3d128") || op.mn.equals("vupkd3d128")) {
            return "Direct3D pack/unpack " + op.mn + " at " + at + " (semantics unverified)";
        }
        return null;
    }

    // ---------- CALLOTHER callbacks ----------
    boolean trapped;  // a trap's condition held this run (TcOracle invalidates the vector)

    static boolean trapTaken(int to, long a, long b) {
        return ((to & 0x10) != 0 && a < b) || ((to & 0x08) != 0 && a > b) || ((to & 0x04) != 0 && a == b)
            || ((to & 0x02) != 0 && Long.compareUnsigned(a, b) < 0) || ((to & 0x01) != 0 && Long.compareUnsigned(a, b) > 0);
    }

    // The CALLOTHER names this language defines: the ISA variants (ppc_isa.sinc, e.g. DataCacheBlockTouch2) are not in
    // PowerPC:BE:64:64-32addr, and the emulator refuses a callback for a name its language lacks.
    static Set<String> userOps(ghidra.program.model.lang.Language lang) {
        Set<String> out = new java.util.HashSet<>();
        for (int i = 0; i < lang.getNumberOfUserDefinedOpNames(); i++) out.add(lang.getUserDefinedOpName(i));
        return out;
    }

    void install(EmulatorHelper emu) {
        Set<String> defined = userOps(emu.getLanguage());
        for (String name : NO_OPS) {
            if (!defined.contains(name)) continue;
            emu.registerCallOtherCallback(name, new BreakCallBack() {
                @Override
                public boolean pcodeCallback(PcodeOpRaw op) {
                    return true;
                }
            });
        }
        for (String name : TRAPS) {
            if (!defined.contains(name)) continue;
            emu.registerCallOtherCallback(name, new BreakCallBack() {
                @Override
                public boolean pcodeCallback(PcodeOpRaw op) {
                    MemoryState ms = emu.getEmulator().getMemState();
                    int to = (int) ms.getValue(op.getInput(1)) & 0x1f;
                    long a = ms.getValue(op.getInput(2)), b = ms.getValue(op.getInput(3));
                    if (op.getInput(3).getSize() == 2) b = (short) b;
                    if (name.equals("trapWord")) {
                        a = (int) a;
                        b = (int) b;
                    }
                    if (trapTaken(to, a, b)) trapped = true;
                    return true;
                }
            });
        }
    }

    // ---------- the Xenon results (tc_ppc.c) ----------
    static long fcti(long b, boolean word, boolean towardZero) {
        double d = Double.longBitsToDouble(b);
        if (!towardZero && Math.abs(d) < 4503599627370496.0) d = Math.rint(d);  // nearest, ties to even
        long lo = word ? Integer.MIN_VALUE : Long.MIN_VALUE;
        if (Double.isNaN(d) || d < (word ? -2147483648.0 : -9223372036854775808.0)) return lo;
        if (d >= (word ? 2147483648.0 : 9223372036854775808.0)) return word ? Integer.MAX_VALUE : Long.MAX_VALUE;
        return (long) d;  // toward zero; a word is sign-extended to 64 bits (ppc_fctiw_fpr)
    }

    // ppc_single_bits: the word stfs stores from the double with bits x.
    static long singleBits(long x) {
        int e = (int) ((x >>> 52) & 0x7ff);
        long sel = ((x >>> 32) & 0xc0000000L) | ((x >>> 29) & 0x3fffffffL);
        if (e > 896 || e < 874) return sel;
        long frac = (1L << 52) | (x & 0x000fffffffffffffL);
        return (((x >>> 32) & 0x80000000L) | (frac >>> (926 - e))) & 0xffffffffL;
    }

    // a * c + b (b negated for the msub forms) rounded once, to single when `single`; negated for the nm forms.
    static double fused(double a, double c, double b, boolean single, boolean sub, boolean neg) {
        double b2 = sub ? -b : b;
        double r = Math.fma(a, c, b2);
        if (single) {
            boolean finite = Double.isFinite(a) && Double.isFinite(c) && Double.isFinite(b2);
            BigDecimal exact = finite ? new BigDecimal(a).multiply(new BigDecimal(c)).add(new BigDecimal(b2)) : null;
            r = exact == null || exact.signum() == 0 ? (double) (float) r : (double) exact.floatValue();
        }
        // the nm forms flip the sign bit, a NaN's too, as tc_ppc.c's ppc_fneg and Ghidra's f- do (the IBM books leave
        // a NaN's sign; the judge compares any two NaNs as equal)
        return neg ? Double.longBitsToDouble(Double.doubleToRawLongBits(r) ^ Long.MIN_VALUE) : r;
    }

    // the instructions whose result TcSingle rounds as IEEE does (the Xenon's; mnemonics without the record dot)
    static final java.util.regex.Pattern SINGLE =
        java.util.regex.Pattern.compile("fadds|fsubs|fmuls|fdivs|fmadds|fmsubs|fnmadds|fnmsubs|frsp|fsqrts");
    static final java.util.regex.Pattern DOUBLE =
        java.util.regex.Pattern.compile("fadd|fsub|fmul|fdiv|fmadd|fmsub|fnmadd|fnmsub|fsqrt");

    // What before() does with a mnemonic, worked out once per distinct mnemonic, not on every step: {the mnemonic
    // lower case without the record dot, "fcti" / "stfs" / null, whether it is a multiply-add, "single" (SINGLE) /
    // "double" (DOUBLE) / null, "sign" (fneg, fabs) / "lfs" (the lfs forms) / null}.
    static final ConcurrentHashMap<String, Object[]> CLASS = new ConcurrentHashMap<>();

    static Object[] classOf(String mnemonic) {
        return CLASS.computeIfAbsent(mnemonic, r -> {
            String m = r.toLowerCase().replace(".", "");
            String k = m.matches("fcti[wd]z?") ? "fcti" : m.matches("stfsu?x?") ? "stfs" : null;
            return new Object[] {m, k, m.matches("f(n?)m(add|sub)s?"),
                SINGLE.matcher(m).matches() ? "single" : DOUBLE.matcher(m).matches() ? "double" : null,
                m.matches("fneg|fabs") ? "sign" : m.matches("lfsu?x?") ? "lfs" : null};
        });
    }

    // ---------- per step ----------
    boolean fusedMode = FMA_FUSED;  // TcVectors flips it for the second run of a multiply-add function
    String kind;       // "fcti", "stfs", "fma", "single", "double", "sign", "lfs" or null
    String dest;       // fcti, fma, single: the destination register
    boolean word, towardZero, single, sub, neg;
    long operand;      // fcti: fB's bits; stfs: fS's bits, before the step
    long[] fma = new long[3];  // fma: fA, fC, fB's bits
    String op;         // single: the mnemonic
    long[] srcBits;    // single: the source operands' bits, in operand order, before the step
    int fixups;        // how many steps this oracle corrected (TcPrepare reports it)

    static String reg(Instruction in, int op) {
        Object[] o = in.getOpObjects(op);
        for (Object x : o) if (x instanceof Register) return ((Register) x).getName().toLowerCase();
        return null;
    }

    static long bits(EmulatorHelper emu, String reg) {
        return emu.readRegister(reg).longValue();
    }

    void before(EmulatorHelper emu, Instruction in) {
        kind = null;
        if (in == null) return;
        Object[] cls = classOf(in.getMnemonicString());
        String m = (String) cls[0];
        if ("fcti".equals(cls[1])) {
            dest = reg(in, 0);
            String src = reg(in, 1);
            if (dest == null || src == null) return;
            kind = "fcti";
            word = m.charAt(4) == 'w';
            towardZero = m.endsWith("z");
            operand = bits(emu, src);
        } else if ("stfs".equals(cls[1])) {
            String src = reg(in, 0);
            if (src == null) return;
            kind = "stfs";
            operand = bits(emu, src);
        } else if (fusedMode && (Boolean) cls[2]) {
            dest = reg(in, 0);
            String a = reg(in, 1), c = reg(in, 2), b = reg(in, 3);
            if (dest == null || a == null || c == null || b == null) return;
            kind = "fma";
            single = m.endsWith("s");  // fmadds, fmsubs, fnmadds, fnmsubs
            sub = m.contains("sub");
            neg = m.startsWith("fn");
            fma[0] = bits(emu, a);
            fma[1] = bits(emu, c);
            fma[2] = bits(emu, b);
            op = m;
        } else if (cls[3] != null) {  // after the fused forms: a fused step is rounded once, by fused()
            dest = reg(in, 0);
            if (dest == null) return;
            srcBits = new long[in.getNumOperands() - 1];
            for (int i = 0; i < srcBits.length; i++) {
                String r = reg(in, i + 1);
                if (r == null) return;
                srcBits[i] = bits(emu, r);
            }
            kind = (String) cls[3];
            op = m;
        } else if ("sign".equals(cls[4])) {
            dest = reg(in, 0);
            String src = reg(in, 1);
            if (dest == null || src == null) return;
            kind = "sign";
            operand = bits(emu, src);
            op = m;
        } else if ("lfs".equals(cls[4])) {
            dest = reg(in, 0);
            if (dest != null) kind = "lfs";
        }
    }

    // Writes v to fD if fD is not v already.
    void put(EmulatorHelper emu, long v) {
        if (bits(emu, dest) == v) return;
        emu.writeRegister(dest, new BigInteger(Long.toUnsignedString(v)));
        fixups++;
    }

    // stored: {address, size} of the step's last data store (TcBank), or null; reads: the step's data reads
    // {pc, address, size, value} (TcBank).
    void after(EmulatorHelper emu, AddressSpace ram, long[] stored, java.util.List<long[]> reads) {
        if (kind == null) return;
        if (kind.equals("fcti")) {
            emu.writeRegister(dest, new BigInteger(Long.toUnsignedString(fcti(operand, word, towardZero))));
            fixups++;
        } else if (kind.equals("fma")) {
            double r = fused(Double.longBitsToDouble(fma[0]), Double.longBitsToDouble(fma[1]),
                Double.longBitsToDouble(fma[2]), single, sub, neg);
            long b = Double.doubleToRawLongBits(r);
            emu.writeRegister(dest, new BigInteger(Long.toUnsignedString(TcNaN.nan(b) ? TcNaN.arith(op, fma) : b)));
            fixups++;
        } else if (kind.equals("single") || kind.equals("double")) {
            long[] r = kind.equals("single") ? TcSingle.singleResult(op, TcSingle.singleSource(op, srcBits)) : null;
            if (r != null && r[0] != r[1] && bits(emu, dest) == r[0]) put(emu, r[1]);  // the step flushed: see above
            if (TcNaN.nan(bits(emu, dest))) put(emu, TcNaN.arith(op, srcBits));
        } else if (kind.equals("sign")) {
            if (TcNaN.nan(operand)) put(emu, TcNaN.sign(op, operand));
        } else if (kind.equals("lfs")) {
            for (long[] rd : reads) {
                if (rd[2] == 4 && TcNaN.nan32(rd[3])) put(emu, TcNaN.load(rd[3]));
            }
        } else if (stored != null && stored[1] == 4) {
            long s = singleBits(operand);
            byte[] b = {(byte) (s >>> 24), (byte) (s >>> 16), (byte) (s >>> 8), (byte) s};
            Address at = ram.getAddress(stored[0]);
            if (!java.util.Arrays.equals(emu.readMemory(at, 4), b)) {
                emu.writeMemory(at, b);
                fixups++;
            }
        }
        kind = null;
    }
}

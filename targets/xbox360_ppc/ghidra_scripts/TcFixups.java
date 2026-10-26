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

public class TcFixups {
    static final boolean FMA_FUSED = false;  // target.json "fma": "two_roundings"
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

    // ---------- CALLOTHER callbacks ----------
    boolean trapped;  // a trap's condition held this run (TcOracle invalidates the vector)

    static boolean trapTaken(int to, long a, long b) {
        return ((to & 0x10) != 0 && a < b) || ((to & 0x08) != 0 && a > b) || ((to & 0x04) != 0 && a == b)
            || ((to & 0x02) != 0 && Long.compareUnsigned(a, b) < 0) || ((to & 0x01) != 0 && Long.compareUnsigned(a, b) > 0);
    }

    void install(EmulatorHelper emu) {
        for (String name : NO_OPS) {
            emu.registerCallOtherCallback(name, new BreakCallBack() {
                @Override
                public boolean pcodeCallback(PcodeOpRaw op) {
                    return true;
                }
            });
        }
        for (String name : TRAPS) {
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
        return neg && !Double.isNaN(r) ? -r : r;
    }

    // ---------- per step ----------
    boolean fusedMode = FMA_FUSED;  // TcVectors flips it for the second run of a multiply-add function
    String kind;       // "fcti", "stfs", "fma" or null
    String dest;       // fcti, fma: the destination register
    boolean word, towardZero, single, sub, neg;
    long operand;      // fcti: fB's bits; stfs: fS's bits, before the step
    long[] fma = new long[3];
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
        String m = in.getMnemonicString().toLowerCase().replace(".", "");
        if (m.matches("fcti[wd]z?")) {
            dest = reg(in, 0);
            String src = reg(in, 1);
            if (dest == null || src == null) return;
            kind = "fcti";
            word = m.charAt(4) == 'w';
            towardZero = m.endsWith("z");
            operand = bits(emu, src);
        } else if (m.matches("stfsu?x?")) {
            String src = reg(in, 0);
            if (src == null) return;
            kind = "stfs";
            operand = bits(emu, src);
        } else if (fusedMode && m.matches("f(n?)m(add|sub)s?")) {
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
        }
    }

    // stored: {address, size} of the step's last data store (TcBank), or null.
    void after(EmulatorHelper emu, AddressSpace ram, long[] stored) {
        if (kind == null) return;
        if (kind.equals("fcti")) {
            emu.writeRegister(dest, new BigInteger(Long.toUnsignedString(fcti(operand, word, towardZero))));
            fixups++;
        } else if (kind.equals("fma")) {
            double r = fused(Double.longBitsToDouble(fma[0]), Double.longBitsToDouble(fma[1]),
                Double.longBitsToDouble(fma[2]), single, sub, neg);
            emu.writeRegister(dest, new BigInteger(Long.toUnsignedString(Double.doubleToRawLongBits(r))));
            fixups++;
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

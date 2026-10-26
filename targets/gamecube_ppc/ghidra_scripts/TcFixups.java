// Where Ghidra's Gekko p-code differs from the 750CL, the oracle corrects the step (not a script itself; TcOracle
// calls before() and after() around every emulated instruction). See targets/gamecube_ppc/README.md, "Emulation
// audit", for the reading of the SLEIGH behind each entry.
//
//  - fctiwz / fctiw: the SLEIGH truncates with trunc() and sign-extends the word into the whole register, so a value
//    outside the s32 range wraps and NaN gives whatever trunc() gives. The 750CL saturates (>= 2^31 -> 0x7fffffff,
//    < -2^31 or NaN -> 0x80000000), rounds fctiw by FPSCR[RN] (nearest here: nothing changes RN, see refuse) and
//    leaves 0xfff80000 in the upper word (0xfff80001 for a negative operand that converts to 0), as Dolphin measured.
//  - stfs / stfsu / stfsx / stfsux: the SLEIGH rounds the double to single (float2float). The architecture's store
//    conversion takes the bits without rounding (Book I 4.6.3; Dolphin ConvertToSingle), so a double that no single
//    holds is stored truncated. The fixup rewrites the 4 stored bytes from the register as it was before the step.
//  - single-precision results (fadds fsubs fmuls fdivs fmadds fmsubs fnmadds fnmsubs frsp fsqrts, and their record
//    forms): the SLEIGH rounds the double result to single with float2float, and Ghidra's FloatFormat.getEncoding
//    returns 0 for every value below the smallest single subnormal 2^-149 (its "XXX is it possible to round up"
//    branch). IEEE round-to-nearest (Book I 4.3.6 with FPSCR[RN] = 0 and NI = 0; the 750CL; the C's (f32) cast)
//    rounds a value above 2^-150 up to 2^-149, bits 0x00000001. The fixup recomputes the step's double result with
//    Ghidra's own 8-byte FloatFormat ops, in the SLEIGH's order (TcSingle, core/ghidra_shared/ppc: the rule
//    shared with the Xbox 360 target), and checks that Ghidra's float2float of it is the
//    value the step produced; only then, and only when that rounding gave a zero where Java's (float) cast (IEEE
//    round to nearest even) gives the subnormal, it writes the subnormal to fD (and to ps1 of fD when the step wrote
//    it). Every other result is left as Ghidra made it, so no other value changes. FPSCR[FPRF] (set from the zero)
//    stays as the step left it: no check observes FPSCR (mffs is refused).
//  - NaN results (lfs lfsu lfsx lfsux, the single-precision results above, fadd fsub fmul fdiv fmadd fmsub fnmadd
//    fnmsub fsqrt, fneg fabs): Ghidra makes every NaN the canonical one (and an invalid operation's NaN negative);
//    the 750CL keeps the payload. Where the step's result is a NaN, the fixup writes the hardware's (TcNaN,
//    core/ghidra_shared/ppc, shared with the Xbox 360 target) to fD and to ps1 of fD when the step wrote it. A step
//    whose result is not a NaN is left alone.
//  - slw / srw (and the record forms): the SLEIGH shifts by the whole of rB (A = S << B), and the p-code shift gives 0
//    for a count of 32 or more. The 750CL takes rB's low 6 bits: 0 when bit 0x20 is set, else a shift by rB & 0x1f
//    (Book I 3.3.13; sraw's SLEIGH masks with 0x3f and is right). They differ only for rB >= 64 with bit 0x20 clear
//    (Skies 801ab6e8, 802377f8, 8023fef8). The fixup writes the 750CL's result to rA, and for slw. / srw. sets CR0's
//    LT, GT and EQ from it (SO as the step left it), only when the result differs.
//  - refuse: instructions whose p-code is not the hardware's and that nothing here repairs. The function is refused
//    (TcPrepare "unsupported") rather than tested against a wrong oracle: the reciprocal estimates (fres, frsqrte:
//    table lookups on the 750CL), the paired-single and quantized load/store families (ps_*, psq_*: ldexpf CALLOTHER,
//    signed conversion of the unsigned GQR types, an unsigned 6-bit scale, no store saturation), FPSCR moves (the
//    p-code float ops always round to nearest) and cache/system instructions (dcbz zeroes a cache line).
//    Exception: psq_l / psq_st through a GQR whose type and scale fields are 0 (GQR0 in CodeWarrior's prologues and
//    epilogues, saving ps1 of f14..f31) take the SLEIGH's float path, a plain single load or store with no
//    CALLOTHER: the hardware's but for rounding (not truncating) the stored ps0, a stack-frame word no check compares
//    and the epilogue's lfd overwrites.
import ghidra.app.emulator.EmulatorHelper;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.pcode.memstate.MemoryState;
import ghidra.pcode.pcoderaw.PcodeOpRaw;
import ghidra.program.model.address.*;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Instruction;
import java.math.BigInteger;

public class TcFixups {
    // Why the oracle cannot run this instruction faithfully, or null. fixed: the GQR values at entry (TcFacts).
    static String refuse(Instruction in, java.util.Map<String, Long> fixed) {
        String m = in.getMnemonicString().toLowerCase();
        if (m.matches("fres\\.?|frsqrte\\.?")) return "estimate instruction " + m;
        if (m.startsWith("psq_")) {
            java.util.regex.Matcher g = java.util.regex.Pattern.compile("gqr([0-7])").matcher(in.toString().toLowerCase());
            Long v = g.find() ? fixed.get("gqr" + g.group(1)) : null;
            long fields = m.startsWith("psq_l") ? 0x3f070000L : 0x00003f07L; // LD_SCALE|LD_TYPE or ST_SCALE|ST_TYPE
            if (v != null && (v & fields) == 0) return null;
            return "paired-single instruction " + m + " (quantized: GQR type or scale not 0)";
        }
        if (m.startsWith("ps_")) return "paired-single instruction " + m;
        for (PcodeOp op : in.getPcode()) {  // a user-defined p-code op the emulator has no callback for
            if (op.getOpcode() != PcodeOp.CALLOTHER) continue;
            String name = in.getProgram().getLanguage().getUserDefinedOpName((int) op.getInput(0).getOffset());
            if (!CALLOTHER_HANDLED.contains(name)) return "CALLOTHER " + name + " in " + m + " (no emulator callback)";
        }
        if (m.matches("mtfsf\\.?|mtfsfi\\.?|mtfsb[01]\\.?|mffs\\.?")) return "FPSCR move " + m + " (rounding mode)";
        if (m.matches("dcbz|dcbz_l|dcbi|dcbf|dcbst|icbi|sync|isync|eieio|mtmsr|mfmsr|sc|rfi|tw|twi|mftb")) {
            return "cache/system instruction " + m;
        }
        return null;
    }

    // ---------- CALLOTHER callbacks ----------
    // cntlzw / cntlzw.: the SLEIGH is A = countLeadingZeros(S:4), a CALLOTHER the emulator does not implement.
    static final java.util.Set<String> CALLOTHER_HANDLED = java.util.Set.of("countLeadingZeros");

    static void install(EmulatorHelper emu) {
        emu.registerCallOtherCallback("countLeadingZeros", new BreakCallBack() {
            @Override
            public boolean pcodeCallback(PcodeOpRaw op) {
                MemoryState ms = emu.getEmulator().getMemState();
                Varnode in = op.getInput(1);
                int bits = 8 * in.getSize();
                long v = ms.getValue(in) & (bits >= 64 ? -1L : (1L << bits) - 1);
                ms.setValue(op.getOutput(), v == 0 ? bits : Long.numberOfLeadingZeros(v) - (64 - bits));
                return true;
            }
        });
    }

    // ---------- the 750CL results ----------
    // fctiwz / fctiw of the double with bits b: the 64-bit register result.
    static long fctiw(long b, boolean towardZero) {
        double d = Double.longBitsToDouble(b);
        long w;
        if (Double.isNaN(d)) w = 0x80000000L;
        else if (d >= 2147483648.0) w = 0x7fffffffL;
        else if (d < -2147483648.0) w = 0x80000000L;
        else w = (towardZero ? (long) d : (long) Math.rint(d)) & 0xffffffffL;
        long r = 0xfff8000000000000L | w;
        if (w == 0 && (b & 0x8000000000000000L) != 0) r |= 0x100000000L;
        return r;
    }

    // slw / srw of the words s and b on the 750CL: the count is b's low 6 bits, 32..63 giving 0.
    static long shift(String op, long s, long b) {
        int n = (int) (b & 0x3f);
        if (n >= 32) return 0;
        return op.startsWith("slw") ? (s << n) & 0xffffffffL : (s & 0xffffffffL) >>> n;
    }

    // The single a store-single writes from the double with bits x (Dolphin ConvertToSingle; hardware-tested).
    static long toSingle(long x) {
        int exp = (int) ((x >>> 52) & 0x7ff);
        if (exp > 896 || (x & 0x7fffffffffffffffL) == 0) {
            return (((x >>> 32) & 0xc0000000L) | ((x >>> 29) & 0x3fffffffL)) & 0xffffffffL;
        }
        if (exp >= 874) {
            long t = 0x80000000L | ((x & 0x000fffffffffffffL) >>> 21);
            t = (t & 0xffffffffL) >>> (905 - exp);
            t |= (x >>> 32) & 0x80000000L;
            return t & 0xffffffffL;
        }
        return (((x >>> 32) & 0xc0000000L) | ((x >>> 29) & 0x3fffffffL)) & 0xffffffffL;
    }

    // ---------- single-precision results ----------
    static final java.util.regex.Pattern SINGLE =
        java.util.regex.Pattern.compile("(fadds|fsubs|fmuls|fdivs|fmadds|fmsubs|fnmadds|fnmsubs|frsp|fsqrts)\\.?");
    static final java.util.regex.Pattern DOUBLE =
        java.util.regex.Pattern.compile("(fadd|fsub|fmul|fdiv|fmadd|fmsub|fnmadd|fnmsub|fsqrt)\\.?");

    // ---------- per step ----------
    String kind;       // "fctiw", "fctiwz", "stfs", "single", "double", "sign", "lfs", "shift" or null
    String dest;       // fctiw*, single, double, sign, lfs, shift: the destination register
    long operand;      // fctiw*, sign: fB's bits; stfs: fS's bits, before the step
    String op;         // single, double, sign: the mnemonic without "."; shift: the mnemonic
    long[] srcBits;    // single, double, shift: the source operands' bits, before the step
    String ps1;        // single, lfs: fD's ps1 register when the step writes it, else null
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
        ps1 = null;
        if (in == null) return;
        String m = in.getMnemonicString().toLowerCase();
        if (m.matches("fctiwz?\\.?")) {
            kind = m.startsWith("fctiwz") ? "fctiwz" : "fctiw";
            dest = reg(in, 0);
            String src = reg(in, 1);
            if (dest == null || src == null) kind = null;
            else operand = bits(emu, src);
        } else if (m.matches("stfsu?x?")) {
            String src = reg(in, 0);
            kind = src == null ? null : "stfs";
            if (src != null) operand = bits(emu, src);
        } else if (SINGLE.matcher(m).matches() || DOUBLE.matcher(m).matches()) {
            int n = in.getNumOperands();
            dest = reg(in, 0);
            srcBits = new long[n - 1];
            for (int i = 1; i < n && dest != null; i++) {
                String r = reg(in, i);
                if (r == null) dest = null;
                else srcBits[i - 1] = bits(emu, r);
            }
            kind = dest == null ? null : SINGLE.matcher(m).matches() ? "single" : "double";
            op = m.replace(".", "");
            ps1 = ps1(in, dest);
        } else if (m.matches("f(neg|abs)\\.?")) {
            dest = reg(in, 0);
            String src = reg(in, 1);
            kind = dest == null || src == null ? null : "sign";
            if (kind != null) operand = bits(emu, src);
            op = m.replace(".", "");
        } else if (m.matches("lfsu?x?")) {
            dest = reg(in, 0);
            kind = dest == null ? null : "lfs";
            ps1 = ps1(in, dest);
        } else if (m.matches("s[lr]w\\.?")) {  // slw A,S,B
            dest = reg(in, 0);
            String s = reg(in, 1), b = reg(in, 2);
            kind = dest == null || s == null || b == null ? null : "shift";
            if (kind != null) srcBits = new long[] {bits(emu, s), bits(emu, b)};
            op = m;
        }
    }

    // fD's ps1 register when the instruction writes it, else null.
    static String ps1(Instruction in, String dest) {
        Register p = dest == null ? null : in.getProgram().getLanguage().getRegister("ps" + dest.substring(1) + "_1");
        for (PcodeOp o : p == null ? new PcodeOp[0] : in.getPcode()) {
            Varnode out = o.getOutput();
            if (out != null && out.isRegister() && out.getAddress().equals(p.getAddress())) return p.getName();
        }
        return null;
    }

    // Writes v to fD (and its ps1 when the step wrote it, with the value fD had) if fD is not v already.
    void put(EmulatorHelper emu, long v) {
        long was = bits(emu, dest);
        if (was == v) return;
        BigInteger b = new BigInteger(Long.toUnsignedString(v));
        emu.writeRegister(dest, b);
        if (ps1 != null && bits(emu, ps1) == was) emu.writeRegister(ps1, b);
        fixups++;
    }

    // stored: {address, size} of the step's last data store (TcBank), or null; reads: the step's data reads
    // {pc, address, size, value} (TcBank).
    void after(EmulatorHelper emu, AddressSpace ram, long[] stored, java.util.List<long[]> reads) {
        if (kind == null) return;
        if (kind.startsWith("fctiw")) {
            emu.writeRegister(dest, new BigInteger(Long.toUnsignedString(fctiw(operand, kind.equals("fctiwz")))));
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
        } else if (kind.equals("shift")) {
            long r = shift(op, srcBits[0], srcBits[1]);
            if ((bits(emu, dest) & 0xffffffffL) != r) {
                put(emu, r);
                if (op.endsWith(".")) {  // cr0 holds LT GT EQ SO as 8 4 2 1 (the SLEIGH's setCrBit)
                    int lt = (int) r < 0 ? 8 : (int) r > 0 ? 4 : 2;
                    emu.writeRegister("cr0", BigInteger.valueOf(lt | (bits(emu, "cr0") & 1)));
                }
            }
        } else if (stored != null && stored[1] == 4) {
            long s = toSingle(operand);
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

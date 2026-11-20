// Paired-single results on the 750CL (Gekko), not a script itself. TcFixups calls it after each ps_* and quantized
// psq_* step, and writes these values over the step's.
//
// The GameCubeLoader SLEIGH (ppc_gekko_broadway.slaspec) gets these wrong in five ways:
//  - It computes each ps_* slot in double and never rounds it to single.
//  - Its NaNs are Ghidra's canonical ones (TcNaN).
//  - Its quantized load multiplies the bits of an int2float by the bits of an ldexpf CALLOTHER's result as integers.
//  - It converts the unsigned GQR types 4 and 5 as signed.
//  - Its quantized store neither rounds to single first nor saturates.
//
// The hardware's rules, as Dolphin's interpreter implements them (Interpreter_Paired.cpp,
// Interpreter_LoadStorePaired.cpp):
//  - Each slot of ps_add ps_sub ps_mul ps_div ps_madd ps_msub ps_nmadd ps_nmsub, of the muls/madds forms, and the
//    summed slot of ps_sum0 / ps_sum1 is the scalar single op's result on that slot's operands. That means the
//    double in the SLEIGH's order, rounded to single by IEEE (TcSingle), with a NaN result per TcNaN. The other
//    slot of ps_sum is the operand's value, as the SLEIGH copies it.
//  - ps_neg, ps_abs, ps_nabs, ps_mr, ps_sel and ps_merge* copy or change only the sign, on both slots, and do not
//    quiet a NaN.
//  - ps_res is fres's table estimate on each slot (TcEstimate). ps_rsqrte is frsqrte's, rounded to single.
//  - psq_l of an integer type: raw (u8 u16 s8 s16 for types 4 5 6 7) times 2^-scale in single precision. The scale
//    is the GQR's signed 6-bit field.
//  - psq_st of an integer type: the double rounded to single, times 2^scale, clamped to the type's range, then
//    truncated toward zero. A NaN gives 0.
// Dolphin applies Force25Bit to fC in the multiply forms. Neither the scalar fmuls/fmadds rule here (TcSingle) nor
// this file does: the oracle and the translator agree, and a double fC that no single holds is a known difference
// from the hardware for both (SKIES_TRANSLATE_SCORE.md).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.listing.Instruction;

public class TcPaired {
    static final long SIGN = 0x8000000000000000L, ONE = 0x3ff0000000000000L;

    // One single-precision slot of the scalar op sop (fadds fsubs fmuls fdivs fmadds fmsubs fnmadds fnmsubs) from its
    // source operands in assembly order (fmadds: a, c, b).
    static long slot(String sop, long... src) {
        long v = TcSingle.singleResult(sop, TcSingle.singleSource(sop, src))[1];
        return TcNaN.nan(v) ? TcNaN.arith(sop, src) : v;
    }

    // ps_rsqrte's slot: frsqrte's estimate rounded to single (a NaN keeps its top 23 fraction bits).
    static long rsqrte(long b) {
        long e = TcEstimate.frsqrte(b);
        if (TcNaN.nan(e)) return e & 0xffffffffe0000000L;
        return Double.doubleToRawLongBits((double) (float) Double.longBitsToDouble(e));
    }

    // {ps0, ps1} of fD after the paired-single op (mnemonic, no "."), from the sources' {ps0, ps1} in assembly order
    // (ps_add fD,fA,fB; ps_mul fD,fA,fC; ps_madd / ps_sum0 / ps_sel fD,fA,fC,fB; ps_neg fD,fB), or null for an op not
    // computed here (ps_cmp*).
    static long[] result(String op, long[][] s) {
        long[] r = new long[2];
        for (int k = 0; k < 2; k++) {
            switch (op) {
                case "ps_add": r[k] = slot("fadds", s[0][k], s[1][k]); break;
                case "ps_sub": r[k] = slot("fsubs", s[0][k], s[1][k]); break;
                case "ps_div": r[k] = slot("fdivs", s[0][k], s[1][k]); break;
                case "ps_mul": r[k] = slot("fmuls", s[0][k], s[1][k]); break;
                case "ps_muls0": r[k] = slot("fmuls", s[0][k], s[1][0]); break;
                case "ps_muls1": r[k] = slot("fmuls", s[0][k], s[1][1]); break;
                case "ps_madd": case "ps_msub": case "ps_nmadd": case "ps_nmsub":
                    r[k] = slot("f" + op.substring(3) + "s", s[0][k], s[1][k], s[2][k]);
                    break;
                case "ps_madds0": r[k] = slot("fmadds", s[0][k], s[1][0], s[2][k]); break;
                case "ps_madds1": r[k] = slot("fmadds", s[0][k], s[1][1], s[2][k]); break;
                case "ps_sum0": r[k] = k == 0 ? slot("fadds", s[0][0], s[2][1]) : s[1][1]; break;
                case "ps_sum1": r[k] = k == 1 ? slot("fadds", s[0][0], s[2][1]) : s[1][0]; break;
                case "ps_sel": r[k] = Double.longBitsToDouble(s[0][k]) >= 0.0 ? s[1][k] : s[2][k]; break;
                case "ps_neg": r[k] = s[0][k] ^ SIGN; break;
                case "ps_abs": r[k] = s[0][k] & ~SIGN; break;
                case "ps_nabs": r[k] = s[0][k] | SIGN; break;
                case "ps_mr": r[k] = s[0][k]; break;
                case "ps_merge00": case "ps_merge01": case "ps_merge10": case "ps_merge11":
                    r[k] = s[k][op.charAt(8 + k) - '0'];
                    break;
                case "ps_res": r[k] = TcEstimate.fres(s[0][k]); break;
                case "ps_rsqrte": r[k] = rsqrte(s[0][k]); break;
                default: return null;
            }
        }
        return r;
    }

    // The GQR's 6-bit scale field as the signed count it is.
    static int scale(long field) {
        int n = (int) (field & 0x3f);
        return n >= 32 ? n - 64 : n;
    }

    // Bytes per value of the GQR type: 1 for u8/s8 (4, 6), 2 for u16/s16 (5, 7), 4 for a single (0..3).
    static int size(int type) {
        return type == 4 || type == 6 ? 1 : type == 5 || type == 7 ? 2 : 4;
    }

    // The double psq_l makes of the raw value of an integer type (4..7) with the scale field sc.
    static long dequant(int type, long sc, long raw) {
        long v = type == 4 ? raw & 0xff : type == 5 ? raw & 0xffff : type == 6 ? (byte) raw : (short) raw;
        float f = (float) v * Math.scalb(1.0f, -scale(sc));
        return Double.doubleToRawLongBits((double) f);
    }

    // The value psq_st writes for an integer type (4..7) with the scale field sc, from the double with bits x.
    static long quant(int type, long sc, long x) {
        double v = (double) (float) Double.longBitsToDouble(x) * Math.scalb(1.0, scale(sc));
        long lo = type == 4 || type == 5 ? 0 : type == 6 ? -128 : -32768;
        long hi = type == 4 ? 255 : type == 5 ? 65535 : type == 6 ? 127 : 32767;
        long r = Double.isNaN(v) ? 0 : v <= lo ? lo : v >= hi ? hi : (long) v;
        return r & (size(type) == 1 ? 0xffL : 0xffffL);
    }

    // {GQR index, W} of a psq_* instruction: I and W are bits 12..14 and 15 (D form), 7..9 and 10 (X form, opcode 4).
    static int[] psq(Instruction in) {
        int w;
        try {
            byte[] b = in.getBytes();
            w = (b[0] & 0xff) << 24 | (b[1] & 0xff) << 16 | (b[2] & 0xff) << 8 | (b[3] & 0xff);
        } catch (ghidra.program.model.mem.MemoryAccessException e) {
            return new int[] {-1, 0};
        }
        return w >>> 26 == 4 ? new int[] {(w >>> 7) & 7, (w >>> 10) & 1} : new int[] {(w >>> 12) & 7, (w >>> 15) & 1};
    }

    // Reads the step's GQR fields into f (qtype, qscale, qone) for a psq_* instruction.
    static void gqr(TcFixups f, EmulatorHelper emu, Instruction in, boolean load) {
        int[] q = psq(in);
        long v = q[0] < 0 ? 0 : TcFixups.bits(emu, "GQR" + q[0]);
        f.qtype = (int) (load ? (v >>> 16) & 7 : v & 7);
        f.qscale = load ? (v >>> 24) & 0x3f : (v >>> 8) & 0x3f;
        f.qone = q[1] != 0;
    }

    // After the step: a ps_* result's two slots, or a psq_l / psq_st through an integer GQR type (4..7), rewritten by
    // the rules above. False when the step is neither (TcFixups.fix goes on).
    static boolean fix(TcFixups f, EmulatorHelper emu, AddressSpace ram, TcBank bank, java.util.List<long[]> reads) {
        if (f.kind.equals("ps")) {
            long[] r = result(f.op, f.pairs);
            if (r != null) {
                f.set(emu, f.dest, r[0]);
                f.set(emu, "ps" + f.dest.substring(1) + "_1", r[1]);
            }
        } else if (f.kind.equals("psq_l") && f.qtype >= 4) {  // the step read both values even for W = 1 (the SLEIGH)
            int size = size(f.qtype), k = 0;
            long[] v = {0, ONE};
            for (long[] rd : reads) {
                if (rd[2] != size) continue;
                v[k] = dequant(f.qtype, f.qscale, rd[3]);
                if (++k == (f.qone ? 1 : 2)) break;
            }
            if (k == (f.qone ? 1 : 2)) {
                f.set(emu, f.dest, v[0]);
                f.set(emu, "ps" + f.dest.substring(1) + "_1", v[1]);
            }
        } else if (f.kind.equals("psq_st") && f.qtype >= 4) {
            int size = size(f.qtype), k = 0;
            for (long[] st : new java.util.ArrayList<>(bank.stores)) {  // a copy: writeMemory adds to bank.stores
                if (st[1] != size || k == (f.qone ? 1 : 2)) continue;
                f.store(emu, ram, bank, st, size, quant(f.qtype, f.qscale, f.srcBits[k++]));
            }
        } else {
            return false;
        }
        return true;
    }
}

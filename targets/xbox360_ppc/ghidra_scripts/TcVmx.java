import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * A pure-Java executor of the Xbox 360 (Xenon) vector unit: AltiVec plus the VMX128 extension. No Ghidra imports.
 *
 * It exists so the oracle (the original machine code run under Ghidra's emulator, which cannot decode VMX128) can
 * execute vector instructions itself. It is the oracle side of a cross-check, so its semantics are written from the
 * architecture (AltiVec Programming Environments Manual; the Xenon VMX128 additions as documented by Xenia) and not
 * from the translator's C helpers (translator/xenontranslate/vmx.py). The DECODING tables are generated from the
 * translator's decode.py (verified against a reference disassembler on every code word of the game; see
 * tests_vmx/gen_tables.py).
 *
 * Model.
 *   - 128 vector registers v[0..127], each int[4]; lane 0 (word 0) is the most significant word and byte 0 is the
 *     leftmost byte, as in memory (big endian).
 *   - VSCR: NJ (non-Java mode) is always treated as set: denormal float inputs are read as zero of the same sign and
 *     denormal results are written as zero of the same sign. SAT is recorded in bit 0 of {@link #vscr}. mtvscr stores
 *     the register but NJ is not modelled as clearable.
 *   - Floats are single precision per lane. NaN: the result is the first NaN operand quieted (operand order is given
 *     per op below), else an invalid operation gives the default NaN 0x7FC00000. Multiply-adds are fused.
 *   - Compares with Rc return the CR6 nibble from {@link #exec} (all lanes true = 8, all false = 2, both 0b1010 can
 *     not happen; none = 0); vcmpbfp. returns 2 when every lane is within bounds, else 0. Ops without Rc return -1.
 *   - The estimate ops (vrefp, vrsqrtefp, vexptefp, vlogefp and the 128 forms) depend on hardware lookup tables and
 *     are NOT implemented: {@link #supported} is false for them and {@link #exec} throws.
 */
public final class TcVmx extends TcVmxInt {

    /**
     * Execute one decoded vector instruction. gpr: the 32 general registers (64-bit; only ra/rb are read);
     * mem: guest memory for loads and stores (may be null for register-only ops).
     * @return the CR6 nibble for a compare with Rc set, else -1 (also stored in {@link #cr6}).
     */
    public int exec(Op op, long[] gpr, Mem mem) {
        cr6 = -1;
        satHit = false;
        String m = op.mn;
        if (ESTIMATES.contains(m)) {
            throw new UnsupportedOperationException(m + ": hardware estimate table is not modelled");
        }
        if (isMem(m) || m.equals("lvsl") || m.equals("lvsr") || m.equals("lvsl128") || m.equals("lvsr128")) {
            execMem(op, gpr, mem);
            return -1;
        }
        if (m.equals("dst") || m.equals("dstst") || m.equals("dss")) {
            return -1;                                  // stream hints: no architectural effect
        }
        int[] r = alu(op);
        if (r != null) {
            v[op.vd] = r;
        }
        if (satHit) {
            vscr |= 1;
        }
        return cr6;
    }

    private int[] alu(Op op) {
        String m = op.mn;
        int[] A = op.va != NONE ? v[op.va] : null, B = op.vb != NONE ? v[op.vb] : null;
        int[] C = op.vc != NONE ? v[op.vc] : null, D = op.vd != NONE ? v[op.vd] : null;
        int[] r = new int[4];
        // ---- forms whose operands are not the plain A, B of their base op
        switch (m) {
            case "vmaddfp128":                          // vD = vA * vB + vD (NaN order: vA, vD, vB)
                for (int i = 0; i < 4; i++) {
                    r[i] = fma3(A[i], B[i], D[i], A[i], D[i], B[i], false);
                }
                return r;
            case "vmaddcfp128":                         // vD = vA * vD + vB (NaN order: vA, vB, vD)
                for (int i = 0; i < 4; i++) {
                    r[i] = fma3(A[i], D[i], B[i], A[i], B[i], D[i], false);
                }
                return r;
            case "vnmsubfp128":                         // vD = -(vA * vB - vD) (NaN order: vA, vD, vB)
                for (int i = 0; i < 4; i++) {
                    r[i] = fma3(A[i], B[i], D[i] ^ 0x80000000, A[i], D[i], B[i], true);
                }
                return r;
            case "vmaddfp":                             // vD = vA * vC + vB (NaN order: vA, vB, vC)
                for (int i = 0; i < 4; i++) {
                    r[i] = fma3(A[i], C[i], B[i], A[i], B[i], C[i], false);
                }
                return r;
            case "vnmsubfp":                            // vD = -(vA * vC - vB)
                for (int i = 0; i < 4; i++) {
                    r[i] = fma3(A[i], C[i], B[i] ^ 0x80000000, A[i], B[i], C[i], true);
                }
                return r;
            case "vsel":
                for (int i = 0; i < 4; i++) {
                    r[i] = (A[i] & ~C[i]) | (B[i] & C[i]);
                }
                return r;
            case "vsel128":                             // the selector is vD
                for (int i = 0; i < 4; i++) {
                    r[i] = (A[i] & ~D[i]) | (B[i] & D[i]);
                }
                return r;
            case "vperm":
            case "vperm128": {
                for (int i = 0; i < 16; i++) {
                    int idx = gb(C, i) & 31;
                    pb(r, i, idx < 16 ? gb(A, idx) : gb(B, idx - 16));
                }
                return r;
            }
            case "vsldoi":
            case "vsldoi128": {
                for (int i = 0; i < 16; i++) {
                    int k = op.sh + i;
                    pb(r, i, k < 16 ? gb(A, k) : gb(B, k - 16));
                }
                return r;
            }
            case "vpermwi128":
                for (int i = 0; i < 4; i++) {
                    r[i] = B[(op.perm >>> (6 - 2 * i)) & 3];
                }
                return r;
            case "vrlimi128": {
                // rotate vB left by z words, then insert the words selected by the 4-bit mask (bit 3 = word 0)
                for (int i = 0; i < 4; i++) {
                    r[i] = ((op.imm >>> (3 - i)) & 1) != 0 ? B[(i + op.z) & 3] : D[i];
                }
                return r;
            }
            case "vpkd3d128":
                return vpkd3d(D, B, op.imm, op.mask, op.z);
            case "vupkd3d128":
                return vupkd3d(B, op.imm);
            case "vmulfp128":
                for (int i = 0; i < 4; i++) {
                    r[i] = fmul(A[i], B[i]);
                }
                return r;
            case "vmsum3fp128":
            case "vmsum4fp128": {
                int s = dot(A, B, m.charAt(5) == '3' ? 3 : 4);
                return new int[] {s, s, s, s};
            }
            case "vspltisb":
                for (int i = 0; i < 16; i++) {
                    pb(r, i, op.simm);
                }
                return r;
            case "vspltish":
                for (int i = 0; i < 8; i++) {
                    ph(r, i, op.simm);
                }
                return r;
            case "vspltisw":
            case "vspltisw128":
                return new int[] {op.simm, op.simm, op.simm, op.simm};
            case "mfvscr":
                return new int[] {0, 0, 0, vscr};
            case "mtvscr":
                vscr = (vscr & ~0x00010001) | (B[3] & 0x00010001);
                return null;
            default:
                break;
        }
        String b = switch (m) {
            case "vcsxwfp128" -> "vcfsx";
            case "vcuxwfp128" -> "vcfux";
            case "vcfpsxws128" -> "vctsxs";
            case "vcfpuxws128" -> "vctuxs";
            default -> m.endsWith("128") ? m.substring(0, m.length() - 3) : m;
        };
        switch (b) {
            // ------------------------------------------------------------------------------------ float
            case "vaddfp":
                for (int i = 0; i < 4; i++) {
                    r[i] = fadd(A[i], B[i]);
                }
                return r;
            case "vsubfp":
                for (int i = 0; i < 4; i++) {
                    r[i] = fsub(A[i], B[i]);
                }
                return r;
            case "vmaxfp":
                for (int i = 0; i < 4; i++) {
                    r[i] = fmax(A[i], B[i]);
                }
                return r;
            case "vminfp":
                for (int i = 0; i < 4; i++) {
                    r[i] = fmin(A[i], B[i]);
                }
                return r;
            case "vrfin":
            case "vrfiz":
            case "vrfip":
            case "vrfim":
                for (int i = 0; i < 4; i++) {
                    r[i] = frnd(B[i], b.charAt(4));
                }
                return r;
            case "vcmpeqfp":
            case "vcmpgefp":
            case "vcmpgtfp":
                for (int i = 0; i < 4; i++) {
                    boolean t;
                    if (isNaN(A[i]) || isNaN(B[i])) {
                        t = false;
                    } else {
                        float x = F(ftz(A[i])), y = F(ftz(B[i]));
                        t = b.equals("vcmpeqfp") ? x == y : b.equals("vcmpgefp") ? x >= y : x > y;
                    }
                    r[i] = t ? -1 : 0;
                }
                return cmpResult(op, r);
            case "vcmpbfp": {
                boolean allIn = true;
                for (int i = 0; i < 4; i++) {
                    if (isNaN(A[i]) || isNaN(B[i])) {
                        r[i] = 0xC0000000;
                    } else {
                        float x = F(ftz(A[i])), y = F(ftz(B[i]));
                        r[i] = (x > y ? 0x80000000 : 0) | (x < -y ? 0x40000000 : 0);
                    }
                    allIn &= r[i] == 0;
                }
                if (op.rc != NONE && op.rc != 0) {
                    cr6 = allIn ? 2 : 0;
                }
                return r;
            }
            case "vcfsx":
            case "vcfux":
                for (int i = 0; i < 4; i++) {
                    float f = b.equals("vcfsx") ? (float) B[i] : (float) (B[i] & 0xFFFFFFFFL);
                    r[i] = fb(f / (float) (1L << op.uimm));      // dividing by a power of two is exact
                }
                return r;
            case "vctsxs":
                for (int i = 0; i < 4; i++) {
                    r[i] = ctsxs(B[i], op.uimm);
                }
                return r;
            case "vctuxs":
                for (int i = 0; i < 4; i++) {
                    r[i] = ctuxs(B[i], op.uimm);
                }
                return r;
            // ------------------------------------------------------------------------------------ logical
            case "vand":
                for (int i = 0; i < 4; i++) {
                    r[i] = A[i] & B[i];
                }
                return r;
            case "vandc":
                for (int i = 0; i < 4; i++) {
                    r[i] = A[i] & ~B[i];
                }
                return r;
            case "vor":
                for (int i = 0; i < 4; i++) {
                    r[i] = A[i] | B[i];
                }
                return r;
            case "vxor":
                for (int i = 0; i < 4; i++) {
                    r[i] = A[i] ^ B[i];
                }
                return r;
            case "vnor":
                for (int i = 0; i < 4; i++) {
                    r[i] = ~(A[i] | B[i]);
                }
                return r;
            default:
                break;
        }
        return intAlu(op, b, A, B, C);
    }

    // ============================================================================================ memory
    private void execMem(Op op, long[] gpr, Mem mem) {
        String m = op.mn.endsWith("128") ? op.mn.substring(0, op.mn.length() - 3) : op.mn;
        long base = (op.ra != NONE && op.ra != 0) ? gpr[op.ra] : 0;
        long ea = (base + gpr[op.rb]) & 0xFFFFFFFFL;
        int eb = (int) (ea & 15);
        switch (m) {
            case "lvsl":
            case "lvsr": {
                int sh = m.equals("lvsl") ? eb : 16 - eb;
                int[] r = new int[4];
                for (int i = 0; i < 16; i++) {
                    pb(r, i, sh + i);
                }
                v[op.vd] = r;
                return;
            }
            case "lvx": case "lvxl":
                v[op.vd] = toW(mem.read(ea & ~15L, 16));
                return;
            case "lvebx": case "lvehx": case "lvewx": {
                int n = m.equals("lvebx") ? 1 : m.equals("lvehx") ? 2 : 4;
                long a = ea & ~(long) (n - 1);
                byte[] d = mem.read(a, n), b = new byte[16];
                System.arraycopy(d, 0, b, (int) (a & 15), n);      // the other elements are undefined: zero
                v[op.vd] = toW(b);
                return;
            }
            case "lvlx": case "lvlxl": {                           // bytes ea..end of the quadword, left aligned
                byte[] d = mem.read(ea, 16 - eb), b = new byte[16];
                System.arraycopy(d, 0, b, 0, 16 - eb);
                v[op.vd] = toW(b);
                return;
            }
            case "lvrx": case "lvrxl": {                           // bytes start of the quadword..ea-1, right aligned
                byte[] b = new byte[16];
                if (eb != 0) {
                    byte[] d = mem.read(ea - eb, eb);
                    System.arraycopy(d, 0, b, 16 - eb, eb);
                }
                v[op.vd] = toW(b);
                return;
            }
            case "stvx": case "stvxl":
                mem.write(ea & ~15L, toB(v[op.vs]));
                return;
            case "stvebx": case "stvehx": case "stvewx": {
                int n = m.equals("stvebx") ? 1 : m.equals("stvehx") ? 2 : 4;
                long a = ea & ~(long) (n - 1);
                byte[] s = toB(v[op.vs]), d = new byte[n];
                System.arraycopy(s, (int) (a & 15), d, 0, n);
                mem.write(a, d);
                return;
            }
            case "stvlx": case "stvlxl": {
                byte[] s = toB(v[op.vs]), d = new byte[16 - eb];
                System.arraycopy(s, 0, d, 0, 16 - eb);
                mem.write(ea, d);
                return;
            }
            case "stvrx": case "stvrxl": {
                if (eb != 0) {
                    byte[] s = toB(v[op.vs]), d = new byte[eb];
                    System.arraycopy(s, 16 - eb, d, 0, eb);
                    mem.write(ea - eb, d);
                }
                return;
            }
            default:
                throw new UnsupportedOperationException(op.mn);
        }
    }
}

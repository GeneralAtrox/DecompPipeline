import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Decoding a 32-bit word to a TcVmx.Op, its dataflow lists and disassembly. */
abstract class TcVmxDecode extends TcVmxTables {
    /** Decode a 32-bit big-endian instruction word; null when it is not a vector instruction. */
    public static Op decode(int w) {
        Op o = null;
        switch (w >>> 26) {
            case 4: o = decode4(w); break;
            case 5: o = decode5(w); break;
            case 6: o = decode6(w); break;
            case 31: o = decode31(w); break;
            default: return null;
        }
        if (o == null) {
            return null;
        }
        finish(o);
        return o;
    }

    static Op decode31(int w) {
        String op = OP31.get(bits(w, 21, 30));
        if (op == null) {
            return null;
        }
        // decode.py tries the 9-bit XO-form arithmetic table first; none of the vector XOs collide with it
        // (checked by the decode test over all 1024 XOs), so the vector table is complete on its own.
        Op o = mk(w, op);
        int r6 = bits(w, 6, 10), r11 = bits(w, 11, 15), r16 = bits(w, 16, 20);
        if (VEC_LOAD31.contains(op)) {
            o.set("vd", r6);
            o.set("ra", r11);
            o.set("rb", r16);
        } else if (VEC_STORE31.contains(op)) {
            o.set("vs", r6);
            o.set("ra", r11);
            o.set("rb", r16);
        } else if (op.equals("dst") || op.equals("dstst")) {
            o.set("ra", r11);
            o.set("rb", r16);
            o.set("strm", bits(w, 9, 10));
            o.set("t", bits(w, 6, 6));
        } else {
            o.set("strm", bits(w, 9, 10));
            o.set("all", bits(w, 6, 6));
        }
        return o;
    }

    static Op decode4(int w) {
        int r6 = bits(w, 6, 10), r11 = bits(w, 11, 15), r16 = bits(w, 16, 20), r21 = bits(w, 21, 25);
        int low6 = w & 0x3F;
        Op o;
        if ((low6 & 0x10) != 0) {                      // VX128_5: vsldoi128 vD,vA,vB,SH (SH in bits 6-9)
            o = mk(w, "vsldoi128");
            o.set("vd", vd128(w));
            o.set("va", va128(w));
            o.set("vb", vb128(w));
            o.set("sh", (w >>> 6) & 15);
            return o;
        }
        if ((low6 & 0x20) != 0) {                      // VA form (XO 32..47)
            String op = VA4.get(low6);
            if (op == null) {
                return null;
            }
            if (op.equals("vsldoi")) {
                if (bits(w, 21, 21) != 0) {
                    return null;
                }
                o = mk(w, op);
                o.set("vd", r6);
                o.set("va", r11);
                o.set("vb", r16);
                o.set("sh", bits(w, 22, 25));
                return o;
            }
            o = mk(w, op);
            o.set("vd", r6);
            o.set("va", r11);
            o.set("vb", r16);
            o.set("vc", r21);
            return o;
        }
        if ((w & 3) == 3) {                            // VX128_1: vector loads/stores through rA/rB
            String op = VX128_1.get(w & 0x7F3);
            if (op == null) {
                return null;
            }
            o = mk(w, op);
            o.set(op.startsWith("st") ? "vs" : "vd", vd128(w));
            o.set("ra", r11);
            o.set("rb", r16);
            return o;
        }
        String vc = VC4.get(w & 0x3FF);
        if (vc != null) {
            o = mk(w, vc);
            o.set("vd", r6);
            o.set("va", r11);
            o.set("vb", r16);
            o.set("rc", bits(w, 21, 21));
            return o;
        }
        String op = VX4.get(w & 0x7FF);
        if (op == null) {
            return null;
        }
        o = mk(w, op);
        if (op.equals("mfvscr")) {
            o.set("vd", r6);
        } else if (op.equals("mtvscr")) {
            o.set("vb", r16);
        } else if (VX_UNARY.contains(op)) {
            o.set("vd", r6);
            o.set("vb", r16);
        } else if (VX_UIMM.contains(op)) {
            o.set("vd", r6);
            o.set("vb", r16);
            o.set("uimm", r11);
        } else if (VX_SIMM.contains(op)) {
            o.set("vd", r6);
            o.set("simm", sext(r11, 5));
        } else {
            o.set("vd", r6);
            o.set("va", r11);
            o.set("vb", r16);
        }
        return o;
    }

    static Op decode5(int w) {
        Op o;
        if ((w & 0x210) == 0) {                        // VX128_2: vperm128 vD,vA,vB,vC (vC in v0..v7)
            o = mk(w, "vperm128");
            o.set("vd", vd128(w));
            o.set("va", va128(w));
            o.set("vb", vb128(w));
            o.set("vc", (w >>> 6) & 7);
            return o;
        }
        String op = VX128_5.get(w & 0x3D0);
        if (op == null) {
            return null;
        }
        o = mk(w, op);
        o.set("vd", vd128(w));
        o.set("va", va128(w));
        o.set("vb", vb128(w));
        return o;
    }

    static Op decode6(int w) {
        Op o;
        if ((w & 0x10) == 0) {                         // VX128_R compares (Rc in bit 6)
            String op = VX128_6R.get(w & 0x390);
            if (op != null) {
                o = mk(w, op);
                o.set("vd", vd128(w));
                o.set("va", va128(w));
                o.set("vb", vb128(w));
                o.set("rc", (w >>> 6) & 1);
                return o;
            }
        }
        if ((w & 0x730) == 0x710) {                    // vrlimi128 vD,vB,IMM,z
            o = mk(w, "vrlimi128");
            o.set("vd", vd128(w));
            o.set("vb", vb128(w));
            o.set("imm", (w >>> 16) & 31);
            o.set("z", (w >>> 6) & 3);
            return o;
        }
        if ((w & 0x730) == 0x610) {                    // vpkd3d128 vD,vB,type,mask,z
            int imm = (w >>> 16) & 31;
            o = mk(w, "vpkd3d128");
            o.set("vd", vd128(w));
            o.set("vb", vb128(w));
            o.set("imm", imm >> 2);
            o.set("mask", imm & 3);
            o.set("z", (w >>> 6) & 3);
            return o;
        }
        String op = VX128_6U.get(w & 0x7F0);
        if (op != null) {                              // VX128_3: vD,vB,UIMM (vspltisw128: SIMM)
            int u = (w >>> 16) & 31;
            o = mk(w, op);
            o.set("vd", vd128(w));
            if (op.equals("vspltisw128")) {
                o.set("simm", sext(u, 5));
                return o;
            }
            o.set("vb", vb128(w));
            if (op.equals("vupkd3d128")) {
                o.set("imm", u >> 2);
                o.set("mask", u & 3);
            } else if (op.equals("vcfpsxws128") || op.equals("vcfpuxws128") || op.equals("vcsxwfp128")
                    || op.equals("vcuxwfp128") || op.equals("vspltw128")) {
                o.set("uimm", u);
            }
            return o;
        }
        if ((w & 0x630) == 0x210) {                    // vpermwi128 vD,vB,PERM
            o = mk(w, "vpermwi128");
            o.set("vd", vd128(w));
            o.set("vb", vb128(w));
            o.set("perm", ((w >>> 16) & 31) | (((w >>> 6) & 7) << 5));
            return o;
        }
        op = VX128_6.get(w & 0x3D0);
        if (op == null) {
            return null;
        }
        o = mk(w, op);
        o.set("vd", vd128(w));
        if (!VX128_6_UNARY.contains(op)) {
            o.set("va", va128(w));
        }
        o.set("vb", vb128(w));
        return o;
    }

    // Ops that read vD as well as write it (the result is merged into / computed from the old vD).
    static final Set<String> READS_VD = set(new String[] {"vmaddfp128", "vmaddcfp128", "vnmsubfp128",
        "vsel128", "vrlimi128", "vpkd3d128"});
    // Ops that may set VSCR[SAT].
    static final Set<String> SATURATING = set(new String[] {"vaddubs", "vadduhs", "vadduws", "vaddsbs",
        "vaddshs", "vaddsws", "vsububs", "vsubuhs", "vsubuws", "vsubsbs", "vsubshs", "vsubsws", "vpkuhus", "vpkuwus",
        "vpkshus", "vpkswus", "vpkshss", "vpkswss", "vpkuhus128", "vpkuwus128", "vpkshus128", "vpkswus128",
        "vpkshss128", "vpkswss128", "vsum4ubs", "vsum4sbs", "vsum4shs", "vsum2sws", "vsumsws", "vmsumuhs", "vmsumshs",
        "vmhaddshs", "vmhraddshs", "vctsxs", "vctuxs", "vcfpsxws128", "vcfpuxws128", "mtvscr"});

    static boolean isStore(String m) {
        return m.startsWith("st");
    }

    static boolean isMem(String m) {
        return m.startsWith("lv") || m.startsWith("stv");
    }

    static int[] ints(List<Integer> l) {
        int[] r = new int[l.size()];
        for (int i = 0; i < r.length; i++) {
            r[i] = l.get(i);
        }
        return r;
    }

    /** Dataflow lists and the disassembly string (in decode.py's disasm style). */
    static void finish(Op o) {
        String m = o.mn;
        List<Integer> vr = new ArrayList<>(), vw = new ArrayList<>(), gr = new ArrayList<>();
        boolean mem = isMem(m);
        if (mem || m.equals("dst") || m.equals("dstst")) {
            if (o.ra != NONE && o.ra != 0) {
                gr.add(o.ra);
            }
            gr.add(o.rb);
        }
        if (o.vs != NONE) {
            vr.add(o.vs);
        }
        if (o.vd != NONE) {
            vw.add(o.vd);
        }
        if (!mem) {
            if (o.va != NONE) {
                vr.add(o.va);
            }
            if (o.vb != NONE) {
                vr.add(o.vb);
            }
            if (o.vc != NONE) {
                vr.add(o.vc);
            }
            if (READS_VD.contains(m)) {
                vr.add(o.vd);
            }
        }
        o.vRead = ints(vr);
        o.vWrite = ints(vw);
        o.gRead = ints(gr);
        o.loads = mem && !isStore(m) && !m.startsWith("lvs");
        o.stores = mem && isStore(m);
        o.readsVscr = m.equals("mfvscr");
        o.writesVscr = SATURATING.contains(m);
        o.writesCr6 = o.rc != NONE && o.rc != 0;
        o.disasm = disasm(o);
    }

    static String disasm(Op o) {
        String m = o.mn;
        String dot = (o.rc != NONE && o.rc != 0 && !m.endsWith(".")) ? "." : "";
        if (isMem(m) || m.equals("lvsl") || m.equals("lvsr") || m.equals("lvsl128") || m.equals("lvsr128")) {
            return m + " v" + (o.vd != NONE ? o.vd : o.vs) + ",r" + o.ra + ",r" + o.rb;
        }
        StringBuilder sb = new StringBuilder();
        String[] regs = {"vd", "va", "vb", "vc"};
        int[] rv = {o.vd, o.va, o.vb, o.vc};
        List<String> parts = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            if (rv[i] != NONE) {
                parts.add("v" + rv[i]);
            }
        }
        int[] iv = {o.sh, o.uimm, o.simm, o.perm, o.imm, o.mask, o.z};
        for (int x : iv) {
            if (x != NONE) {
                parts.add(Integer.toString(x));
            }
        }
        sb.append(m).append(dot).append(' ').append(String.join(",", parts));
        return sb.toString();
    }
}

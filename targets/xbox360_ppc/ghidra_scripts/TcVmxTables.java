import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The decode tables and the set of mnemonics (generated from decode.py; see TcVmx), the field helpers. */
abstract class TcVmxTables extends TcVmxOp {
    // ============================================================================================ decode tables
    // BEGIN GENERATED TABLES (gen_tables.py, from decode.py)
    static final String[] T_VA4 = {
        "20=vmhaddshs", "21=vmhraddshs", "22=vmladduhm", "24=vmsumubm", "25=vmsummbm", "26=vmsumuhm", "27=vmsumuhs",
        "28=vmsumshm", "29=vmsumshs", "2a=vsel", "2b=vperm", "2c=vsldoi", "2e=vmaddfp", "2f=vnmsubfp"
    };
    static final String[] T_VC4 = {
        "6=vcmpequb", "46=vcmpequh", "86=vcmpequw", "c6=vcmpeqfp", "1c6=vcmpgefp", "206=vcmpgtub", "246=vcmpgtuh",
        "286=vcmpgtuw", "2c6=vcmpgtfp", "306=vcmpgtsb", "346=vcmpgtsh", "386=vcmpgtsw", "3c6=vcmpbfp"
    };
    static final String[] T_VX4 = {
        "0=vaddubm", "2=vmaxub", "4=vrlb", "8=vmuloub", "a=vaddfp", "c=vmrghb", "e=vpkuhum", "40=vadduhm",
        "42=vmaxuh", "44=vrlh", "48=vmulouh", "4a=vsubfp", "4c=vmrghh", "4e=vpkuwum", "80=vadduwm", "82=vmaxuw",
        "84=vrlw", "8c=vmrghw", "8e=vpkuhus", "ce=vpkuwus", "102=vmaxsb", "104=vslb", "108=vmulosb", "10a=vrefp",
        "10c=vmrglb", "10e=vpkshus", "142=vmaxsh", "144=vslh", "148=vmulosh", "14a=vrsqrtefp", "14c=vmrglh",
        "14e=vpkswus", "180=vaddcuw", "182=vmaxsw", "184=vslw", "18a=vexptefp", "18c=vmrglw", "18e=vpkshss",
        "1c4=vsl", "1ca=vlogefp", "1ce=vpkswss", "200=vaddubs", "202=vminub", "204=vsrb", "208=vmuleub", "20a=vrfin",
        "20c=vspltb", "20e=vupkhsb", "240=vadduhs", "242=vminuh", "244=vsrh", "248=vmuleuh", "24a=vrfiz",
        "24c=vsplth", "24e=vupkhsh", "280=vadduws", "282=vminuw", "284=vsrw", "28a=vrfip", "28c=vspltw",
        "28e=vupklsb", "2c4=vsr", "2ca=vrfim", "2ce=vupklsh", "300=vaddsbs", "302=vminsb", "304=vsrab",
        "308=vmulesb", "30a=vcfux", "30c=vspltisb", "30e=vpkpx", "340=vaddshs", "342=vminsh", "344=vsrah",
        "348=vmulesh", "34a=vcfsx", "34c=vspltish", "34e=vupkhpx", "380=vaddsws", "382=vminsw", "384=vsraw",
        "38a=vctuxs", "38c=vspltisw", "3ca=vctsxs", "3ce=vupklpx", "400=vsububm", "402=vavgub", "404=vand",
        "40a=vmaxfp", "40c=vslo", "440=vsubuhm", "442=vavguh", "444=vandc", "44a=vminfp", "44c=vsro", "480=vsubuwm",
        "482=vavguw", "484=vor", "4c4=vxor", "502=vavgsb", "504=vnor", "542=vavgsh", "580=vsubcuw", "582=vavgsw",
        "600=vsububs", "604=mfvscr", "608=vsum4ubs", "640=vsubuhs", "644=mtvscr", "648=vsum4shs", "680=vsubuws",
        "688=vsum2sws", "700=vsubsbs", "708=vsum4sbs", "740=vsubshs", "780=vsubsws", "788=vsumsws"
    };
    static final String[] T_OP31 = {
        "6=lvsl", "7=lvebx", "26=lvsr", "27=lvehx", "47=lvewx", "67=lvx", "87=stvebx", "a7=stvehx", "c7=stvewx",
        "e7=stvx", "156=dst", "167=lvxl", "176=dstst", "1e7=stvxl", "207=lvlx", "227=lvrx", "287=stvlx", "2a7=stvrx",
        "307=lvlxl", "327=lvrxl", "336=dss", "387=stvlxl", "3a7=stvrxl"
    };
    static final String[] T_VX128_1 = {
        "3=lvsl128", "43=lvsr128", "83=lvewx128", "c3=lvx128", "183=stvewx128", "1c3=stvx128", "2c3=lvxl128",
        "3c3=stvxl128", "403=lvlx128", "443=lvrx128", "503=stvlx128", "543=stvrx128", "603=lvlxl128", "643=lvrxl128",
        "703=stvlxl128", "743=stvrxl128"
    };
    static final String[] T_VX128_5 = {
        "10=vaddfp128", "50=vsubfp128", "90=vmulfp128", "d0=vmaddfp128", "110=vmaddcfp128", "150=vnmsubfp128",
        "190=vmsum3fp128", "1d0=vmsum4fp128", "200=vpkshss128", "210=vand128", "240=vpkshus128", "250=vandc128",
        "280=vpkswss128", "290=vnor128", "2c0=vpkswus128", "2d0=vor128", "300=vpkuhum128", "310=vxor128",
        "340=vpkuhus128", "350=vsel128", "380=vpkuwum128", "390=vslo128", "3c0=vpkuwus128", "3d0=vsro128"
    };
    static final String[] T_VX128_6R = {
        "0=vcmpeqfp128", "80=vcmpgefp128", "100=vcmpgtfp128", "180=vcmpbfp128", "200=vcmpequw128"
    };
    static final String[] T_VX128_6 = {
        "50=vrlw128", "d0=vslw128", "150=vsraw128", "1d0=vsrw128", "280=vmaxfp128", "2c0=vminfp128", "300=vmrghw128",
        "340=vmrglw128", "380=vupkhsb128", "3c0=vupklsb128"
    };
    static final String[] T_VX128_6U = {
        "230=vcfpsxws128", "270=vcfpuxws128", "2b0=vcsxwfp128", "2f0=vcuxwfp128", "330=vrfim128", "370=vrfin128",
        "3b0=vrfip128", "3f0=vrfiz128", "630=vrefp128", "670=vrsqrtefp128", "6b0=vexptefp128", "6f0=vlogefp128",
        "730=vspltw128", "770=vspltisw128", "7f0=vupkd3d128"
    };
    static final String[] N_VX_UNARY = {
        "vexptefp", "vlogefp", "vrefp", "vrfim", "vrfin", "vrfip", "vrfiz", "vrsqrtefp", "vupkhpx", "vupkhsb",
        "vupkhsh", "vupklpx", "vupklsb", "vupklsh"
    };
    static final String[] N_VX_UIMM = {
        "vcfsx", "vcfux", "vctsxs", "vctuxs", "vspltb", "vsplth", "vspltw"
    };
    static final String[] N_VX_SIMM = {
        "vspltisb", "vspltish", "vspltisw"
    };
    static final String[] N_VX128_6_UNARY = {
        "vupkhsb128", "vupklsb128"
    };
    static final String[] N_VEC_LOAD31 = {
        "lvebx", "lvehx", "lvewx", "lvlx", "lvlxl", "lvrx", "lvrxl", "lvsl", "lvsr", "lvx", "lvxl"
    };
    static final String[] N_VEC_STORE31 = {
        "stvebx", "stvehx", "stvewx", "stvlx", "stvlxl", "stvrx", "stvrxl", "stvx", "stvxl"
    };
    // END GENERATED TABLES

    static Map<Integer, String> tab(String[] a) {
        Map<Integer, String> m = new HashMap<>();
        for (String s : a) {
            int i = s.indexOf('=');
            m.put(Integer.parseInt(s.substring(0, i), 16), s.substring(i + 1));
        }
        return m;
    }

    static Set<String> set(String[] a) {
        Set<String> s = new HashSet<>();
        for (String x : a) {
            s.add(x);
        }
        return s;
    }

    static final Map<Integer, String> VA4 = tab(T_VA4), VC4 = tab(T_VC4), VX4 = tab(T_VX4), OP31 = tab(T_OP31),
            VX128_1 = tab(T_VX128_1), VX128_5 = tab(T_VX128_5), VX128_6R = tab(T_VX128_6R), VX128_6 = tab(T_VX128_6),
            VX128_6U = tab(T_VX128_6U);
    static final Set<String> VX_UNARY = set(N_VX_UNARY), VX_UIMM = set(N_VX_UIMM), VX_SIMM = set(N_VX_SIMM),
            VX128_6_UNARY = set(N_VX128_6_UNARY), VEC_LOAD31 = set(N_VEC_LOAD31), VEC_STORE31 = set(N_VEC_STORE31);

    /** Every mnemonic decode() can return. */
    public static final Set<String> ALL = new HashSet<>();
    /** Decoded but not executable: the hardware estimate tables are unknown. */
    static final Set<String> ESTIMATES = set(new String[] {"vrefp", "vrsqrtefp", "vexptefp", "vlogefp",
        "vrefp128", "vrsqrtefp128", "vexptefp128", "vlogefp128"});

    static {
        for (Map<Integer, String> m : List.of(VA4, VC4, VX4, OP31, VX128_1, VX128_5, VX128_6R, VX128_6, VX128_6U)) {
            ALL.addAll(m.values());
        }
        ALL.addAll(List.of("vperm128", "vsldoi128", "vpermwi128", "vrlimi128", "vpkd3d128"));
    }

    /** True when exec() implements the mnemonic (every vector op except the estimate ops). */
    public static boolean supported(String mnemonic) {
        return ALL.contains(mnemonic) && !ESTIMATES.contains(mnemonic);
    }

    // ================================================================================================== decode
    static int bits(int w, int a, int b) {
        return (w >>> (31 - b)) & ((1 << (b - a + 1)) - 1);
    }

    static int sext(int v, int n) {
        v &= (1 << n) - 1;
        return (v & (1 << (n - 1))) != 0 ? v - (1 << n) : v;
    }

    static int vd128(int w) {
        return ((w >>> 21) & 31) | (((w >>> 2) & 3) << 5);
    }

    static int va128(int w) {
        return ((w >>> 16) & 31) | (((w >>> 5) & 1) << 5) | (((w >>> 10) & 1) << 6);
    }

    static int vb128(int w) {
        return ((w >>> 11) & 31) | ((w & 3) << 5);
    }

    static Op mk(int w, String mn) {
        Op o = new Op();
        o.word = w;
        o.mn = mn;
        return o;
    }
}

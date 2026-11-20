import java.util.HashSet;
import java.util.Set;

/**
 * The vector ops the judge may rely on: the one list (the coordinator's rule, 2026-11-14). TcFixups.refuseVector
 * refuses any function that holds a vector op not listed here, as "VMX unconfirmed <op>", and tools/catalog_refuse.py
 * reads this same file (the rows below, by pattern) so the catalog and the oracle agree.
 *
 * An op is listed only once something independent of the translator checks it: the AltiVec PEM or IBM's Cell BE
 * Architecture, the game's own code, Xenia's PPC tables read as a second reference, or hardware. Each row names that
 * source (a page, a section, a game address). TcVmx running an op is not a check: its VMX128 semantics come from
 * Xenia's descriptions and its decode tables are generated from the translator's decode.py.
 *
 * To add an op, add a row: row("vaddfp", "AltiVec PEM, vaddfp"). Keep the row on one line and the op a plain
 * lower-case mnemonic as decode.py names it; the catalog scan reads it with a regular expression.
 * Plan: .work/tx1112/VMX_JUDGE_PLAN.md (P1 and P2 add the ops). No Ghidra imports.
 */
public final class TcVmxConfirmed {
    private TcVmxConfirmed() {
    }

    static final Set<String> OPS = new HashSet<>();

    private static void row(String op, String source) {
        if (source.isEmpty()) throw new IllegalArgumentException("no source for " + op);
        OPS.add(op);
    }

    static {
        // BEGIN CONFIRMED
        // P1 (2026-11-14): the plain AltiVec ops (a) checked against the AltiVec PEM's operation text (ch.6, by
        // instruction; lane order, saturation, non-Java mode, NaN, shift counts modulo the element, permute and splat
        // indices) with Xenia's emitter as the second reading; the Cell forms lvlx/lvrx/stvlx/stvrx (CBEA) the same;
        // each VX128 form (b) checked as its base op with the register fields of Xenia's ppc_decode_data.h (commit
        // 95a5c3ee) and its opcode against ppc_opcode_lookup_gen.cc on every low-11-bit pattern and every game word.
        // Not listed: vupkhsb128 and vupklsb128, whose VA = 0x60 words are the halfword form (see TcVmxDecode.decode6).
        row("vaddshs", "AltiVec PEM ch.6 vaddshs; Xenia 95a5c3ee ppc_emit_altivec.cc:391");
        row("vslh", "AltiVec PEM ch.6 vslh; Xenia 95a5c3ee ppc_emit_altivec.cc:1416");
        row("vspltw", "AltiVec PEM ch.6 vspltw; Xenia 95a5c3ee ppc_emit_altivec.cc:1526");
        row("vadduhm", "AltiVec PEM ch.6 vadduhm; Xenia 95a5c3ee ppc_emit_altivec.cc:422");
        row("vor", "AltiVec PEM ch.6 vor; Xenia 95a5c3ee ppc_emit_altivec.cc:1195");
        row("vmrghw", "AltiVec PEM ch.6 vmrghw; Xenia 95a5c3ee ppc_emit_altivec.cc:1011");
        row("vsubshs", "AltiVec PEM ch.6 vsubshs; Xenia 95a5c3ee ppc_emit_altivec.cc:1695");
        row("vmaddfp", "AltiVec PEM ch.6 vmaddfp; Xenia 95a5c3ee ppc_emit_altivec.cc:828");
        row("vmrghb", "AltiVec PEM ch.6 vmrghb; Xenia 95a5c3ee ppc_emit_altivec.cc:978");
        row("vsrah", "AltiVec PEM ch.6 vsrah; Xenia 95a5c3ee ppc_emit_altivec.cc:1599");
        row("vsubfp", "AltiVec PEM ch.6 vsubfp; Xenia 95a5c3ee ppc_emit_altivec.cc:1679");
        row("stvewx", "AltiVec PEM ch.6 stvewx; Xenia 95a5c3ee ppc_emit_altivec.cc:180");
        row("vaddfp", "AltiVec PEM ch.6 vaddfp; Xenia 95a5c3ee ppc_emit_altivec.cc:376");
        row("vmrglb", "AltiVec PEM ch.6 vmrglb; Xenia 95a5c3ee ppc_emit_altivec.cc:1018");
        row("vspltish", "AltiVec PEM ch.6 vspltish; Xenia 95a5c3ee ppc_emit_altivec.cc:1548");
        row("vxor", "AltiVec PEM ch.6 vxor; Xenia 95a5c3ee ppc_emit_altivec.cc:2212");
        row("lvsl", "AltiVec PEM ch.6 lvsl; Xenia 95a5c3ee ppc_emit_altivec.cc:111");
        row("vaddsws", "AltiVec PEM ch.6 vaddsws; Xenia 95a5c3ee ppc_emit_altivec.cc:399");
        row("vsldoi", "AltiVec PEM ch.6 vsldoi; Xenia 95a5c3ee ppc_emit_altivec.cc:1474");
        row("vmrglw", "AltiVec PEM ch.6 vmrglw; Xenia 95a5c3ee ppc_emit_altivec.cc:1052");
        row("vsubuhm", "AltiVec PEM ch.6 vsubuhm; Xenia 95a5c3ee ppc_emit_altivec.cc:1721");
        row("vcfsx", "AltiVec PEM ch.6 vcfsx; Xenia 95a5c3ee ppc_emit_altivec.cc:535");
        row("vmaxsh", "AltiVec PEM ch.6 vmaxsh; Xenia 95a5c3ee ppc_emit_altivec.cc:867");
        row("vsubsws", "AltiVec PEM ch.6 vsubsws; Xenia 95a5c3ee ppc_emit_altivec.cc:1704");
        row("vspltisw", "AltiVec PEM ch.6 vspltisw; Xenia 95a5c3ee ppc_emit_altivec.cc:1577");
        row("vmrghh", "AltiVec PEM ch.6 vmrghh; Xenia 95a5c3ee ppc_emit_altivec.cc:990");
        row("vand", "AltiVec PEM ch.6 vand; Xenia 95a5c3ee ppc_emit_altivec.cc:458");
        row("vperm", "AltiVec PEM ch.6 vperm; Xenia 95a5c3ee ppc_emit_altivec.cc:1208");
        row("vadduwm", "AltiVec PEM ch.6 vadduwm; Xenia 95a5c3ee ppc_emit_altivec.cc:437");
        row("vnmsubfp", "AltiVec PEM ch.6 vnmsubfp; Xenia 95a5c3ee ppc_emit_altivec.cc:1163");
        row("vmrglh", "AltiVec PEM ch.6 vmrglh; Xenia 95a5c3ee ppc_emit_altivec.cc:1030");
        row("vspltisb", "AltiVec PEM ch.6 vspltisb; Xenia 95a5c3ee ppc_emit_altivec.cc:1533");
        row("vupkhsh", "AltiVec PEM ch.6 vupkhsh; Xenia 95a5c3ee ppc_emit_altivec.cc:1985");
        row("vctsxs", "AltiVec PEM ch.6 vctsxs; Xenia 95a5c3ee ppc_emit_altivec.cc:571");
        row("vcmpgtuh", "AltiVec PEM ch.6 vcmpgtuh; Xenia 95a5c3ee ppc_emit_altivec.cc:786");
        row("stvx", "AltiVec PEM ch.6 stvx; Xenia 95a5c3ee ppc_emit_altivec.cc:193");
        row("vavgub", "AltiVec PEM ch.6 vavgub; Xenia 95a5c3ee ppc_emit_altivec.cc:503");
        row("vcmpgtfp", "AltiVec PEM ch.6 vcmpgtfp; Xenia 95a5c3ee ppc_emit_altivec.cc:674");
        row("vsel", "AltiVec PEM ch.6 vsel; Xenia 95a5c3ee ppc_emit_altivec.cc:1395");
        row("vsplth", "AltiVec PEM ch.6 vsplth; Xenia 95a5c3ee ppc_emit_altivec.cc:1510");
        row("vminsh", "AltiVec PEM ch.6 vminsh; Xenia 95a5c3ee ppc_emit_altivec.cc:935");
        row("vpkshus", "AltiVec PEM ch.6 vpkshus; Xenia 95a5c3ee ppc_emit_altivec.cc:1917");
        row("lvewx", "AltiVec PEM ch.6 lvewx; Xenia 95a5c3ee ppc_emit_altivec.cc:96");
        row("vcmpgtsh", "AltiVec PEM ch.6 vcmpgtsh; Xenia 95a5c3ee ppc_emit_altivec.cc:774");
        row("vaddubm", "AltiVec PEM ch.6 vaddubm; Xenia 95a5c3ee ppc_emit_altivec.cc:407");
        row("vminfp", "AltiVec PEM ch.6 vminfp; Xenia 95a5c3ee ppc_emit_altivec.cc:921");
        row("vupklsh", "AltiVec PEM ch.6 vupklsh; Xenia 95a5c3ee ppc_emit_altivec.cc:2003");
        row("vmaxfp", "AltiVec PEM ch.6 vmaxfp; Xenia 95a5c3ee ppc_emit_altivec.cc:853");
        row("vcfux", "AltiVec PEM ch.6 vcfux; Xenia 95a5c3ee ppc_emit_altivec.cc:553");
        row("vupkhsb", "AltiVec PEM ch.6 vupkhsb; Xenia 95a5c3ee ppc_emit_altivec.cc:2021");
        row("lvx", "AltiVec PEM ch.6 lvx; Xenia 95a5c3ee ppc_emit_altivec.cc:139");
        row("vcmpeqfp", "AltiVec PEM ch.6 vcmpeqfp; Xenia 95a5c3ee ppc_emit_altivec.cc:658");
        row("vcmpgtsw", "AltiVec PEM ch.6 vcmpgtsw; Xenia 95a5c3ee ppc_emit_altivec.cc:778");
        row("vcmpequw", "AltiVec PEM ch.6 vcmpequw; Xenia 95a5c3ee ppc_emit_altivec.cc:762");
        row("vsr", "AltiVec PEM ch.6 vsr; Xenia 95a5c3ee ppc_emit_altivec.cc:1584");
        row("vcmpgefp", "AltiVec PEM ch.6 vcmpgefp; Xenia 95a5c3ee ppc_emit_altivec.cc:666");
        row("vslb", "AltiVec PEM ch.6 vslb; Xenia 95a5c3ee ppc_emit_altivec.cc:1410");
        row("vsrw", "AltiVec PEM ch.6 vsrw; Xenia 95a5c3ee ppc_emit_altivec.cc:1657");
        row("vpkswss", "AltiVec PEM ch.6 vpkswss; Xenia 95a5c3ee ppc_emit_altivec.cc:1831");
        row("vadduws", "AltiVec PEM ch.6 vadduws; Xenia 95a5c3ee ppc_emit_altivec.cc:444");
        row("vavgsh", "AltiVec PEM ch.6 vavgsh; Xenia 95a5c3ee ppc_emit_altivec.cc:485");
        row("vcmpequh", "AltiVec PEM ch.6 vcmpequh; Xenia 95a5c3ee ppc_emit_altivec.cc:758");
        row("vslw", "AltiVec PEM ch.6 vslw; Xenia 95a5c3ee ppc_emit_altivec.cc:1430");
        row("vpkshss", "AltiVec PEM ch.6 vpkshss; Xenia 95a5c3ee ppc_emit_altivec.cc:1809");
        row("vaddsbs", "AltiVec PEM ch.6 vaddsbs; Xenia 95a5c3ee ppc_emit_altivec.cc:383");
        row("vrlh", "AltiVec PEM ch.6 vrlh; Xenia 95a5c3ee ppc_emit_altivec.cc:1303");
        row("stvehx", "AltiVec PEM ch.6 stvehx; Xenia 95a5c3ee ppc_emit_altivec.cc:160");
        row("vspltb", "AltiVec PEM ch.6 vspltb; Xenia 95a5c3ee ppc_emit_altivec.cc:1500");
        row("vavguh", "AltiVec PEM ch.6 vavguh; Xenia 95a5c3ee ppc_emit_altivec.cc:510");
        row("vandc", "AltiVec PEM ch.6 vandc; Xenia 95a5c3ee ppc_emit_altivec.cc:471");
        row("vsrh", "AltiVec PEM ch.6 vsrh; Xenia 95a5c3ee ppc_emit_altivec.cc:1626");
        row("vcmpgtub", "AltiVec PEM ch.6 vcmpgtub; Xenia 95a5c3ee ppc_emit_altivec.cc:782");
        row("vupklsb", "AltiVec PEM ch.6 vupklsb; Xenia 95a5c3ee ppc_emit_altivec.cc:2042");
        row("vaddubs", "AltiVec PEM ch.6 vaddubs; Xenia 95a5c3ee ppc_emit_altivec.cc:414");
        row("vsububm", "AltiVec PEM ch.6 vsububm; Xenia 95a5c3ee ppc_emit_altivec.cc:1713");
        row("vsrab", "AltiVec PEM ch.6 vsrab; Xenia 95a5c3ee ppc_emit_altivec.cc:1592");
        row("vrfim", "AltiVec PEM ch.6 vrfim; Xenia 95a5c3ee ppc_emit_altivec.cc:1249");
        row("vrfiz", "AltiVec PEM ch.6 vrfiz; Xenia 95a5c3ee ppc_emit_altivec.cc:1288");
        row("lvsr", "AltiVec PEM ch.6 lvsr; Xenia 95a5c3ee ppc_emit_altivec.cc:126");
        row("vrfin", "AltiVec PEM ch.6 vrfin; Xenia 95a5c3ee ppc_emit_altivec.cc:1262");
        row("vcmpequb", "AltiVec PEM ch.6 vcmpequb; Xenia 95a5c3ee ppc_emit_altivec.cc:754");
        row("vsraw", "AltiVec PEM ch.6 vsraw; Xenia 95a5c3ee ppc_emit_altivec.cc:1612");
        row("vmaxsw", "AltiVec PEM ch.6 vmaxsw; Xenia 95a5c3ee ppc_emit_altivec.cc:874");
        row("vavgsb", "AltiVec PEM ch.6 vavgsb; Xenia 95a5c3ee ppc_emit_altivec.cc:478");
        row("lvlx", "Cell BE VMX PEM v2.07c lvlx (CBEA); Xenia 95a5c3ee ppc_emit_altivec.cc:220");
        row("lvlx128", "Cell BE VMX PEM v2.07c lvlx (CBEA); Xenia 95a5c3ee ppc_emit_altivec.cc:223; VX128_1 fields ppc_decode_data.h:493");
        row("lvrx128", "Cell BE VMX PEM v2.07c lvrx (CBEA); Xenia 95a5c3ee ppc_emit_altivec.cc:264; VX128_1 fields ppc_decode_data.h:493");
        row("stvlx128", "Cell BE VMX PEM v2.07c stvlx (CBEA); Xenia 95a5c3ee ppc_emit_altivec.cc:297; VX128_1 fields ppc_decode_data.h:493");
        row("stvlx", "Cell BE VMX PEM v2.07c stvlx (CBEA); Xenia 95a5c3ee ppc_emit_altivec.cc:294");
        row("stvrx", "Cell BE VMX PEM v2.07c stvrx (CBEA); Xenia 95a5c3ee ppc_emit_altivec.cc:337");
        row("stvrx128", "Cell BE VMX PEM v2.07c stvrx (CBEA); Xenia 95a5c3ee ppc_emit_altivec.cc:340; VX128_1 fields ppc_decode_data.h:493");
        row("lvrx", "Cell BE VMX PEM v2.07c lvrx (CBEA); Xenia 95a5c3ee ppc_emit_altivec.cc:261");
        row("lvx128", "VX128 form of lvx (PEM ch.6 lvx); Xenia 95a5c3ee ppc_emit_altivec.cc:142; fields ppc_decode_data.h:468-660");
        row("stvx128", "VX128 form of stvx (PEM ch.6 stvx); Xenia 95a5c3ee ppc_emit_altivec.cc:196; fields ppc_decode_data.h:468-660");
        row("vmulfp128", "VX128 form of vmaddfp (PEM ch.6 vmaddfp); Xenia 95a5c3ee ppc_emit_altivec.cc:1147; fields ppc_decode_data.h:468-660");
        row("vor128", "VX128 form of vor (PEM ch.6 vor); Xenia 95a5c3ee ppc_emit_altivec.cc:1198; fields ppc_decode_data.h:468-660");
        row("vperm128", "VX128 form of vperm (PEM ch.6 vperm); Xenia 95a5c3ee ppc_emit_altivec.cc:1211; fields ppc_decode_data.h:468-660");
        row("vcsxwfp128", "VX128 form of vcfsx (PEM ch.6 vcfsx); Xenia 95a5c3ee ppc_emit_altivec.cc:538; fields ppc_decode_data.h:468-660");
        row("vspltw128", "VX128 form of vspltw (PEM ch.6 vspltw); Xenia 95a5c3ee ppc_emit_altivec.cc:1529; fields ppc_decode_data.h:468-660");
        row("vsubfp128", "VX128 form of vsubfp (PEM ch.6 vsubfp); Xenia 95a5c3ee ppc_emit_altivec.cc:1682; fields ppc_decode_data.h:468-660");
        row("vpkshus128", "VX128 form of vpkshus (PEM ch.6 vpkshus); Xenia 95a5c3ee ppc_emit_altivec.cc:1920; fields ppc_decode_data.h:468-660");
        row("stvewx128", "VX128 form of stvewx (PEM ch.6 stvewx); Xenia 95a5c3ee ppc_emit_altivec.cc:183; fields ppc_decode_data.h:468-660");
        row("vsraw128", "VX128 form of vsraw (PEM ch.6 vsraw); Xenia 95a5c3ee ppc_emit_altivec.cc:1615; fields ppc_decode_data.h:468-660");
        row("vsldoi128", "VX128 form of vsldoi (PEM ch.6 vsldoi); Xenia 95a5c3ee ppc_emit_altivec.cc:1477; fields ppc_decode_data.h:468-660");
        row("vand128", "VX128 form of vand (PEM ch.6 vand); Xenia 95a5c3ee ppc_emit_altivec.cc:461; fields ppc_decode_data.h:468-660");
        row("vmrghw128", "VX128 form of vmrghw (PEM ch.6 vmrghw); Xenia 95a5c3ee ppc_emit_altivec.cc:1014; fields ppc_decode_data.h:468-660");
        row("vslw128", "VX128 form of vslw (PEM ch.6 vslw); Xenia 95a5c3ee ppc_emit_altivec.cc:1433; fields ppc_decode_data.h:468-660");
        row("vxor128", "VX128 form of vxor (PEM ch.6 vxor); Xenia 95a5c3ee ppc_emit_altivec.cc:2215; fields ppc_decode_data.h:468-660");
        row("vcfpsxws128", "VX128 form of vctsxs (PEM ch.6 vctsxs); Xenia 95a5c3ee ppc_emit_altivec.cc:574; fields ppc_decode_data.h:468-660");
        row("vmrglw128", "VX128 form of vmrglw (PEM ch.6 vmrglw); Xenia 95a5c3ee ppc_emit_altivec.cc:1055; fields ppc_decode_data.h:468-660");
        row("vaddfp128", "VX128 form of vaddfp (PEM ch.6 vaddfp); Xenia 95a5c3ee ppc_emit_altivec.cc:379; fields ppc_decode_data.h:468-660");
        row("vandc128", "VX128 form of vandc (PEM ch.6 vandc); Xenia 95a5c3ee ppc_emit_altivec.cc:474; fields ppc_decode_data.h:468-660");
        row("vspltisw128", "VX128 form of vspltisw (PEM ch.6 vspltisw); Xenia 95a5c3ee ppc_emit_altivec.cc:1580; fields ppc_decode_data.h:468-660");
        row("vcuxwfp128", "VX128 form of vcfux (PEM ch.6 vcfux); Xenia 95a5c3ee ppc_emit_altivec.cc:556; fields ppc_decode_data.h:468-660");
        row("vpkswss128", "VX128 form of vpkswss (PEM ch.6 vpkswss); Xenia 95a5c3ee ppc_emit_altivec.cc:1834; fields ppc_decode_data.h:468-660");
        row("vcfpuxws128", "VX128 form of vctuxs (PEM ch.6 vctuxs); Xenia 95a5c3ee ppc_emit_altivec.cc:592; fields ppc_decode_data.h:468-660");
        row("vmaddfp128", "VX128 form of vmaddfp (PEM ch.6 vmaddfp); Xenia 95a5c3ee ppc_emit_altivec.cc:832; fields ppc_decode_data.h:468-660");
        row("vmaxfp128", "VX128 form of vmaxfp (PEM ch.6 vmaxfp); Xenia 95a5c3ee ppc_emit_altivec.cc:856; fields ppc_decode_data.h:468-660");
        row("vsrw128", "VX128 form of vsrw (PEM ch.6 vsrw); Xenia 95a5c3ee ppc_emit_altivec.cc:1660; fields ppc_decode_data.h:468-660");
        row("vcmpgtfp128", "VX128 form of vcmpgtfp (PEM ch.6 vcmpgtfp); Xenia 95a5c3ee ppc_emit_altivec.cc:678; fields ppc_decode_data.h:468-660");
        row("vminfp128", "VX128 form of vminfp (PEM ch.6 vminfp); Xenia 95a5c3ee ppc_emit_altivec.cc:924; fields ppc_decode_data.h:468-660");
        row("vcmpeqfp128", "VX128 form of vcmpeqfp (PEM ch.6 vcmpeqfp); Xenia 95a5c3ee ppc_emit_altivec.cc:662; fields ppc_decode_data.h:468-660");
        row("vpkswus128", "VX128 form of vpkswus (PEM ch.6 vpkswus); Xenia 95a5c3ee ppc_emit_altivec.cc:1856; fields ppc_decode_data.h:468-660");
        row("vpkuhus128", "VX128 form of vpkuhus (PEM ch.6 vpkuhus); Xenia 95a5c3ee ppc_emit_altivec.cc:1898; fields ppc_decode_data.h:468-660");
        row("vmaddcfp128", "VX128 form of vmaddfp (PEM ch.6 vmaddfp); Xenia 95a5c3ee ppc_emit_altivec.cc:839; fields ppc_decode_data.h:468-660");
        row("vcmpequw128", "VX128 form of vcmpequw (PEM ch.6 vcmpequw); Xenia 95a5c3ee ppc_emit_altivec.cc:766; fields ppc_decode_data.h:468-660");
        row("vnmsubfp128", "VX128 form of vnmsubfp (PEM ch.6 vnmsubfp); Xenia 95a5c3ee ppc_emit_altivec.cc:1166; fields ppc_decode_data.h:468-660");
        row("vsel128", "VX128 form of vsel (PEM ch.6 vsel); Xenia 95a5c3ee ppc_emit_altivec.cc:1398; fields ppc_decode_data.h:468-660");
        row("vcmpgefp128", "VX128 form of vcmpgefp (PEM ch.6 vcmpgefp); Xenia 95a5c3ee ppc_emit_altivec.cc:670; fields ppc_decode_data.h:468-660");
        row("vpkshss128", "VX128 form of vpkshss (PEM ch.6 vpkshss); Xenia 95a5c3ee ppc_emit_altivec.cc:1812; fields ppc_decode_data.h:468-660");
        // P2 (2026-11-14): the VMX128-only shuffles, from the game's own code, where scalar code fixes each lane (decode
        // and meaning together: the immediates below are decode.py's fields of the game's words). Checked in
        // tests/test_x360_vmx_p2.py (TcVmxP2Stub and the C helpers) on these sites' words and lanes. Xenia (XeniaRedux
        // 3c660748 ppc_emit_altivec.cc:1183 and :1291) reads both the same way, as a second reference only.
        // vpermwi128: result word k = vB word ((PERM >> (6 - 2k)) & 3). 82e90d48 (lane order of a plane): perms 135, 99
        // on two points, vmulfp128, vsubfp make (cross, 0); vspltw lane 3 is then added to vmsum3fp128 of lanes 0-2, the
        // plane-point distance. 82e74950: the same perms on two triangle edges; the cross product is stored at 0x80(r1)
        // and lfs reads 0x80, 0x84, 0x88 into the w lanes of rows 0xf0, 0x100, 0x110 in that order. The reversed reading
        // makes lane 0 of both a*b - a*b (always 0) and puts the x term in lane 3.
        row("vpermwi128", "game code: 82e90d48 (82e90d6c-82e90d84) and 82e74950 (82e749d4-82e749ec, read back 82e74a18); Xenia 3c660748 ppc_emit_altivec.cc:1183");
        // vrlimi128: rotate vB left by z words, then word k = rotated word k where IMM bit (8 >> k) is set, else vD.
        // 827be458 (a script call, x += arg 2, y += arg 3, z += arg 4): args stored at 0x54, 0x50, 0x58 (r1), 0.0 at 0x5c;
        // lvlx, vrlimi128 4,3 / 4,3 / 3,2 make (arg2, arg3, arg4, 0); stvx 0x70, lfs 0x70/0x74/0x78 are added to the
        // object's +0x30/+0x34/+0x38. 827c5530 (scale a matrix's rows by *r4, *r5, *r6): the same three words make
        // (*r4, *r5, *r6, 0.0), read back by lfs -0x40/-0x3c/-0x38, then rows 0, 1, 2 multiplied by (s, s, s, 1.0).
        // Rotating right reads lvlx's zero fill or another argument (arg 3 is never used, arg 4 twice); a reversed mask
        // (bit 0 = word 0) overwrites lane 0 and leaves the rows' scale factors zero.
        row("vrlimi128", "game code: 827be458 (827be644-827be654, read back 827be660-827be668) and 827c5530 (827c5548-827c5574, read back 827c559c-827c55b4); Xenia 3c660748 ppc_emit_altivec.cc:1291");
        // END CONFIRMED
    }

    /** Why a vector op is refused, or null when it is confirmed. */
    public static String refusal(String mnemonic, Set<String> confirmed) {
        return confirmed.contains(mnemonic) ? null : "VMX unconfirmed " + mnemonic;
    }

    public static String refusal(String mnemonic) {
        return refusal(mnemonic, OPS);
    }
}

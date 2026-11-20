"""Converts the Xbox 360 shader programs of a game image to HLSL, offline (DESIGN_PORT.md section 2: shaders are converted before
the game runs; nothing is read from microcode at play time, and the generated text is plain Direct3D 11 HLSL).

A shader in the image is a container (magic 0x102A1100 pixel, 0x102A1101 vertex; 0x40 set on those the game compiles at run time) of `size` bytes (header word 1) followed
at once by `X` bytes of microcode (header word 2): control-flow words first, two 48-bit words to every 3 dwords, then the
fetch and arithmetic instructions in 3-dword slots that the control flow executes by slot number. The container holds the
constant table (a D3DX CTAB: register names and ranges). What is converted is only what the containers in the image use:
`--list` prints every opcode, and an opcode the converter has no rule for is reported by name and stops the conversion of
that shader (nothing is guessed).

Layouts and opcode numbers: the bit fields of the Xenos instruction set as published in Xenia's ucode.h (BSD-3-Clause,
facts only; see data/NOTICE-xenia.txt). Each field was checked against the shaders here before use (the position transform
of 8201b9c0 comes out as the four-row matrix multiply with lanes permuted by the compiler, the pixel shader of 820184e0 as a
copy of the first interpolator to colour 0).

Nearest existing: targets/xbox360_ppc/port/tools/boundary.py - it reads the same image but looks for library functions; this
reads shader data.

  python shader_convert.py --image image.bin --base 0x82000000 --list
  python shader_convert.py --image image.bin --base 0x82000000 --out DIR      # one .hlsl per shader and shaders.json
"""
import argparse
import json
import pathlib
import struct

from shader_decode import CF, CUBE_ALU_FN, CUBE_DIR_FN, EXEC_END, EXEC_OPS, FETCH, MADE, PRED_SCA, PRED_VEC, SCA, pred_vec, SWZ, Unsupported, VEC, VEC_SRCS, alu_fields, bits, container_at, ctab, decode_cf, disasm, fetch_fields, find_containers, pack_blobs, split_cf, swz_str, walk_program  # noqa: F401

COMP = "xyzw"
VEC_EXPR = {
    0: "{a} + {b}", 1: "xm({a}, {b})", 2: "max({a}, {b})", 3: "min({a}, {b})", 4: "(float4)({a} == {b})",
    5: "(float4)({a} > {b})", 6: "(float4)({a} >= {b})", 7: "(float4)({a} != {b})", 8: "frac({a})", 9: "trunc({a})",
    10: "floor({a})", 11: "xm({a}, {b}) + {c}", 19: "(float4)max(max({a}.x, {a}.y), max({a}.z, {a}.w))",
    12: "float4(({a}.x == 0.0) ? {b}.x : {c}.x, ({a}.y == 0.0) ? {b}.y : {c}.y, ({a}.z == 0.0) ? {b}.z : {c}.z, ({a}.w == 0.0) ? {b}.w : {c}.w)",
    13: "float4(({a}.x >= 0.0) ? {b}.x : {c}.x, ({a}.y >= 0.0) ? {b}.y : {c}.y, ({a}.z >= 0.0) ? {b}.z : {c}.z, ({a}.w >= 0.0) ? {b}.w : {c}.w)",
    14: "float4(({a}.x > 0.0) ? {b}.x : {c}.x, ({a}.y > 0.0) ? {b}.y : {c}.y, ({a}.z > 0.0) ? {b}.z : {c}.z, ({a}.w > 0.0) ? {b}.w : {c}.w)",
    15: "(float4)xdot(xm({a}, {b}))", 16: "(float4)xdot(float4(xm({a}, {b}).xyz, 0))",
    17: "(float4)(xdot(float4(xm({a}, {b}).xy, 0, 0)) + {c}.x)", 18: "xb_cube({a})",
}
# scalar operations written so far: (operands used, expression); a and b are two components of the third source: a its last
# swizzle slot, b its first; ps the previous scalar result. The constant forms (42..47) read a from the third source, a
# constant, and b from a temporary whose number is in the swizzle's middle bits, the third source's select and the opcode's
# low bit: the UI's font shader 8203b9e0, MUL_CONST_0 with c1 (ColorFactor) and swizzle 0, is c1.w * r0.x, its colour's
# alpha by the texture's alpha that TFETCH put in r0.x.
SCA_EXPR = {
    0: (2, "{a} + {b}"), 1: (1, "{a} + ps"), 2: (2, "xs({a}, {b})"), 3: (1, "xs({a}, ps)"), 5: (2, "max({a}, {b})"),
    6: (2, "min({a}, {b})"), 11: (1, "frac({a})"), 12: (1, "trunc({a})"), 13: (1, "floor({a})"), 19: (1, "1.0 / {a}"),
    25: (2, "{a} - {b}"), 26: (1, "{a} - ps"), 40: (1, "sqrt({a})"), 42: (2, "xs({a}, {b})"), 43: (2, "xs({a}, {b})"),
    44: (2, "{a} + {b}"), 45: (2, "{a} + {b}"), 46: (2, "{a} - {b}"), 47: (2, "{a} - {b}"), 50: (0, None),
    7: (1, "({a} == 0.0) ? 1.0 : 0.0"), 8: (1, "({a} > 0.0) ? 1.0 : 0.0"), 9: (1, "({a} >= 0.0) ? 1.0 : 0.0"),
    10: (1, "({a} != 0.0) ? 1.0 : 0.0"), 14: (1, "exp2({a})"), 15: (1, "max(log2({a}), -3.402823466e+38)"), 16: (1, "log2({a})"),
    17: (1, "clamp(1.0 / {a}, -3.402823466e+38, 3.402823466e+38)"), 20: (1, "clamp(rsqrt({a}), -3.402823466e+38, 3.402823466e+38)"),
    22: (1, "rsqrt({a})"), 48: (1, "sin({a})"), 49: (1, "cos({a})"),
    # MOVAs and MOVA_FLOORs load the address register a0 from a, rounded to nearest or floored and clamped to -256..255, and give that
    # value as the scalar result (Xenia ucode.h kMovas/kMovasFloor). The load happens after this instruction's sources are read, so a
    # relative constant in the same instruction still sees the old a0. Too Human's foliage vertex shaders fb836ea3 and f6e52b02 (leaf
    # clusters, wind matrices) read c[a0 + n]: refused, their draws were skipped, 80,000 in the user's run of 2026-11-12
    23: (1, "clamp(floor({a} + 0.5), -256.0, 255.0)"), 24: (1, "clamp(floor({a}), -256.0, 255.0)"),
}
MOVA_SCA = (23, 24)
# Kills (Xenia ucode.h kKillEq.. / kKillsEq..): the vector forms compare a and b lane by lane and kill the pixel when any lane holds, giving
# 1 or 0 in every lane; the scalar forms test a against 0 (or 1 for KILLONEs) and give 1 or 0. Before 2026-11-15 a vector kill with no lanes
# written was dropped and a scalar kill refused, so Too Human's leaves (pixel shader f9c3df8d, KILLGTv on c254.y > r0.y) drew as opaque
# cards and 4762 of its 11962 shader containers lost their cut-outs. The discard is applied when xb_gam.z is set: the runtime's
# XB_SHADER_KILL (on; =0 draws every pixel, the old picture, as an answer key; xb_gpu_draw.h kit constants)
KILL_VEC = {24: "==", 25: ">", 26: ">=", 27: "!="}
KILL_SCA = (35, 36, 37, 38, 39)
SCA_EXPR.update({35: (1, "({a} == 0.0) ? 1.0 : 0.0"), 36: (1, "({a} > 0.0) ? 1.0 : 0.0"), 37: (1, "({a} >= 0.0) ? 1.0 : 0.0"),
                 38: (1, "({a} != 0.0) ? 1.0 : 0.0"), 39: (1, "({a} == 1.0) ? 1.0 : 0.0")})
MAXA_VEC = 29   # MAXAv: max(a, b), and a0 from a.w rounded to nearest (Xenia ucode.h kMaxA); shader_decode names it MOVAv


def swz_comps(sw):
    return [(i + bits(sw, 2 * i, 2)) & 3 for i in range(4)]


def const_rel(a, i):
    """Whether source i, a constant, is addressed relative: the instruction's first constant source by its c0 bit, the second by its
    c1 bit (Xenia shader_translator.cc ParseAluInstruction). With the absolute-address bit the index is a0 + n; without it, the loop
    counter aL, which is 0 here because loops are refused."""
    k = sum(1 for j in range(i) if not a["sel"][j])
    return bool(a["c1_rel"] if k else a["c0_rel"]), bool(a["addr_abs"])


def operand(a, i, const, reads):
    """The source i of an arithmetic instruction: (register text without swizzle, swizzle components, negated). The
    (register, component) pairs it reads from temporaries are added to `reads`."""
    reg = a["reg"][i]
    sw = swz_comps(a["swz"][i])
    if a["sel"][i]:
        # Bit 6 makes the register relative to the loop index aL (COGBox ucode.cpp 231-233, as Xenia), which is 0 outside a loop
        # (COGBox hlsl.cpp 1447, 1302); this converter refuses loops, so it is the register itself. 818 of Too Human's run-time pixel
        # shaders read one, among them fcc03901, f248feda and fb7a378b, whose draws were skipped (4,000 in 150 s, build box A, 2026-11-07)
        text = "r%d" % (reg & 0x3F)
        for c in sw:
            reads.add((reg & 0x3F, c))
        if reg & 0x80:
            text = "abs(%s)" % text
    else:
        rel, via_a0 = const_rel(a, i)
        text = const(reg, rel and via_a0)
        if a["abs_const"]:
            text = "abs(%s)" % text
    return text, sw, bool(a["neg"][i])


def sw_text(op, comps=None):
    text, sw, neg = op
    t = "%s.%s" % (text, "".join(COMP[c] for c in (comps or sw)))
    return "(-%s)" % t if neg else t


class Shader:
    def __init__(self, c):
        self.c = c
        self.kind = c["kind"]
        self.slots = []
        self.steps, self.nslots = walk_program(c["ucode"], self.slots)
        self.nlit = len(c["prefix"]) // 16
        self.lit = [struct.unpack(">4f", c["prefix"][16 * k:16 * k + 16]) for k in range(self.nlit)]
        self.ctab = ctab(c["container"])

    def const(self, n, rel=False):
        if rel:   # c[a0 + n]: the cbuffer only; out of range reads 0 as the console's constant file does not wrap
            if n >= 256 - self.nlit:
                raise Unsupported("relative address into the embedded constants")
            self.uses_a0 = True
            return "xb_crel(xb_a0 + %d)" % n
        if n >= 256 - self.nlit:
            v = self.lit[n - (256 - self.nlit)]
            return "float4(%s)" % ", ".join(repr(float(x)) for x in v)
        return "c[%d]" % n

    def convert(self):
        kind = self.kind
        body, regs = [], set()
        written = set()
        vfetch, samplers, outs, self.fetch_at = [], [], set(), []
        max_read_ps = -1
        self.uses_p0 = self.uses_end = self.uses_a0 = False
        self.tex_kind = {}   # fetch constant: "2d" or "cube", as the fetches sample it
        blk = [None, None, False]   # the exec whose instructions are being written, the registers written before its guard opened, a conditional end seen
        jumps = []   # forward COND_JMPs: (target control-flow index, the bool that says it was taken)

        def close_block():
            if blk[1] is not None:
                if blk[0]["op"] in EXEC_END:
                    body.append("    xb_end = true;")
                    self.uses_end = blk[2] = True
                body.append("    }")
                written.intersection_update(blk[1])   # a guarded write may not happen: an interpolator read after it still needs the input
            blk[0] = blk[1] = None

        def guarded(start, snap, pred, cond):
            """Wraps the lines from body[start] in the instruction's predicate test."""
            if pred:
                self.uses_p0 = True
                body.insert(start, "    if (%sp0) {" % ("" if cond else "!"))
                body.append("    }")
                written.intersection_update(snap)

        def note_read(reg, comps):
            nonlocal max_read_ps
            regs.add(reg)
            if kind == "ps" and any((reg, c) not in written for c in comps):
                max_read_ps = max(max_read_ps, reg)

        for si, (kind_s, x, d) in enumerate(self.steps):
            if kind_s == "cf" and d["op"] == 11:
                # COND_JMP: forward only, on the predicate or always, as Xenia's shader translators; the execs it passes over are guarded
                # by the bool it sets (its own test skipped when an earlier jump passes over it). A bool-constant condition needs the
                # bool constants, which the kit does not pass
                if d["back"] or d["target"] <= d["idx"]:
                    raise Unsupported("control flow COND_JMP backward")
                if not d["uncond"] and not d["pred"]:
                    raise Unsupported("control flow COND_JMP on a bool constant")
                close_block()
                test = (["!xb_end"] if blk[2] else []) + ["!" + v for t, v in jumps if d["idx"] < t] + (
                    [] if d["uncond"] else ["p0" if d["cond"] else "!p0"])
                self.uses_p0 |= not d["uncond"]
                jumps.append((d["target"], "xb_j%d" % len(jumps)))
                body.append("    bool %s = %s;" % (jumps[-1][1], " && ".join(test) or "true"))
                continue
            if kind_s == "cf":
                if x != "ALLOC" and d["op"] not in EXEC_OPS:   # an exec's own step marks where its instructions end
                    raise Unsupported("control flow %s" % x)
                continue
            if d is not blk[0]:
                close_block()
                if d["op"] not in (1, 2, 5, 6):
                    raise Unsupported("conditional exec %s" % d["name"])
                guard = (["!xb_end"] if blk[2] else []) + ["!" + v for t, v in jumps if d["idx"] < t] + (
                    [("p0" if d["cond"] else "!p0")] if d["op"] in (5, 6) else [])
                blk[0] = d
                if guard:
                    self.uses_p0 |= d["op"] in (5, 6)
                    body.append("    if (%s) {" % " && ".join(guard))
                    blk[1] = set(written)
            start, snap = len(body), set(written)
            if kind_s == "fetch":
                f = fetch_fields(x)
                if f["op"] == 0:
                    k = len(vfetch)
                    vfetch.append((f["dst"], f["dst_swz"]))
                    self.fetch_at.append(len(self.c["prefix"]) + 12 * self.slots[si])   # its place in the microcode block
                    regs.add(f["dst"])
                    body.append("    // VFETCH #%d into r%d (swizzle %03x)" % (k, f["dst"], f["dst_swz"]))
                    for i in range(4):
                        sw = (f["dst_swz"] >> (3 * i)) & 7
                        if sw == 7:
                            continue
                        val = {0: "v.a%d.x" % k, 1: "v.a%d.y" % k, 2: "v.a%d.z" % k, 3: "v.a%d.w" % k, 4: "0.0", 5: "1.0"}.get(sw)
                        if self.c["addr"] >= MADE:   # a made shader: the game patches the selects when it binds a declaration, so the draw applies them (xb_d3d.c)
                            val = "v.a%d.%s" % (k, COMP[i])
                        if val is None:
                            raise Unsupported("vertex fetch destination swizzle %d" % sw)
                        body.append("    r%d.%s = %s;" % (f["dst"], COMP[i], val))
                        written.add((f["dst"], i))
                elif f["op"] == 1:
                    cx, cy, cz = f["src_swz"] & 3, (f["src_swz"] >> 2) & 3, (f["src_swz"] >> 4) & 3      # two bits per coordinate, absolute
                    cube = f["dim"] == 3
                    if cube and (f["off"][0] or f["off"][1]):
                        raise Unsupported("cube fetch with a texel offset")
                    if cube and f["denorm"]:
                        raise Unsupported("cube fetch with unnormalised coordinates")
                    if self.tex_kind.setdefault(f["const"], "cube" if cube else "2d") != ("cube" if cube else "2d"):
                        raise Unsupported("one fetch constant sampled as 2D and as a cube")
                    note_read(f["src"], (cx, cy, cz) if cube else (cx, cy))
                    if f["const"] not in samplers:
                        samplers.append(f["const"])
                    regs.add(f["dst"])
                    parts = []
                    for i in range(4):
                        sw = (f["dst_swz"] >> (3 * i)) & 7
                        if sw == 7:
                            continue
                        val = {0: "t.x", 1: "t.y", 2: "t.z", 3: "t.w", 4: "0.0", 5: "1.0"}.get(sw)
                        if val is None:
                            raise Unsupported("texture fetch destination swizzle %d" % sw)
                        parts.append("r%d.%s = %s;" % (f["dst"], COMP[i], val))
                    coord = "r%d.%s%s" % (f["src"], COMP[cx], COMP[cy])
                    if cube:
                        coord = "xb_cube_dir(float3(r%d.%s, r%d.%s, r%d.%s%s))" % (f["src"], COMP[cx], f["src"], COMP[cy], f["src"], COMP[cz],
                                                                                 " + %.1f" % (f["off"][2] * 0.5) if f["off"][2] else "")
                    # xb_tg: the fetch constant's gamma channels (sign 3) through the console's piecewise-linear curve to linear, as the texture unit
                    # gives them; the runtime passes each slot's channels in xb_gam (XB_TEX_GAMMA, runtime/xb_xenos_gamma.h xenos_gamma_mask)
                    body.append("    { float4 t = xb_tg(tex%d.Sample(smp%d, %s), %du); %s }" % (f["const"], f["const"], coord, f["const"], " ".join(parts)))
                    for i in range(4):
                        if (f["dst_swz"] >> (3 * i)) & 7 != 7:
                            written.add((f["dst"], i))
                else:
                    raise Unsupported("fetch %s" % FETCH.get(f["op"], f["op"]))
                guarded(start, snap, f["predicated"], f["pred_cond"])
                continue
            a = alu_fields(x)
            vpred = a["vop"] in PRED_VEC
            if a["vdest_rel"] or (a["sdest_rel"] and not a["export"]):
                raise Unsupported("relative destination")
            if a["vmask"] and a["vop"] not in VEC_EXPR and a["vop"] not in KILL_VEC and not vpred and a["vop"] != MAXA_VEC:
                raise Unsupported(VEC.get(a["vop"], "vector op %d" % a["vop"]))
            if a["sop"] not in SCA_EXPR and a["sop"] not in PRED_SCA:
                raise Unsupported(SCA.get(a["sop"], "scalar op %d" % a["sop"]))
            reads = set()
            ops = [operand(a, i, self.const, reads) for i in range(3)]
            const_form = 42 <= a["sop"] <= 47
            if const_form:
                reg2 = (a["swz"][2] & 0x3C) | (a["sel"][2] << 1) | (a["sop"] & 1)
                reads.add((reg2, swz_comps(a["swz"][2])[0]))
            for reg in {r for r, _ in reads}:
                note_read(reg, [c for r, c in reads if r == reg])
            lines, tail = [], []
            if vpred:
                st, expr = pred_vec(a["vop"], sw_text(ops[0]), sw_text(ops[1]))
                if a["vclamp"]:
                    expr = "saturate(%s)" % expr
                lines += [st, "float4 vr = %s;" % expr]
                tail.append("p0 = pv;")
            elif a["vop"] == MAXA_VEC:   # a0 loads even with no lanes written
                self.uses_a0 = True
                lines.append("int na = (int)clamp(floor(%s + 0.5), -256.0, 255.0);" % sw_text(ops[0], [swz_comps(a["swz"][0])[3]]))
                tail.append("xb_a0 = na;")
                if a["vmask"]:
                    e = "max(%s, %s)" % (sw_text(ops[0]), sw_text(ops[1]))
                    lines.append("float4 vr = %s;" % ("saturate(%s)" % e if a["vclamp"] else e))
            elif a["vop"] in KILL_VEC:
                op = KILL_VEC[a["vop"]]
                lines.append("float4 vr = any(%s %s %s) ? (float4)1.0 : (float4)0.0;" % (sw_text(ops[0]), op, sw_text(ops[1])))
                if kind == "ps":
                    lines.append("if (xb_gam.z != 0u && vr.x != 0.0) discard;")
            elif a["vmask"]:
                expr = VEC_EXPR[a["vop"]].format(a=sw_text(ops[0]), b=sw_text(ops[1]), c=sw_text(ops[2]))
                if a["vclamp"]:
                    expr = "saturate(%s)" % expr
                lines.append("float4 vr = %s;" % expr)
            if a["sop"] in PRED_SCA:
                sw = swz_comps(a["swz"][2])
                lines.append(PRED_SCA[a["sop"]].format(a=sw_text(ops[2], [sw[3]])))
                if a["sclamp"]:
                    lines.append("s = saturate(s);")
                tail.append("p0 = pn;")
            elif a["sop"] != 50:
                n, expr = SCA_EXPR[a["sop"]]
                sw = swz_comps(a["swz"][2])
                # As Xenia's shader_translator.cc (ParseAluInstruction; see data/NOTICE-xenia.txt): b is the swizzle's first slot, or its third
                # when the vector op takes three sources (MULADD, the CNDs, DOT2ADD). In the constant forms a is always the constant numbered by
                # the third source (its select bit is part of the temporary's number), b the temporary from its first slot, and the absolute bit
                # for constants and the third source's negation apply to both
                first, second = ops[2], ops[2]
                if const_form:
                    if const_rel(a, 2)[0]:
                        raise Unsupported("relative constant addressing in a scalar constant form")
                    wrap = (lambda s: "abs(%s)" % s) if a["abs_const"] else (lambda s: s)
                    first, second = (wrap(self.const(a["reg"][2])), sw, ops[2][2]), (wrap("r%d" % reg2), sw, ops[2][2])
                bslot = 0 if const_form or a["vop"] not in (11, 12, 13, 14, 17) else 2
                e = expr.format(a=sw_text(first, [sw[3]]), b=sw_text(second, [sw[bslot]]))
                if a["sclamp"]:
                    e = "saturate(%s)" % e
                lines.append("float s = %s;" % e)
                if a["sop"] in KILL_SCA and kind == "ps":
                    lines.append("if (xb_gam.z != 0u && s != 0.0) discard;")
                if a["sop"] in MOVA_SCA:
                    self.uses_a0 = True
                    tail.append("xb_a0 = (int)s;")

            def target(idx):
                if a["export"]:
                    if kind == "vs":
                        if idx == 62:
                            outs.add("pos")
                            return "o.pos"
                        if 0 <= idx <= 15:
                            outs.add(idx)
                            return "o.i%d" % idx
                    elif 0 <= idx <= 3:
                        outs.add(idx)
                        return "oC%d" % idx
                    elif idx == 61:   # the depth export, its x (Xenia: pixel export 61); Too Human's depth copy fc88cedc
                        outs.add("depth")
                        return "oDep"
                    raise Unsupported("export register %d" % idx)
                regs.add(idx)
                return "r%d" % idx

            # An export writes one register, the vector's: its lanes in the vector mask alone take the vector result, in the
            # scalar mask alone the scalar result (the scalar destination is not used; the compiler leaves it 0), and in both a
            # constant, 1 with the scalar destination's relative bit, else 0 (Too Human's full-screen vertex shader fc5f5950:
            # position y from the vector, x from the scalar, w both and 1). Every pack shader that sets both masks on a lane sets that bit.
            both = a["vmask"] & a["smask"] if a["export"] and a["sop"] != 50 else 0
            vmask, smask = a["vmask"] & ~both, a["smask"] & ~both
            if vmask:
                lanes = "".join(COMP[i] for i in range(4) if vmask >> i & 1)
                lines.append("%s.%s = vr.%s;" % (target(a["vdest"]), lanes, lanes))
                if not a["export"]:
                    written.update((a["vdest"], i) for i in range(4) if vmask >> i & 1)
            if a["sop"] != 50:
                if smask:
                    lanes = "".join(COMP[i] for i in range(4) if smask >> i & 1)
                    lines.append("%s.%s = float4(s, s, s, s).%s;" % (target(a["vdest"] if a["export"] else a["sdest"]), lanes, lanes))
                    if not a["export"]:
                        written.update((a["sdest"], i) for i in range(4) if smask >> i & 1)
                if both:
                    lanes = "".join(COMP[i] for i in range(4) if both >> i & 1)
                    k = "1.0" if a["sdest_rel"] else "0.0"
                    lines.append("%s.%s = float4(%s, %s, %s, %s).%s;" % (target(a["vdest"]), lanes, k, k, k, k, lanes))
                lines.append("ps = s;")
            if tail:
                self.uses_p0 = True
            body.append("    { %s }" % " ".join(lines + tail))
            guarded(start, snap, a["predicated"], a["pred_cond"])
        close_block()
        self.vfetch, self.samplers, self.outs = vfetch, samplers, outs
        # The GPU has 16 interpolators, so only r0..r15 arrive from the vertex stage; a higher register is a temporary (starts at 0). Without the cap a
        # Too Human pixel shader reading r35 declared 36 inputs, past ps_5_0's 32, and did not compile (frame 635)
        self.n_interp_in = min(max_read_ps + 1, 16) if kind == "ps" else 0
        return body, sorted(regs)

    def hlsl(self):
        body, regs = self.convert()
        c = self.c
        out = ["// generated by shader_convert.py from the %s shader container at %08x; do not edit" % (
            "pixel" if self.kind == "ps" else "vertex", c["addr"])]
        for name, rset, idx, cnt in self.ctab:
            out.append("//   constant %s: register set %d, c%d, %d register(s)" % (name, rset, idx, cnt))
        if self.nlit:
            out.append("//   embedded constants c%d..c255" % (256 - self.nlit))
        out.append("cbuffer Constants : register(b%d) { float4 c[256]; };" % (0 if self.kind == "vs" else 1))
        out.append("cbuffer Kit : register(b2) { float4 xb_vte; float4 xb_at; uint4 xb_gam; };   // runtime/xb_gpu.c: viewport off, alpha test, colour exponent bias, gamma textures (.x .y), kills on (.z)")
        # The console's multiplies (MUL, MULADD, the dot products, the scalar MULs) follow Direct3D 9: 0 times anything is 0. Too Human's
        # post passes draw quads with corners at +-infinity through a matrix with zeros: on the host that was NaN, nothing drew, and every
        # frame after the scene was black (build box A, 2026-11-06)
        out.append("float4 xm(float4 a, float4 b) { return (a == 0 || b == 0) ? 0 : a * b; }")
        out.append("float xs(float a, float b) { return (a == 0 || b == 0) ? 0 : a * b; }")
        out.append("float xdot(float4 p) { return p.x + p.y + p.z + p.w; }")
        if self.kind == "ps":
            # What render target 0's format holds of the colour, after the exponent bias (xb_at.w, runtime/xb_gpu.c target_clamp_mode): the
            # fixed-point formats 0..1, the 7e3 float 0..31.875 with a two-bit alpha, the 16-bit fixed -32..32; 0 keeps it (a float target)
            out.append("float4 xb_rt(float4 v) { return xb_at.w == 1 ? saturate(v) : xb_at.w == 2 ? float4(clamp(v.rgb, 0, 31.875), round(saturate(v.a) * 3) / 3) : "
                       "xb_at.w == 3 ? clamp(v, -32, 32) : v; }")
        text = NL.join(body)
        if "xb_tg(" in text:
            out.append(GAMMA_FN)
        if "xb_crel(" in text:
            out.append("float4 xb_crel(int n) { return (n >= 0 && n < 256) ? c[n] : float4(0, 0, 0, 0); }")
        if "xb_cube(" in text:
            out.append(CUBE_ALU_FN)
        if "xb_cube_dir(" in text:
            out.append(CUBE_DIR_FN)
        if self.kind == "vs":
            out.append("struct VSIn {")
            for k in range(len(self.vfetch)):
                out.append("    float4 a%d : ATTR%d;" % (k, k))
            out.append("};")
            out.append("struct VSOut {")
            out.append("    float4 pos : SV_Position;")
            for i in sorted(i for i in self.outs if i != "pos"):
                out.append("    float4 i%d : TEXCOORD%d;" % (i, i))
            out.append("};")
            out.append("VSOut main(VSIn v)")
            out.append("{")
            out.append("    VSOut o = (VSOut)0;")
        else:
            for s in self.samplers:
                out.append("%s tex%d : register(t%d);" % ("TextureCube" if self.tex_kind.get(s) == "cube" else "Texture2D", s, s))
                out.append("SamplerState smp%d : register(s%d);" % (s, s))
            out.append("struct PSIn {")
            out.append("    float4 xb_pos : SV_Position;   // the vertex stage's first output: the host links the two by position")
            for i in range(self.n_interp_in):
                out.append("    float4 i%d : TEXCOORD%d;" % (i, i))
            out.append("};")
            if "depth" in self.outs:
                out.append("struct PSOut { float4 c : SV_Target0; float d : SV_Depth; };")
            out.append("%s main(PSIn p)%s" % (("PSOut", "") if "depth" in self.outs else ("float4", " : SV_Target0")))
            out.append("{")
            out.append("    float4 oC0 = 0, oC1 = 0, oC2 = 0, oC3 = 0, oDep = 0;")
        out.append("    float ps = 0;")
        if self.uses_p0:
            out.append("    bool p0 = false;")
        if self.uses_end:
            out.append("    bool xb_end = false;")
        if self.uses_a0:
            out.append("    int xb_a0 = 0;")
        for r in regs:
            out.append("    float4 r%d = %s;" % (r, "p.i%d" % r if self.kind == "ps" and r < self.n_interp_in else "0"))
        out += body
        if self.kind == "vs":
            # Screen-space positions (viewport transform off) are clamped to the guard band, +-8192 pixels, as the console's rasteriser clips them.
            # Too Human's post passes (vertex shaders f5442086, faa61062, f17a7555) draw full-screen quads with corners at +-infinity: the host
            # dropped every such triangle, each pass drew nothing, and the title, menu and game showed black (build box A, 2026-11-06)
            out.append("    if (xb_vte.z != 0) o.pos.xy = clamp(o.pos.xy, -8192.0 * abs(o.pos.w), 8192.0 * abs(o.pos.w));")
            out.append("    if (xb_vte.z != 0) { o.pos.x = (o.pos.x + 0.5) * xb_vte.x - o.pos.w; o.pos.y = o.pos.w - (o.pos.y + 0.5) * xb_vte.y; }")
            # xb_vte.w: the viewport's depth range is upside down (Too Human's 1..0), which the host cannot set; the console stores 1 - z/w there, so
            # z = w - z makes the host's 0..1 store the same (runtime/xb_gpu_draw.h zflip_vs_on; 0 leaves the position as before)
            out.append("    if (xb_vte.z == 0 && xb_vte.w != 0) o.pos.z = o.pos.w - o.pos.z;")
        else:
            out.append("    if (xb_at.y < 6.5) { float d = oC0.a * 255 - xb_at.x; int f = (int)xb_at.y; if (!((f == 1 && d < 0) || (f == 2 && d == 0) || (f == 3 && d <= 0) || (f == 4 && d > 0) || (f == 5 && d != 0) || (f == 6 && d >= 0))) discard; }")
        # The render target's own exponent bias (RB_COLOR_INFO bits 20..25) times the colour, after the alpha test, as COGBox's epilogue (hlsl.cpp
        # 1511-1512): xb_at.z is 2^bias with XB_COGBOX_RT_BIAS=1 and exactly 1 without (runtime/xb_gpu_draw.h)
        if self.kind == "ps" and "depth" in self.outs:
            out.append("    PSOut r; r.c = xb_rt(oC0 * xb_at.z); r.d = saturate(oDep.x); return r;")
        else:
            out.append("    return o;" if self.kind == "vs" else "    return xb_rt(oC0 * xb_at.z);")
        out.append("}")
        return "\n".join(out) + "\n"


# A texture whose fetch constant marks channels gamma (sign 3) is sampled as linear: the console's piecewise-linear curve, in 8-bit steps v of the
# stored value, gives 10-bit linear v below 64, then slopes 2, 4 and 8 from 64, 96 and 192 (the inverse of its linear-to-gamma curve, whose slopes
# halve at 64, 128 and 512 of 1024; REFERENCES.md, "Xenos gamma"). xb_gam holds 4 bits a slot, slots 0-7 in x and 8-15 in y, a bit per channel of
# the sampled value (after the slot's swizzle). Too Human marks its colour textures and its 256x1 glow lookup gamma (sign 3330); sampled raw, the
# menu's hexagon glow (fc52e146, 15 times two lookups) came out far too bright (2026-11-13)
GAMMA_FN = ("float4 xb_pwl(float4 g) { float4 v = saturate(g) * 255.0; return (v < 64.0 ? v : v < 96.0 ? 2.0 * v - 64.0 : v < 192.0 ? 4.0 * v - 256.0 : 8.0 * v - 1024.0) / 1024.0; }" + chr(10) +
            "float4 xb_tg(float4 t, uint s) { uint m = ((s < 8u ? xb_gam.x : xb_gam.y) >> ((s & 7u) * 4u)) & 15u; if (m == 0u) return t; "
            "bool4 b = (m & uint4(1u, 2u, 4u, 8u)) != 0u; float4 l = xb_pwl(t); return float4(b.x ? l.x : t.x, b.y ? l.y : t.y, b.z ? l.z : t.z, b.w ? l.w : t.w); }")

NL = chr(10)       # line end of the generated text
BSN = chr(92) + "n"    # backslash and n: the escape written inside a C string literal


def gs_rect(shader):
    """The geometry shader that turns the three vertices of a rectangle list into a quad: the fourth corner is
    the two ends of the longest edge minus the third corner, in every output the vertex shader writes (the platform derives it after the vertex shader)."""
    outs = sorted(i for i in shader.outs if i != "pos")
    st = ["struct VSOut {", "    float4 pos : SV_Position;"] + ["    float4 i%d : TEXCOORD%d;" % (i, i) for i in outs] + ["};"]
    fields = ["pos"] + ["i%d" % i for i in outs]
    # The game gives the corners in either order: top left, top right, bottom left (82093a98) or top left, top right, bottom right (8209e9c0). The right angle is opposite the longest edge
    body = ["[maxvertexcount(4)] void main(triangle VSOut v[3], inout TriangleStream<VSOut> s)", "{", "    VSOut d;",
            "    float2 p0 = v[0].pos.xy / v[0].pos.w, p1 = v[1].pos.xy / v[1].pos.w, p2 = v[2].pos.xy / v[2].pos.w;",
            "    float l01 = dot(p0 - p1, p0 - p1), l12 = dot(p1 - p2, p1 - p2), l20 = dot(p2 - p0, p2 - p0);",
            "    int j = 0, a = 1, b = 2;",
            "    if (l20 >= l01 && l20 >= l12) { j = 1; a = 0; b = 2; }",
            "    else if (l01 >= l12) { j = 2; a = 0; b = 1; }"]
    body += ["    d.%s = v[a].%s + v[b].%s - v[j].%s;" % (f, f, f, f) for f in fields]
    body += ["    s.Append(v[j]); s.Append(v[a]); s.Append(v[b]); s.Append(d);", "}"]
    return NL.join(st + body) + NL


def c_literal(text):
    return NL.join('    "%s' % ln.replace("\\", "\\\\").replace('"', '\\"') + BSN + '"' for ln in text.splitlines())


class Bytecode:
    """Compiles each converted text once at build time with the system's Direct3D compiler (d3dcompiler_47.dll, the one the runtime calls), with the
    runtime's flags (none), stripped of reflection and debug data (the input signature stays, for input layouts), so the port never compiles during
    play. A compiled text is kept in `cache` as <sha256 of compiler, profile and text>.dxbc and reused while the text and the compiler are the same.
    `place` gives each text's (offset, length) in `blob`, which gen_xbox.py links in as xb_shader_dxbc.

    Nearest existing: tools/hlsl_check.py - it compiles .hlsl files to report errors and keeps nothing."""

    STRIP = 1 | 2 | 4 | 8   # D3DCOMPILER_STRIP_REFLECTION_DATA, _DEBUG_INFO, _TEST_BLOBS, _PRIVATE_DATA

    def __init__(self, cache):
        import ctypes
        import hashlib
        self.ct = ctypes
        self.d3dc = ctypes.WinDLL("d3dcompiler_47.dll")
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetModuleFileNameW(ctypes.c_void_p(self.d3dc._handle), buf, 1024)
        self.ver = hashlib.sha256(pathlib.Path(buf.value).read_bytes()).hexdigest()[:16]
        self.cache = pathlib.Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.code, self.blob, self.at, self.failed, self.compiled = {}, bytearray(), {}, [], 0

    def key(self, text, profile):
        import hashlib
        return hashlib.sha256(("%s|%s|" % (self.ver, profile)).encode() + text.encode()).hexdigest()

    def _blob_bytes(self, b):
        vt = self.ct.cast(b, self.ct.POINTER(self.ct.POINTER(self.ct.c_void_p))).contents
        ptr = self.ct.WINFUNCTYPE(self.ct.c_void_p, self.ct.c_void_p)(vt[3])(b)
        size = self.ct.WINFUNCTYPE(self.ct.c_size_t, self.ct.c_void_p)(vt[4])(b)
        data = self.ct.string_at(ptr, size)
        self.ct.WINFUNCTYPE(self.ct.c_ulong, self.ct.c_void_p)(vt[2])(b)   # Release
        return data

    def _compile(self, text, profile):
        ct = self.ct
        src = text.encode()
        code, err = ct.c_void_p(), ct.c_void_p()
        hr = self.d3dc.D3DCompile(src, len(src), b"converted", None, None, b"main", profile.encode(), 0, 0, ct.byref(code), ct.byref(err))
        if err.value:
            self._blob_bytes(err)
        if (hr & 0xFFFFFFFF) or not code.value:
            return None
        full = self._blob_bytes(code)
        out = ct.c_void_p()
        if self.d3dc.D3DStripShader(full, len(full), self.STRIP, ct.byref(out)) & 0xFFFFFFFF or not out.value:
            return full
        return self._blob_bytes(out)

    def run(self, wants, threads=None):
        """Compiles every (text, profile) not in the cache yet, on threads (the compiler is thread-safe and ctypes lets go of the interpreter)."""
        import concurrent.futures
        import os
        todo = []
        for text, profile in dict.fromkeys(wants):
            f = self.cache / (self.key(text, profile) + ".dxbc")
            if f.exists():
                self.code[(text, profile)] = f.read_bytes()
            else:
                todo.append((text, profile, f))
        n = threads or max(1, min(8, (os.cpu_count() or 2) // 2))
        with concurrent.futures.ThreadPoolExecutor(n) as ex:
            for (text, profile, f), data in zip(todo, ex.map(lambda t: self._compile(t[0], t[1]), todo)):
                if data is None:
                    self.failed.append((profile, text.splitlines()[0][:100]))
                    continue
                f.write_bytes(data)
                self.code[(text, profile)] = data
                self.compiled += 1

    def place(self, text, profile):
        data = self.code.get((text, profile))
        if not data:
            return 0, 0
        k = (text, profile)
        if k not in self.at:
            while len(self.blob) % 16:
                self.blob.append(0)
            self.at[k] = (len(self.blob), len(data))
            self.blob += data
        return self.at[k]


def coff_obj(data, symbol):
    """An x64 COFF object holding `data` in a read-only section under the external C symbol `symbol` (the linker takes it as it is: no compiler pass
    over a C array of tens of megabytes)."""
    data = bytes(data) or bytes(16)
    name = symbol.encode() + b"\0"
    strtab = struct.pack("<I", 4 + len(name)) + name
    sym_off = 20 + 40 + len(data)
    head = struct.pack("<HHIIIHH", 0x8664, 1, 0, sym_off, 1, 0, 0)
    sect = struct.pack("<8sIIIIIIHHI", b".rdata", 0, 0, len(data), 60, 0, 0, 0, 0, 0x40000040 | 0x00500000)   # initialised data, read, 16-byte aligned
    sym = struct.pack("<IIIhHBB", 0, 4, 0, 1, 0, 2, 0)   # name in the string table at 4, value 0, section 1, external
    return head + sect + data + sym + strtab


def c_table(image, base, blobs=None, code=None):
    """gen/shaders.c: xb_shader_table (runtime/xb.h XbShader) for every container of the image, and with blobs (a folder of
    <key>.bin files, --packs) for every container the game makes at run time, by key. Rows by address; one text each. With `code` (a cache
    folder) each text is also compiled (Bytecode) and the result is (text of shaders.c, the bytecode blob for xb_shader_dxbc)."""
    out = ['/* generated by tools/shader_convert.py: the shaders of the image as HLSL; do not edit */', '#include "xb.h"', ""]
    found, names = [], {}
    cs = find_containers(pathlib.Path(image).read_bytes(), base)
    seen = set()
    for folder in ([blobs] if isinstance(blobs, (str, pathlib.Path)) else blobs or []):   # one folder or several (a run's shader_capture): a key once
        if folder and pathlib.Path(folder).is_dir():
            for p in sorted(pathlib.Path(folder).glob("*.bin")):
                if p.stem.lower() not in seen:
                    seen.add(p.stem.lower())
                    cs.append(container_at(p.read_bytes(), 0, int(p.stem, 16)))

    def text_name(text):
        if text not in names:
            names[text] = "sh_%d" % len(names)
            out.append("static const char %s[] =" % names[text] + NL + "%s;" % c_literal(text))
        return names[text]
    for c in sorted((c for c in cs if c), key=lambda c: c["addr"]):
        sh = Shader(c)
        try:
            text = sh.hlsl()
            if len(sh.vfetch) > 32:   # Too Human's made vertex shaders f47cd46e, f31e8144 and f61653a1 fetch 17 to 32: most of its menu's skipped draws (2026-11-06)
                raise Unsupported("more than 32 vertex fetches")   # the host's input layout and XbShader.fetch take 32, Direct3D 11's input slots
        except Unsupported as e:   # the reason goes in the row, for the run log's skipped draws
            found.append((c, None, None, str(e)))
            continue
        found.append((c, sh, text, None))
    bc = None
    if code is not None:
        bc = Bytecode(code)
        wants = []
        for c, sh, text, why in found:
            if text is not None:
                wants.append((text, "ps_5_0" if c["kind"] == "ps" else "vs_5_0"))
                if c["kind"] == "vs":
                    wants.append((gs_rect(sh), "gs_5_0"))
        bc.run(wants)
    rows = []
    for c, sh, text, why in found:
        if text is None:
            rows.append('    {0x%08xu, %d, 0, 0, 0, 0, {0}, "%s", 0, 0, 0, 0},' % (c["addr"], c["kind"] == "ps", why.replace("\\", "").replace('"', "'")))
            continue
        gtext = gs_rect(sh) if c["kind"] == "vs" else None
        gs = text_name(gtext) if gtext else "0"
        made = c["addr"] >= MADE and c["kind"] == "vs"
        fetch = ", ".join("%du" % (o + 1) for o in sh.fetch_at) if made and sh.fetch_at else "0"
        co, cl = bc.place(text, "ps_5_0" if c["kind"] == "ps" else "vs_5_0") if bc else (0, 0)
        go, gl = bc.place(gtext, "gs_5_0") if bc and gtext else (0, 0)
        rows.append("    {0x%08xu, %d, %d, %d, %s, %s, {%s}, 0, %du, %du, %du, %du}," % (c["addr"], c["kind"] == "ps", len(sh.vfetch), sh.n_interp_in, text_name(text), gs, fetch,
                                                                             co, cl, go, gl))
    out += ["", "const XbShader xb_shader_table[] = {"] + (rows or ["    {0, 0, 0, 0, 0, 0}"]) + ["};",
            "const unsigned xb_shader_count = %d;" % len(rows), ""]
    if bc:
        out.insert(1, "/* %d texts compiled to bytecode at build time (%d new, %d reused from the cache), %d failed and are compiled by the host; %d bytes */" % (
            len(bc.code), bc.compiled, len(bc.code) - bc.compiled, len(bc.failed), len(bc.blob)))
        return NL.join(out), bytes(bc.blob)
    return NL.join(out)


def convert_all(image, base, outdir):
    data = pathlib.Path(image).read_bytes()
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    report, unsupported = [], {}
    for c in find_containers(data, base):
        sh = Shader(c)
        entry = {"addr": "%08x" % c["addr"], "kind": c["kind"], "constants": [list(k) for k in sh.ctab],
                 "embedded_constants": sh.nlit, "slots": sh.nslots}
        try:
            text = sh.hlsl()
            name = "%s_%08x.hlsl" % (c["kind"], c["addr"])
            (outdir / name).write_text(text, encoding="utf-8", newline="\n")
            entry.update(file=name, vertex_inputs=[list(v) for v in sh.vfetch], samplers=sh.samplers,
                         outputs=sorted(i for i in sh.outs if i != "pos"), interpolators_in=sh.n_interp_in)
        except Unsupported as e:
            entry["unsupported"] = str(e)
            unsupported.setdefault(str(e), []).append("%08x" % c["addr"])
        report.append(entry)
    (outdir / "shaders.json").write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
    return report, unsupported


def survey(image, base):
    for c in find_containers(pathlib.Path(image).read_bytes(), base):
        try:
            steps, n = walk_program(c["ucode"])
        except Unsupported as e:
            print("%08x %s: %s" % (c["addr"], c["kind"], e))
            continue
        print("%08x %s size %x ucode %x (%d slots)  constants %s  embedded %d" % (
            c["addr"], c["kind"], c["size"], len(c["ucode"]), n,
            ", ".join("%s c%d+%d" % (k[0], k[2], k[3]) for k in ctab(c["container"])), len(c["prefix"]) // 16))
        for line in disasm(steps):
            print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--base", default="0x82000000")
    ap.add_argument("--list", action="store_true", help="print the disassembly of every container")
    ap.add_argument("--out", help="directory for the .hlsl files and shaders.json")
    ap.add_argument("--c-table", help="write the C table the port links (xb_shader_table) to this file")
    ap.add_argument("--packs", help="the game's data folder: write every shader container in its .gz packs to --blobs as <key>.bin")
    ap.add_argument("--blobs", help="the folder of <key>.bin containers (the workspace folder shaders, which gen_xbox.py reads)")
    a = ap.parse_args()
    if a.packs:
        found, clash = pack_blobs(a.packs)
        pathlib.Path(a.blobs).mkdir(parents=True, exist_ok=True)
        for k, blob in found.items():
            pathlib.Path(a.blobs, "%08x.bin" % k).write_bytes(blob)
        print("%d shaders from the packs, %d keys two shaders share left out" % (len(found), len(clash)))
    if a.list:
        survey(a.image, int(a.base, 0))
    if a.c_table:
        pathlib.Path(a.c_table).write_text(c_table(a.image, int(a.base, 0)), encoding="utf-8", newline=NL)
    if a.out:
        report, bad = convert_all(a.image, int(a.base, 0), a.out)
        print("%d shaders, %d converted" % (len(report), sum(1 for r in report if "file" in r)))
        for why, addrs in sorted(bad.items()):
            print("  cannot convert (%s): %s" % (why, " ".join(addrs)))


if __name__ == "__main__":
    main()

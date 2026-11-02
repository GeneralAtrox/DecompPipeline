"""Converts the Xbox 360 shader programs of a game image to HLSL, offline (DESIGN_PORT.md section 2: shaders are converted before
the game runs; nothing is read from microcode at play time, and the generated text is plain Direct3D 11 HLSL).

A shader in the image is a container (magic 0x102A1100 pixel, 0x102A1101 vertex) of `size` bytes (header word 1) followed
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

from shader_decode import CF, EXEC_END, EXEC_OPS, FETCH, SCA, SWZ, Unsupported, VEC, VEC_SRCS, alu_fields, bits, ctab, decode_cf, disasm, fetch_fields, find_containers, split_cf, swz_str, walk_program  # noqa: F401

COMP = "xyzw"
VEC_EXPR = {
    0: "{a} + {b}", 1: "{a} * {b}", 2: "max({a}, {b})", 3: "min({a}, {b})", 4: "(float4)({a} == {b})",
    5: "(float4)({a} > {b})", 6: "(float4)({a} >= {b})", 7: "(float4)({a} != {b})", 8: "frac({a})", 9: "trunc({a})",
    10: "floor({a})", 11: "{a} * {b} + {c}",
    12: "float4(({a}.x == 0.0) ? {b}.x : {c}.x, ({a}.y == 0.0) ? {b}.y : {c}.y, ({a}.z == 0.0) ? {b}.z : {c}.z, ({a}.w == 0.0) ? {b}.w : {c}.w)",
    13: "float4(({a}.x >= 0.0) ? {b}.x : {c}.x, ({a}.y >= 0.0) ? {b}.y : {c}.y, ({a}.z >= 0.0) ? {b}.z : {c}.z, ({a}.w >= 0.0) ? {b}.w : {c}.w)",
    14: "float4(({a}.x > 0.0) ? {b}.x : {c}.x, ({a}.y > 0.0) ? {b}.y : {c}.y, ({a}.z > 0.0) ? {b}.z : {c}.z, ({a}.w > 0.0) ? {b}.w : {c}.w)",
    15: "(float4)dot({a}, {b})", 16: "(float4)dot({a}.xyz, {b}.xyz)", 17: "(float4)(dot({a}.xy, {b}.xy) + {c}.x)",
}
# scalar operations written so far: (operands used, expression); a and b are two components of the third source: a its last
# swizzle slot, b its first; ps the previous scalar result. The constant forms (42..47) read a from the third source, a
# constant, and b from a temporary whose number is in the swizzle's middle bits, the third source's select and the opcode's
# low bit: the UI's font shader 8203b9e0, MUL_CONST_0 with c1 (ColorFactor) and swizzle 0, is c1.w * r0.x, its colour's
# alpha by the texture's alpha that TFETCH put in r0.x.
SCA_EXPR = {
    0: (2, "{a} + {b}"), 1: (1, "{a} + ps"), 2: (2, "{a} * {b}"), 3: (1, "{a} * ps"), 5: (2, "max({a}, {b})"),
    6: (2, "min({a}, {b})"), 11: (1, "frac({a})"), 12: (1, "trunc({a})"), 13: (1, "floor({a})"), 19: (1, "1.0 / {a}"),
    25: (2, "{a} - {b}"), 26: (1, "{a} - ps"), 40: (1, "sqrt({a})"), 42: (2, "{a} * {b}"), 43: (2, "{a} * {b}"),
    44: (2, "{a} + {b}"), 45: (2, "{a} + {b}"), 46: (2, "{a} - {b}"), 47: (2, "{a} - {b}"), 50: (0, None),
}


def swz_comps(sw):
    return [(i + bits(sw, 2 * i, 2)) & 3 for i in range(4)]


def operand(a, i, const, reads):
    """The source i of an arithmetic instruction: (register text without swizzle, swizzle components, negated). The
    (register, component) pairs it reads from temporaries are added to `reads`."""
    reg = a["reg"][i]
    sw = swz_comps(a["swz"][i])
    if a["sel"][i]:
        if reg & 0x40:
            raise Unsupported("temporary register operand with bit 6 set (%02x)" % reg)
        text = "r%d" % (reg & 0x3F)
        for c in sw:
            reads.add((reg & 0x3F, c))
        if reg & 0x80:
            text = "abs(%s)" % text
    else:
        if a["c0_rel"] or a["c1_rel"] or a["addr_abs"]:
            raise Unsupported("relative constant addressing")
        text = const(reg)
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
        self.steps, self.nslots = walk_program(c["ucode"])
        self.nlit = len(c["prefix"]) // 16
        self.lit = [struct.unpack(">4f", c["prefix"][16 * k:16 * k + 16]) for k in range(self.nlit)]
        self.ctab = ctab(c["container"])

    def const(self, n):
        if n >= 256 - self.nlit:
            v = self.lit[n - (256 - self.nlit)]
            return "float4(%s)" % ", ".join(repr(float(x)) for x in v)
        return "c[%d]" % n

    def convert(self):
        kind = self.kind
        body, regs = [], set()
        written = set()
        vfetch, samplers, outs = [], [], set()
        max_read_ps = -1

        def note_read(reg, comps):
            nonlocal max_read_ps
            regs.add(reg)
            if kind == "ps" and any((reg, c) not in written for c in comps):
                max_read_ps = max(max_read_ps, reg)

        for kind_s, x, d in self.steps:
            if kind_s == "cf":
                if x != "ALLOC":
                    raise Unsupported("control flow %s" % x)
                continue
            if d["op"] not in (1, 2):
                raise Unsupported("conditional exec %s" % d["name"])
            if kind_s == "fetch":
                f = fetch_fields(x)
                if f["op"] == 0:
                    k = len(vfetch)
                    vfetch.append((f["dst"], f["dst_swz"]))
                    regs.add(f["dst"])
                    body.append("    // VFETCH #%d into r%d (swizzle %03x)" % (k, f["dst"], f["dst_swz"]))
                    for i in range(4):
                        sw = (f["dst_swz"] >> (3 * i)) & 7
                        if sw == 7:
                            continue
                        val = {0: "v.a%d.x" % k, 1: "v.a%d.y" % k, 2: "v.a%d.z" % k, 3: "v.a%d.w" % k, 4: "0.0", 5: "1.0"}.get(sw)
                        if val is None:
                            raise Unsupported("vertex fetch destination swizzle %d" % sw)
                        body.append("    r%d.%s = %s;" % (f["dst"], COMP[i], val))
                        written.add((f["dst"], i))
                elif f["op"] == 1:
                    cx, cy = f["src_swz"] & 3, (f["src_swz"] >> 2) & 3      # two bits per coordinate, absolute
                    note_read(f["src"], (cx, cy))
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
                    body.append("    { float4 t = tex%d.Sample(smp%d, r%d.%s%s); %s }" % (f["const"], f["const"], f["src"], COMP[cx], COMP[cy], " ".join(parts)))
                    for i in range(4):
                        if (f["dst_swz"] >> (3 * i)) & 7 != 7:
                            written.add((f["dst"], i))
                else:
                    raise Unsupported("fetch %s" % FETCH.get(f["op"], f["op"]))
                continue
            a = alu_fields(x)
            if a["predicated"]:
                raise Unsupported("predicated instruction")
            if a["vdest_rel"] or a["sdest_rel"]:
                raise Unsupported("relative destination")
            if a["vmask"] and a["vop"] not in VEC_EXPR:
                raise Unsupported(VEC.get(a["vop"], "vector op %d" % a["vop"]))
            if a["sop"] not in SCA_EXPR:
                raise Unsupported(SCA.get(a["sop"], "scalar op %d" % a["sop"]))
            reads = set()
            ops = [operand(a, i, self.const, reads) for i in range(3)]
            const_form = 42 <= a["sop"] <= 47
            if const_form:
                reg2 = (a["swz"][2] & 0x3C) | (a["sel"][2] << 1) | (a["sop"] & 1)
                reads.add((reg2, swz_comps(a["swz"][2])[0]))
            for reg in {r for r, _ in reads}:
                note_read(reg, [c for r, c in reads if r == reg])
            lines = []
            if a["vmask"]:
                expr = VEC_EXPR[a["vop"]].format(a=sw_text(ops[0]), b=sw_text(ops[1]), c=sw_text(ops[2]))
                if a["vclamp"]:
                    expr = "saturate(%s)" % expr
                lines.append("float4 vr = %s;" % expr)
            if a["sop"] != 50:
                n, expr = SCA_EXPR[a["sop"]]
                sw = swz_comps(a["swz"][2])
                second = ("r%d" % reg2, sw, ops[2][2]) if const_form else ops[2]
                e = expr.format(a=sw_text(ops[2], [sw[3]]), b=sw_text(second, [sw[0]]))
                if a["sclamp"]:
                    e = "saturate(%s)" % e
                lines.append("float s = %s;" % e)

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
                    raise Unsupported("export register %d" % idx)
                regs.add(idx)
                return "r%d" % idx

            if a["vmask"]:
                lanes = "".join(COMP[i] for i in range(4) if a["vmask"] >> i & 1)
                lines.append("%s.%s = vr.%s;" % (target(a["vdest"]), lanes, lanes))
                if not a["export"]:
                    written.update((a["vdest"], i) for i in range(4) if a["vmask"] >> i & 1)
            if a["sop"] != 50:
                if a["smask"]:
                    lanes = "".join(COMP[i] for i in range(4) if a["smask"] >> i & 1)
                    lines.append("%s.%s = float4(s, s, s, s).%s;" % (target(a["sdest"]), lanes, lanes))
                    if not a["export"]:
                        written.update((a["sdest"], i) for i in range(4) if a["smask"] >> i & 1)
                lines.append("ps = s;")
            body.append("    { %s }" % " ".join(lines))
        self.vfetch, self.samplers, self.outs = vfetch, samplers, outs
        self.n_interp_in = (max_read_ps + 1) if kind == "ps" else 0
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
        out.append("cbuffer Kit : register(b2) { float4 xb_vte; float4 xb_at; };   // runtime/xb_gpu.c: viewport off, alpha test")
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
                out.append("Texture2D tex%d : register(t%d);" % (s, s))
                out.append("SamplerState smp%d : register(s%d);" % (s, s))
            out.append("struct PSIn {")
            out.append("    float4 xb_pos : SV_Position;   // the vertex stage's first output: the host links the two by position")
            for i in range(self.n_interp_in):
                out.append("    float4 i%d : TEXCOORD%d;" % (i, i))
            out.append("};")
            out.append("float4 main(PSIn p) : SV_Target0")
            out.append("{")
            out.append("    float4 oC0 = 0, oC1 = 0, oC2 = 0, oC3 = 0;")
        out.append("    float ps = 0;")
        for r in regs:
            out.append("    float4 r%d = %s;" % (r, "p.i%d" % r if self.kind == "ps" and r < self.n_interp_in else "0"))
        out += body
        if self.kind == "vs":
            out.append("    if (xb_vte.z != 0) { o.pos.x = (o.pos.x + 0.5) * xb_vte.x - o.pos.w; o.pos.y = o.pos.w - (o.pos.y + 0.5) * xb_vte.y; }")
        else:
            out.append("    if (xb_at.y < 6.5) { float d = oC0.a * 255 - xb_at.x; int f = (int)xb_at.y; if (!((f == 1 && d < 0) || (f == 2 && d == 0) || (f == 3 && d <= 0) || (f == 4 && d > 0) || (f == 5 && d != 0) || (f == 6 && d >= 0))) discard; }")
        out.append("    return o;" if self.kind == "vs" else "    return oC0;")
        out.append("}")
        return "\n".join(out) + "\n"


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


def c_table(image, base):
    """gen/shaders.c: xb_shader_table (runtime/xb.h XbShader) for every container of the image."""
    out = ['/* generated by tools/shader_convert.py: the shaders of the image as HLSL; do not edit */', '#include "xb.h"', ""]
    rows = []
    for c in find_containers(pathlib.Path(image).read_bytes(), base):
        sh = Shader(c)
        name = "sh_%08x" % c["addr"]
        try:
            text = sh.hlsl()
        except Unsupported:
            rows.append("    {0x%08xu, %d, 0, 0, 0, 0}," % (c["addr"], c["kind"] == "ps"))
            continue
        out.append("static const char %s_hlsl[] =" % name + NL + "%s;" % c_literal(text))
        gs = "0"
        if c["kind"] == "vs":
            out.append("static const char %s_gs[] =" % name + NL + "%s;" % c_literal(gs_rect(sh)))
            gs = name + "_gs"
        rows.append("    {0x%08xu, %d, %d, %d, %s_hlsl, %s}," % (c["addr"], c["kind"] == "ps", len(sh.vfetch), sh.n_interp_in, name, gs))
    out += ["", "const XbShader xb_shader_table[] = {"] + (rows or ["    {0, 0, 0, 0, 0, 0}"]) + ["};",
            "const unsigned xb_shader_count = %d;" % len(rows), ""]
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
    ap.add_argument("--image", required=True)
    ap.add_argument("--base", default="0x82000000")
    ap.add_argument("--list", action="store_true", help="print the disassembly of every container")
    ap.add_argument("--out", help="directory for the .hlsl files and shaders.json")
    ap.add_argument("--c-table", help="write the C table the port links (xb_shader_table) to this file")
    a = ap.parse_args()
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

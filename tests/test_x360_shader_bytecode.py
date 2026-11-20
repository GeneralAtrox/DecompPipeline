"""The Xbox 360 kit's shader converter: relative constants through a0 (MOVA), the build-time bytecode and its COFF object.

No game data: the converter's operand rule is called on hand-made instruction fields, and the bytecode on a hand-written HLSL text."""
import os
import struct
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc" / "port" / "tools"))
import shader_convert as sc  # noqa: E402


def fields(**kw):
    a = {"reg": [1, 180, 0], "sel": [1, 0, 1], "swz": [0, 0, 0], "neg": [0, 0, 0], "c0_rel": 0, "c1_rel": 0, "addr_abs": 0, "abs_const": 0}
    a.update(kw)
    return a


def test_relative_constant_reads_a0():
    # fb836ea3's step 12: MULv with the constant source c[a0 + 180], c0 relative, absolute-address bit set
    calls = []
    text, _, _ = sc.operand(fields(c0_rel=1, addr_abs=1), 1, lambda n, rel=False: calls.append((n, rel)) or "C", set())
    assert calls == [(180, True)]


def test_second_constant_takes_c1_bit():
    a = fields(reg=[3, 140, 7], sel=[0, 0, 1], c0_rel=0, c1_rel=1, addr_abs=1)
    seen = []
    for i in range(3):
        sc.operand(a, i, lambda n, rel=False: seen.append((n, rel)) or "C", set())
    assert seen == [(3, False), (140, True)]


def test_relative_without_a0_is_the_register_itself():
    # without the absolute-address bit the index is the loop counter, 0 here (loops are refused)
    seen = []
    sc.operand(fields(c0_rel=1, addr_abs=0), 1, lambda n, rel=False: seen.append((n, rel)) or "C", set())
    assert seen == [(180, False)]


def test_coff_object_layout():
    data = bytes(range(40))
    o = sc.coff_obj(data, "xb_shader_dxbc")
    machine, nsect, _, symtab, nsym, opt, _ = struct.unpack_from("<HHIIIHH", o, 0)
    assert (machine, nsect, nsym, opt) == (0x8664, 1, 1, 0)
    name, _, _, size, raw = struct.unpack_from("<8sIIII", o, 20)
    assert name.rstrip(b"\0") == b".rdata" and size == len(data) and o[raw:raw + size] == data
    _, stroff, value, sect, _, cls, aux = struct.unpack_from("<IIIhHBB", o, symtab)
    assert (value, sect, cls, aux) == (0, 1, 2, 0)
    strtab = o[symtab + 18:]
    assert strtab[stroff:].split(b"\0")[0] == b"xb_shader_dxbc"
    assert struct.unpack_from("<I", strtab, 0)[0] == len(strtab)


def test_vertex_shader_flips_z_for_upside_down_range():
    # the epilogue every converted vertex shader ends with: z = w - z when the kit sets xb_vte.w (runtime/xb_gpu_draw.h zflip_vs_on), after the
    # screen-space lines and only with the viewport transform on
    sh = sc.Shader.__new__(sc.Shader)
    sh.kind, sh.c, sh.ctab, sh.nlit = "vs", {"addr": 0x82000000}, [], 0
    sh.convert = lambda: (["    o.pos = float4(1, 2, 3, 4);"], [])
    sh.vfetch, sh.outs = [], {"pos"}
    sh.uses_p0 = sh.uses_end = sh.uses_a0 = False
    text = sh.hlsl()
    lines = text.splitlines()
    flip = "    if (xb_vte.z == 0 && xb_vte.w != 0) o.pos.z = o.pos.w - o.pos.z;"
    assert flip in lines
    assert lines.index(flip) > max(i for i, ln in enumerate(lines) if "xb_vte.z != 0" in ln)
    assert lines.index(flip) < lines.index("    return o;")


@pytest.mark.skipif(os.name != "nt", reason="the Direct3D compiler is Windows only")
def test_bytecode_cached_and_placed(tmp_path):
    text = "float4 main(float4 p : SV_Position) : SV_Target0 { return p; }\n"
    bc = sc.Bytecode(tmp_path)
    bc.run([(text, "ps_5_0"), (text, "ps_5_0")])
    assert bc.compiled == 1 and not bc.failed
    off, n = bc.place(text, "ps_5_0")
    assert off == 0 and n > 0 and bytes(bc.blob[:4]) == b"DXBC"
    again = sc.Bytecode(tmp_path)
    again.run([(text, "ps_5_0")])
    assert again.compiled == 0 and again.code[(text, "ps_5_0")] == bc.code[(text, "ps_5_0")]
    assert sc.Bytecode(tmp_path).place("not compiled", "ps_5_0") == (0, 0)

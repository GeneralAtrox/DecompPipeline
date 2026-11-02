"""The Direct3D unpack (vupkd3d128) of the vector helpers in vmx.py, in its own file (the 400-line cap).

Nearest existing: vmx_dot.py - the same kind of split, for another helper.
"""
from .ir import Assign
from .vmx import helper

# vupkd3d128: the Direct3D unpack formats, the oracle's TcVmxFloat.vupkd3d (types 0..5; the packed word is lane 3, a 64-bit
# format also lane 2). Fixed-point fields become 3.0 + n * 2^-22 (the game subtracts the 3.0), the most negative one a QNaN.
helper("xv_h2f", """
static u32 xv_h2f(u32 h)
{
    u32 sg = (h & 0x8000u) << 16, e = (h >> 10) & 31u, m = h & 1023u;
    if (e == 31u) return sg | 0x7f800000u | (m << 13);
    if (e == 0u) {
        if (m == 0u) return sg;
        e = 1u;
        while (!(m & 1024u)) { m <<= 1; e--; }
        m &= 1023u;
    }
    return sg | ((e + 112u) << 23) | (m << 13);
}""")
helper("xv_vupkd3d", """
static xv xv_vupkd3d(xv b, int t)
{
    xv r;
    u32 hi = b.w[2], lo = b.w[3];
    int i;
    switch (t) {
    case 0:
        r.w[0] = 0x3f800000u + ((lo >> 16) & 0xffu); r.w[1] = 0x3f800000u + ((lo >> 8) & 0xffu);
        r.w[2] = 0x3f800000u + (lo & 0xffu); r.w[3] = 0x3f800000u + (lo >> 24);
        break;
    case 1:
        r.w[0] = (lo >> 16) == 0x8000u ? 0x7fc00000u : 0x40400000u + (u32)(s32)(short)(lo >> 16);
        r.w[1] = (lo & 0xffffu) == 0x8000u ? 0x7fc00000u : 0x40400000u + (u32)(s32)(short)lo;
        r.w[2] = 0; r.w[3] = 0x3f800000u;
        break;
    case 4:
        r.w[0] = (hi >> 16) == 0x8000u ? 0x7fc00000u : 0x40400000u + (u32)(s32)(short)(hi >> 16);
        r.w[1] = (hi & 0xffffu) == 0x8000u ? 0x7fc00000u : 0x40400000u + (u32)(s32)(short)hi;
        r.w[2] = (lo >> 16) == 0x8000u ? 0x7fc00000u : 0x40400000u + (u32)(s32)(short)(lo >> 16);
        r.w[3] = (lo & 0xffffu) == 0x8000u ? 0x7fc00000u : 0x40400000u + (u32)(s32)(short)lo;
        break;
    case 2:
        for (i = 0; i < 3; i++) {
            s32 n = (s32)((lo >> (10 * i)) & 0x3ffu);
            if (n & 0x200) n -= 0x400;
            r.w[i] = n == -512 ? 0x7fc00000u : 0x40400000u + (u32)n;
        }
        r.w[3] = 0x3f800000u + (lo >> 30);
        break;
    case 3:
        r.w[0] = xv_h2f(lo >> 16); r.w[1] = xv_h2f(lo & 0xffffu); r.w[2] = 0; r.w[3] = 0x3f800000u;
        break;
    default:
        r.w[0] = xv_h2f(hi >> 16); r.w[1] = xv_h2f(hi & 0xffffu);
        r.w[2] = xv_h2f(lo >> 16); r.w[3] = xv_h2f(lo & 0xffffu);
        break;
    }
    return r;
}""", ["xv_h2f"])


def lift(ft, i, o, V, vcall, s):
    from .translate import TranslateError
    if i.imm > 5:
        raise TranslateError(f"{o} at {i.addr:08x}: unpack type {i.imm} is not modelled (the oracle has 0..5)")
    ft.vhelpers.add("xv_vupkd3d")
    return [Assign(f"v{i.vd}", vcall("xv_vupkd3d", [V(i.vb), s(i.imm)]))]

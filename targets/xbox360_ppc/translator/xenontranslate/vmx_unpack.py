"""The Direct3D unpack (vupkd3d128) and pack (vpkd3d128) of the vector helpers in vmx.py, in its own file (the 400-line cap).

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


# vpkd3d128: the Direct3D pack formats, the oracle's TcVmxFloat.vpkd3d (types 0..5), pkFix and pkHalf. Floats are read as they are
# (the oracle's F does not flush). A field is (f - base) * 2^scale, clamped, truncated; NaN gives the lower bound
helper("xv_pkfix", """
static s32 xv_pkfix(u32 w, f64 base, f64 scale, s32 lo, s32 hi)
{
    union { f32 f; u32 w; } v;
    f64 t;
    v.w = w;
    if (v.f != v.f) return lo;
    t = ((f64)v.f - base) * scale;
    if (t <= (f64)lo) return lo;
    if (t >= (f64)hi) return hi;
    return (s32)t;
}""")
# Java's Float.floatToFloat16, rounding to nearest even, after the oracle's clamp to +-65504; NaN is 0xfbff
helper("xv_pkhalf", """
static u32 xv_pkhalf(u32 w)
{
    union { f32 f; u32 w; } v;
    u32 sg, e, m, h, rest, half;
    int sh;
    v.w = w;
    if (v.f != v.f) return 0xfbffu;
    if (v.f > 65504.0f) v.f = 65504.0f;
    if (v.f < -65504.0f) v.f = -65504.0f;
    sg = (v.w >> 16) & 0x8000u; e = (v.w >> 23) & 255u; m = v.w & 0x7fffffu;
    if (e >= 113u) {
        h = ((e - 112u) << 10) | (m >> 13);
        rest = m & 0x1fffu;
        if (rest > 0x1000u || (rest == 0x1000u && (h & 1u))) h++;
        return sg | h;
    }
    sh = 126 - (int)e;
    if (sh > 24) return sg;
    m |= 0x800000u;
    h = m >> sh;
    rest = m & ((1u << sh) - 1u);
    half = 1u << (sh - 1);
    if (rest > half || (rest == half && (h & 1u))) h++;
    return sg | h;
}""")
helper("xv_vpkd3d", """
static xv xv_vpkd3d(xv d, xv b, int t, int mask, int z)
{
    const f64 p22 = 4194304.0, p23 = 8388608.0;
    u32 hi = 0, lo;
    int p;
    switch (t) {
    case 0:
        lo = ((u32)xv_pkfix(b.w[3], 3.0, p22, 0, 255) << 24) | ((u32)xv_pkfix(b.w[0], 3.0, p22, 0, 255) << 16)
           | ((u32)xv_pkfix(b.w[1], 3.0, p22, 0, 255) << 8) | (u32)xv_pkfix(b.w[2], 3.0, p22, 0, 255);
        break;
    case 1:
        lo = ((u32)xv_pkfix(b.w[0], 3.0, p22, -32767, 32767) << 16) | ((u32)xv_pkfix(b.w[1], 3.0, p22, -32767, 32767) & 0xffffu);
        break;
    case 2:
        lo = ((u32)xv_pkfix(b.w[3], 1.0, p23, 0, 3) << 30) | (((u32)xv_pkfix(b.w[2], 3.0, p22, -511, 511) & 0x3ffu) << 20)
           | (((u32)xv_pkfix(b.w[1], 3.0, p22, -511, 511) & 0x3ffu) << 10) | ((u32)xv_pkfix(b.w[0], 3.0, p22, -511, 511) & 0x3ffu);
        break;
    case 3:
        lo = (xv_pkhalf(b.w[0]) << 16) | xv_pkhalf(b.w[1]);
        break;
    case 4:
        hi = ((u32)xv_pkfix(b.w[0], 3.0, p22, -32767, 32767) << 16) | ((u32)xv_pkfix(b.w[1], 3.0, p22, -32767, 32767) & 0xffffu);
        lo = ((u32)xv_pkfix(b.w[2], 3.0, p22, -32767, 32767) << 16) | ((u32)xv_pkfix(b.w[3], 3.0, p22, -32767, 32767) & 0xffffu);
        break;
    default:
        hi = (xv_pkhalf(b.w[0]) << 16) | xv_pkhalf(b.w[1]);
        lo = (xv_pkhalf(b.w[2]) << 16) | xv_pkhalf(b.w[3]);
        break;
    }
    /* mask 1: the word at lane 3 - z; mask 2 (and 3 with z < 3): hi:lo at lanes 2 - z, 3 - z, a lane outside 0..3 dropped; mask 3, z 3: hi at lane 3 */
    if (mask == 1) {
        d.w[3 - z] = lo;
    } else if (mask == 2 || (mask == 3 && z < 3)) {
        p = 2 - z;
        if (p >= 0) d.w[p] = hi;
        if (p + 1 >= 0 && p + 1 <= 3) d.w[p + 1] = lo;
    } else if (mask == 3) {
        d.w[3] = hi;
    }
    return d;
}""", ["xv_pkfix", "xv_pkhalf"])


def lift_pack(ft, i, o, V, vcall, s):
    from .translate import TranslateError
    if i.imm > 5:
        raise TranslateError(f"{o} at {i.addr:08x}: pack type {i.imm} is not modelled (the oracle has 0..5)")
    ft.vhelpers.add("xv_vpkd3d")
    return [Assign(f"v{i.vd}", vcall("xv_vpkd3d", [V(i.vd), V(i.vb), s(i.imm), s(i.mask), s(i.z)]))]

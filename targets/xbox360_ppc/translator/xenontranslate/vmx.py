"""The Xenon vector unit (AltiVec plus the VMX128 extension) -> C.

A vector register is the 128-bit struct `xv` (four big-endian words: w[0] holds bytes 0..3, the leftmost); every
vector instruction becomes a call of a static helper that work.c defines itself (DecompPipeline's raw-C form allows
static helpers and a top-level typedef struct, and tc_rt.h has no vector interface yet). The helpers are written from
the AltiVec Technology Programming Environments Manual's operation descriptions and the VMX128 extension's
(REFERENCES.md); the operand order of every VMX128 form was checked against a reference disassembler's operand
display (tools/check_decoder.py).

Floating-point model (DESIGN NOTE: the judge's vector phase does not exist yet, so none of this is proven against an
oracle; it is one place to change):
- non-Java mode as the Xbox 360 runs it: denormal inputs and results are flushed to zero of the same sign;
- a NaN operand gives that NaN quieted (operands in instruction order), an invalid operation the default NaN
  0x7fc00000;
- the multiply-adds round once (fused); the dot products (vmsum3fp128 / vmsum4fp128) sum exact products in double
  and round once to single; the estimates (vrefp, vrsqrtefp, vexptefp, vlogefp) are computed to single precision,
  not with the hardware's tables;
- saturating integer operations do not record VSCR[SAT].
"""
from __future__ import annotations

from . import expr as X
from .expr import Call, Const, Var, s, u
from .ir import Assign, Store

XV = "xv"


class VmxUnsupported(Exception):
    pass


def V(n):
    return Var(f"v{n}", XV)


def vcall(fn, args):
    return Call(fn, args, XV)


# ------------------------------------------------------------------------------------------------ C helpers
TYPEDEF = "typedef struct { u32 w[4]; } xv;"

H = {}          # name -> C text
DEP = {}        # name -> [names]


def helper(name, text, deps=()):
    H[name] = text.strip("\n")
    DEP[name] = list(deps)


# lane access
helper("xv_gb", """
static u32 xv_gb(xv a, int k)
{
    return (a.w[k >> 2] >> (24 - 8 * (k & 3))) & 0xffu;
}""")
helper("xv_sb", """
static void xv_sb(xv *a, int k, u32 v)
{
    int s = 24 - 8 * (k & 3);
    a->w[k >> 2] = (a->w[k >> 2] & ~(0xffu << s)) | ((v & 0xffu) << s);
}""")
helper("xv_gh", """
static u32 xv_gh(xv a, int k)
{
    return (a.w[k >> 1] >> (16 - 16 * (k & 1))) & 0xffffu;
}""")
helper("xv_sh", """
static void xv_sh(xv *a, int k, u32 v)
{
    int s = 16 - 16 * (k & 1);
    a->w[k >> 1] = (a->w[k >> 1] & ~(0xffffu << s)) | ((v & 0xffffu) << s);
}""")
# floats: words to and from f32, denormals flushed (non-Java mode)
helper("xv_wf", """
static f32 xv_wf(u32 w)
{
    union { f32 f; u32 w; } v;
    if ((w & 0x7f800000u) == 0) w &= 0x80000000u;
    v.w = w;
    return v.f;
}""")
helper("xv_fw", """
static u32 xv_fw(f32 f)
{
    union { f32 f; u32 w; } v;
    v.f = f;
    if ((v.w & 0x7f800000u) == 0) v.w &= 0x80000000u;
    return v.w;
}""")
helper("xv_isnan", """
static int xv_isnan(u32 w)
{
    return (w & 0x7f800000u) == 0x7f800000u && (w & 0x007fffffu) != 0;
}""")
# the NaN result of an operation on a, b (and c): the first NaN operand quieted, else r's NaN made the default one
helper("xv_nan3", """
static u32 xv_nan3(u32 r, u32 a, u32 b, u32 c, int n)
{
    if (xv_isnan(a)) return a | 0x00400000u;
    if (xv_isnan(b)) return b | 0x00400000u;
    if (n > 2 && xv_isnan(c)) return c | 0x00400000u;
    if (xv_isnan(r)) return 0x7fc00000u;
    return r;
}""", ["xv_isnan"])
helper("xv_f2b", """
static u64 xv_f2b(f64 x)
{
    union { f64 d; u64 b; } v;
    v.d = x;
    return v.b;
}""")
helper("xv_b2f", """
static f64 xv_b2f(u64 x)
{
    union { f64 d; u64 b; } v;
    v.b = x;
    return v.d;
}""")
# a double rounded once to single, when the double s is a + b with b's error lost: s is moved off an exact
# single-precision tie in the direction of the lost part, so the double-then-single rounding equals one rounding
helper("xv_rnd1", """
static f32 xv_rnd1(f64 s, f64 e)
{
    u64 b = xv_f2b(s);
    if (e != 0.0 && (b & 0x1fffffffull) == 0x10000000ull) {
        if ((e > 0.0) == (s > 0.0)) b++; else b--;
    }
    return (f32)xv_b2f(b);
}""", ["xv_f2b", "xv_b2f"])
# a * b + c in one rounding (a * b is exact in double; TwoSum gives the error of the double sum)
helper("xv_fma1", """
static u32 xv_fma1(u32 x, u32 y, u32 z, int neg)
{
    f64 p = (f64)xv_wf(x) * (f64)xv_wf(y);
    f64 c = (f64)xv_wf(z);
    f64 s = p + c;
    f64 bb = s - p;
    f64 e = (p - (s - bb)) + (c - bb);
    u32 r = xv_fw(xv_rnd1(s, e));
    r = xv_nan3(r, x, y, z, 3);
    if (neg && !xv_isnan(r)) r ^= 0x80000000u;
    return r;
}""", ["xv_wf", "xv_fw", "xv_rnd1", "xv_nan3", "xv_isnan"])


def lanes_fp2(name, expr):
    helper(name, f"""
static xv {name}(xv a, xv b)
{{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {{
        f32 x = xv_wf(a.w[k]), y = xv_wf(b.w[k]);
        r.w[k] = xv_nan3(xv_fw({expr}), a.w[k], b.w[k], 0, 2);
    }}
    return r;
}}""", ["xv_wf", "xv_fw", "xv_nan3"])


lanes_fp2("xv_vaddfp", "x + y")
lanes_fp2("xv_vsubfp", "x - y")
lanes_fp2("xv_vmulfp", "x * y")
helper("xv_vmaddfp", """
static xv xv_vmaddfp(xv a, xv b, xv c)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = xv_fma1(a.w[k], b.w[k], c.w[k], 0);
    return r;
}""", ["xv_fma1"])
# -(a * b - c) = -a * b + c: the product's sign flipped, one rounding (a NaN result keeps its sign)
helper("xv_vnmsubfp", """
static xv xv_vnmsubfp(xv a, xv b, xv c)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        u32 x = xv_isnan(a.w[k]) ? a.w[k] : a.w[k] ^ 0x80000000u;
        r.w[k] = xv_fma1(x, b.w[k], c.w[k], 0);
        if (xv_isnan(r.w[k]) && r.w[k] == ((a.w[k] ^ 0x80000000u) | 0x00400000u)) r.w[k] ^= 0x80000000u;
    }
    return r;
}""", ["xv_fma1", "xv_isnan"])
helper("xv_vmaxfp", """
static xv xv_vmaxfp(xv a, xv b)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        u32 x = a.w[k], y = b.w[k];
        f32 fx = xv_wf(x), fy = xv_wf(y);
        if (xv_isnan(x) || xv_isnan(y)) r.w[k] = xv_nan3(0u, x, y, 0, 2);
        else if (fx > fy) r.w[k] = xv_fw(fx);
        else if (fy > fx) r.w[k] = xv_fw(fy);
        else r.w[k] = xv_fw(fx) & xv_fw(fy);
    }
    return r;
}""", ["xv_wf", "xv_fw", "xv_nan3", "xv_isnan"])
helper("xv_vminfp", """
static xv xv_vminfp(xv a, xv b)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        u32 x = a.w[k], y = b.w[k];
        f32 fx = xv_wf(x), fy = xv_wf(y);
        if (xv_isnan(x) || xv_isnan(y)) r.w[k] = xv_nan3(0u, x, y, 0, 2);
        else if (fx < fy) r.w[k] = xv_fw(fx);
        else if (fy < fx) r.w[k] = xv_fw(fy);
        else r.w[k] = xv_fw(fx) | xv_fw(fy);
    }
    return r;
}""", ["xv_wf", "xv_fw", "xv_nan3", "xv_isnan"])
# dot products: exact products summed in double, rounded once to single, in every word
helper("xv_dot", """
static xv xv_dot(xv a, xv b, int n)
{
    xv r;
    int k;
    f64 t = 0.0;
    u32 w = 0;
    int nan = 0;
    for (k = 0; k < n; k++) {
        t += (f64)xv_wf(a.w[k]) * (f64)xv_wf(b.w[k]);
        if (!nan && (xv_isnan(a.w[k]) || xv_isnan(b.w[k]))) {
            w = xv_nan3(0u, a.w[k], b.w[k], 0, 2);
            nan = 1;
        }
    }
    if (!nan) w = xv_fw((f32)t);
    if (!nan && xv_isnan(w)) w = 0x7fc00000u;
    for (k = 0; k < 4; k++) r.w[k] = w;
    return r;
}""", ["xv_wf", "xv_fw", "xv_nan3", "xv_isnan"])
helper("xv_vmsum3fp", """
static xv xv_vmsum3fp(xv a, xv b)
{
    return xv_dot(a, b, 3);
}""", ["xv_dot"])
helper("xv_vmsum4fp", """
static xv xv_vmsum4fp(xv a, xv b)
{
    return xv_dot(a, b, 4);
}""", ["xv_dot"])
# estimates, computed to single precision (the hardware's tables are not modelled)
helper("xv_vrefp", """
static xv xv_vrefp(xv b)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        f64 x = (f64)xv_wf(b.w[k]);
        f64 one = 1.0;
        r.w[k] = xv_isnan(b.w[k]) ? b.w[k] | 0x00400000u : xv_fw((f32)(one / x));
    }
    return r;
}""", ["xv_wf", "xv_fw", "xv_isnan"])
helper("xv_vrsqrtefp", """
static xv xv_vrsqrtefp(xv b)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        f64 x = (f64)xv_wf(b.w[k]);
        f64 one = 1.0;
        if (xv_isnan(b.w[k])) r.w[k] = b.w[k] | 0x00400000u;
        else if (x < 0.0) r.w[k] = 0x7fc00000u;
        else r.w[k] = xv_fw((f32)(one / ppc_fsqrt(x)));
    }
    return r;
}""", ["xv_wf", "xv_fw", "xv_isnan"])
# 2^x: 2^floor(x) times 2^f for the fraction f in [0, 1) by its series (to well past single precision)
helper("xv_exp2", """
static f64 xv_exp2(f64 x)
{
    f64 n, f, t, s, p;
    int k, e;
    if (x != x) return x;
    if (x >= 128.0) return xv_b2f(0x7ff0000000000000ull);
    if (x <= -150.0) return 0.0;
    n = (f64)(s32)x;
    if (n > x) n -= 1.0;
    f = (x - n) * 0.6931471805599453;
    s = 1.0;
    t = 1.0;
    for (k = 1; k < 20; k++) {
        t = t * f / (f64)k;
        s += t;
    }
    e = (s32)n;
    p = 1.0;
    for (; e > 0; e--) p *= 2.0;
    for (; e < 0; e++) p *= 0.5;
    return s * p;
}""", ["xv_b2f"])
helper("xv_vexptefp", """
static xv xv_vexptefp(xv b)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++)
        r.w[k] = xv_isnan(b.w[k]) ? b.w[k] | 0x00400000u : xv_fw((f32)xv_exp2((f64)xv_wf(b.w[k])));
    return r;
}""", ["xv_wf", "xv_fw", "xv_isnan", "xv_exp2"])
# log2(x) = exponent + log2(mantissa), the mantissa's by 2 atanh((m - 1) / (m + 1)) / ln 2
helper("xv_log2", """
static u32 xv_log2(u32 w)
{
    f64 m, z, z2, t, s;
    int e, k;
    if (xv_isnan(w)) return w | 0x00400000u;
    if ((w & 0x7fffffffu) == 0 || (w & 0x7f800000u) == 0) return 0xff800000u;
    if (w & 0x80000000u) return 0x7fc00000u;
    if (w == 0x7f800000u) return w;
    e = (s32)((w >> 23) & 0xffu) - 127;
    m = (f64)xv_wf((w & 0x007fffffu) | 0x3f800000u);
    z = (m - 1.0) / (m + 1.0);
    z2 = z * z;
    t = z;
    s = 0.0;
    for (k = 1; k < 40; k += 2) {
        s += t / (f64)k;
        t *= z2;
    }
    return xv_fw((f32)((f64)e + 2.0 * s / 0.6931471805599453));
}""", ["xv_wf", "xv_fw", "xv_isnan"])
helper("xv_vlogefp", """
static xv xv_vlogefp(xv b)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = xv_log2(b.w[k]);
    return r;
}""", ["xv_log2"])
# round to an integral value: mode 0 nearest even, 1 toward zero, 2 toward +inf, 3 toward -inf
helper("xv_rfi", """
static xv xv_rfi(xv b, int mode)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        u32 w = b.w[k];
        f64 x = (f64)xv_wf(w), t;
        if (xv_isnan(w)) { r.w[k] = w | 0x00400000u; continue; }
        if ((w & 0x7f800000u) >= 0x4b000000u) { r.w[k] = xv_fw(xv_wf(w)); continue; }
        t = (f64)(s32)x;
        if (mode == 0) {
            f64 d = x - t;
            if (d > 0.5 || (d == 0.5 && ((s32)t & 1))) t += 1.0;
            else if (d < -0.5 || (d == -0.5 && ((s32)t & 1))) t -= 1.0;
        } else if (mode == 2) {
            if (t < x) t += 1.0;
        } else if (mode == 3) {
            if (t > x) t -= 1.0;
        }
        r.w[k] = xv_fw((f32)t) | (w & 0x80000000u);
    }
    return r;
}""", ["xv_wf", "xv_fw", "xv_isnan"])
for _n, _m in (("xv_vrfin", 0), ("xv_vrfiz", 1), ("xv_vrfip", 2), ("xv_vrfim", 3)):
    helper(_n, f"""
static xv {_n}(xv b)
{{
    return xv_rfi(b, {_m});
}}""", ["xv_rfi"])
helper("xv_pow2", """
static f64 xv_pow2(int n)
{
    f64 p = 1.0;
    for (; n > 0; n--) p *= 2.0;
    for (; n < 0; n++) p *= 0.5;
    return p;
}""")
# integer words to floats, scaled by 2^-uimm
helper("xv_vcfsx", """
static xv xv_vcfsx(xv b, int sh)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = xv_fw((f32)((f64)(s32)b.w[k] * xv_pow2(-sh)));
    return r;
}""", ["xv_fw", "xv_pow2"])
helper("xv_vcfux", """
static xv xv_vcfux(xv b, int sh)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = xv_fw((f32)((f64)b.w[k] * xv_pow2(-sh)));
    return r;
}""", ["xv_fw", "xv_pow2"])
# floats scaled by 2^uimm to integer words, toward zero, saturating (NaN gives 0)
helper("xv_vctsxs", """
static xv xv_vctsxs(xv b, int sh)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        f64 x = (f64)xv_wf(b.w[k]) * xv_pow2(sh);
        if (xv_isnan(b.w[k])) r.w[k] = 0;
        else if (x >= 2147483647.0) r.w[k] = 0x7fffffffu;
        else if (x <= -2147483648.0) r.w[k] = 0x80000000u;
        else r.w[k] = (u32)(s32)x;
    }
    return r;
}""", ["xv_wf", "xv_isnan", "xv_pow2"])
helper("xv_vctuxs", """
static xv xv_vctuxs(xv b, int sh)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        f64 x = (f64)xv_wf(b.w[k]) * xv_pow2(sh);
        if (xv_isnan(b.w[k]) || x <= 0.0) r.w[k] = 0;
        else if (x >= 4294967295.0) r.w[k] = 0xffffffffu;
        else r.w[k] = (u32)(u64)x;
    }
    return r;
}""", ["xv_wf", "xv_isnan", "xv_pow2"])


def fcmp(name, cond):
    helper(name, f"""
static xv {name}(xv a, xv b)
{{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {{
        f32 x = xv_wf(a.w[k]), y = xv_wf(b.w[k]);
        r.w[k] = (!xv_isnan(a.w[k]) && !xv_isnan(b.w[k]) && {cond}) ? 0xffffffffu : 0u;
    }}
    return r;
}}""", ["xv_wf", "xv_isnan"])


fcmp("xv_vcmpeqfp", "x == y")
fcmp("xv_vcmpgefp", "x >= y")
fcmp("xv_vcmpgtfp", "x > y")
helper("xv_vcmpbfp", """
static xv xv_vcmpbfp(xv a, xv b)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        f32 x = xv_wf(a.w[k]), y = xv_wf(b.w[k]);
        int nan = xv_isnan(a.w[k]) || xv_isnan(b.w[k]);
        r.w[k] = ((nan || !(x <= y)) ? 0x80000000u : 0u) | ((nan || !(x >= -y)) ? 0x40000000u : 0u);
    }
    return r;
}""", ["xv_wf", "xv_isnan"])
# CR6 of a dotted compare: every element all ones / every element zero
helper("xv_all1", """
static u32 xv_all1(xv a)
{
    return (a.w[0] & a.w[1] & a.w[2] & a.w[3]) == 0xffffffffu;
}""")
helper("xv_all0", """
static u32 xv_all0(xv a)
{
    return (a.w[0] | a.w[1] | a.w[2] | a.w[3]) == 0;
}""")

# integer lanes: (lane kind, count, get, set, signed cast, bits)
LANE = {"b": (16, "xv_gb", "xv_sb", "s8", 8), "h": (8, "xv_gh", "xv_sh", "s16", 16), "w": (4, None, None, "s32", 32)}


def lanes_int(name, kind, expr, signed=False, wide=False):
    """r[k] = expr over x = a[k], y = b[k] (signed: x, y as signed values; wide: in 64 bits)."""
    n, g, st, sc, nb = LANE[kind]
    ty = ("s64" if signed else "u64") if wide or kind == "w" else ("s32" if signed else "u32")
    if kind == "w":
        gx, gy = "a.w[k]", "b.w[k]"
    else:
        gx, gy = f"{g}(a, k)", f"{g}(b, k)"
    if signed:
        gx, gy = f"({ty})({sc}){gx}", f"({ty})({sc}){gy}"
    else:
        gx, gy = f"({ty}){gx}", f"({ty}){gy}"
    put = f"r.w[k] = (u32)({expr});" if kind == "w" else f"{st}(&r, k, (u32)({expr}));"
    helper(name, f"""
static xv {name}(xv a, xv b)
{{
    xv r = a;
    int k;
    for (k = 0; k < {n}; k++) {{
        {ty} x = {gx}, y = {gy};
        {put}
    }}
    return r;
}}""", [d for d in (g, st) if d])


def sat(lo, hi):
    return f"x {{op}} y < {lo} ? {lo} : x {{op}} y > {hi} ? {hi} : x {{op}} y"


for kind, bits in (("b", 8), ("h", 16), ("w", 32)):
    full = (1 << bits) - 1
    smax, smin = (1 << (bits - 1)) - 1, -(1 << (bits - 1))
    sfx = {"b": "ub", "h": "uh", "w": "uw"}[kind]
    ssx = {"b": "sb", "h": "sh", "w": "sw"}[kind]
    lanes_int(f"xv_vadd{sfx}m", kind, "x + y")
    lanes_int(f"xv_vsub{sfx}m", kind, "x - y")
    lanes_int(f"xv_vadd{sfx}s", kind, f"x + y > {full}u ? {full}u : x + y")
    lanes_int(f"xv_vsub{sfx}s", kind, "x < y ? 0u : x - y")
    lo_, hi_ = (f"({smin + 1}ll - 1)", f"{smax}ll") if bits == 32 else (smin, smax)
    lanes_int(f"xv_vadd{ssx}s", kind, sat(lo_, hi_).format(op="+"), signed=True)
    lanes_int(f"xv_vsub{ssx}s", kind, sat(lo_, hi_).format(op="-"), signed=True)
    lanes_int(f"xv_vmax{sfx}", kind, "x > y ? x : y")
    lanes_int(f"xv_vmin{sfx}", kind, "x < y ? x : y")
    lanes_int(f"xv_vmax{ssx}", kind, "x > y ? x : y", signed=True)
    lanes_int(f"xv_vmin{ssx}", kind, "x < y ? x : y", signed=True)
    lanes_int(f"xv_vavg{sfx}", kind, "(x + y + 1) >> 1", wide=True)
    lanes_int(f"xv_vavg{ssx}", kind, "(x + y + 1) >> 1", signed=True, wide=True)
    lanes_int(f"xv_vcmpequ{kind}", kind, f"x == y ? {full}u : 0u")
    lanes_int(f"xv_vcmpgtu{kind}", kind, f"x > y ? {full}u : 0u")
    lanes_int(f"xv_vcmpgts{kind}", kind, f"x > y ? {full}u : 0u", signed=True)
    m = bits - 1
    lanes_int(f"xv_vsl{kind}", kind, f"x << (y & {m})")
    lanes_int(f"xv_vsr{kind}", kind, f"x >> (y & {m})")
    lanes_int(f"xv_vsra{kind}", kind, f"x >> (y & {m})", signed=True)
    lanes_int(f"xv_vrl{kind}", kind, f"(y & {m}) ? (x << (y & {m})) | (x >> ({bits} - (y & {m}))) : x")
# a signed shift right is arithmetic (MSVC, as the C standard leaves it to the implementation)


def words(name, expr):
    helper(name, f"""
static xv {name}(xv a, xv b)
{{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = {expr};
    return r;
}}""")


words("xv_vand", "a.w[k] & b.w[k]")
words("xv_vandc", "a.w[k] & ~b.w[k]")
words("xv_vor", "a.w[k] | b.w[k]")
words("xv_vxor", "a.w[k] ^ b.w[k]")
words("xv_vnor", "~(a.w[k] | b.w[k])")
helper("xv_vsel", """
static xv xv_vsel(xv a, xv b, xv c)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = (a.w[k] & ~c.w[k]) | (b.w[k] & c.w[k]);
    return r;
}""")
helper("xv_bytes", """
static void xv_bytes(xv a, u32 *o)
{
    int k;
    for (k = 0; k < 16; k++) o[k] = xv_gb(a, k);
}""", ["xv_gb"])
helper("xv_vperm", """
static xv xv_vperm(xv a, xv b, xv c)
{
    xv r = a;
    u32 x[32];
    int k;
    xv_bytes(a, x);
    xv_bytes(b, x + 16);
    for (k = 0; k < 16; k++) xv_sb(&r, k, x[xv_gb(c, k) & 31u]);
    return r;
}""", ["xv_bytes", "xv_gb", "xv_sb"])
helper("xv_vsldoi", """
static xv xv_vsldoi(xv a, xv b, int sh)
{
    xv r = a;
    u32 x[32];
    int k;
    xv_bytes(a, x);
    xv_bytes(b, x + 16);
    for (k = 0; k < 16; k++) xv_sb(&r, k, x[k + sh]);
    return r;
}""", ["xv_bytes", "xv_sb"])
# whole-register shifts by octets (vB's byte 15, bits 1..4) and by bits (its bits 5..7)
helper("xv_vslo", """
static xv xv_vslo(xv a, xv b)
{
    xv r = a;
    u32 x[16];
    int k, sh = (int)((xv_gb(b, 15) >> 3) & 15u);
    xv_bytes(a, x);
    for (k = 0; k < 16; k++) xv_sb(&r, k, k + sh < 16 ? x[k + sh] : 0u);
    return r;
}""", ["xv_bytes", "xv_gb", "xv_sb"])
helper("xv_vsro", """
static xv xv_vsro(xv a, xv b)
{
    xv r = a;
    u32 x[16];
    int k, sh = (int)((xv_gb(b, 15) >> 3) & 15u);
    xv_bytes(a, x);
    for (k = 0; k < 16; k++) xv_sb(&r, k, k >= sh ? x[k - sh] : 0u);
    return r;
}""", ["xv_bytes", "xv_gb", "xv_sb"])
helper("xv_vsl", """
static xv xv_vsl(xv a, xv b)
{
    xv r;
    int k, sh = (int)(xv_gb(b, 15) & 7u);
    for (k = 0; k < 4; k++)
        r.w[k] = sh ? (a.w[k] << sh) | (k < 3 ? a.w[k + 1] >> (32 - sh) : 0u) : a.w[k];
    return r;
}""", ["xv_gb"])
helper("xv_vsr", """
static xv xv_vsr(xv a, xv b)
{
    xv r;
    int k, sh = (int)(xv_gb(b, 15) & 7u);
    for (k = 0; k < 4; k++)
        r.w[k] = sh ? (a.w[k] >> sh) | (k > 0 ? a.w[k - 1] << (32 - sh) : 0u) : a.w[k];
    return r;
}""", ["xv_gb"])


def merge(name, kind, high):
    n, g, st, _, _ = LANE[kind]
    half = n // 2
    off = "" if high else f" + {half}"
    if kind == "w":
        body = f"r.w[2 * k] = a.w[k{off}];\n        r.w[2 * k + 1] = b.w[k{off}];"
        deps = []
    else:
        body = f"{st}(&r, 2 * k, {g}(a, k{off}));\n        {st}(&r, 2 * k + 1, {g}(b, k{off}));"
        deps = [g, st]
    helper(name, f"""
static xv {name}(xv a, xv b)
{{
    xv r = a;
    int k;
    for (k = 0; k < {half}; k++) {{
        {body}
    }}
    return r;
}}""", deps)


for _k in "bhw":
    merge(f"xv_vmrgh{_k}", _k, True)
    merge(f"xv_vmrgl{_k}", _k, False)
helper("xv_vspltb", """
static xv xv_vspltb(xv b, int n)
{
    xv r = b;
    u32 v = xv_gb(b, n & 15);
    int k;
    for (k = 0; k < 16; k++) xv_sb(&r, k, v);
    return r;
}""", ["xv_gb", "xv_sb"])
helper("xv_vsplth", """
static xv xv_vsplth(xv b, int n)
{
    xv r = b;
    u32 v = xv_gh(b, n & 7);
    int k;
    for (k = 0; k < 8; k++) xv_sh(&r, k, v);
    return r;
}""", ["xv_gh", "xv_sh"])
helper("xv_vspltw", """
static xv xv_vspltw(xv b, int n)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = b.w[n & 3];
    return r;
}""")
helper("xv_vspltis", """
static xv xv_vspltis(u32 w)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = w;
    return r;
}""")


def pack(name, src, dst, expr, signed_src):
    """r = src lanes of a then b narrowed to dst lanes by expr over v (the source lane value)."""
    n, g, _, sc, _ = LANE[src]
    m, _, st, _, _ = LANE[dst]
    get = "x.w[j]" if src == "w" else f"{g}(x, j)"
    ty = "s64" if signed_src else "u64"
    val = f"({ty})({sc}){get}" if signed_src else f"({ty}){get}"
    put = f"{st}(&r, k, (u32)({expr}));"
    helper(name, f"""
static xv {name}(xv a, xv b)
{{
    xv r = a;
    int k;
    for (k = 0; k < {m}; k++) {{
        xv x = k < {n} ? a : b;
        int j = k < {n} ? k : k - {n};
        {ty} v = {val};
        {put}
    }}
    return r;
}}""", [d for d in (g, st) if d])


pack("xv_vpkuhum", "h", "b", "v", False)
pack("xv_vpkuwum", "w", "h", "v", False)
pack("xv_vpkuhus", "h", "b", "v > 255u ? 255u : v", False)
pack("xv_vpkuwus", "w", "h", "v > 65535u ? 65535u : v", False)
pack("xv_vpkshus", "h", "b", "v < 0 ? 0 : v > 255 ? 255 : v", True)
pack("xv_vpkswus", "w", "h", "v < 0 ? 0 : v > 65535 ? 65535 : v", True)
pack("xv_vpkshss", "h", "b", "v < -128 ? -128 : v > 127 ? 127 : v", True)
pack("xv_vpkswss", "w", "h", "v < -32768 ? -32768 : v > 32767 ? 32767 : v", True)


def unpack(name, src, dst, high):
    n, g, _, sc, _ = LANE[src]
    m, gd, st, _, _ = LANE[dst]
    off = "" if high else f" + {m}"
    get = f"{g}(b, k{off})"
    put = f"r.w[k] = (u32)(s32)({sc}){get};" if dst == "w" else f"{st}(&r, k, (u32)(s32)({sc}){get});"
    helper(name, f"""
static xv {name}(xv b)
{{
    xv r = b;
    int k;
    for (k = 0; k < {m}; k++) {put}
    return r;
}}""", [d for d in (g, st) if d])


unpack("xv_vupkhsb", "b", "h", True)
unpack("xv_vupklsb", "b", "h", False)
unpack("xv_vupkhsh", "h", "w", True)
unpack("xv_vupklsh", "h", "w", False)
helper("xv_vpermwi", """
static xv xv_vpermwi(xv b, int p)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = b.w[(p >> (6 - 2 * k)) & 3];
    return r;
}""")
helper("xv_vrlimi", """
static xv xv_vrlimi(xv d, xv b, int m, int z)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = (m & (8 >> k)) ? b.w[(k + z) & 3] : d.w[k];
    return r;
}""")
# memory
helper("xv_ld", """
static xv xv_ld(u32 ea)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) r.w[k] = rd32(ea + 4u * (u32)k);
    return r;
}""")
helper("xv_st", """
static void xv_st(u32 ea, xv v)
{
    int k;
    for (k = 0; k < 4; k++) wr32(ea + 4u * (u32)k, v.w[k]);
}""")
# lvlx: the bytes from ea to the end of its quadword, left-justified, zeros after; lvrx: the bytes of the quadword
# before ea, right-justified, zeros before
helper("xv_ldl", """
static xv xv_ldl(u32 ea)
{
    xv r = {0};
    int k, n = 16 - (int)(ea & 15u);
    for (k = 0; k < n; k++) xv_sb(&r, k, rd8(ea + (u32)k));
    return r;
}""", ["xv_sb"])
helper("xv_ldr", """
static xv xv_ldr(u32 ea)
{
    xv r = {0};
    int k, n = (int)(ea & 15u);
    u32 b = ea & ~15u;
    for (k = 0; k < n; k++) xv_sb(&r, 16 - n + k, rd8(b + (u32)k));
    return r;
}""", ["xv_sb"])
helper("xv_stl", """
static void xv_stl(u32 ea, xv v)
{
    int k, n = 16 - (int)(ea & 15u);
    for (k = 0; k < n; k++) wr8(ea + (u32)k, (u8)xv_gb(v, k));
}""", ["xv_gb"])
helper("xv_str", """
static void xv_str(u32 ea, xv v)
{
    int k, n = (int)(ea & 15u);
    u32 b = ea & ~15u;
    for (k = 0; k < n; k++) wr8(b + (u32)k, (u8)xv_gb(v, 16 - n + k));
}""", ["xv_gb"])
# lvewx / lvehx / lvebx: the element at the address's position; the other elements are undefined on hardware and 0 here
helper("xv_ldew", """
static xv xv_ldew(u32 ea)
{
    xv r = {0};
    r.w[(ea >> 2) & 3u] = rd32(ea);
    return r;
}""")
helper("xv_ldeh", """
static xv xv_ldeh(u32 ea)
{
    xv r = {0};
    xv_sh(&r, (int)((ea >> 1) & 7u), rd16(ea));
    return r;
}""", ["xv_sh"])
helper("xv_ldeb", """
static xv xv_ldeb(u32 ea)
{
    xv r = {0};
    xv_sb(&r, (int)(ea & 15u), rd8(ea));
    return r;
}""", ["xv_sb"])
helper("xv_stew", """
static void xv_stew(u32 ea, xv v)
{
    wr32(ea, v.w[(ea >> 2) & 3u]);
}""")
helper("xv_steh", """
static void xv_steh(u32 ea, xv v)
{
    wr16(ea, (u16)xv_gh(v, (int)((ea >> 1) & 7u)));
}""", ["xv_gh"])
helper("xv_steb", """
static void xv_steb(u32 ea, xv v)
{
    wr8(ea, (u8)xv_gb(v, (int)(ea & 15u)));
}""", ["xv_gb"])
helper("xv_lvsl", """
static xv xv_lvsl(u32 ea)
{
    xv r;
    int k;
    u32 s = ea & 15u;
    for (k = 0; k < 4; k++)
        r.w[k] = ((s + 4u * (u32)k) << 24) | ((s + 4u * (u32)k + 1u) << 16) | ((s + 4u * (u32)k + 2u) << 8) | (s + 4u * (u32)k + 3u);
    return r;
}""")
helper("xv_lvsr", """
static xv xv_lvsr(u32 ea)
{
    xv r;
    int k;
    u32 s = 16u - (ea & 15u);
    for (k = 0; k < 4; k++)
        r.w[k] = ((s + 4u * (u32)k) << 24) | ((s + 4u * (u32)k + 1u) << 16) | ((s + 4u * (u32)k + 2u) << 8) | (s + 4u * (u32)k + 3u);
    return r;
}""")
helper("xv_vscr", """
static xv xv_vscr(u32 w)
{
    xv r = {0};
    r.w[3] = w;
    return r;
}""")


def helper_closure(names):
    need = set()
    work = [n for n in names if n in H]
    while work:
        n = work.pop()
        if n in need:
            continue
        need.add(n)
        work += DEP.get(n, [])
    return need


def helper_text(names):
    """The C of the vector helpers named (and what they use), each after its dependencies."""
    need = helper_closure(names)
    out, done = [], set()

    def put(n):
        if n in done:
            return
        done.add(n)
        for d in DEP.get(n, []):
            put(d)
        out.append(H[n])
    for n in sorted(need):
        put(n)
    return out


# ------------------------------------------------------------------------------------------------- lifting
# AltiVec and VMX128 names -> (helper, operand list) where operands are fields of the instruction
SAME = {
    "vaddfp128": "vaddfp", "vsubfp128": "vsubfp", "vand128": "vand", "vandc128": "vandc", "vor128": "vor",
    "vxor128": "vxor", "vnor128": "vnor", "vmaxfp128": "vmaxfp", "vminfp128": "vminfp", "vmrghw128": "vmrghw",
    "vmrglw128": "vmrglw", "vslw128": "vslw", "vsrw128": "vsrw", "vsraw128": "vsraw", "vrlw128": "vrlw",
    "vslo128": "vslo", "vsro128": "vsro", "vpkshss128": "vpkshss", "vpkshus128": "vpkshus",
    "vpkswss128": "vpkswss", "vpkswus128": "vpkswus", "vpkuhum128": "vpkuhum", "vpkuhus128": "vpkuhus",
    "vpkuwum128": "vpkuwum", "vpkuwus128": "vpkuwus", "vupkhsb128": "vupkhsb", "vupklsb128": "vupklsb",
    "vrefp128": "vrefp", "vrsqrtefp128": "vrsqrtefp", "vexptefp128": "vexptefp", "vlogefp128": "vlogefp",
    "vrfim128": "vrfim", "vrfin128": "vrfin", "vrfip128": "vrfip", "vrfiz128": "vrfiz",
    "vcsxwfp128": "vcfsx", "vcuxwfp128": "vcfux", "vcfpsxws128": "vctsxs", "vcfpuxws128": "vctuxs",
    "vspltw128": "vspltw", "vcmpeqfp128": "vcmpeqfp", "vcmpgefp128": "vcmpgefp", "vcmpgtfp128": "vcmpgtfp",
    "vcmpbfp128": "vcmpbfp", "vcmpequw128": "vcmpequw", "vsldoi128": "vsldoi", "vperm128": "vperm",
    "vmulfp128": "vmulfp", "vmsum3fp128": "vmsum3fp", "vmsum4fp128": "vmsum4fp",
}
UNARY = {"vrefp", "vrsqrtefp", "vexptefp", "vlogefp", "vrfin", "vrfiz", "vrfip", "vrfim", "vupkhsb", "vupklsb",
         "vupkhsh", "vupklsh"}
UIMM = {"vspltb", "vsplth", "vspltw", "vcfsx", "vcfux", "vctsxs", "vctuxs"}
COMPARES = {"vcmpeqfp", "vcmpgefp", "vcmpgtfp", "vcmpbfp", "vcmpequb", "vcmpequh", "vcmpequw", "vcmpgtub",
            "vcmpgtuh", "vcmpgtuw", "vcmpgtsb", "vcmpgtsh", "vcmpgtsw"}
VLOADS = {"lvx": "xv_ld", "lvxl": "xv_ld", "lvx128": "xv_ld", "lvxl128": "xv_ld", "lvlx": "xv_ldl",
          "lvlxl": "xv_ldl", "lvlx128": "xv_ldl", "lvlxl128": "xv_ldl", "lvrx": "xv_ldr", "lvrxl": "xv_ldr",
          "lvrx128": "xv_ldr", "lvrxl128": "xv_ldr", "lvewx": "xv_ldew", "lvewx128": "xv_ldew", "lvehx": "xv_ldeh",
          "lvebx": "xv_ldeb"}
VSTORES = {"stvx": "xv_st", "stvxl": "xv_st", "stvx128": "xv_st", "stvxl128": "xv_st", "stvlx": "xv_stl",
           "stvlxl": "xv_stl", "stvlx128": "xv_stl", "stvlxl128": "xv_stl", "stvrx": "xv_str", "stvrxl": "xv_str",
           "stvrx128": "xv_str", "stvrxl128": "xv_str", "stvewx": "xv_stew", "stvewx128": "xv_stew",
           "stvehx": "xv_steh", "stvebx": "xv_steb"}
ALIGN = {"xv_ld": 15, "xv_st": 15, "xv_ldew": 3, "xv_stew": 3, "xv_ldeh": 1, "xv_steh": 1}


def lift(ft, i):
    from .translate import RA0, R, TranslateError, lo32
    o, f = i.op, i.f
    note = ft.notes.append
    if o in ("dst", "dstst", "dss"):
        return []
    if o in VLOADS or o in VSTORES or o in ("lvsl", "lvsr", "lvsl128", "lvsr128"):
        ea = lo32(X.add(RA0(i.ra), R(i.rb)))
        if o.startswith("lvs"):
            fn = "xv_lvsl" if "lvsl" in o else "xv_lvsr"
            ft.vhelpers.add(fn)
            return [Assign(f"v{i.vd}", vcall(fn, [ea]))]
        fn = VLOADS.get(o) or VSTORES[o]
        ft.vhelpers.add(fn)
        if fn in ALIGN:
            ea = X.band(ea, u(~ALIGN[fn] & 0xFFFFFFFF))
        if o in VLOADS:
            if fn in ("xv_ldew", "xv_ldeh", "xv_ldeb"):
                note(f"{o} at {i.addr:08x}: the other elements are 0 (undefined on hardware)")
            return [Assign(f"v{i.vd}", Call(fn, [ea], XV, load=True))]
        return [Store(fn, ea, V(i.vs))]
    if o == "mfvscr":
        ft.vhelpers.add("xv_vscr")
        note(f"mfvscr at {i.addr:08x}: VSCR is a variable (SAT is not recorded)")
        return [Assign(f"v{i.vd}", vcall("xv_vscr", [Var("vscr", "u32")]))]
    if o == "mtvscr":
        return [Assign("vscr", Call("xv_w3", [V(i.vb)], "u32"))]
    if o in ("vupkd3d128", "vpkd3d128"):
        raise TranslateError(f"{o} at {i.addr:08x}: the Direct3D pack/unpack formats are not modelled")
    base = SAME.get(o, o)
    fn = "xv_" + base
    d = f"v{i.vd}"
    if o == "vmaddfp128":                          # vD = vA * vB + vD
        args, fn = [V(i.va), V(i.vb), V(i.vd)], "xv_vmaddfp"
    elif o == "vmaddcfp128":                       # vD = vA * vD + vB
        args, fn = [V(i.va), V(i.vd), V(i.vb)], "xv_vmaddfp"
    elif o == "vnmsubfp128":                       # vD = -(vA * vB - vD)
        args, fn = [V(i.va), V(i.vb), V(i.vd)], "xv_vnmsubfp"
    elif o in ("vmaddfp", "vnmsubfp"):             # vD = vA * vC + vB, -(vA * vC - vB)
        args = [V(i.va), V(i.vc), V(i.vb)]
    elif o == "vsel":                              # vD = (vA & ~vC) | (vB & vC)
        args = [V(i.va), V(i.vb), V(i.vc)]
    elif o == "vsel128":                           # the selector is vD
        args, fn = [V(i.va), V(i.vb), V(i.vd)], "xv_vsel"
    elif o in ("vperm", "vperm128"):
        args, fn = [V(i.va), V(i.vb), V(i.vc)], "xv_vperm"
    elif o in ("vsldoi", "vsldoi128"):
        args, fn = [V(i.va), V(i.vb), s(i.sh)], "xv_vsldoi"
    elif o == "vpermwi128":
        args, fn = [V(i.vb), s(i.perm)], "xv_vpermwi"
    elif o == "vrlimi128":
        args, fn = [V(i.vd), V(i.vb), s(i.imm), s(i.z)], "xv_vrlimi"
    elif o in ("vspltisb", "vspltish", "vspltisw", "vspltisw128"):
        k = i.simm
        w = {"b": (k & 0xFF) * 0x01010101, "h": (k & 0xFFFF) * 0x00010001}.get(o[7], k & 0xFFFFFFFF)
        args, fn = [u(w)], "xv_vspltis"
    elif base in UIMM:
        args = [V(i.vb), s(i.uimm)]
    elif base in UNARY:
        args = [V(i.vb)]
    else:
        if "va" not in f or "vb" not in f:
            raise TranslateError(f"unsupported vector instruction {o} at {i.addr:08x}")
        args = [V(i.va), V(i.vb)]
    if fn not in H:
        raise TranslateError(f"unsupported vector instruction {o} at {i.addr:08x}")
    ft.vhelpers.add(fn)
    if fn in ("xv_vrefp", "xv_vrsqrtefp", "xv_vexptefp", "xv_vlogefp"):
        note(f"{o} at {i.addr:08x}: an estimate, computed to single precision (the hardware's table is not modelled)")
    if fn in ("xv_vmsum3fp", "xv_vmsum4fp"):
        note(f"{o} at {i.addr:08x}: dot product rounded once (the hardware's rounding is unproven)")
    out = [Assign(d, vcall(fn, args))]
    if f.get("rc") and base in COMPARES:
        ft.vhelpers |= {"xv_all1", "xv_all0"}
        if base == "vcmpbfp":
            out += [Assign("c6lt", X.ZERO), Assign("c6gt", X.ZERO),
                    Assign("c6eq", Call("xv_all0", [V(i.vd)], "u32")), Assign("c6so", X.ZERO)]
        else:
            out += [Assign("c6lt", Call("xv_all1", [V(i.vd)], "u32")), Assign("c6gt", X.ZERO),
                    Assign("c6eq", Call("xv_all0", [V(i.vd)], "u32")), Assign("c6so", X.ZERO)]
    return out


helper("xv_w3", """
static u32 xv_w3(xv a)
{
    return a.w[3];
}""")

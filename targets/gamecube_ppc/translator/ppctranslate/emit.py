"""C text for a translated function: DecompPipeline's raw-C form (only `#include "tc_rt.h"`, the exported function
and static helpers, no globals), clean under MSVC /W4 /WX.

The blocks print in goto form (labels, `goto`, `if (c) goto`, a jump table as `{ u32 g_ = E; switch (g_) { case
V: goto L; ... default: break; } <tail call> }`); structure.py / cfgstruct.py then lay it out as loops and ifs.
"""
from __future__ import annotations

import math
import re

from . import expr as X
from .expr import render
from .ir import Assign, CallS, Comment, Goto, If, Ret, Store, Switch

CTYPE = {"flag": "u32", "bool": "u32"}
GOTO_TARGET = re.compile(r"\bgoto (L_\w+);")

HELPERS = {
    "px_fr": "static f64 px_fr(f64 p)\n{\n    f64 m = p < 0.0 ? -p : p;\n"
             "    if (m != 0.0 && m < 1.4012984643248171e-45) return p < 0.0 ? -0.0 : 0.0;\n    return (f64)(f32)p;\n}",
    "px_f2b": "static u64 px_f2b(f64 x)\n{\n    union { f64 d; u64 b; } v;\n    v.d = x;\n    return v.b;\n}",
    "px_b2f": "static f64 px_b2f(u64 x)\n{\n    union { f64 d; u64 b; } v;\n    v.b = x;\n    return v.d;\n}",
    "px_w2f": "static f32 px_w2f(u32 x)\n{\n    union { f32 f; u32 w; } v;\n    v.w = x;\n    return v.f;\n}",
    "px_fdiv": "static f64 px_fdiv(f64 a, f64 b)\n{\n    return a / b;\n}",
    # fctiw / fctiwz leave the 750CL's upper word: 0xfff80000, or 0xfff80001 for a negative operand giving 0
    "px_ctiw": "static f64 px_ctiw(f64 b, s32 w)\n{\n    u64 r = 0xfff8000000000000ull | (u32)w;\n"
               "    if (w == 0 && (px_f2b(b) >> 63) != 0) r |= 0x100000000ull;\n    return px_b2f(r);\n}",
    # 1/sqrt(x) by Newton's method from a bit-level first guess (the estimate instructions' table is not modelled)
    "px_rsqrt": "static f64 px_rsqrt(f64 x)\n{\n    f64 y;\n    int k;\n    if (x != x || x < 0.0) return px_b2f(0x7ff8000000000000ull);\n"
                "    if (x == 0.0) return px_b2f((px_f2b(x) & 0x8000000000000000ull) | 0x7ff0000000000000ull);\n"
                "    if (x > 1.7976931348623157e308) return 0.0;\n"
                "    y = px_b2f(0x5fe6eb50c7b537a9ull - (px_f2b(x) >> 1));\n"
                "    for (k = 0; k < 6; k++) y = y * (1.5 - 0.5 * x * y * y);\n    return y;\n}",
    # quantized store: scale, clamp to the type's range, truncate toward zero
    "px_quant": "static s32 px_quant(f64 v, f64 m, s32 lo, s32 hi)\n{\n    f64 x = v * m;\n"
                "    if (x != x) return 0;\n    if (x <= (f64)lo) return lo;\n    if (x >= (f64)hi) return hi;\n"
                "    return (s32)x;\n}",
    "px_qsize": "static u32 px_qsize(u32 t)\n{\n    return t == 4u || t == 6u ? 1u : t == 5u || t == 7u ? 2u : 4u;\n}",
    "px_qscale": "static f64 px_qscale(u32 sc)\n{\n    f64 m = 1.0;\n    s32 n = (s32)(sc & 0x3fu);\n    if (n >= 32) n -= 64;\n"
                 "    for (; n > 0; n--) m *= 2.0;\n    for (; n < 0; n++) m *= 0.5;\n    return m;\n}",
    # psq_l through a GQR known only at run time (type in bits 16..18, scale in 24..29)
    "px_dq": "static f64 px_dq(u32 g, u32 ea)\n{\n    u32 t = (g >> 16) & 7u;\n    f64 m = px_qscale(g >> 24);\n"
             "    if (t == 4u) return (f64)(f32)((f64)rd8(ea) / m);\n    if (t == 5u) return (f64)(f32)((f64)rd16(ea) / m);\n"
             "    if (t == 6u) return (f64)(f32)((f64)(s8)rd8(ea) / m);\n    if (t == 7u) return (f64)(f32)((f64)(s16)rd16(ea) / m);\n"
             "    return (f64)rdf32(ea);\n}",
    # psq_st through a GQR known only at run time (type in bits 0..2, scale in 8..13)
    "px_q": "static void px_q(u32 g, u32 ea, f64 v)\n{\n    u32 t = g & 7u;\n    f64 m = px_qscale(g >> 8);\n"
            "    if (t == 4u) wr8(ea, (u8)px_quant(v, m, 0, 255));\n    else if (t == 5u) wr16(ea, (u16)px_quant(v, m, 0, 65535));\n"
            "    else if (t == 6u) wr8(ea, (u8)px_quant(v, m, -128, 127));\n"
            "    else if (t == 7u) wr16(ea, (u16)px_quant(v, m, -32768, 32767));\n    else wrf32d(ea, v);\n}",
}
# the dense form (Emitter.dense): a call with only r3.. arguments, and a chain of loads at fixed offsets
for _k in range(4):
    _ps = "".join(f", u32 x{j}" for j in range(_k))
    _set = "".join(f"\n    a.r[{j}] = x{j};" for j in range(_k))
    for _n, _t, _f in (("px_c", "u32", "tc_call"), ("px_cf", "f64", "tc_callf")):
        HELPERS[f"{_n}{_k}"] = f"static {_t} {_n}{_k}(u32 t{_ps})\n{{\n    TcArgs a = {{0}};{_set}\n    return {_f}(t, &a);\n}}"
HELPERS["px_l2"] = "static u32 px_l2(u32 p, u32 a, u32 b)\n{\n    return rd32(rd32(p + a) + b);\n}"
HELPERS["px_l3"] = "static u32 px_l3(u32 p, u32 a, u32 b, u32 c)\n{\n    return rd32(rd32(rd32(p + a) + b) + c);\n}"
DEPS = {"px_ctiw": ["px_f2b", "px_b2f"], "px_rsqrt": ["px_f2b", "px_b2f"], "px_dq": ["px_qscale"],
        "px_q": ["px_qscale", "px_quant"]}
ORDER = ["px_fr", "px_f2b", "px_b2f", "px_w2f", "px_fdiv", "px_ctiw", "px_rsqrt", "px_quant", "px_qsize", "px_qscale", "px_dq",
         "px_q", "px_l2", "px_l3"] + [f"px_c{k}" for k in range(4)] + [f"px_cf{k}" for k in range(4)]


def coerce(e, ty):
    """e as a value for a variable/field of type ty, with an explicit cast where C would narrow or convert."""
    ty = CTYPE.get(ty, ty)
    et = CTYPE.get(e.ty, e.ty)
    if et == ty:
        return e
    if (ty in X.FLOAT) != (et in X.FLOAT):
        return X.cast(ty, e)
    if ty in X.FLOAT:
        return e if ty == "f64" else X.cast(ty, e)
    if X.INT_BITS[et] > X.INT_BITS[ty]:
        return X.cast(ty, e)
    if (et in X.SIGNED) != (ty in X.SIGNED) and X.INT_BITS[et] == X.INT_BITS[ty]:
        return e        # same width: C converts without a warning
    return e


def zero_const(e):
    """A literal 0 (u32) or +0.0 (f64): what a zeroed TcArgs field already holds."""
    if not isinstance(e, X.Const):
        return False
    if e.ty in ("f64", "f32"):
        return isinstance(e.v, float) and e.v == 0.0 and math.copysign(1.0, e.v) > 0
    return e.v == 0


class Emitter:
    def __init__(self, ft, fn, lean=False, dense=False):
        self.ft, self.fn = ft, fn
        self.lean = lean or dense   # calls start from a zeroed TcArgs (za) and set only their nonzero fields
        self.dense = dense          # and a call with only r3.. arguments (up to 3) goes through px_c / px_cf

    def r(self, e):
        return render(e)

    def stmt(self, s, out, ind="    "):
        if isinstance(s, Comment):
            out.append(f"{ind}/* {s.text} */")
        elif isinstance(s, Assign):
            out.append(f"{ind}{s.dst} = {self.r(coerce(s.e, self.fn.ty(s.dst)))};")
        elif isinstance(s, Store):
            if s.fn == "px_q":
                out.append(f"{ind}px_q({self.r(s.g)}, {self.r(s.addr)}, {self.r(coerce(s.val, 'f64'))});")
            else:
                vt = {"wr8": "u8", "wr16": "u16", "wr32": "u32", "wr64": "u64", "wrf32d": "f64", "wrf64": "f64"}[s.fn]
                out.append(f"{ind}{s.fn}({self.r(s.addr)}, {self.r(coerce(s.val, vt))});")
        elif isinstance(s, CallS) and s.selfc:
            kinds = {n: k for n, k, _, _ in self.ft.sig.params}
            vals = {k: e for _, k, e in s.args}
            c = "px_rec(" + ", ".join([self.r(vals["sp_"])] + [self.r(coerce(vals[n], kinds[n])) for n in kinds]) + ")"
            out.append(f"{ind}{c};" if s.note == "dead" else f"{ind}f1 = {c};" if s.kind == "f" else f"{ind}r3 = {c};")
        elif isinstance(s, CallS):
            args = [(fld, k, e) for fld, k, e in s.args if not zero_const(e)]
            if self.dense and len(args) <= 3 and [(fld, k) for fld, k, _ in args] == [("r", j) for j in range(len(args))]:
                c = (f"px_cf{len(args)}" if s.kind == "f" else f"px_c{len(args)}") + "(" + ", ".join(
                    [self.r(coerce(s.target, "u32"))] + [self.r(coerce(e, "u32")) for _, _, e in args]) + ")"
                out.append(f"{ind}{c};" if s.note == "dead" else f"{ind}f1 = {c};" if s.kind == "f" else f"{ind}r3 = {c};")
                if s.note == "both":
                    out.append(f"{ind}f1 = tc_lastf();" if s.kind == "r" else f"{ind}r3 = tc_lastr();")
                return
            if self.lean:
                out.append(f"{ind}a = za;")
            for fld, k, e in s.args:
                if self.lean and zero_const(e):
                    continue
                out.append(f"{ind}a.{fld}[{k}] = {self.r(coerce(e, 'f64' if fld == 'f' else 'u32'))};")
            t = self.r(coerce(s.target, "u32"))
            if s.note == "dead":
                out.append(f"{ind}(void)tc_call({t}, &a);")
            elif s.kind == "f":
                out.append(f"{ind}f1 = tc_callf({t}, &a);")
            else:
                out.append(f"{ind}r3 = tc_call({t}, &a);")
            if s.note == "both":
                out.append(f"{ind}f1 = tc_lastf();" if s.kind == "r" else f"{ind}r3 = tc_lastr();")
        else:
            raise TypeError(s)

    def body(self):
        out = []
        blocks = self.fn.blocks
        refs = set()
        for b in blocks:
            refs |= set(b.term.succs())
        for n, b in enumerate(blocks):
            nxt = blocks[n + 1].label if n + 1 < len(blocks) else None
            if b.label in refs:
                out.append(f"{b.label}:;")
            for s in b.stmts:
                self.stmt(s, out)
            t = b.term
            if isinstance(t, Goto):
                if t.label != nxt:
                    out.append(f"    goto {t.label};")
            elif isinstance(t, If):
                c = t.cond
                if t.yes == nxt:
                    out.append(f"    if ({self.r(X.lnot(c))}) goto {t.no};")
                else:
                    out.append(f"    if ({self.r(c)}) goto {t.yes};")
                    if t.no != nxt:
                        out.append(f"    goto {t.no};")
            elif isinstance(t, Switch):
                out.append(f"    {{ u32 g_ = {self.r(coerce(t.e, 'u32'))};")
                out.append("    switch (g_) {")
                for v, lab in t.cases:
                    out.append(f"    case {X.lit(v, 'u32') if v >= 10 else f'0x{v:x}u'}: goto {lab};")
                out.append("    default: break;")
                out.append("    }")
                for s in t.default:
                    if isinstance(s, Ret):
                        out.append(f"    {self.ret(s)}")
                    else:
                        self.stmt(s, out)
                out.append("    }")
            elif isinstance(t, Ret):
                out.append(f"    {self.ret(t)}")
        named = {m for x in out for m in GOTO_TARGET.findall(x)}
        return [x for x in out if not (x.endswith(":;") and x[:-2] not in named)]

    def ret(self, t):
        if t.e is None:
            return "return;"
        return f"return {self.r(coerce(t.e, self.ft.sig.ret))};"


DECL_TYPES = ("u32", "u64", "s32", "f64", "f32")


def declarations(names, fn):
    by = {}
    for n in sorted(names, key=natural):
        t = CTYPE.get(fn.ty(n), fn.ty(n))
        by.setdefault(t, []).append(n)
    out = []
    for t in sorted(by, key=lambda t: DECL_TYPES.index(t) if t in DECL_TYPES else 99):
        z = "0.0" if t in X.FLOAT else "0"
        names_ = by[t]
        for k in range(0, len(names_), 12):
            out.append(f"    {t} " + ", ".join(f"{n} = {z}" for n in names_[k:k + 12]) + ";")
    return out


def natural(n):
    m = re.match(r"([a-z_]+)(\d*)(.*)", n)
    return (m.group(1), int(m.group(2)) if m.group(2) else -1, m.group(3))


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


_OFF = r"(0x[0-9a-f]+u|\d+u)"
_L3 = re.compile(r"rd32\(rd32\(rd32\((\w+) \+ " + _OFF + r"\) \+ " + _OFF + r"\) \+ " + _OFF + r"\)")
_L2 = re.compile(r"rd32\(rd32\((\w+) \+ " + _OFF + r"\) \+ " + _OFF + r"\)")
_UPARAM = re.compile(r"\(u32\)(param_\w+)(?![A-Za-z0-9_])")


def densify(body, params):
    """The dense form's text: each (u32)param_N read through one u32 copy made at entry (a parameter is never
    assigned), and loads through fixed-offset rd32 chains as px_l3 / px_l2. The same reads, in the same order."""
    if any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(p)} = ", x) for x in body for p in params):
        return body, []
    ups = sorted({m.group(1) for x in body for m in _UPARAM.finditer(x)} & set(params))
    out = []
    for x in body:
        x = _UPARAM.sub(lambda m: "u" + m.group(1) if m.group(1) in params else m.group(0), x)
        x = _L3.sub(r"px_l3(\1, \2, \3, \4)", x)
        x = _L2.sub(r"px_l2(\1, \2, \3)", x)
        out.append(x)
    return out, [f"    u32 u{p} = (u32){p};" for p in ups]


def function_text(ft, fn, structured=True, lean=False, dense=False):
    """lean: the form for functions near DP's 60 KB cap (Emitter.lean); the same values reach every call.
    dense: lean plus Emitter.dense and densify, for functions still over it."""
    from . import structure
    em = Emitter(ft, fn, lean, dense)
    body = em.body()
    if structured:
        structure.CURRENT = f"{ft.entry:08x}"
        structure.FLOATS = {n for n, t in fn.types.items() if t in X.FLOAT} | {
            n for n, k, _, _ in ft.sig.params if k in X.FLOAT}
        body = structure.structure(body)
    ucopies = []
    if dense:
        body, ucopies = densify(body, {p[0] for p in ft.sig.params})
    code = "\n".join(x for x in ucopies + body if not x.strip().startswith("/*"))
    named = set(IDENT.findall(code))
    params = {p[0] for p in ft.sig.params}
    local = [n for n in named if n in fn.types and n not in params and n not in ("g_", "sp_")]
    for extra in ("ct_", "sw_", "fl_", "jt_"):
        if extra in named and extra not in local:
            local.append(extra)
            fn.types.setdefault(extra, "u32")
    sig = ft.sig
    if sig.generic:
        used = [p for p in sig.params if p[0] in named]
        ptxt = ", ".join(f"{k} {n}" for n, k, _, _ in used) or "void"
        sig.params = used
        sig.text = f"{sig.ret} {sig.name}({ptxt})"
    out = ["#include \"tc_rt.h\"", ""]
    need = {h for h in HELPERS if h in named}
    for h in list(need):
        need |= set(DEPS.get(h, []))
    for h in ORDER:
        if h in need:
            out += [HELPERS[h], ""]
    if ft.recursive:
        ps = sig.text[sig.text.index("(") + 1:sig.text.rindex(")")].strip()
        rt = "void" if sig.ret == "void" else "f64" if sig.ret in ("f32", "f64") else "u32"
        out.append(f"static {rt} px_rec(u32 sp_" + ("" if ps in ("", "void") else ", " + ps) + ")")
    else:
        out.append(sig.text)
    out.append("{")
    out += declarations(local, fn)
    out += ucopies
    if "tc_call" in named or "tc_callf" in named:
        out.append("    TcArgs a = {0};")
        if lean or dense:
            out.append("    TcArgs za = {0};")
    for n, _, _, _ in sig.params:
        if n not in named:
            out.append(f"    (void){n};")
    out += body
    out.append("}")
    if ft.recursive:
        call = "px_rec(" + ", ".join([f"0x{ft.facts.entry_sp:x}u"] + [p[0] for p in sig.params]) + ")"
        conv = {"void": "", "u32": "return ", "f64": "return ", "s32": "return (s32)", "f32": "return (f32)"}
        out += ["", sig.text, "{", f"    {conv[sig.ret]}{call};", "}"]
    return "\n".join(out) + "\n"


def compact(src):
    """The same C without optional whitespace, several statements to a line (sh2-translate's form for functions
    near DP's 60 KB cap). A space stays only between two identifier/number characters or two operator characters,
    so no token changes; the output has no string literals."""
    def idc(c):
        return c.isalnum() or c == "_"
    ops = set("+-*/%&|^<>=!~")
    out = []
    for ln in src.splitlines():
        ln = ln.strip()
        if ln.startswith("#"):
            out.append(ln)
            continue
        r = []
        for k, ch in enumerate(ln):
            if ch == " " and r and k + 1 < len(ln):
                left, right = r[-1], ln[k + 1]
                if not (idc(left) and idc(right)) and not (left in ops and right in ops):
                    continue
            r.append(ch)
        out.append("".join(r))
    joined, cur = [], ""
    for x in out:
        if x.startswith("#") or not x:
            if cur:
                joined.append(cur)
            joined.append(x)
            cur = ""
        elif cur and len(cur) + len(x) <= 160:
            cur += (" " if idc(cur[-1]) and idc(x[0]) else "") + x
        else:
            if cur:
                joined.append(cur)
            cur = x
    if cur:
        joined.append(cur)
    return "\n".join(joined) + "\n"

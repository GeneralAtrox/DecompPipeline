"""C text for a translated function: DecompPipeline's raw-C form (only `#include "tc_rt.h"`, the exported function
and static helpers, no globals), clean under MSVC /W4 /WX.

The blocks print in goto form (labels, `goto`, `if (c) goto`, a jump table as `{ u32 g_ = E; switch (g_) { case
V: goto L; ... default: break; } <tail call> }`); structure.py / cfgstruct.py then lay it out as loops and ifs.
"""
from __future__ import annotations

import re

from . import expr as X
from . import vmx
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
    # the 8 stack-argument doublewords of a call, at r1 + 0x50
    "px_sargs": "static void px_sargs(TcArgs *a, u32 ea)\n{\n    int k;\n"
                "    for (k = 0; k < 8; k++) a->s[k] = rd64(ea + 8u * (u32)k);\n}",
    # a whole call's TcArgs in one statement (the short form, for functions near DP's 60 KB cap): r3..r10, f1..f13,
    # then the 8 stack doublewords at ea
    "px_a": ("static void px_a(TcArgs *a, " + ", ".join(f"u64 r{k}" for k in range(3, 11)) + ", "
             + ", ".join(f"f64 f{k}" for k in range(1, 14)) + ", u32 ea)\n{\n    int k;\n"
             + "".join(f"    a->r[{k - 3}] = r{k};\n" for k in range(3, 11))
             + "".join(f"    a->f[{k - 1}] = f{k};\n" for k in range(1, 14))
             + "    for (k = 0; k < 8; k++) a->s[k] = rd64(ea + 8u * (u32)k);\n}"),
    # the same with the 8 stack doublewords given as values
    "px_b": ("static void px_b(TcArgs *a, " + ", ".join(f"u64 r{k}" for k in range(3, 11)) + ", "
             + ", ".join(f"f64 f{k}" for k in range(1, 14)) + ", "
             + ", ".join(f"u64 s{k}" for k in range(8)) + ")\n{\n"
             + "".join(f"    a->r[{k - 3}] = r{k};\n" for k in range(3, 11))
             + "".join(f"    a->f[{k - 1}] = f{k};\n" for k in range(1, 14))
             + "".join(f"    a->s[{k}] = s{k};\n" for k in range(8)) + "}"),
    "px_fdiv": "static f64 px_fdiv(f64 a, f64 b)\n{\n    return a / b;\n}",
    # a / +-0 (sb: the divisor's sign bit): a NaN stays, 0 / 0 is NaN, anything else infinity of the xored sign
    "px_fdz": "static f64 px_fdz(f64 a, u64 sb)\n{\n    u64 x = px_f2b(a);\n    if (a != a) return a;\n"
              "    if ((x << 1) == 0) return px_b2f(0x7ff8000000000000ull);\n"
              "    return px_b2f(((x ^ sb) & 0x8000000000000000ull) | 0x7ff0000000000000ull);\n}",
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
DEPS = {"px_fdz": ["px_f2b", "px_b2f"], "px_ctiw": ["px_f2b", "px_b2f"], "px_rsqrt": ["px_f2b", "px_b2f"], "px_dq": ["px_qscale"],
        "px_q": ["px_qscale", "px_quant"]}
ORDER = ["px_sargs", "px_a", "px_b", "px_fr", "px_f2b", "px_b2f", "px_w2f", "px_fdiv", "px_fdz", "px_ctiw", "px_rsqrt", "px_quant", "px_qsize", "px_qscale", "px_dq",
         "px_q"]
FULL_ORDER = [("r", k) for k in range(8)] + [("f", k) for k in range(13)]  # px_a's parameters
FULL_ARGS = sorted(FULL_ORDER)
S_ORDER = [("s", k) for k in range(8)]  # px_b's last 8


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


class Emitter:
    def __init__(self, ft, fn, short=False):
        self.ft, self.fn = ft, fn
        self.avals = {}
        self.short = short  # a call passing every field fills its TcArgs with one px_a

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
                vt = STORE_TYPES[s.fn]
                out.append(f"{ind}{s.fn}({self.r(coerce(s.addr, 'u32'))}, {self.r(coerce(s.val, vt))});")
        elif isinstance(s, CallS):
            # tc_call reads the TcArgs and never changes it, so a field already holding the same value in this
            # block is not written again (self.avals: field -> (text, variables it reads, whether it reads memory))
            sargs = self.stack_args(s.args)
            lines, new = [], {}
            for fld, k, e in s.args:
                if sargs is not None and fld == "s":
                    continue
                txt = self.r(coerce(e, 'f64' if fld == 'f' else 'u64'))
                new[(fld, k)] = (txt, set(IDENT.findall(txt)), e.has_load())
                if self.avals.get((fld, k), (None,))[0] != txt:
                    lines.append(f"{ind}a.{fld}[{k}] = {txt};")
            if sargs is not None:
                txt = self.r(coerce(sargs, "u32"))
                new[("S", 0)] = (txt, set(IDENT.findall(txt)), True)
                if self.avals.get(("S", 0), (None,))[0] != txt:
                    lines.append(f"{ind}px_sargs(&a, {txt});")
            if self.short:
                keys = sorted(k for k in new if k[0] in "rf")
                pa = None
                if keys == FULL_ARGS and ("S", 0) in new:
                    pa = f"{ind}px_a(&a, {', '.join(new[k][0] for k in FULL_ORDER)}, {new[('S', 0)][0]});"
                elif keys == FULL_ARGS and all(("s", k) in new for k in range(8)):
                    pa = f"{ind}px_b(&a, {', '.join(new[k][0] for k in FULL_ORDER + S_ORDER)});"
                if pa and len(pa.replace(' ', '')) < sum(len(x.replace(' ', '')) for x in lines):
                    lines = [pa]
            out += lines
            self.avals.update(new)
            if ("S", 0) in new:     # px_sargs wrote all 8 stack fields: a field's own remembered value is gone
                for k in range(8):
                    self.avals.pop(("s", k), None)
            elif any(k[0] == "s" for k in new):
                self.avals.pop(("S", 0), None)
            t = self.r(coerce(s.target, "u32"))
            if s.note == "dead":
                out.append(f"{ind}(void)tc_call({t}, &a);")
            elif s.kind == "f":
                out.append(f"{ind}f1 = tc_callf({t}, &a);")
            else:
                out.append(f"{ind}r3 = tc_call({t}, &a);")
        else:
            raise TypeError(s)
        self.forget(s)

    def forget(self, s):
        """Drop the remembered TcArgs fields that s may have changed: those reading a variable s writes, and after a
        store or a call (a callee may write memory) those reading memory."""
        if not self.avals:
            return
        w = set(s.writes()) if hasattr(s, "writes") else set()
        mem = isinstance(s, (Store, CallS))
        for key in [k for k, (_, vs, ld) in self.avals.items() if (vs & w) or (mem and ld)]:
            del self.avals[key]

    @staticmethod
    def stack_args(args):
        """The address of s0 when the call's 8 stack arguments are rd64 of 8 consecutive doublewords, else None."""
        s = [e for fld, _, e in args if fld == "s"]
        if len(s) != 8 or not all(isinstance(e, X.Call) and e.fn == "rd64" for e in s):
            return None
        ads = [e.args[0] for e in s]
        if all(isinstance(x, X.Const) for x in ads):
            return ads[0] if all(ads[k].v == ads[0].v + 8 * k for k in range(8)) else None
        return None

    def body(self):
        out = []
        blocks = self.fn.blocks
        refs = set()
        for b in blocks:
            refs |= set(b.term.succs())
        for n, b in enumerate(blocks):
            nxt = blocks[n + 1].label if n + 1 < len(blocks) else None
            self.avals = {}
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


DECL_TYPES = ("u32", "u64", "s32", "f64", "f32", "xv")
STORE_TYPES = {"wr8": "u8", "wr16": "u16", "wr32": "u32", "wr64": "u64", "wrf32d": "f64", "wrf64": "f64",
               "wr16r": "u16", "wr32r": "u32", "xv_st": "xv", "xv_stl": "xv", "xv_str": "xv", "xv_stew": "xv",
               "xv_steh": "xv", "xv_steb": "xv"}


def declarations(names, fn):
    by = {}
    for n in sorted(names, key=natural):
        t = CTYPE.get(fn.ty(n), fn.ty(n))
        by.setdefault(t, []).append(n)
    out = []
    for t in sorted(by, key=lambda t: DECL_TYPES.index(t) if t in DECL_TYPES else 99):
        z = "0.0" if t in X.FLOAT else "{0}" if t == "xv" else "0"
        names_ = by[t]
        for k in range(0, len(names_), 12):
            out.append(f"    {t} " + ", ".join(f"{n} = {z}" for n in names_[k:k + 12]) + ";")
    return out


def natural(n):
    m = re.match(r"([a-z_]+)(\d*)(.*)", n)
    return (m.group(1), int(m.group(2)) if m.group(2) else -1, m.group(3))


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
EXIT = re.compile(r"\bbreak;|\bgoto ")


def drop_after_endless(body):
    """Statements after a `for (;;)` loop that has no break or goto, up to the end of the block holding the loop,
    when no label is among them: nothing reaches them (the loop leaves only by return, or the judge model makes it
    endless, e.g. a counter kept in a register a mocked callee clears), and MSVC /W4 rejects unreachable code
    (C4702)."""
    n = 0
    while n < len(body):
        ln = body[n]
        ind = len(ln) - len(ln.lstrip(" "))
        if ln.strip() != "for (;;) {":
            n += 1
            continue
        close = " " * ind + "}"
        end = next((m for m in range(n + 1, len(body)) if body[m] == close), None)
        if end is None:
            return body
        stop = end + 1
        while stop < len(body) and (len(body[stop]) - len(body[stop].lstrip(" ")) >= ind or not body[stop].strip()):
            stop += 1
        if stop > end + 1 and not any(EXIT.search(x) for x in body[n + 1:end]) and \
                not any(x.endswith(":;") for x in body[end + 1:stop]):
            body = body[:end + 1] + body[stop:]
        n += 1
    return body


def function_text(ft, fn, structured=True, short=False):
    from . import structure
    em = Emitter(ft, fn, short)
    body = em.body()
    if structured:
        structure.CURRENT = f"{ft.entry:08x}"
        body = structure.structure(body)
    body = drop_after_endless(body)
    code = "\n".join(x for x in body if not x.strip().startswith("/*"))
    named = set(IDENT.findall(code))
    params = {p[0] for p in ft.sig.params}
    local = [n for n in named if n in fn.types and n not in params and n != "g_"]
    for extra in ("ct_", "sw_", "fl_", "jt_"):
        if extra in named and extra not in local:  # (a second text of the same function has it already)
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
    vh = vmx.helper_text([n for n in named if n.startswith("xv_")])
    if vh or "xv" in named:
        out += [vmx.TYPEDEF, ""]
        for h in vh:
            out += [h, ""]
    out.append(sig.text)
    out.append("{")
    out += declarations(local, fn)
    if "tc_call" in named or "tc_callf" in named:
        out.append("    TcArgs a = {0};")
    for n, _, _, _ in sig.params:
        if n not in named:
            out.append(f"    (void){n};")
    out += body
    out.append("}")
    return "\n".join(out) + "\n"


LV = r"(?:[A-Za-z_]\w*|a\.[rfs]\[\d+\])"  # a local or a TcArgs field
ZERO = re.compile(rf"((?:{LV}=)*{LV})=(0ull|0\.0|0u);")


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
        x = "".join(r)
        m = ZERO.fullmatch(x)
        p = ZERO.fullmatch(out[-1]) if out and m else None
        if p and p.group(2) == m.group(2):     # r4=0ull; r5=0ull;  ->  r4=r5=0ull;
            out[-1] = f"{p.group(1)}={m.group(1)}={m.group(2)};"
        elif m and not (out and out[-1].endswith((";", "{", "}"))):
            out.append(x + " ")  # not after a complete statement (a brace-less if's body): the space keeps it alone
        else:
            out.append(x)
    out = [x.rstrip() for x in out]
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

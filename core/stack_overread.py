"""Readable C that hands the port host stack garbage: a local read past its end, or copied out half written (r15).

The judge cannot see either fault. Callees are mocked, so a callee's read of its caller's local never runs, and a
host local's unwritten bytes are whatever the host stack held. Phase D found both in TC, and both made the port
nondeterministic:
- 8c017382 Projectile_Sweep passed &hit_pos, a lone Vec3, down to Explosion_HitEntity, which reads `center + 1`
  (the original keeps q.dir there);
- 8c06fc14 Lib_PoseMixerInit copied a LibAnimPoseBuf whole into guest RAM after Anim_ReadFrame had filled only
  the motion's joints.

The static check runs on each readable file, preprocessed by cl /EP (stub stdint/stdarg, so pycparser parses it)
against the library's game.h:
- R1 overread: a local's address (at a known offset) reaches an access, or a callee chain, that goes past the
  local's end. A function's extent through a pointer parameter is the furthest byte it, or any callee it passes
  the pointer on to, accesses at a constant offset (a fixpoint over the call graph). A copy helper (memcpy-like
  names, or a body-less function whose prototype names a size/len/count/bytes parameter) reaches its size
  argument when that is a constant.
- R2 copied out half written: a struct or array local without an initializer, copied out whole or in part (a
  copy helper's source, a struct assignment, a by-value argument, a return) while some of the copied bytes are
  not written on every path before the copy. Field writes, whole assignments, zeroing helpers and the callees'
  definite writes count (a callee's writes through a parameter that every path to its end makes outside loops).
  Padding bytes are not required.

It is conservative where it cannot follow: an unknown offset, a callee without a body that is not a copy helper,
a function pointer, or a pointer stored away all count as "no overread" and "fully written". So a hit is a fault
or a pattern the readable agent must make explicit (zero the local, or give it the original's layout).
"""
import collections
import concurrent.futures
import pathlib
import re
import subprocess

from pycparser import c_ast, c_parser

MARK = "__overread_file_start__"
SIZE_NAMES = re.compile(r"^(size|len|length|n|count|bytes|nbytes|num_bytes|byte_count|size_bytes)$", re.I)
COPY_NAMES = {"memcpy": ("dst", "src", "size"), "memmove": ("dst", "src", "size"), "memset": ("dst", None, "size")}
FAKE_STD = {
    "stdint.h": "typedef signed char int8_t; typedef unsigned char uint8_t; typedef short int16_t;\n"
                "typedef unsigned short uint16_t; typedef int int32_t; typedef unsigned int uint32_t;\n"
                "typedef long long int64_t; typedef unsigned long long uint64_t;\n"
                "typedef int intptr_t; typedef unsigned int uintptr_t;\n",
    "stdarg.h": "typedef char *va_list;\n#define va_start(ap, v) ((void)0)\n#define va_arg(ap, t) (*(t *)(ap))\n"
                "#define va_end(ap) ((void)0)\n",
    "stddef.h": "typedef unsigned int size_t; typedef int ptrdiff_t;\n#define NULL ((void *)0)\n"
                "#define offsetof(t, m) ((size_t)&((t *)0)->m)\n",
    "string.h": "typedef unsigned int size_t;\nvoid *memcpy(void *dst, const void *src, size_t size);\n"
                "void *memmove(void *dst, const void *src, size_t size);\nvoid *memset(void *dst, int c, size_t size);\n",
}
TOP = None  # an unknown extent / offset


# ---------------------------------------------------------------------------------------------- types (MSVC x86)

class T:
    """kind: int, float, void, ptr, arr, rec (struct/union), func. size/align in bytes (rec: laid out on demand)."""
    __slots__ = ("kind", "size", "align", "to", "n", "fields", "union", "name", "ret", "params")

    def __init__(self, kind, size=0, align=1, to=None, n=None, fields=None, union=False, name=None, ret=None,
                 params=None):
        self.kind, self.size, self.align, self.to, self.n = kind, size, align, to, n
        self.fields, self.union, self.name, self.ret, self.params = fields, union, name, ret, params


INT = T("int", 4, 4)
VOID = T("void", 0, 1)
BASE = {"char": 1, "short": 2, "int": 4, "long": 4, "float": 4, "double": 8, "_Bool": 1, "__int64": 8}


class Types:
    """typedefs, records and enum constants of one translation unit's file-level declarations."""

    def __init__(self, ext, parent=None):
        """parent: the shared game.h Types; this unit's own declarations shadow it (ChainMaps, no copies)."""
        self.parent, self.typer = parent, None
        if parent is None:
            self.typedefs, self.recs, self.enums, self.decls, self.layouts = {}, {}, {}, {}, {}
        else:
            self.typedefs, self.recs, self.enums, self.decls, self.layouts = (
                collections.ChainMap({}, parent.typedefs), collections.ChainMap({}, parent.recs),
                collections.ChainMap({}, parent.enums), collections.ChainMap({}, parent.decls),
                collections.ChainMap({}, parent.layouts))
        for n in ext:
            if isinstance(n, c_ast.Typedef):
                self.typedefs[n.name] = n.type
            elif isinstance(n, c_ast.Decl):
                if n.name:
                    self.decls[n.name] = n.type
            self._collect(n)

    def _collect(self, node):
        for sub in node:
            if isinstance(sub, (c_ast.Struct, c_ast.Union)) and sub.name and sub.decls is not None:
                own = self.recs if self.parent is None else self.recs.maps[0]
                own.setdefault((type(sub).__name__, sub.name), sub)
            if isinstance(sub, c_ast.Enum) and sub.values is not None:
                v = -1
                for e in sub.values.enumerators:
                    v = self.const(e.value) if e.value is not None else v + 1
                    v = 0 if v is None else v
                    self.enums[e.name] = v
            self._collect(sub)

    def resolve(self, t):
        if isinstance(t, c_ast.Typename) or isinstance(t, c_ast.Decl):
            return self.resolve(t.type)
        if isinstance(t, c_ast.TypeDecl):
            return self.resolve(t.type)
        if isinstance(t, c_ast.PtrDecl):
            return T("ptr", 4, 4, to=_Lazy(self, t.type))
        if isinstance(t, c_ast.ArrayDecl):
            el = self.resolve(t.type)
            n = self.const(t.dim) if t.dim is not None else None
            n = n if isinstance(n, int) and n >= 0 else None
            return T("arr", el.size * (n or 0), el.align, to=el, n=n)
        if isinstance(t, c_ast.FuncDecl):
            ps = []
            for p in (t.args.params if t.args else []):
                if isinstance(p, c_ast.EllipsisParam):
                    continue
                pt = self.resolve(p.type) if not isinstance(p, c_ast.ID) else INT
                if pt.kind == "void":
                    continue
                ps.append(((getattr(p, "name", None) or ""), T("ptr", 4, 4, to=pt.to) if pt.kind == "arr" else pt))
            return T("func", 0, 1, ret=_Lazy(self, t.type), params=ps)
        if isinstance(t, c_ast.IdentifierType):
            names = [x for x in t.names if x not in ("signed", "unsigned", "const", "volatile")]
            if not names:
                return INT
            if len(names) == 1 and names[0] in self.typedefs:
                return self.resolve(self.typedefs[names[0]])
            if names[0] == "void":
                return VOID
            if names.count("long") == 2:
                return T("int", 8, 8)
            size = max(BASE.get(x, 4) for x in names)
            return T("float" if names[-1] in ("float", "double") else "int", size, size)
        if isinstance(t, (c_ast.Struct, c_ast.Union)):
            return self.record(t)
        if isinstance(t, c_ast.Enum):
            return INT
        return INT

    def record(self, node):
        kind = type(node).__name__
        if node.decls is None and node.name:
            node = self.recs.get((kind, node.name), node)
        key = (kind, node.name) if node.name else id(node)
        if key in self.layouts:
            return self.layouts[key]
        rec = T("rec", 0, 1, fields=[], union=kind == "Union", name=node.name)
        shared = self.parent is not None and node.name and self.parent.recs.get(key) is node
        (self.parent.layouts if shared else self.layouts)[key] = rec  # (a self-reference sees it being laid out)
        off, size, align, bits = 0, 0, 1, None
        for d in node.decls or []:
            ft = self.resolve(d.type)
            if d.bitsize is not None:  # MSVC: a run of bitfields of one size shares a unit
                if bits is None or bits[0] != ft.size or rec.union:
                    off = _round(off, ft.align) if not rec.union else 0
                    bits = (ft.size, off)
                    off += ft.size
                if d.name:
                    rec.fields.append((d.name, bits[1], ft))
                size, align = max(size, off), max(align, ft.align)
                continue
            bits = None
            a = ft.align
            at = 0 if rec.union else _round(off, a)
            if d.name:
                rec.fields.append((d.name, at, ft))
            elif ft.kind == "rec":  # an anonymous member: its fields are the parent's
                rec.fields += [(n, at + o, t) for n, o, t in ft.fields]
            off = at + ft.size
            size, align = max(size, off), max(align, a)
        rec.size, rec.align = _round(size, align), align
        return rec

    def field(self, rec, name):
        for n, o, t in (rec.fields or []) if rec.kind == "rec" else []:
            if n == name:
                return o, t
        return None, INT

    def const(self, e):
        """An integer constant expression's value, else None."""
        try:
            return self._const(e)
        except (ArithmeticError, ValueError, TypeError, RecursionError):
            return None

    def _const(self, e):
        if isinstance(e, c_ast.Constant):
            if e.type in ("int", "unsigned int", "long int", "unsigned long int", "long long int",
                          "unsigned long long int") or e.type.endswith("int"):
                s = e.value.rstrip("uUlL")
                return int(s, 16) if s.lower().startswith("0x") else int(s, 8) if len(s) > 1 and s[0] == "0" \
                    else int(s)
            if e.type == "char":
                s = e.value[1:-1]
                return ord(s) if len(s) == 1 else {"\\0": 0, "\\n": 10, "\\t": 9}.get(s)
            return None
        if isinstance(e, c_ast.ID):
            return self.enums.get(e.name)
        if isinstance(e, c_ast.Cast):
            return self._const(e.expr)
        if isinstance(e, c_ast.UnaryOp):
            if e.op == "sizeof":
                t = self.resolve(e.expr) if isinstance(e.expr, c_ast.Typename) else                     self.typer(e.expr) if self.typer is not None else None
                return t.size if t is not None else None
            v = self._const(e.expr)
            return None if v is None else {"-": -v, "+": v, "~": ~v, "!": int(not v)}.get(e.op)
        if isinstance(e, c_ast.BinaryOp):
            a, b = self._const(e.left), self._const(e.right)
            if a is None or b is None:
                return None
            ops = {"+": a + b, "-": a - b, "*": a * b, "<<": a << b if 0 <= b < 64 else None, ">>": a >> b if
                   0 <= b < 64 else None, "&": a & b, "|": a | b, "^": a ^ b, "==": int(a == b), "!=": int(a != b),
                   "<": int(a < b), ">": int(a > b), "<=": int(a <= b), ">=": int(a >= b)}
            if e.op in ("/", "%"):
                return (int(a / b) if e.op == "/" else a - b * int(a / b)) if b else None
            return ops.get(e.op)
        if isinstance(e, c_ast.TernaryOp):
            c = self._const(e.cond)
            return None if c is None else self._const(e.iftrue if c else e.iffalse)
        return None


class _Lazy:
    """A pointer's target, resolved on first use (a record can point at itself)."""
    __slots__ = ("types", "node", "t")

    def __init__(self, types, node):
        self.types, self.node, self.t = types, node, None

    def get(self):
        if self.t is None:
            self.t = self.types.resolve(self.node)
        return self.t


def tgt(t):
    """The pointed-to / element type of a pointer or array type (INT for anything else)."""
    if t.kind == "ptr":
        return t.to.get() if isinstance(t.to, _Lazy) else t.to
    if t.kind == "arr":
        return t.to
    return INT


def ret_of(t):
    if t.kind == "ptr":
        t = tgt(t)
    if t.kind != "func":
        return INT
    return t.ret.get() if isinstance(t.ret, _Lazy) else t.ret


def _round(x, a):
    return (x + a - 1) // a * a if a > 1 else x


def field_mask(t, base=0):
    """Bitmask of the bytes of t that hold a value (padding left out)."""
    if t.kind == "rec":
        m = 0
        for _, o, ft in t.fields or []:
            m |= field_mask(ft, base + o)
        return m
    if t.kind == "arr":
        el = t.to
        m = 0
        for k in range(t.n or 0):
            m |= field_mask(el, base + k * el.size)
        return m
    return ((1 << t.size) - 1) << base if t.size else 0


def span(off, size):
    return ((1 << size) - 1) << off if off is not None and off >= 0 and size > 0 else 0


# ------------------------------------------------------------------------------------------ per-function facts

class Fn:
    """One function definition: its parameters, locals, accesses and calls, as the fixpoint and R2 need them."""

    def __init__(self, key, file, node, types):
        self.key, self.file, self.node, self.types = key, file, node, types
        ft = types.resolve(node.decl.type)
        self.params = ft.params or []
        self.pindex = {n: i for i, (n, _) in enumerate(self.params) if n}
        self.locals = {}  # name -> type (block scopes flattened; the readable lint forbids shadowing)
        self.inits = set()  # locals declared with an initializer
        self.statics = set()
        for d in _walk(node.body):
            if isinstance(d, c_ast.Decl) and d.name and not isinstance(d.type, c_ast.FuncDecl):
                if "static" in (d.storage or []) or "extern" in (d.storage or []):
                    self.statics.add(d.name)
                    continue
                self.locals[d.name] = types.resolve(d.type)
                if d.init is not None:
                    self.inits.add(d.name)
        self.moved = set()  # parameters the body reassigns or steps: offsets through them are unknown
        for n in _walk(node.body):
            target = n.lvalue if isinstance(n, c_ast.Assignment) else \
                n.expr if isinstance(n, c_ast.UnaryOp) and n.op in ("p++", "p--", "++", "--") else None
            if isinstance(target, c_ast.ID) and target.name in self.pindex and target.name not in self.locals:
                self.moved.add(target.name)
        self.derived = {}  # local name -> {(root, off)} it may hold (a pointer or an integer from one)
        self._derive_locals()

    # roots: ("p", i) the pointee of parameter i; ("l", name) local `name`'s own storage

    def const(self, e):
        """An integer constant expression's value (sizeof of an expression typed in this function), else None."""
        self.types.typer = self.type_of
        try:
            return self.types.const(e)
        finally:
            self.types.typer = None

    def type_of(self, e):
        ty = self.types
        if isinstance(e, c_ast.ID):
            if e.name in self.locals:
                return self.locals[e.name]
            if e.name in self.pindex:
                return self.params[self.pindex[e.name]][1]
            d = ty.decls.get(e.name)
            return ty.resolve(d) if d is not None else INT
        if isinstance(e, c_ast.Constant):
            return T("float", 4, 4) if e.type in ("float", "double") else INT
        if isinstance(e, c_ast.Cast):
            return ty.resolve(e.to_type)
        if isinstance(e, c_ast.StructRef):
            bt = self.type_of(e.name)
            rec = tgt(bt) if e.type == "->" else bt
            return ty.field(rec, e.field.name)[1]
        if isinstance(e, c_ast.ArrayRef):
            return tgt(self.type_of(e.name))
        if isinstance(e, c_ast.UnaryOp):
            if e.op == "*":
                return tgt(self.type_of(e.expr))
            if e.op == "&":
                return T("ptr", 4, 4, to=self.type_of(e.expr))
            if e.op == "sizeof":
                return INT
            return self.type_of(e.expr)
        if isinstance(e, c_ast.BinaryOp):
            a, b = self.type_of(e.left), self.type_of(e.right)
            if e.op in ("+", "-"):
                if a.kind in ("ptr", "arr") and b.kind not in ("ptr", "arr"):
                    return T("ptr", 4, 4, to=tgt(a))
                if b.kind in ("ptr", "arr") and a.kind not in ("ptr", "arr") and e.op == "+":
                    return T("ptr", 4, 4, to=tgt(b))
                if a.kind in ("ptr", "arr"):
                    return INT
            if e.op in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                return INT
            return a
        if isinstance(e, c_ast.FuncCall):
            return ret_of(self.type_of(e.name))
        if isinstance(e, c_ast.TernaryOp):
            return self.type_of(e.iftrue)
        if isinstance(e, c_ast.Assignment):
            return self.type_of(e.lvalue)
        if isinstance(e, c_ast.ExprList) and e.exprs:
            return self.type_of(e.exprs[-1])
        if isinstance(e, c_ast.CompoundLiteral):
            return ty.resolve(e.type)
        return INT

    def derive(self, e, depth=0):
        """{(root, offset or TOP)}: where a pointer-valued (or pointer-holding integer) expression may point."""
        if depth > 40:
            return set()
        if isinstance(e, c_ast.Cast):
            return self.derive(e.expr, depth + 1)
        if isinstance(e, c_ast.ID):
            if e.name in self.locals:
                t = self.locals[e.name]
                if t.kind in ("arr",):
                    return {(("l", e.name), 0)}
                got = self.derived.get(e.name, ())
                moved = {r for r, o in got if o is TOP}  # a cursor (stepped in a loop): its offsets are unknown
                return {(r, TOP if r in moved else o) for r, o in got}
            if e.name in self.pindex:
                return {(("p", self.pindex[e.name]), TOP if e.name in self.moved else 0)}
            return set()
        if isinstance(e, c_ast.UnaryOp):
            if e.op == "&":
                return self.lv_addr(e.expr, depth + 1)
            if e.op in ("p++", "p--", "++", "--"):
                return {(r, TOP) for r, _ in self.derive(e.expr, depth + 1)}
            return set()
        if isinstance(e, c_ast.BinaryOp) and e.op in ("+", "-"):
            lt, rt = self.type_of(e.left), self.type_of(e.right)
            if lt.kind in ("ptr", "arr") or (rt.kind not in ("ptr", "arr") and self.derive(e.left, depth + 1)):
                base, idx = e.left, e.right
            elif e.op == "+":
                base, idx = e.right, e.left
            else:
                return set()
            bt = self.type_of(base)
            es = tgt(bt).size if bt.kind in ("ptr", "arr") else 1
            k = self.const(idx)
            out = set()
            for r, o in self.derive(base, depth + 1):
                if o is TOP or k is None:
                    out.add((r, TOP))
                else:
                    out.add((r, o + (k if e.op == "+" else -k) * max(es, 1)))
            return out
        if isinstance(e, c_ast.TernaryOp):
            return self.derive(e.iftrue, depth + 1) | self.derive(e.iffalse, depth + 1)
        if isinstance(e, c_ast.Assignment):
            return self.derive(e.rvalue, depth + 1) if e.op == "=" else set()
        if isinstance(e, c_ast.ExprList) and e.exprs:
            return self.derive(e.exprs[-1], depth + 1)
        if isinstance(e, (c_ast.StructRef, c_ast.ArrayRef)) and self.type_of(e).kind == "arr":
            return self.lv_addr(e, depth + 1)  # an array member decays to its address
        return set()

    def lv_addr(self, e, depth=0):
        """{(root, offset)} of an lvalue's storage."""
        if depth > 40:
            return set()
        if isinstance(e, c_ast.ID):
            return {(("l", e.name), 0)} if e.name in self.locals else set()
        if isinstance(e, c_ast.UnaryOp) and e.op == "*":
            return self.derive(e.expr, depth + 1)
        if isinstance(e, c_ast.StructRef):
            base = self.derive(e.name, depth + 1) if e.type == "->" else self.lv_addr(e.name, depth + 1)
            bt = self.type_of(e.name)
            off, _ = self.types.field(tgt(bt) if e.type == "->" else bt, e.field.name)
            return {(r, TOP if o is TOP or off is None else o + off) for r, o in base}
        if isinstance(e, c_ast.ArrayRef):
            bt = self.type_of(e.name)
            base = self.lv_addr(e.name, depth + 1) if bt.kind == "arr" else self.derive(e.name, depth + 1)
            k = self.const(e.subscript)
            es = tgt(bt).size
            return {(r, TOP if o is TOP or k is None else o + k * es) for r, o in base}
        if isinstance(e, c_ast.Cast):
            return self.lv_addr(e.expr, depth + 1)
        return set()

    def _derive_locals(self):
        assigns = []
        for n in _walk(self.node.body):
            if isinstance(n, c_ast.Decl) and n.name in self.locals and n.init is not None \
                    and not isinstance(n.init, c_ast.InitList):
                assigns.append((n.name, n.init, "="))
            elif isinstance(n, c_ast.Assignment) and isinstance(n.lvalue, c_ast.ID) and n.lvalue.name in self.locals:
                assigns.append((n.lvalue.name, n.rvalue, n.op))
            elif isinstance(n, c_ast.UnaryOp) and n.op in ("p++", "p--", "++", "--") \
                    and isinstance(n.expr, c_ast.ID) and n.expr.name in self.locals:
                assigns.append((n.expr.name, None, "+="))
        for _ in range(8):
            changed = False
            for name, rhs, op in assigns:
                cur = self.derived.setdefault(name, set())
                if op == "=":
                    new = self.derive(rhs)
                elif op in ("+=", "-="):
                    new = {(r, TOP) for r, _ in cur}
                else:
                    continue
                if not new <= cur:
                    cur |= new
                    changed = True
            if not changed:
                break


def _walk(node):
    if node is None:
        return
    yield node
    for _, c in node.children():
        yield from _walk(c)


def _children_value(e):
    """Sub-expressions of e evaluated for their value (not as e's storage)."""
    if isinstance(e, c_ast.StructRef):
        return [e.name] if e.type == "->" else _children_addr(e.name)
    if isinstance(e, c_ast.ArrayRef):
        return [e.subscript, e.name]  # an array-typed base is not itself an access (visit skips arrays)
    return [c for _, c in e.children()]


def _children_addr(e):
    if isinstance(e, c_ast.StructRef):
        return [e.name] if e.type == "->" else _children_addr(e.name)
    if isinstance(e, c_ast.ArrayRef):
        return [e.subscript] + _children_addr(e.name)
    if isinstance(e, c_ast.ID):
        return []
    return [e]


# ---------------------------------------------------------------------------------------- the program analysis

class Program:
    """Every function definition of the readable files, keyed "name" (extern) or "file:name" (static)."""

    def __init__(self):
        self.fns, self.protos, self.types_of = {}, {}, {}

    def add_header(self, ext, types):
        for n in ext:
            if isinstance(n, c_ast.Decl) and isinstance(n.type, c_ast.FuncDecl):
                self.protos.setdefault(n.name, types.resolve(n.type))

    def add_unit(self, file, own, types):
        """own: the file's declarations after game.h."""
        for n in own:
            if isinstance(n, c_ast.FuncDef):
                static = "static" in (n.decl.storage or [])
                key = f"{file}:{n.decl.name}" if static else n.decl.name
                self.fns[key] = Fn(key, file, n, types)

    def callee(self, fn, call):
        if not isinstance(call.name, c_ast.ID):
            return None
        name = call.name.name
        if name in fn.locals or name in fn.pindex:
            return None  # a function pointer
        local = f"{fn.file}:{name}"
        return local if local in self.fns else name

    def copy_roles(self, name):
        """(dst index, src index, size index) of a body-less copy / fill helper, else None."""
        if name in self.fns:
            return None
        if name in COPY_NAMES:
            return (0, 1 if COPY_NAMES[name][1] else None, 2)
        ft = self.protos.get(name)
        if ft is None or not ft.params:
            return None
        size = next((i for i, (n, t) in enumerate(ft.params) if t.kind == "int" and SIZE_NAMES.match(n or "")), None)
        if size is None:
            return None
        ptrs = [(i, n or "") for i, (n, t) in enumerate(ft.params) if t.kind == "ptr"]
        src = next((i for i, n in ptrs if re.search(r"src|from|^in", n, re.I)), None)
        dst = next((i for i, n in ptrs if i != src and re.search(r"dst|dest|to|out|buf|^p$|ptr", n, re.I)), None)
        if dst is None:
            dst = next((i for i, _ in ptrs if i != src), None)
        if dst is None and src is None:
            return None
        return (dst, src, size)


class Facts:
    """Per function: direct accesses through roots, and call edges, gathered once."""

    def __init__(self, prog, fn):
        self.acc = []    # (root, off, size, line)
        self.calls = []  # (callee key, arg index, root, off, line, size value or None, call node)
        for n in _walk(fn.node.body):
            if isinstance(n, c_ast.FuncCall):
                key = prog.callee(fn, n)
                args = n.args.exprs if n.args else []
                roles = prog.copy_roles(key) if key else None
                size = fn.const(args[roles[2]]) if roles and roles[2] is not None and roles[2] < len(args) else None
                for j, a in enumerate(args):
                    for r, o in fn.derive(a):
                        self.calls.append((key, j, r, o, _line(n), size, n))
        for e, size in _accesses(fn, fn.node.body):
            for r, o in fn.lv_addr(e):
                self.acc.append((r, o, size, _line(e)))


def _line(n):
    return n.coord.line if getattr(n, "coord", None) else 0


def _accesses(fn, body):
    """(lvalue, size) of every memory access (a read or write of a scalar or a whole record)."""
    out = []

    def visit(e, as_value):
        if e is None:
            return
        if isinstance(e, (c_ast.FuncDef, c_ast.Typename)):
            return
        if isinstance(e, c_ast.UnaryOp) and e.op in ("sizeof", "&"):
            if e.op == "&":
                for c in _children_addr(e.expr):
                    visit(c, True)
            return
        lv = isinstance(e, (c_ast.StructRef, c_ast.ArrayRef)) or (isinstance(e, c_ast.UnaryOp) and e.op == "*")
        if lv:
            t = fn.type_of(e)
            if t.kind != "arr" and t.kind != "func" and as_value and t.size:
                out.append((e, t.size))
            for c in (_children_value(e) if not isinstance(e, c_ast.UnaryOp) else [e.expr]):
                visit(c, True)
            return
        for _, c in e.children():
            visit(c, True)

    visit(body, True)
    return out


def extents(prog, facts):
    """{fn key: {param index: furthest byte reached through it}}, a fixpoint over the call graph."""
    ext = {k: {} for k in prog.fns}
    for k, f in facts.items():
        for (kind, i), o, size, _ in [(a[0], a[1], a[2], a[3]) for a in f.acc if a[0][0] == "p"]:
            if o is not TOP and o >= 0:
                ext[k][i] = max(ext[k].get(i, 0), o + size)
    for _ in range(64):
        changed = False
        for k, f in facts.items():
            for key, j, (kind, i), o, _, size, _ in f.calls:
                if kind != "p" or o is TOP or o < 0:
                    continue
                reach = callee_reach(prog, ext, key, j, size)
                if reach and o + reach > ext[k].get(i, 0):
                    ext[k][i] = min(o + reach, 1 << 24)
                    changed = True
        if not changed:
            break
    return ext


def callee_reach(prog, ext, key, j, size):
    if key in prog.fns:
        return ext[key].get(j, 0)
    roles = prog.copy_roles(key) if key else None
    if roles and j in (roles[0], roles[1]) and size is not None and size > 0:
        return size
    return 0


# ------------------------------------------------------------------------------- R2: must-written local bytes

class Writes:
    """Definite writes, flow-sensitive over the statement tree. state: {root: bitmask of bytes written}."""

    def __init__(self, prog, fn, mw, hits):
        self.prog, self.fn, self.mw, self.hits = prog, fn, mw, hits
        self.tracked = {("l", n) for n, t in fn.locals.items() if n not in fn.inits and t.kind in ("rec", "arr")}
        self.tracked |= {("p", i) for i, (_, t) in enumerate(fn.params) if t.kind == "ptr"}
        self.returns, self.brk = [], []

    def run(self):
        st = self.stmt(self.fn.node.body, {r: 0 for r in self.tracked})
        if st is not None:
            self.returns.append(st)
        return self.returns

    def stmt(self, s, st):
        """The state after s (None: s does not complete normally)."""
        if st is None or s is None:
            return st
        if isinstance(s, c_ast.Compound):
            for x in s.block_items or []:
                st = self.stmt(x, st)
            return st
        if isinstance(s, c_ast.If):
            st = self.expr(s.cond, st)
            a, b = self.stmt(s.iftrue, dict(st)), self.stmt(s.iffalse, dict(st))
            return _meet(a, b)
        # A loop's exit keeps only the writes made before it (its body may run zero times; writes only add, so the
        # state before the loop is under every exit's). A break inside it ends at the loop, not at a switch.
        if isinstance(s, c_ast.For):
            st = self.stmt(s.init, st) if isinstance(s.init, c_ast.DeclList) else self.expr(s.init, st)
            st = self.expr(s.cond, st)
            self.brk.append([])
            body = self.stmt(s.stmt, dict(st))
            self.expr(s.next, body) if body is not None else None
            self.brk.pop()
            return st
        if isinstance(s, c_ast.While):
            st = self.expr(s.cond, st)
            self.brk.append([])
            self.stmt(s.stmt, dict(st))
            self.brk.pop()
            return st
        if isinstance(s, c_ast.DoWhile):
            self.brk.append([])
            body = self.stmt(s.stmt, dict(st))
            self.expr(s.cond, body) if body is not None else None
            self.brk.pop()
            return st
        if isinstance(s, c_ast.Switch):  # each case is entered from the switch or falls in; breaks end it
            entry = self.expr(s.cond, st)
            self.brk.append([])
            cur, default = None, False
            items = s.stmt.block_items if isinstance(s.stmt, c_ast.Compound) else [s.stmt]
            for x in items or []:
                if isinstance(x, (c_ast.Case, c_ast.Default)):
                    default |= isinstance(x, c_ast.Default)
                    cur = _meet(cur, dict(entry))
                    for y in x.stmts or []:
                        cur = self.stmt(y, cur)
                else:
                    cur = self.stmt(x, cur)
            out = cur
            for x in self.brk.pop() + ([] if default else [entry]):
                out = _meet(out, x)
            return out
        if isinstance(s, (c_ast.Case, c_ast.Default)):  # a case label outside a switch's own list (Duff's device)
            for x in s.stmts or []:
                st = self.stmt(x, st)
            return st
        if isinstance(s, c_ast.Label):
            return self.stmt(s.stmt, st)
        if isinstance(s, c_ast.Return):
            st = self.expr(s.expr, st)
            if s.expr is not None:
                self.copy_out_value(s.expr, st)
            self.returns.append(st)
            return None
        if isinstance(s, c_ast.Break) and self.brk:
            self.brk[-1].append(st)
            return None
        if isinstance(s, (c_ast.Break, c_ast.Continue, c_ast.Goto)):
            return None
        if isinstance(s, c_ast.DeclList):
            for d in s.decls:
                st = self.stmt(d, st)
            return st
        if isinstance(s, c_ast.Decl):
            if s.init is not None and not isinstance(s.init, c_ast.InitList):
                st = self.expr(s.init, st)
                if s.name in self.fn.locals and self.fn.locals[s.name].kind == "rec":
                    self.copy_out_value(s.init, st)
            elif isinstance(s.init, c_ast.InitList):
                st = self.expr(s.init, st)
            return st
        return self.expr(s, st)

    def write(self, st, roots, mask_size):
        for r, o in roots:
            if r not in st:
                continue
            if o is TOP:
                st[r] = -1  # an unknown offset: count every byte as written
            elif o >= 0 and st[r] != -1:
                st[r] |= span(o, mask_size)

    def need(self, st, roots, size, what, line):
        """A copy reads size bytes at roots: report bytes of a tracked local never written on some path."""
        for r, o in roots:
            if r[0] != "l" or r not in st or o is TOP or st[r] == -1 or size is None or size <= 0:
                continue
            lt = self.fn.locals[r[1]]
            want = span(o, min(size, max(lt.size - o, 0))) & field_mask(lt)
            missing = want & ~st[r]
            if missing:
                lo = (missing & -missing).bit_length() - 1
                hi = missing.bit_length()
                self.hits.append({"fn": self.fn.key, "rule": "R2", "line": line, "local": r[1],
                                  "local_size": lt.size, "why": f"{what} reads bytes {o}..{o + size} of `{r[1]}` "
                                  f"({lt.size} bytes); 0x{lo:x}..0x{hi:x} are not written on every path before it"})

    def copy_out_value(self, e, st):
        """A whole local read as a value (assignment source, argument, return value). A member's copy is a field
        read, as a scalar's is: often guarded by another field (`if (q.hit) *out = q.hit_pos;`), so not checked."""
        e2 = e.expr if isinstance(e, c_ast.Cast) else e
        if isinstance(e2, c_ast.ID) and e2.name in self.fn.locals and self.fn.type_of(e2).kind == "rec":
            self.need(st, self.fn.lv_addr(e2), self.fn.type_of(e2).size, "a whole-record copy", _line(e2))

    def expr(self, e, st):
        if st is None or e is None:
            return st
        fn = self.fn
        if isinstance(e, c_ast.Assignment):
            st = self.expr(e.rvalue, st)
            if e.op == "=" and fn.type_of(e.lvalue).kind == "rec":
                self.copy_out_value(e.rvalue, st)
            for c in _children_addr(e.lvalue) if not isinstance(e.lvalue, c_ast.UnaryOp) else [e.lvalue.expr]:
                st = self.expr(c, st)
            t = fn.type_of(e.lvalue)
            self.write(st, fn.lv_addr(e.lvalue), t.size)
            return st
        if isinstance(e, c_ast.FuncCall):
            args = e.args.exprs if e.args else []
            for a in args:
                st = self.expr(a, st)
            key = self.prog.callee(fn, e)
            roles = self.prog.copy_roles(key) if key else None
            if roles:
                dst, src, si = roles
                size = fn.const(args[si]) if si is not None and si < len(args) else None
                if src is not None and src < len(args):
                    self.need(st, fn.derive(args[src]), size, f"{key}'s source", _line(e))
                if dst is not None and dst < len(args):
                    for r, o in fn.derive(args[dst]):
                        if r in st:
                            if size is None or o is TOP:
                                st[r] = -1
                            elif st[r] != -1:
                                st[r] |= span(o, size)
                return st
            for a in args:
                self.copy_out_value(a, st)
            for j, a in enumerate(args):
                roots = fn.derive(a)
                if not roots:
                    continue
                w = self.mw(key, j) if key in self.prog.fns else -1  # unknown callee: it may fill everything
                for r, o in roots:
                    if r not in st:
                        continue
                    if w == -1 or o is TOP:
                        st[r] = -1
                    elif st[r] != -1:
                        st[r] |= w << o if o >= 0 else 0
            return st
        if isinstance(e, c_ast.UnaryOp) and e.op == "sizeof":
            return st
        if isinstance(e, (c_ast.TernaryOp,)):
            st = self.expr(e.cond, st)
            return _meet(self.expr(e.iftrue, dict(st)), self.expr(e.iffalse, dict(st)))
        if isinstance(e, c_ast.BinaryOp) and e.op in ("&&", "||"):
            st = self.expr(e.left, st)
            self.expr(e.right, dict(st))
            return st
        for _, c in e.children():
            if isinstance(c, (c_ast.Typename,)):
                continue
            st = self.expr(c, st)
        return st


def _meet(a, b):
    if a is None:
        return b
    if b is None:
        return a
    out = {}
    for r in a:
        x, y = a[r], b.get(r, 0)
        out[r] = y if x == -1 else x if y == -1 else x & y
    return out


def analyse(prog):
    """{"hits": [...], "functions": N, "extents": {...}}: R1 and R2 over the whole program."""
    facts = {k: Facts(prog, f) for k, f in prog.fns.items()}
    ext = extents(prog, facts)
    hits = []
    for k, f in facts.items():  # R1: a local reached past its end, directly or through a callee
        fn = prog.fns[k]
        seen = set()
        for (kind, name), o, size, line in f.acc:
            if kind == "l" and o is not TOP and name in fn.locals:
                lsz = fn.locals[name].size
                if lsz and o + size > lsz and (name, line) not in seen:
                    seen.add((name, line))
                    hits.append({"fn": k, "rule": "R1", "line": line, "local": name, "local_size": lsz,
                                 "why": f"an access reaches byte {o + size} of `{name}` ({lsz} bytes)"})
        for key, j, (kind, name), o, line, size, _ in f.calls:
            if kind != "l" or o is TOP or name not in fn.locals:
                continue
            lsz = fn.locals[name].size
            reach = callee_reach(prog, ext, key, j, size)
            if lsz and reach and o + reach > lsz and (name, line, key) not in seen:
                seen.add((name, line, key))
                hits.append({"fn": k, "rule": "R1", "line": line, "local": name, "local_size": lsz,
                             "why": f"`{name}` ({lsz} bytes) is passed at +{o} to {key} (argument {j + 1}), "
                                    f"which reaches {reach} bytes from there"})
    memo, busy = {}, set()

    def mw(key, j):
        """Bytes callee `key` writes through parameter j on every path to its end (-1: cannot tell)."""
        if key not in memo:
            if key in busy:
                return -1  # recursion: assume it fills (no hit from a cycle)
            busy.add(key)
            w = Writes(prog, prog.fns[key], mw, [])
            states = w.run()
            busy.discard(key)
            res = {}
            for i, (_, t) in enumerate(prog.fns[key].params):
                if t.kind != "ptr":
                    continue
                vals = [s.get(("p", i), 0) for s in states] or [0]
                acc = -1
                for v in vals:
                    acc = v if acc == -1 else (acc if v == -1 else acc & v)
                res[i] = acc
            memo[key] = res
        return memo[key].get(j, -1)

    for k, fn in prog.fns.items():  # R2: a local copied out before all of the copied bytes are written
        r2 = []
        Writes(prog, fn, mw, r2).run()
        dedup = {(h["local"], h["line"], h["why"]): h for h in r2}
        hits += dedup.values()
    return {"hits": hits, "functions": len(prog.fns), "extents": ext, "files": {k: f.file for k, f in prog.fns.items()}}


# ----------------------------------------------------------------------------------------------- the front end

def preprocess(cl, env, inc_dirs, fake_dir, src_text, work, timeout=120):
    """cl /EP of one readable file with a marker declaration after its last #include: (text, the source line the marker maps to)."""
    lines = src_text.splitlines()
    k = max((i for i, ln in enumerate(lines) if ln.strip().startswith("#include")), default=-1)
    lines.insert(k + 1, f"int {MARK};")
    work.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [cl, "/nologo", "/EP", "/X", "/TC", f"/I{fake_dir}", *(f"/I{d}" for d in inc_dirs), str(work)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(" ".join((r.stdout + r.stderr).strip().splitlines()[-2:]))
    return r.stdout, k + 1  # the marker stands in for the include line, so the lines after it keep their numbers


def clean(text):
    """What pycparser cannot read, blanked without moving a line: __declspec(...), MSVC keywords, #pragma."""
    text = re.sub(r"__declspec\s*\(\s*\w+\s*(\([^)]*\))?\s*\)", "", text)
    text = re.sub(r"\b__(?:forceinline|inline|cdecl|stdcall|restrict|unaligned|ptr32)\b", "", text)
    return re.sub(r"^[ \t]*#[ \t]*pragma.*$", "", text, flags=re.M)


def split_unit(ast, marker_src_line):
    """(the declarations before the marker, the file's own ones with their source lines restored)."""
    ext = ast.ext
    m = next(i for i, n in enumerate(ext) if isinstance(n, c_ast.Decl) and n.name == MARK)
    delta = marker_src_line - ext[m].coord.line
    seen = set()
    for n in ext[m + 1:]:
        for x in _walk(n):
            c = getattr(x, "coord", None)
            if c is not None and id(c) not in seen:
                seen.add(id(c))
                c.line += delta
    return ext[:m], ext[m + 1:]


def unit_job(args):
    """Worker: preprocess and parse one readable file; (file, its own declarations, error)."""
    file, src_text, cl, env, inc_dirs, fake_dir, work_dir = args
    try:
        text, line = preprocess(cl, env, inc_dirs, fake_dir, src_text, pathlib.Path(work_dir) / f"{file}.c")
        ast = c_parser.CParser().parse(clean(text), filename=f"{file}.c")
        return file, split_unit(ast, line)[1], None
    except Exception as e:  # noqa: BLE001  (pycparser raises ParseError / AssertionError; cl a RuntimeError)
        return file, None, f"{type(e).__name__}: {str(e)[:300]}"


def write_fake_std(d):
    d.mkdir(parents=True, exist_ok=True)
    for name, body in FAKE_STD.items():
        (d / name).write_text(body, encoding="utf-8")


def run(sources, cl, env, inc_dirs, work_dir, workers=8):
    """sources: {file key (the address): readable C}. Every file includes game.h only (header_lint): the header is
    parsed once, here, for the shared types and prototypes. Returns (analysis, {file: error})."""
    work_dir = pathlib.Path(work_dir)
    fake = work_dir / "fake_std"
    write_fake_std(fake)
    head_text, line = preprocess(cl, env, inc_dirs, fake, '#include "game.h"\n', work_dir / "_header.c")
    head, _ = split_unit(c_parser.CParser().parse(clean(head_text), filename="game.h"), line)
    shared = Types(head)
    prog, errors = Program(), {}
    prog.add_header(head, shared)
    jobs = [(f, t, cl, env, [str(d) for d in inc_dirs], str(fake), str(work_dir)) for f, t in sorted(sources.items())]
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        for file, own, err in ex.map(unit_job, jobs, chunksize=4):
            if err:
                errors[file] = err
                continue
            prog.add_unit(file, own, Types(own, parent=shared))
    return analyse(prog), errors


def check_text(header, files):
    """The analysis of already preprocessed C (tests): header, then {file: its own text}, as run() would see them."""
    parser = c_parser.CParser()
    marker = f"\nint {MARK};\n"
    head, _ = split_unit(parser.parse(header + marker), 1)
    shared = Types(head)
    prog = Program()
    prog.add_header(head, shared)
    for file, text in sorted(files.items()):
        own = split_unit(c_parser.CParser().parse(header + marker + text, filename=f"{file}.c"), 0)[1]
        prog.add_unit(file, own, Types(own, parent=shared))
    return analyse(prog)

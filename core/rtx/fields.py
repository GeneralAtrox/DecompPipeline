"""Struct fields and named constants for core/rtx/lift, from the project's readable headers.

- fields: `*(T *)((u32)p + off)`, for a p whose type is a pointer to a struct the headers lay out
  (core/lean_layout: 32-bit MSVC, as the judge builds), becomes the member at that offset, `p->a.b[2]`,
  when its size and kind are the access's (a cast keeps the access's type when the member's differs). A
  struct-typed global's `(u32)&g + off` becomes `g.a.b`.
- pointer locals: a local every assignment of which is `v = (u32)E` with E of one pointer type (or 0) takes that
  type, so the fields through it are found too. The two run to a fixpoint.
- constants: a literal combined (&, |, ^, ==, !=, =) with a member whose comment names a family (`ENTITY_STATE_*`) or
  whose type is an enum becomes the family's one name for that value (or the | of the names of its bits).
Nothing is guessed: no member, or more than one name, leaves the expression as it is.
"""
from __future__ import annotations

import re

from .. import lean_layout, readable_headers
from ..raw_names import mask
from . import flow
from .cast import Assign, Bin, Cast, Index, Member, Num, Unary, Var

WILD = re.compile(r"\b([A-Z][A-Z0-9_]*_)\*")
PLACEHOLDER = re.compile(r"(?:unknown|_?pad|unk|reserved)_?[0-9a-fA-Fx]*$")


class Field:
    __slots__ = ("off", "name", "ctype", "size", "count", "esize", "sub", "family", "shape")

    def __init__(self, off, name, ctype, size, count=1, sub=None, family=None, shape=None):
        self.off, self.name, self.ctype, self.size, self.count = off, name, ctype, size, count
        self.esize, self.sub, self.family = size // max(count, 1), sub, family
        self.shape = shape  # a declared array's dimensions ([6][4]: (6, 4); [1]: (1,)); count is their product

    @property
    def array(self):
        """Whether the member is an array (cells[1] too: its element is cells[0], not cells)."""
        return self.shape is not None or self.count > 1

    def indexes(self, k):
        """The element k of the flattened array as one index per dimension (faces[6][4], 9: [2, 1])."""
        out = []
        for d in reversed((self.shape or (self.count,))[1:]):
            k, r = divmod(k, d)
            out.append(r)
        return [k] + out[::-1]


class Struct:
    def __init__(self, name, size, fields, union=False):
        self.name, self.size, self.fields, self.union = name, size, fields, union
        self.by_name = {f.name: f for f in fields}


class Types:
    """The structs of include_dir's headers (laid out on demand) and the enum typedefs' constants."""

    def __init__(self, include_dir, kind):
        self.hs = readable_headers.load(include_dir)
        self.kind = kind
        self.cache = {}

    def struct(self, name):
        name = re.sub(r"\bconst\b|\bvolatile\b", "", name).strip()
        m = re.fullmatch(r"(struct|union)\s+(\w+)", name)
        key = f"{m.group(1)}:{m.group(2)}" if m else name
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None
        it = self.hs.by_name.get(key)
        st = None
        if it is not None:
            try:
                lay = lean_layout.layout(self.hs, it)
                st = self._struct(key, lay) if lay is not None else None
            except Exception:  # noqa: BLE001  (a struct the layout cannot size: no fields from it)
                st = None
        self.cache[key] = st
        return st

    def pointee(self, ctype):
        """The struct a pointer type points at (one level), else None."""
        if ctype is None or not isinstance(ctype, str):
            return None
        t = re.sub(r"\bconst\b|\bvolatile\b", "", ctype).strip()
        if not t.endswith("*") or t[:-1].rstrip().endswith("*"):
            return None
        return self.struct(t[:-1].strip())

    def enum_names(self, ctype):
        t = re.sub(r"\bconst\b", "", ctype or "").strip()
        it = self.hs.by_name.get(t)
        if it is None or not re.match(r"\s*typedef\s+enum\b", mask(it.text)[0]):
            return None
        return set(it.names) - {t}

    def _struct(self, name, lay):
        fields = []
        for m in lay.members:
            fields += self._fields(m)
        return Struct(name, lay.size, fields, lay.union)

    def _fields(self, m):
        code = mask(m.text)[0].strip()
        comment = m.text
        fam = WILD.search(comment)
        fam = fam.group(1) if fam else None
        code = code.strip().rstrip(";").strip()
        if "{" in code:
            brace = code.find("{")
            inner = lean_layout._layout_of(self.hs, None, mask(m.text)[0], m.text, frozenset())
            sub = self._struct(None, inner)
            rest = code[lean_layout._close(code, brace) + 1:].strip()
            if not rest:   # anonymous: its members are the outer struct's
                return [Field(m.offset + f.off, f.name, f.ctype, f.size, f.count, f.sub, f.family, f.shape)
                        for f in sub.fields]
            names = re.findall(r"[A-Za-z_]\w*", rest)
            if len(names) != 1 or "[" in rest:
                return []
            return [Field(m.offset, names[0], sub, sub.size, 1, sub, fam)]
        if re.search(r"\(\s*\*", code):
            return []      # an inline function-pointer member: no type text to cast with
        words, toks = lean_layout._words(lean_layout.TOKEN.findall(code))
        out, off = [], m.offset
        for part in lean_layout._commas(" ".join(toks)):
            ptoks = lean_layout.TOKEN.findall(part)
            names = [t for t in ptoks if re.fullmatch(r"[A-Za-z_]\w*", t)]
            if len(names) != 1:
                return out
            ptrs, dims = ptoks.count("*"), [t for t in ptoks if t.startswith("[")]
            base = " ".join(words)
            ctype = base + (" " + "*" * ptrs if ptrs else "")
            try:
                n = lean_layout._dims(self.hs, dims)
                shape = tuple(lean_layout._dims(self.hs, [d]) for d in dims) or None
            except lean_layout.Unsized:
                return out
            size = m.size // max(m.count, 1)
            sub = None if ptrs else self.struct(base)
            f = Field(off, names[0], ctype, size, n, sub, fam, shape)
            out.append(f)
            off += size
        return out

    def start(self, st, off):
        """[('.', (name, type)) | ('[]', (i, type))] of the outermost member starting at off (off > 0), else None."""
        for f in st.fields:
            if not (f.off <= off < f.off + f.size):
                continue
            if PLACEHOLDER.fullmatch(f.name):
                return None
            rel = off - f.off
            if f.array:
                i, rel = divmod(rel, f.esize)
                ix = f.indexes(i)
                seg = [(".", (f.name, None))] + [("[]", (j, None)) for j in ix[:-1]] + [("[]", (ix[-1], f.ctype))]
            else:
                seg = [(".", (f.name, f.ctype))]
            if rel == 0:
                return seg
            if isinstance(f.sub, Struct):
                got = self.start(f.sub, rel)
                if got:
                    return seg + got
            if not st.union:
                return None
        return None

    def find(self, st, off, size, want):
        """[(path [('.', name) | ('[]', i)], member type)] of the scalar members at off with this size whose kind
        fits want ('f': a float, 'i': an integer or a pointer). The first declared wins in a union."""
        for f in st.fields:
            if not (f.off <= off < f.off + f.size):
                continue
            if PLACEHOLDER.fullmatch(f.name):
                return None
            rel = off - f.off
            seg = [(".", f.name)]
            esize = f.esize
            if f.array:
                if rel % esize and f.sub is None:
                    return None
                seg += [("[]", j) for j in f.indexes(rel // esize)]
                rel %= esize
            if f.sub is not None and isinstance(f.sub, Struct):
                got = self.find(f.sub, rel, size, want)
                if got:
                    return [(seg + p, t, fam) for p, t, fam in got]
                if st.union:
                    continue
                return None
            if rel or esize != size or isinstance(f.ctype, Struct):
                if st.union:
                    continue
                return None
            try:
                k = self.kind(f.ctype)
            except Exception:  # noqa: BLE001
                return None
            if (k == "f") != (want == "f"):
                if st.union:
                    continue
                return None
            return [(seg, f.ctype, f.family)]
        return None


def _through_const(e, job, types):
    """Whether the lvalue e is reached through a pointer to const (its address is then a pointer to const)."""
    while isinstance(e, (Member, Index)):
        inner = e.e if isinstance(e, Member) else e.a
        if isinstance(e, Index) or e.arrow:
            t = typeof(inner, job, types)
            if isinstance(t, str) and re.match(r"\s*const\b", t):
                return True
        e = inner
    return False


def same(t, u):
    return isinstance(t, str) and isinstance(u, str) and re.sub(r"\s+", "", t) == re.sub(r"\s+", "", u)


def typeof(e, job, types):
    if isinstance(e, Cast):
        return e.type
    if isinstance(e, Var):
        if e.name in types:
            return types[e.name]
        p = dict((n, t) for t, n in job.proto_params).get(e.name)
        return p if p is not None else job.global_types.get(e.name)
    if isinstance(e, (Member, Index)):
        from .cast import pr
        return job.etypes.get(pr(e))
    if isinstance(e, Unary) and e.op == "&":
        t = typeof(e.e, job, types)
        if not isinstance(t, str):
            return None
        return ("const " + t if _through_const(e.e, job, types) and not t.startswith("const") else t) + " *"
    from .cast import Call
    if isinstance(e, Call) and isinstance(e.fn, str):
        return job.callee_ret.get(e.fn) or job.spec.helper_types.get(e.fn)
    return None


ACCESS = {"u8": (1, "i"), "s8": (1, "i"), "u16": (2, "i"), "s16": (2, "i"), "u32": (4, "i"), "s32": (4, "i"),
          "f32": (4, "f")}


def _base(addr, job, types, tys):
    """(the struct, the expression naming it, '->' or '.', offset) of an address `(u32)P + off`, else None."""
    b, c = flow.linear(addr)
    if isinstance(b, Cast) and b.type == "u32":
        inner = b.e
        if isinstance(inner, Unary) and inner.op == "&":
            t = typeof(inner.e, job, types)
            st = tys.struct(t) if isinstance(t, str) else t if isinstance(t, Struct) else None
            return (st, inner.e, False, c) if st else None
        pt = typeof(inner, job, types)
        st = tys.pointee(pt)
        if st is None:
            return None
        if st.size and 0 < c < 0x80000000 and c >= st.size:
            # past the struct: an element of the array P points into, P[i]
            from .cast import pr
            i, c = divmod(c, st.size)
            ix = Index(inner, Num(i, str(i)))
            job.etypes[pr(ix)] = re.sub(r"\s*\*\s*$", "", re.sub(r"\b(?:volatile)\b", "", pt)).strip()
            return st, ix, False, c
        return st, inner, True, c
    if b is addr:   # P itself (a cast the tidy dropped): its first member
        st = tys.pointee(typeof(addr, job, types))
        return (st, addr, True, 0) if st else None
    return None


def fields(body, job, types):
    """One round of field recovery; whether anything changed."""
    from .cast import pr
    from .lift import all_stmts, map_exprs, rewrite
    tys = job.types
    changed = [False]

    def member(addr, t):
        hit = _base(addr, job, types, tys)
        if hit is None or t not in ACCESS:
            return None
        st, e, arrow, off = hit
        if off & 0x80000000:
            return None
        size, want = ACCESS[t]
        got = tys.find(st, off, size, want)
        if not got:
            return None
        path, mt, fam = got[0]
        x = e
        for k, (kind, v) in enumerate(path):
            if kind == ".":
                x = Member(x, v, arrow and k == 0)
            else:
                x = Index(x, Num(v, str(v)))
        if isinstance(mt, str):
            job.etypes[pr(x)] = mt
            if fam:
                job.families[pr(x)] = fam
        return x, mt

    def scalar_ptr(addr, t):
        """`*(T *)(u32)v` for a v of a pointer type to a scalar of T's size and kind: *v; `*(T *)((u32)v + k)`,
        k a multiple of that size: v[k / size]."""
        off = 0
        if isinstance(addr, Bin) and addr.op == "+" and flow.is_int(addr.b) and 0 < addr.b.v < 0x10000:
            addr, off = addr.a, addr.b.v
        if not (isinstance(addr, Cast) and addr.type == "u32") or t not in ACCESS:
            return None
        pt = typeof(addr.e, job, types)
        if not (isinstance(pt, str) and pt.strip().endswith("*")) or tys.pointee(pt) is not None:
            return None
        mt = pt.strip()[:-1].strip()
        try:
            k = tys.kind(mt)
        except Exception:  # noqa: BLE001
            return None
        size, want = ACCESS[t]
        if (k == "f") != (want == "f") or "*" in mt and size != 4 or \
                "*" not in mt and {"c": 1, "C": 1, "h": 2, "H": 2}.get(k, 4) != size:
            return None
        if re.sub(r"\b(?:const|volatile)\b", "", mt).strip() == "void" or off and (off % size or not same(mt, t)):
            return None  # no element of a void *; an element only of its own type (a cast is no store target)
        return (Unary("*", addr.e) if off == 0 else Index(addr.e, Num(off // size, str(off // size)))), mt

    def deref(e):
        if isinstance(e, Unary) and e.op == "*" and isinstance(e.e, Cast) and e.e.type.endswith("*"):
            t = e.e.type[:-1].strip()
            got = member(e.e.e, t) or scalar_ptr(e.e.e, t)
            if got is not None:
                changed[0] = True
                x, mt = got
                return x if same(mt, t) else Cast(t, x)
        return e

    def address(e):
        """`(u32)P + off` at the start of a member: `(u32)&P->member`."""
        if not (isinstance(e, Bin) and e.op == "+" and flow.is_int(e.b)):
            return e
        hit = _base(e, job, types, tys)
        if hit is None:
            return e
        st, x, arrow, off = hit
        path = tys.start(st, off)
        if not path:
            if off == 0 and isinstance(x, Index):
                changed[0] = True
                return Cast("u32", Unary("&", x))
            return e
        for k, (kind, v) in enumerate(path):
            x = Member(x, v[0], arrow and k == 0) if kind == "." else Index(x, Num(v[0], str(v[0])))
            if isinstance(v[1], str):
                job.etypes[pr(x)] = v[1]
        changed[0] = True
        return Cast("u32", Unary("&", x))
    for stmts, k, s in list(all_stmts(body.body)):
        if isinstance(s, Assign) and isinstance(s.lhs, Unary) and s.lhs.op == "*" and isinstance(s.lhs.e, Cast):
            t = s.lhs.e.type[:-1].strip()
            addr = rewrite(s.lhs.e.e, deref)
            got = member(addr, t)
            rhs = rewrite(s.rhs, deref)
            if got is not None:
                changed[0] = True
                x, mt = got
                stmts[k] = Assign(x, s.op, rhs if same(mt, t) else Cast(mt, rhs))
            else:
                stmts[k] = Assign(Unary("*", Cast(s.lhs.e.type, addr)), s.op, rhs)
    map_exprs(body.body, lambda e: rewrite(e, deref))
    map_exprs(body.body, lambda e: rewrite(e, address))
    return changed[0]


def _retype_ok(job, t):
    """A type a u32 local can take with every read cast back to u32 (a pointer, or a scalar integer type)."""
    if t.strip().endswith("*"):
        return True
    if t in ("u32", "f32"):
        return False
    try:
        return job.kind(t) in ("i", "c", "C", "h", "H")
    except Exception:  # noqa: BLE001
        return False


def _scale(e):
    """(x, k) when e is x * k built of <<, * and + over one expression x (k a constant), else (e, 1)."""
    if isinstance(e, Bin) and e.op == "<<" and flow.is_int(e.b) and e.b.v < 32:
        x, k = _scale(e.a)
        return x, k << e.b.v
    if isinstance(e, Bin) and e.op == "*" and flow.is_int(e.b):
        x, k = _scale(e.a)
        return x, k * e.b.v
    if isinstance(e, Bin) and e.op == "*" and flow.is_int(e.a):
        x, k = _scale(e.b)
        return x, k * e.a.v
    if isinstance(e, Bin) and e.op == "+":
        (x, k), (y, m) = _scale(e.a), _scale(e.b)
        if x == y:
            return x, k + m
    return e, 1


def elem_size(t, tys):
    """sizeof(*p) for a p of pointer type t, when the headers or the base types give it."""
    if not isinstance(t, str):
        return None
    t = re.sub(r"\b(?:const|volatile)\b", "", t).strip()
    if not t.endswith("*"):
        return None
    base = t[:-1].strip()
    if base.endswith("*"):
        return 4
    st = tys.struct(base)
    if st is not None:
        return st.size
    try:
        k = tys.kind(base)
    except Exception:  # noqa: BLE001
        return None
    return {"c": 1, "C": 1, "h": 2, "H": 2, "i": 4, "f": 4}.get(k)


def indexes(body, job, types):
    """`(u32)P + i * sizeof(*P)` (as the compiler scaled it: shifts and adds) is `(u32)&P[i]`; whether any was."""
    from .cast import pr
    from .lift import map_exprs, rewrite
    tys = job.types
    changed = [False]

    def one(e):
        if not (isinstance(e, Bin) and e.op == "+"):
            return e
        for p, other in ((e.a, e.b), (e.b, e.a)):
            if not (isinstance(p, Cast) and p.type == "u32") or flow.is_int(other):
                continue
            t = typeof(p.e, job, types)
            size = elem_size(t, tys)
            if not size:
                continue
            x, k = _scale(other)
            if k % size or (k == 1 and size != 1):
                continue
            i = x if k == size else Bin("*", x, Num(k // size, str(k // size)))
            if isinstance(i, Cast) and i.type in ("u32", "s32") and typeof(i.e, job, types) in ("u32", "s32", "int"):
                i = i.e
            ix = Index(p.e, i)
            job.etypes[pr(ix)] = re.sub(r"\b(?:const|volatile)\b", "", t).strip()[:-1].strip()
            changed[0] = True
            return Cast("u32", Unary("&", ix))
        return e
    map_exprs(body.body, lambda e: rewrite(e, one))
    return changed[0]


def pointer_locals(body, job, types):
    """Locals assigned only `(u32)E` of one pointer type (or 0) take that type; whether any did."""
    from .lift import all_stmts, map_exprs, rewrite
    defs = {}
    for _, _, s in all_stmts(body.body):
        if isinstance(s, Assign) and isinstance(s.lhs, Var):
            defs.setdefault(s.lhs.name, []).append(s)
    params = {n for _, n in job.proto_params}
    new = {}
    for v, ss in defs.items():
        if types.get(v) != "u32" or v in params:
            continue
        ptr = None
        for s in ss:
            r = s.rhs
            if s.op != "=":
                ptr = False
                break
            if flow.is_int(r) and r.v == 0:
                continue
            if isinstance(r, Cast) and r.type == "u32":
                t = typeof(r.e, job, types)
                if isinstance(t, str) and _retype_ok(job, t) and (ptr is None or same(ptr, t)):
                    ptr = t
                    continue
            ptr = False
            break
        if ptr and (ptr.strip().endswith("*") or not any(flow.is_int(s.rhs) for s in ss)):
            new[v] = ptr
    if not new:
        return False
    map_exprs(body.body, lambda e: rewrite(e, lambda x: Cast("u32", x) if isinstance(x, Var) and x.name in new
                                                 else x))
    for stmts, k, s in list(all_stmts(body.body)):
        if isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name in new:
            r = s.rhs
            zero = Var("NULL") if new[s.lhs.name].strip().endswith("*") else Num(0, "0")
            stmts[k] = Assign(s.lhs, "=", zero if flow.is_int(r) else r.e)
    types.update(new)
    return True


def constants(body, job, types):
    """Literals by the family name their member's comment or enum type gives."""
    from .cast import pr
    from .lift import all_stmts, map_exprs, rewrite
    values = job.named_values

    def family(e):
        if isinstance(e, Cast):
            e = e.e
        if isinstance(e, Bin) and e.op in ("&", "|", "^"):
            return family(e.a) or family(e.b)
        if isinstance(e, (Member, Index)):
            key = pr(e)
            fam = job.families.get(key)
            if fam:
                return ("prefix", fam)
            en = job.types.enum_names(job.etypes.get(key))
            if en:
                return ("set", en)
        return None

    def name(fam, v):
        def ok(n):
            return n.startswith(fam[1]) if fam[0] == "prefix" else n in fam[1]
        one = [n for n in values.get(v, ()) if ok(n)]
        if len(one) == 1:
            return Var(one[0])
        if fam[0] != "prefix" or v <= 0 or v & (v - 1) == 0:
            return None
        parts, bit = [], 1
        while bit <= v:
            if v & bit:
                got = [n for n in values.get(bit, ()) if ok(n)]
                if len(got) != 1:
                    return None
                parts.append(Var(got[0]))
            bit <<= 1
        out = parts[0]
        for p in parts[1:]:
            out = Bin("|", out, p)
        return out

    def lit(x, fam):
        if fam is None:
            return x
        if flow.is_int(x) and x.v not in (0,):
            return name(fam, x.v) or x
        if isinstance(x, Unary) and x.op == "~" and flow.is_int(x.e):
            n = name(fam, x.e.v)
            return Unary("~", n) if n is not None else x
        return x

    from .. import draft_gate
    from .cast import Call
    from .usage import usage
    use = usage(values) if getattr(job, "addr", None) else None

    def by_use(site, x):
        """A literal the other readable files name at this site (core/rtx/usage), else x."""
        inner = x.e if isinstance(x, Cast) else x
        if use is None or not flow.is_int(inner) or inner.v in draft_gate.TRIVIAL or inner.v not in values:
            return x
        n = use.name(site, inner.v, job.addr)
        return x if n is None else Cast(x.type, Var(n)) if isinstance(x, Cast) else Var(n)

    def member_of(e):
        while isinstance(e, Cast):
            e = e.e
        return e.name if isinstance(e, Member) else None

    def mask(e):
        """x & 0xff (0xffff), a named value: (u32)(u8)x, the same bits."""
        if isinstance(e, Bin) and e.op == "&" and flow.is_int(e.b) and e.b.v in (0xFF, 0xFFFF) and e.b.v in values:
            return Cast("u32", Cast("u8" if e.b.v == 0xFF else "u16", e.a))
        return e

    def c(e):
        if isinstance(e, Bin) and e.op in ("&", "|", "^", "==", "!="):
            e = Bin(e.op, lit(e.a, family(e.b)), lit(e.b, family(e.a)))
            ma, mb = member_of(e.a), member_of(e.b)
            if e.op in ("==", "!=") and (ma or mb):
                e = Bin(e.op, by_use(("member", mb), e.a) if mb else e.a, by_use(("member", ma), e.b) if ma else e.b)
            return mask(e)
        if isinstance(e, Call) and isinstance(e.fn, str):
            pts = job.callee_params.get(e.fn, []) if hasattr(job, "callee_params") else []
            args = []
            for k, a in enumerate(e.args):
                en = job.types.enum_names(pts[k]) if k < len(pts) else None
                a = lit(a, ("set", en)) if en else a
                args.append(by_use(("arg", e.fn, k), a))
            return Call(e.fn, args)
        return e
    map_exprs(body.body, lambda e: rewrite(e, c))
    for stmts, k, s in list(all_stmts(body.body)):
        if isinstance(s, Assign) and isinstance(s.lhs, (Member, Index)):
            rhs = lit(s.rhs, family(s.lhs))
            m = member_of(s.lhs)
            stmts[k] = Assign(s.lhs, s.op, by_use(("member", m), rhs) if m and s.op == "=" else rhs)

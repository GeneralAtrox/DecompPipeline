"""Big-endian guest memory in readable C (Saturn SH-2, GameCube PowerPC): the rules that keep every guest-memory
value behind rd_be.h's be_ types.

The readable runtime of a big-endian target maps guest RAM at its own address with the guest's byte order, so a
guest pointer is a host pointer but a plain `u32` read through it would read the bytes the wrong way round. Readable
C therefore reads and writes every multi-byte guest-memory value through a be_ type (be_u16, be_s32, be_f32, be_f64,
BE_PTR(T), be_fnptr, ...: rd_be.h), with be_get and be_set; a plain use of one is a compile error. Locals,
parameters and results are plain host values.

- BE1 (headers: types.h, sys/*.h, the registry's globals and prototypes): a struct or union member, or a named
  global, of a plain multi-byte scalar type (u16, s32, f32, f64, an enum, ...), a plain pointer (use BE_PTR(T) or
  be_fnptr) or a bit-field (the host's bit order is not the guest's). A prototype parameter or result that points at
  a plain multi-byte scalar or at a pointer (it would point into guest memory: use the be_ type).
- BE2 (readable source): a pointer to a plain multi-byte scalar or to a plain pointer, declared or cast (`u32 *p`,
  `(f32 *)x`, `T **`): such a pointer would read guest bytes in host order. Point at the be_ type instead.

The judge itself does not depend on these rules: a value read the wrong way round changes the function's behavior
and fails the comparison. They make the mistake a clear message instead of a wrong value, and keep readable C
portable to the native port, which keeps guest data in its original layout.
"""
import re

from . import lint

# Plain C scalar types wider than one byte (after typedefs are resolved): read through a be_ type in guest memory.
WIDE = {"u16", "s16", "u32", "s32", "u64", "s64", "f32", "f64", "short", "signed short", "unsigned short", "int",
        "signed int", "unsigned int", "unsigned", "signed", "long", "unsigned long", "signed long", "long long",
        "unsigned long long", "float", "double", "uint16_t", "int16_t", "uint32_t", "int32_t", "uint64_t", "int64_t",
        "uintptr_t", "intptr_t", "size_t", "ptrdiff_t"}
NARROW = {"u8", "s8", "char", "signed char", "unsigned char", "uint8_t", "int8_t", "void"}


def _norm(t):
    t = re.sub(r"\b(const|volatile|static|extern|register)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _resolve(t, typedefs, depth=0):
    """The basic type a typedef name stands for, 'struct'/'union'/'enum', or a pointer type ('... *')."""
    t = _norm(t)
    while depth < 20 and t in typedefs:
        t, depth = _norm(typedefs[t]), depth + 1
    return t


def wide_scalar(t, typedefs):
    """Whether a type is a plain multi-byte scalar (an enum is an int) or a plain pointer."""
    r = _resolve(t, typedefs)
    return "*" in r or r in WIDE or r == "enum" or r.startswith("enum ")


def _members(body):
    """(declaration text) of each member of a struct/union body, nested bodies flattened."""
    body = re.sub(r"\{[^{}]*\}", " ", body) if body.count("{") else body
    return [m.strip() for m in body.split(";") if m.strip()]


def _bodies(src):
    """(name, body) of each struct/union definition in a header, outermost first (nested ones as their own)."""
    out = []
    for m in re.finditer(r"\b(struct|union)\b\s*(\w*)\s*\{", src):
        depth, i = 0, m.end() - 1
        while i < len(src):
            depth += {"{": 1, "}": -1}.get(src[i], 0)
            if depth == 0:
                break
            i += 1
        inner = src[m.end():i]
        while "{" in inner:  # a nested struct/union body: checked as its own definition
            inner = re.sub(r"\b(struct|union)\b\s*\w*\s*\{[^{}]*\}", r"\1 _nested", inner)
        out.append((m.group(2) or "(anonymous)", inner))
    return out


def _decl_problem(decl, typedefs):
    """Why a member or global declaration holds a plain guest value, or None."""
    d = _norm(re.sub(r"\[[^\]]*\]", "", decl))
    if ":" in d:
        return "a bit-field (the host's bit order is not the guest's): use a be_ field and masks"
    if re.search(r"\(\s*\*", d):
        return "a plain function pointer: use be_fnptr"
    m = re.fullmatch(r"(.+?)\s*\b([A-Za-z_]\w*)", d)
    if not m:
        return None
    t = m.group(1).strip()
    if "*" in t:
        return "a plain pointer: use BE_PTR(T)"
    if t.startswith(("struct ", "union ")) or t.startswith("be_") or t == "BE_PTR":
        return None
    if wide_scalar(t, typedefs):
        return f"a plain {t}: use the be_ type of the same size (be_u16, be_s32, be_f32, ...)"
    return None


def _member_name(decl):
    """The name a member declaration declares (`T *link`, `void (*cb)(void)`, `be_u32 flags : 3`, `u8 b[4]`)."""
    m = re.search(r"\(\s*\*\s*(\w+)\s*\)", decl)
    if m:
        return m.group(1)
    names = re.findall(r"[A-Za-z_]\w*", re.sub(r"\[[^\]]*\]|:\s*\w+\s*$", "", decl))
    return names[-1] if names else decl


def header_lint(header_texts, typedefs, globals_decls=(), protos=()):
    """BE1 problems of the readable headers. header_texts: {file name: text} (types.h, sys/*.h); typedefs: the
    registry's (readable.parse_typedefs); globals_decls: the registry's global declarations; protos: its prototypes."""
    errors = []
    for fname, text in header_texts.items():
        src = lint.strip_comments(text)
        for name, body in _bodies(src):
            for member in _members(body):
                if member.startswith(("struct _nested", "union _nested")):
                    continue
                why = _decl_problem(member, typedefs)
                if why:
                    errors.append(f"BE1: {fname}: {name}.{_member_name(member)} is {why}")
    for decl in globals_decls:
        why = _decl_problem(decl.rstrip(";"), typedefs)
        if why:
            errors.append(f"BE1: global `{decl}` is {why}")
    for proto in protos:
        from .readable import parse_proto
        ret, name, params = parse_proto(proto)
        for t, pname in [(ret, "its result"), *[(t, n) for t, n in params]]:
            why = pointer_problem(t, typedefs)
            if why:
                errors.append(f"BE1: {name}: {pname} `{t}` {why}")
    return sorted(set(errors))


def pointer_problem(t, typedefs):
    """Why a pointer type points at plain guest data (a pointer to a wide scalar or to a pointer), or None."""
    t = _norm(t)
    if "*" not in t:
        return None
    target = _norm(t[:t.rindex("*")])
    if "*" in target:
        return "points at a plain pointer in guest memory: point at a BE_PTR(T) (or be_fnptr)"
    if target.startswith(("struct ", "union ")) or target.startswith("be_") or target in NARROW:
        return None
    if wide_scalar(target, typedefs):
        return f"points at a plain {target} in guest memory: point at its be_ type"
    return None


# A pointer declarator or cast: a type name, then one or more `*` (`u32 *p`, `(f32 *)x`, `const s16 **q`).
_PTR = re.compile(r"\b((?:(?:const|volatile|unsigned|signed|struct|union|enum)\s+)*[A-Za-z_]\w*)"
                  r"\s*((?:\*\s*(?:const\s*)?)+)")


def source_lint(src, typedefs):
    """BE2 problems of a readable source file."""
    code = re.sub(r'"(\\.|[^"\\])*"', '""', lint.strip_comments(src))
    errors = []
    for m in _PTR.finditer(code):
        base, stars = _norm(m.group(1)), m.group(2).count("*")
        # Only a type name followed by `*` is a pointer type (`a * b`, a product, names no type).
        if base in typedefs or base in WIDE or base in NARROW or base.startswith(("struct ", "union ", "be_")):
            t = base + " *" * stars
            why = pointer_problem(t, typedefs)
            if why:
                errors.append(f"BE2: `{t}` {why} (read and write it with be_get / be_set)")
    return sorted(set(errors))


def registry_lint(reg, include_dir):
    """BE1 on the registry's headers (types.h, sys/*.h under include_dir), globals and prototypes."""
    texts = {} if reg.isolated else {p.name: p.read_text(encoding="utf-8", errors="replace")
                                     for p in [include_dir / "types.h", *sorted((include_dir / "sys").glob("*.h"))]
                                     if p.exists()}
    return header_lint(texts, reg.typedefs, [e["decl"] for e in reg.globals.values()],
                       [e["proto"] for e in reg.functions.values()])


def unmapped_lint(ranges, raw_text, harness):
    """A function whose original touches guest memory the readable build cannot map at its own address (target.json
    judge_memory "unmapped": the Saturn's low work RAM, 0x00200000, which Windows keeps for itself): its raw C names
    an address there, or its test vectors set memory there (M lines) or expect a write there (W lines). Refused with
    that reason, not judged: its readable build would fault on the first access."""
    def inside(v):
        return any(lo <= v < hi for lo, hi in ranges)
    hits = {int(m, 16) for m in re.findall(r"\b0[xX]([0-9a-fA-F]{8})", raw_text or "") if inside(int(m, 16))}
    for name in ("vectors.txt", "expected.txt"):
        f = harness / name
        if f.exists():
            hits |= {int(m, 16) for m in re.findall(r"^[MW] ([0-9a-fA-F]{8})\b", f.read_text(errors="replace"), re.M)
                     if inside(int(m, 16))}
    if not hits:
        return []
    return [f"BE3: the original uses guest memory at {', '.join(f'0x{v:08x}' for v in sorted(hits)[:3])}"
            f"{', ...' if len(hits) > 3 else ''}, which the readable build cannot map at its own address (Windows "
            "keeps it): this function stays raw C for now"]


def target_lint(reg, src, raw_text, harness):
    """BE1-BE3 for a big-endian target's readable file (core/readable_check.check_readable); [] on a little-endian
    target (the Dreamcast's guest memory is in host order)."""
    from .compare import UNMAPPED, WORD
    if WORD != ">I":
        return []
    from .readable import INCLUDE
    return registry_lint(reg, INCLUDE) + source_lint(src, reg.typedefs) + unmapped_lint(UNMAPPED, raw_text, harness)

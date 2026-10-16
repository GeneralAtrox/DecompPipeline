"""What a lean sandbox (core/readable_lean) holds beyond what raw.c names (batch 24).

- uses(): the names the function's current readable file (and the work.c an attempt starts from) uses: registry
  functions and globals, and header types, constants and macros. A redo then has every name its file already uses,
  and `refine.py verify --lean` proves each file builds against the sandbox it would get. Before batch 24 the names
  designers added after raw.c (a constant for a magic number, a type for a cast, a string global) never reached the
  sandbox: 64 TC files did not build against it.
- values() and named(): the integer value of every header constant (a #define with a constant expression, an enum
  value, explicit or counted), and the constants whose value raw.c writes as a literal, so a fresh agent can name
  the number instead of copying it (named_constants picks the relevant ones).

Pure text work on readable_headers' items; nothing here is project-specific.
"""
import re

from . import readable_headers
from .raw_names import decl_name, mask, proto_name

IDENT = re.compile(r"[A-Za-z_]\w*")
_CAST = re.compile(r"\(\s*(?:const\s+)?(?:unsigned|signed|int|long|short|char|u8|u16|u32|s8|s16|s32|size_t)"
                   r"(?:\s+int)?\s*\)")
_NUMBER = re.compile(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\b")
_SAFE = re.compile(r"[\d\s()+\-*/%<>|&^~]*")


def code_idents(text):
    """The identifiers C text uses in code (comments, strings and character literals left out)."""
    return set(IDENT.findall(mask(text)[0]))


def registry_names(reg):
    """({function name: addr}, {global name: addr}) of the names registry (cached on it)."""
    got = getattr(reg, "_lean_names", None)
    if got is None:
        fns = {proto_name(e.get("proto") or ""): a for a, e in reg.functions.items()}
        gls = {decl_name(e.get("decl") or ""): a for a, e in reg.globals.items()}
        fns.pop(None, None)
        gls.pop(None, None)
        got = reg._lean_names = (fns, gls)
    return got


def uses(reg, hs, text):
    """(function addrs, global addrs, header refs) a readable file names: registry functions and globals, and the
    header items (by name, struct/union/enum tags as 'struct:Tag') it uses."""
    if not text:
        return set(), set(), set()
    code = mask(text)[0]
    ids = set(IDENT.findall(code))
    fns, gls = registry_names(reg)
    refs = {r for r in readable_headers.refs_of(code) if r in hs.by_name or r in hs.forward}
    return {fns[n] for n in ids if n in fns}, {gls[n] for n in ids if n in gls}, refs


def _eval(expr, known):
    """The integer value of a constant C expression (casts, suffixes, other constants by name), else None."""
    expr = _CAST.sub(" ", readable_headers.split_comments(expr)[0]).strip()
    if not expr or '"' in expr or "'" in expr or "." in expr:
        return None
    unknown = False

    def name(m):
        nonlocal unknown
        if m.group(0) in known:
            return f"({known[m.group(0)]})"
        unknown = True
        return "0"
    expr = re.sub(r"(?<![\w])[A-Za-z_]\w*", name, _NUMBER.sub(lambda m: str(int(m.group(1), 0) if m.group(1)[:2].lower()
                                                                             == "0x" else int(m.group(1))), expr))
    if unknown or not _SAFE.fullmatch(expr) or "**" in expr or "//" in expr:
        return None
    try:
        v = eval(expr.replace("/", "//"), {"__builtins__": {}}, {})  # digits and operators only (_SAFE)
    except (ArithmeticError, SyntaxError, TypeError, ValueError):
        return None
    return v if isinstance(v, int) and -2 ** 31 <= v < 2 ** 32 else None


def _members(it):
    """[(name, value text or None)] of an enum item, in order."""
    body = re.search(r"\{(.*)\}", readable_headers.split_comments(it.text)[0], re.S)
    out = []
    for part in (body.group(1).split(",") if body else ()):
        m = re.match(r"\s*([A-Za-z_]\w*)\s*(?:=\s*(.+?))?\s*$", part, re.S)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def values(hs):
    """{constant name: (int value, item)} of the headers (cached on them): object-like #defines whose value is a
    constant expression, and enum values (explicit, or counted from the previous one)."""
    got = getattr(hs, "_lean_values", None)
    if got is not None:
        return got
    known, out = {}, {}
    for it in hs.items:
        if it.kind == "define":
            for m in re.finditer(r"^\s*#\s*define\s+([A-Za-z_]\w*)(?!\()[ \t]*(.*)$",
                                 readable_headers.split_comments(it.text)[0], re.M):
                v = _eval(m.group(2), known)
                if v is not None:
                    known[m.group(1)], out[m.group(1)] = v, (v, it)
        elif re.match(r"\s*(?:typedef\s+)?enum\b", readable_headers.split_comments(it.text)[0]):
            nxt = 0
            for n, text in _members(it):
                v = nxt if text is None else _eval(text, known)
                if v is None:
                    break
                known[n], out[n], nxt = v, (v, it), v + 1
    hs._lean_values = out
    return out


def value_key(v):
    """A value as raw.c's literals are compared (readable_lean.int_literals: 32-bit)."""
    return v & 0xFFFFFFFF


def named(hs, evidence, minimum=0x10):
    """{constant name: item} of the constants whose value raw.c writes (evidence: readable_lean.int_literals), with
    a value of at least `minimum` (a smaller one is in too many unrelated constants to mean anything alone)."""
    return {n: it for n, (v, it) in values(hs).items()
            if value_key(v) in evidence and minimum <= value_key(v) <= 0xFFFFFFFF - minimum}


def unique(hs, evidence, minimum=0x10):
    """The items of the constants whose value raw.c writes and no other header constant has (a value of at least
    `minimum`): the number names that one constant, so the sandbox's types.h holds it (on TC 349 such constants
    across 2229 functions, about 40 bytes of types.h per function)."""
    got = getattr(hs, "_lean_count", None)
    if got is None:  # built whole, then published: verify's worker threads share the parsed headers
        got = {}
        for v, _ in values(hs).values():
            got[value_key(v)] = got.get(value_key(v), 0) + 1
        hs._lean_count = got
    return {it for n, it in named(hs, evidence, minimum).items() if got[value_key(values(hs)[n][0])] == 1}

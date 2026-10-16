"""C text helpers for sync-names (core/sync_names.py). Every function reads masked code (core/raw_names.mask:
comments and literals blanked), so brackets and identifiers inside comments never count; offsets are the text's."""
import re

from . import readable


class SyncRefused(Exception):
    pass


def close_of(code, i):
    """Index of the bracket closing the one at code[i]."""
    pairs, depth = {"(": ")", "[": "]", "{": "}"}, []
    for k in range(i, len(code)):
        c = code[k]
        if c in pairs:
            depth.append(pairs[c])
        elif c in ")]}":
            if not depth or depth.pop() != c:
                raise SyncRefused(f"unbalanced brackets near offset {k}")
            if not depth:
                return k
    raise SyncRefused(f"unclosed bracket at offset {i}")


def open_of(code, k):
    """Index of the '(' that the ')' at code[k] closes."""
    depth = 0
    for i in range(k, -1, -1):
        depth += {")": 1, "(": -1}.get(code[i], 0)
        if depth == 0:
            return i
    raise SyncRefused(f"unopened ')' at offset {k}")


def split_args(code, s, e):
    """[(start, end)] of the comma-separated arguments in code[s:e], whitespace trimmed."""
    if not code[s:e].strip():
        return []
    out, depth, start = [], 0, s
    for k in range(s, e):
        c = code[k]
        depth += c in "([{"
        depth -= c in ")]}"
        if c == "," and depth == 0:
            out.append((start, k))
            start = k + 1
    out.append((start, e))
    return [(a + len(code[a:b]) - len(code[a:b].lstrip()), b - len(code[a:b]) + len(code[a:b].rstrip()))
            for a, b in out]


def unary(text):
    """Whether text is one postfix/unary expression (a cast can go in front of it without parentheses)."""
    t, i = text.strip(), 0
    while i < len(t) and t[i] in "&*!~ ":
        i += 1
    if i < len(t) and t[i] == "(":
        i = close_of(t, i) + 1
    else:
        m = re.match(r"[A-Za-z_]\w*|\d[\w.]*", t[i:])
        if not m:
            return False
        i += m.end()
    while i < len(t):
        rest = t[i:]
        m = re.match(r"\s*(\.|->)\s*[A-Za-z_]\w*", rest)
        if m:
            i += m.end()
        elif rest.lstrip()[:1] in ("[", "("):
            i = close_of(t, i + len(rest) - len(rest.lstrip())) + 1
        else:
            return False
    return True


WHOLE_CAST = re.compile(r"\(\s*u32\s*\)\s*\(\s*uintptr_t\s*\)\s*")
CAST = re.compile(r"\(\s*(?:const\s+|volatile\s+)*[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*(?:\s*\*\s*(?:const\s*)?)*\s*\)\s*")


def norm_type(t):
    return re.sub(r"\s+", " ", t.replace("*", " * ")).strip()


def is_ptr(reg, t):
    t = re.sub(r"\b(const|volatile)\b", "", t).strip()
    for _ in range(8):
        if "*" in t:
            return True
        if t not in reg.typedefs:
            return False
        t = reg.typedefs[t].strip()
    return False


def conv(reg, expr, src_t, dst_t, mode):
    """expr (of type src_t) as a dst_t: unchanged, cast, or stripped of a (u32)(uintptr_t) cast. SyncRefused when an
    f32 meets an integer (that would convert the value, not move the bits)."""
    if norm_type(src_t) == norm_type(dst_t):
        return expr
    try:
        sk, dk = reg.kind(src_t), reg.kind(dst_t)
    except readable.RegistryError as e:
        raise SyncRefused(str(e))
    if (sk == "f") != (dk == "f") or "v" in (sk, dk):
        raise SyncRefused(f"`{src_t}` and `{dst_t}` share a storage but one is a float or void")
    sp, dp = is_ptr(reg, src_t), is_ptr(reg, dst_t)
    m = WHOLE_CAST.match(expr)
    inner = expr[m.end():] if m and unary(expr[m.end():]) else None
    if mode == "clean":
        if not dp or inner is None:
            return expr
        t = inner.strip()  # `(buffer + 0x300)` as an argument or a result needs no parentheses
        if t.startswith("(") and close_of(t, 0) == len(t) - 1 and not split_args(t, 1, len(t) - 1)[1:]:
            return t[1:-1].strip()
        return inner
    if sk == dk and not sp and not dp:
        return expr  # two 32-bit integers (or the same small one): no cast needed
    if dp and inner is not None:
        return f"({dst_t}){inner}"
    body = expr if unary(expr) else f"({expr})"
    return f"({dst_t})(uintptr_t){body}" if sp != dp else f"({dst_t}){body}"


def statement_call(code, s, e):
    """Whether the call code[s:e] is a whole expression statement (its result unused)."""
    after = code[e:].lstrip()[:1]
    before = code[:s].rstrip()
    if after != ";":
        return False
    if not before or before[-1] in ";{}:" or re.search(r"\belse$", before):
        return True
    if before[-1] == ")":
        return bool(re.search(r"\b(if|while|for)\s*$", before[:open_of(before, len(before) - 1)]))
    return False


def decl_start(code, pos):
    """Start of the top-level declaration whose name is at pos: after the previous ';', '}' or preprocessor line."""
    pp = list(re.finditer(r"^[ \t]*#[^\n]*", code[:pos], re.M))
    start = max(code.rfind(";", 0, pos), code.rfind("}", 0, pos), pp[-1].end() - 1 if pp else -1) + 1
    return start + len(code[start:pos]) - len(code[start:pos].lstrip())


def exported(code):
    """(header start, ')' index, '{' index, '}' index) of the one top-level function definition that is not static,
    or None (none, or more than one: the lint reports that)."""
    found, i = [], 0
    while True:
        j = code.find("{", i)
        if j < 0:
            break
        end = close_of(code, j)
        before = code[:j].rstrip()
        if before.endswith(")"):
            close = len(before) - 1
            name = re.search(r"([A-Za-z_]\w*)\s*$", code[:open_of(code, close)])
            if name:
                start = decl_start(code, name.start(1))
                if not re.match(r"static\b", code[start:]):
                    found.append((start, close, j, end))
        i = end + 1
    return found[0] if len(found) == 1 else None


def pure_of(init, name):
    """Whether init is name under casts and parentheses only (a value-preserving conversion of it)."""
    t = init.strip()
    for _ in range(12):
        m = CAST.match(t)
        if m:
            t = t[m.end():].strip()
        elif t.startswith("(") and close_of(t, 0) == len(t) - 1:
            t = t[1:-1].strip()
        else:
            break
    return t == name


def modified(code, name):
    """Whether code assigns name, increments it or takes its address."""
    n = re.escape(name)
    return bool(re.search(rf"(?<![.>\w]){n}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|(?:\+\+|--)\s*{n}\b|\b{n}\s*(?:\+\+|--)"
                          rf"|&\s*{n}\b", code))


def line_span(code, s, e):
    """(s, e) widened to whole lines when the span is alone on its line(s)."""
    ls = code.rfind("\n", 0, s) + 1
    le = code.find("\n", e)
    le = len(code) if le < 0 else le
    if code[ls:s].strip() == "" and code[e:le].strip() == "":
        return ls, min(le + 1, len(code))
    return s, e


def adopter(code, open_, end, old, new_t):
    """A local of the body code[open_+1:end] that holds the old parameter converted to new_t: `T x = conv(old);` or
    `T x;` and, at its first use, `x = conv(old);`, both directly in the function's block, T the new type, old never
    assigned. Returns (x, [(start, end, kind)] of the initializer or assignment right side ("init") and of the
    statements ("decl", "assign")), or None."""
    body = code[open_ + 1:end]
    if modified(body, old):
        return None
    for m in re.finditer(r"(?<=[;{}])\s*((?:const\s+)?[A-Za-z_]\w*(?:\s*\*\s*(?:const\s*)?|\s+)+)([A-Za-z_]\w*)\s*(=|;)",
                         code[open_:end]):
        s = open_ + m.start(1)
        if code[:s].count("{") - code[:s].count("}") != code[:open_ + 1].count("{") - code[:open_ + 1].count("}"):
            continue  # not directly in the function's block
        t, x = m.group(1).strip(), m.group(2)
        if norm_type(t) != norm_type(new_t) or re.match(r"(return|else|goto|case)\b", t):
            continue
        first = re.search(rf"(?<![.>\w]){re.escape(x)}\b", code[open_ + 1:end])
        if not first or open_ + 1 + first.start() != open_ + m.start(2):
            continue  # x is used before its declaration (another x)
        if m.group(3) == "=":
            semi = _semi(code, open_ + m.end(3))
            if pure_of(code[open_ + m.end(3):semi], old):
                return x, [(open_ + m.end(3), semi, "init"), (*line_span(code, s, semi + 1), "decl")]
            continue
        nxt = re.compile(rf"(?<![.>\w]){re.escape(x)}\b").search(code, open_ + m.end(2), end)
        if not nxt or code[open_ + m.end():nxt.start()].count("{") != code[open_ + m.end():nxt.start()].count("}"):
            continue
        a = re.compile(rf"{re.escape(x)}\s*=(?!=)").match(code, nxt.start())
        if not a or code[:nxt.start()].rstrip()[-1:] not in ";{}":
            continue
        semi = _semi(code, a.end())
        if pure_of(code[a.end():semi], old):
            return x, [(a.end(), semi, "init"), (*line_span(code, s, open_ + m.end()), "decl"),
                       (*line_span(code, nxt.start(), semi + 1), "assign")]
    return None


def _semi(code, i):
    while code[i] != ";":
        i = close_of(code, i) + 1 if code[i] in "([{" else i + 1
    return i

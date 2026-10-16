"""Rewrite a function's matched raw C for a new raw return type (core/sig_override.py). Mechanical, then judged:
the caller of rewrite() keeps the new text only when check.py passes it on the overridden harness.

Inside `KIND fn_ADDR(...) { ... }` only (helpers such as u2f keep their returns):
- the definition's return type becomes the new kind;
- to void: `return X;` becomes `return;` (X a plain name) or `(void)(X); return;` (X with a call);
- from void: the last statement, a call `(void)tc_call(...)` / `tc_call(...)` (a tail call), becomes
  `return (KIND)tc_call(...)` (`return tc_callf(...)` for f32);
- between u32/s32 and f32: `return fr0;` becomes `return (KIND)r0;` and `return r0;` / `return (T)r0;` becomes
  `return fr0;`, when the function keeps those register variables (register-style raw C).
Anything else returns None: the function is listed to be matched again.
"""
import re

INT = ("u32", "s32")


def _body(src, addr):
    """(definition match, index of the body's `{`, index after its matching `}`) or None."""
    m = re.search(r"^([A-Za-z_][\w ]*?\**)\s*\b(fn_" + addr + r")\s*\(", src, re.M)
    if not m:
        return None
    open_ = src.find("{", m.end())
    depth = 0
    for i in range(open_, len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return m, open_, i + 1
    return None


def _returns_to_void(body):
    def one(m):
        expr = m.group(1).strip()
        return "return;" if re.fullmatch(r"\(?\w+\)?", expr) else f"(void)({expr});\n    return;"
    return re.sub(r"\breturn\s+([^;]+);", one, body)


def _tail_to_value(body, kind):
    # a trailing comment on the call's line stays after the new statement
    calls = list(re.finditer(r"(?m)^(\s*)(?:\(void\)\s*)?(tc_callf?)\s*\(([^;]*)\)\s*;"
                             r"([ \t]*(?:/\*[^\n]*?\*/|//[^\n]*))?[ \t]*$", body))
    if not calls:
        return None
    last = calls[-1]
    rest = body[last.end():]
    if re.sub(r"/\*.*?\*/|//[^\n]*|\breturn\s*;|\s|}", "", rest, flags=re.S):
        return None  # the call is not the last statement
    fn = "tc_callf" if kind == "f32" else "tc_call"
    cast = f"({kind})" if kind == "s32" else ""
    stmt = f"{last.group(1)}return {cast}{fn}({last.group(3)});{last.group(4) or ''}"
    return body[:last.start()] + stmt + re.sub(r"\breturn\s*;", "", rest)


def _swap_reg(body, kind):
    if kind == "f32":
        pat, new = r"\breturn\s+(?:\(\s*[us]32\s*\)\s*)?r0\s*;", "return fr0;"
        need = r"\bf32\s+[^;]*\bfr0\b"
    else:
        pat, new = r"\breturn\s+fr0\s*;", f"return {'(s32)' if kind == 's32' else ''}r0;"
        need = r"\bu32\s+[^;]*\br0\b"
    if not re.search(pat, body) or not re.search(need, body):
        return None
    return re.sub(pat, new, body)


def rewrite(src, addr, old, new):
    """The matched C for raw ret `new` (was `old`), or None when it cannot be rewritten mechanically."""
    b = _body(src, addr)
    if not b or old == new:
        return None if not b else src
    m, lo, hi = b
    body = src[lo:hi]
    if new == "void":
        nb = _returns_to_void(body)
    elif old == "void":
        nb = _tail_to_value(body, new)
    elif old in INT and new in INT:
        nb = re.sub(r"\breturn\s+\(\s*[us]32\s*\)\s*", "return ", body)
        nb = re.sub(r"\breturn\s+(?!;)", f"return ({new})", nb) if new == "s32" else nb
    else:
        nb = _swap_reg(body, new)
    if nb is None:
        return None
    head = src[:m.start(1)] + new + " " + src[m.start(2):lo]
    return head + nb + src[hi:]

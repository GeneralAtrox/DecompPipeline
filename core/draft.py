"""Machine drafts of readable C (batch 23): the matched raw C, rewritten mechanically with the registry's names.

The rewrite only does what is certain from the text and the registry:
- comments go (raw C's are assembly notes); `#include "tc_rt.h"` becomes `#include "game.h"`;
- a raw access at a named global's address becomes the name: rd32(0x8c0c6be0u) -> g_snapshot, wr32(0x8c0c6be0u, v)
  -> g_snapshot = v; an element of a named array of scalars by index; only when the access width is the
  declared type's (a pointer global is left alone: a u32 is no pointer);
- a read of the function's own literal pool becomes the constant (an f32 as its shortest exact literal);
- an address used as a whole value (assigned, passed, returned) becomes the function's name, or a named global's
  address (`&g_x`, or `g_x` for an array);
- tc_call/tc_callf of a known callee becomes a call by name, when every argument register the callee's prototype
  places (core/readable_abi) was set in the statements right before the call (`args.r[0] = E;`, side-effect free)
  or never set after its initializer; the TcArgs block goes when no call needs it any more.
Everything else stays as it is: raw memory access through pointers, register-named variables, gotos. Such a draft
still holds raw-runtime idioms, and the readable lints (RR2, RR3) refuse it: the draft never relaxes a lint.
The header and parameter names come from core/draft_run (it knows the prototype).
"""
import re

from .raw_names import mask

RW = re.compile(r"\b(rd8|rd16|rd32|rdf32|wr8|wr16|wr32|wrf32|tc_call|tc_callf)\s*\(")
WIDTH = {"8": "cC", "16": "hH", "32": "i", "f32": "f"}
SIZE = {"8": 1, "16": 2, "32": 4, "f32": 4}
ADDR = re.compile(r"\(?\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*(?:\+\s*(0[xX][0-9a-fA-F]+|\d+)[uU]?\s*)?\)?")
HEXLIT = re.compile(r"\b0[xX](8[cC][0-9a-fA-F]{6})[uU]?\b")
RW_OPEN = re.compile(r"\b(?:rd8|rd16|rd32|rdf32|wr8|wr16|wr32|wrf32|tc_callf?)\s*\(\s*\(?\s*$")


def strip_comments(text):
    """text without comments; a line that held only a comment goes too."""
    _, spans = mask(text)
    chars = list(text)
    for a, b in spans:  # blank the comment, newlines kept: the lines stay where they were
        for k in range(a, b):
            if chars[k] != "\n":
                chars[k] = " "
    lines, old = "".join(chars).split("\n"), text.split("\n")
    keep = [ln.rstrip() for ln, o in zip(lines, old) if ln.strip() or not o.strip()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep))


def close_paren(code, i):
    """Index of the ')' matching the '(' at code[i]."""
    depth = 0
    for j in range(i, len(code)):
        depth += {"(": 1, ")": -1}.get(code[j], 0)
        if depth == 0:
            return j
    return -1


def split_args(text):
    """Top-level comma-separated arguments of a call's argument text."""
    code, out, depth, last = mask(text)[0], [], 0, 0
    for i, c in enumerate(code):
        depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(c, 0)
        if c == "," and depth == 0:
            out.append(text[last:i])
            last = i + 1
    out.append(text[last:])
    return [a.strip() for a in out]


def rewrite_calls(text, handler):
    """text with each raw-API call replaced by handler(name, [argument text], code before, code after) (arguments
    rewritten first; before/after: the 2 characters of code around the call); a None answer keeps the call."""
    out, i = [], 0
    while True:
        code = mask(text)[0]
        m = RW.search(code, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        open_at = m.end() - 1
        close = close_paren(code, open_at)
        if close < 0:
            out.append(text[i:])
            return "".join(out)
        args = [rewrite_calls(a, handler) for a in split_args(text[open_at + 1:close])]
        rep = handler(m.group(1), args, code[max(0, m.start() - 3):m.start()].rstrip()[-2:],
                      code[close + 1:close + 4].lstrip()[:2])
        out.append(text[i:m.start()])
        out.append(rep if rep is not None else f"{m.group(1)}({', '.join(args)})")
        i = close + 1


def side_effect_free(expr):
    """No call, assignment, ++ or -- (a cast is no call: no name right before its parenthesis)."""
    e = mask(expr)[0]
    if re.search(r"\b(?!sizeof\b)[A-Za-z_]\w*\s*\(", e) or "++" in e or "--" in e:
        return False
    return not re.search(r"(?<![=!<>])=(?!=)", e)


class Names:
    """What a draft may name, from the registry: globals {addr int: (name, type, dims)}, functions {hex: name},
    kind (Registry.kind), extents [(base, size)] (core/global_extents, or None), pool {addr int: bytes reader},
    callees {hex: (name, [storage], result kind, variadic)}, raw_ok {hex}: raw names game.h declares (fn_ADDR)."""

    def __init__(self, globals_, functions, kind, extents=None, pool=None, callees=None, raw_ok=()):
        self.globals, self.functions, self.kind = globals_, functions, kind
        self.extents, self.pool, self.callees, self.raw_ok = extents, pool or {}, callees or {}, set(raw_ok)

    def global_at(self, v):
        """(base, (name, type, dims)) of the named global holding v."""
        if v in self.globals:
            return v, self.globals[v]
        for base, size in self.extents or ():
            if base <= v < base + max(size, 1) and base in self.globals:
                return base, self.globals[base]
        return None

    def access(self, width, v, pointer_ok=False):
        """The name of the scalar (or array element) read or written at v with this width, else None. A pointer
        global only when pointer_ok (no pointer arithmetic around it)."""
        hit = self.global_at(v)
        if not hit:
            return None
        base, (name, ctype, dims) = hit
        if ("*" in ctype and not pointer_ok) or len(dims) > 1:
            return None
        try:
            k = self.kind(ctype)
        except Exception:  # noqa: BLE001  (a struct or unknown type: not a scalar)
            return None
        if k not in WIDTH[width]:
            return None
        off = v - base
        if not dims:
            return name if off == 0 else None
        if off % SIZE[width] or (dims[0].isdigit() and off // SIZE[width] >= int(dims[0])):
            return None
        return f"{name}[{off // SIZE[width]}]"

    def value(self, v):
        """A name for the address v used as a value: a function, or a named global's start."""
        a = f"{v:08x}"
        if a in self.functions:
            return self.functions[a]
        if a in self.raw_ok:
            return f"fn_{a}"
        if v in self.globals:
            name, _, dims = self.globals[v]
            return name if dims else f"&{name}"
        return None


def _arith(before, after):
    """Whether the code around a read does arithmetic with it (u32 arithmetic is no pointer arithmetic)."""
    op = re.compile(r"[-+*/%^]|<<|>>|&(?!&)|\|(?!\|)")
    return bool(op.match(after) or re.search(r"(?:[-+*/%^]|<<|>>|(?<![&=!<>])&|(?<!\|)\|)$", before))


def rewrite_memory(text, names):
    """Accesses at named globals and literal-pool reads (the module doc)."""
    def handler(fn, args, before, after):
        if fn.startswith("tc_call"):
            return None
        width = fn[2:]
        m = ADDR.fullmatch(args[0])
        if not m:
            return None
        v = int(m.group(1), 16) + (int(m.group(2), 0) if m.group(2) else 0)
        name = names.access(width, v, pointer_ok=not (fn.startswith("rd") and _arith(before, after)))
        if fn.startswith("rd"):
            if name:
                hit = names.global_at(v)[1]
                return f"({'u8' if width == '8' else 'u16'}){name}" if names.kind(hit[1]) in "ch" else name
            return pool_value(names, width, v)
        if name and len(args) == 2:
            hit = names.global_at(v)[1]
            val = args[1]
            if width in ("8", "16"):
                val = f"({hit[1]})" + (val if re.fullmatch(r"\w+", val) else f"({val})")
            return f"{name} = {val}"
        return None
    return rewrite_calls(text, handler)


def rewrite_values(text, names):
    """Addresses used as whole values (assigned, passed, returned, initialized) by name; never the address operand of
    a raw access or a tc_call target left in the text (that stays raw: the lint refuses it)."""
    out = text
    code = mask(out)[0]
    edits = []
    for m in HEXLIT.finditer(code):
        if RW_OPEN.search(code[:m.start()]):
            continue
        before = code[:m.start()].rstrip()[-1:]
        after = code[m.end():].lstrip()[:1]
        if before not in ("=", ",", "(") or after not in (";", ",", ")"):
            continue
        if code[:m.start()].rstrip()[-2:] in ("==", "!=", "<=", ">="):
            continue
        nm = names.value(int(m.group(1), 16))
        if nm:
            edits.append((m.start(), m.end(), nm))
    for a, b, nm in reversed(edits):
        out = out[:a] + nm + out[b:]
    return out


def pool_value(names, width, v):
    """A literal-pool read as its constant (an address value by its name), else None."""
    read = names.pool.get(v)
    if read is None:
        return None
    bits = read(v, SIZE[width])
    if bits is None:
        return None
    if width == "f32":
        from .readable_context import f32_literal
        lit = f32_literal(bits)
        if lit.startswith("("):
            return None
        return f"({lit})" if lit.startswith("-") else lit
    if width == "32" and 0x8C000000 <= bits < 0x8D000000:
        return names.value(bits)
    return f"0x{bits:x}u"


from .draft_calls import rewrite_tc_calls  # noqa: E402,F401  (the tc_call pass)

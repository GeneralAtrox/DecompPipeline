"""Struct layouts of the project's readable headers, and projections of a struct onto the members one function needs
(batch 25: the lean sandbox's types.h was 112 KB at the TC median, mostly whole hub structs).

layout(hs, item) splits a struct's body into members (one top-level declaration each, with its comments) and computes
each member's offset, size and alignment as the readable judge's compiler lays them out (32-bit MSVC, /Zp8: natural
alignment, pointers and enums 4 bytes). project(lay, keep) renders the struct with only the kept members and
`u8 _pad_0xOFF[0xLEN];` arrays for the rest, so it keeps its size, alignment and the offset of every member it shows;
core/lean_proof proves that against the full definition with static asserts.

Anything the layout cannot size (a bitfield, a flexible array, an unknown type, an array length it cannot evaluate)
makes the struct unprojectable: it is shown whole, as before. Nothing here is project-specific.
"""
import re

from . import lean_uses, readable_headers
from .raw_names import mask

BASE = {"u8": 1, "s8": 1, "char": 1, "_Bool": 1, "u16": 2, "s16": 2, "short": 2, "u32": 4, "s32": 4, "int": 4,
        "long": 4, "f32": 4, "float": 4, "size_t": 4, "uintptr_t": 4, "intptr_t": 4, "ptrdiff_t": 4, "u64": 8,
        "s64": 8, "double": 8, "uint8_t": 1, "int8_t": 1, "uint16_t": 2, "int16_t": 2, "uint32_t": 4,
        "int32_t": 4, "uint64_t": 8, "int64_t": 8}
QUALIFIERS = {"const", "volatile", "signed", "unsigned", "register", "static", "extern"}
PACK = 8  # MSVC's default /Zp8
TOKEN = re.compile(r"[A-Za-z_]\w*|\[[^\]]*\]|\.\.\.|\S")


class Member:
    __slots__ = ("text", "names", "offset", "size", "align", "value_refs", "pointer_refs", "count")

    def __init__(self, text):
        self.text, self.names, self.offset, self.size, self.align, self.count = text, [], 0, 0, 1, 1
        self.value_refs, self.pointer_refs = set(), set()


class Layout:
    """head (the item's text up to and including '{'), members, tail ('}' to the end), size, align, union."""
    __slots__ = ("item", "head", "members", "tail", "size", "align", "union")


class Unsized(Exception):
    """A type or member whose size this module does not compute."""


def _close(code, i, op="{", cl="}"):
    depth = 0
    for j in range(i, len(code)):
        depth += (code[j] == op) - (code[j] == cl)
        if depth == 0:
            return j
    raise Unsized("unbalanced braces")


def _split_members(text, code):
    """[(member text, member code)] of a struct body: each top-level declaration with the comment lines above it, and
    its trailing comment (also one that goes on over the next lines). A comment after the last member is dropped."""
    out, start, depth, i = [], 0, 0, 0
    while i < len(code):
        c = code[i]
        depth += (c in "{(") - (c in "})")
        if c == ";" and depth == 0:
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            if code[i + 1:end].strip():  # another member on the same line (`{ u32 a; u32 b; }`)
                out.append((text[start:i + 1].strip("\n"), code[start:i + 1]))
                start = i = i + 1
                continue
            while True:  # a trailing comment that goes on: up to the line closing it
                tail = text[i + 1:end]
                opened = tail.rfind("/*") > tail.rfind("*/")
                if not opened:
                    break
                close = text.find("*/", end)
                end = len(text) if close < 0 else (text.find("\n", close) if text.find("\n", close) >= 0 else len(text))
            out.append((text[start:end].strip("\n"), code[start:end]))
            start, i = end + 1, end + 1
            continue
        i += 1
    return out


def _known(hs):
    got = getattr(hs, "_layout_known", None)
    if got is None:
        got = {n: v for n, (v, _) in lean_uses.values(hs).items()}
        hs._layout_known = got
    return got


def _dims(hs, arrays):
    n = 1
    for a in arrays:
        expr = a[1:-1].strip()
        v = lean_uses._eval(expr, _known(hs)) if expr else None
        if v is None or v < 0:
            raise Unsized(f"array length {a}")
        n *= v
    return n


def _ref(words):
    """The header name a base type refers to (`struct:Entity`, `EntityList`), or None for a C base type."""
    words = [w for w in words if w not in QUALIFIERS]
    if len(words) == 2 and words[0] in ("struct", "union", "enum"):
        return f"{words[0]}:{words[1]}"
    return words[0] if len(words) == 1 and words[0] not in BASE else None


def _type_info(hs, words, seen):
    """(size, align) of a base type given as words (`const struct Entity`, `u32`, `EntityList`)."""
    words = [w for w in words if w not in QUALIFIERS]
    if not words:  # `unsigned` alone
        return 4, 4
    if words[0] in ("struct", "union", "enum") and len(words) == 2:
        ref = f"{words[0]}:{words[1]}"
        if words[0] == "enum":
            return 4, 4
        it = hs.by_name.get(ref)
        lay = layout(hs, it, seen) if it is not None else None
        if lay is None:
            raise Unsized(ref)
        return lay.size, lay.align
    if len(words) > 1 and all(w in BASE or w == "long" for w in words):
        size = 8 if words.count("long") == 2 else max(BASE[w] for w in words)
        return size, min(size, PACK)
    if len(words) != 1:
        raise Unsized(" ".join(words))
    w = words[0]
    if w in BASE:
        return BASE[w], min(BASE[w], PACK)
    it = hs.by_name.get(w)
    if it is None or w in seen:
        raise Unsized(w)
    return _typedef_info(hs, it, w, seen | {w})


def _typedef_info(hs, it, name, seen):
    """(size, align) of the typedef name an item defines."""
    code = mask(it.text)[0]
    if re.match(r"\s*(?:typedef\s+)?enum\b", code):
        return 4, 4
    if it.kind != "typedef":
        raise Unsized(name)
    body = code.find("{")
    if body >= 0:  # typedef struct X {...} A, *PA, B[2];
        end = _close(code, body)
        lay = layout(hs, it, seen)
        if lay is None:
            raise Unsized(name)
        ptrs, arrays = _declarator(code[end + 1:].split(";")[0], name)
        return (4 if ptrs else lay.size) * _dims(hs, arrays), (4 if ptrs else lay.align)
    flat = " ".join(code.split()).rstrip(";").strip()
    if re.search(r"\(\s*\*\s*" + re.escape(name) + r"\s*\)", flat):
        return 4, 4  # a function pointer type
    words, toks = _words(TOKEN.findall(flat[len("typedef"):]))
    ptrs, arrays = _declarator(" ".join(toks), name)
    size, align = (4, 4) if ptrs else _type_info(hs, words, seen)
    return size * _dims(hs, arrays), align


def _words(toks):
    """(the base type's words, the declarator tokens) of a declaration's tokens."""
    words = []
    while toks and re.fullmatch(r"[A-Za-z_]\w*", toks[0]) and len(toks) > 1 and toks[1] not in ",;" \
            and not toks[1].startswith("["):
        words.append(toks.pop(0))
    return words, toks


def _declarator(text, name):
    """(pointer count, [array dims]) of the declarator of `name` in a comma-separated declarator list."""
    for part in _commas(text):
        toks = TOKEN.findall(part)
        if name in toks:
            return toks.count("*"), [t for t in toks if t.startswith("[")]
    raise Unsized(name)


def _commas(text):
    out, depth, last = [], 0, 0
    for i, c in enumerate(text):
        depth += (c in "([{") - (c in ")]}")
        if c == "," and depth == 0:
            out.append(text[last:i])
            last = i + 1
    return out + [text[last:]]


def _member(hs, m, text, raw_code, seen):
    """Fill m (names, size, align, refs) from one member declaration (text, and its code with comments blanked)."""
    code = raw_code.strip().rstrip(";").strip()
    if ":" in code:
        raise Unsized("bitfield")
    brace = code.find("{")
    if brace >= 0:  # struct/union {...} [declarators]: one member, inner names flattened
        if not re.fullmatch(r"(?:struct|union)\s*", code[:brace]):
            raise Unsized("a tagged type defined inside a struct")  # dropping it would drop the tag's definition
        end = _close(code, brace)
        inner = _layout_of(hs, None, raw_code, text, seen)
        m.names = [n for x in inner.members for n in x.names]
        for x in inner.members:
            m.value_refs |= x.value_refs
            m.pointer_refs |= x.pointer_refs
        rest = code[end + 1:].strip()
        if rest:
            names = [TOKEN.findall(p) for p in _commas(rest)]
            m.names = [next(t for t in reversed(p) if re.fullmatch(r"[A-Za-z_]\w*", t)) for p in names]
            m.size = inner.size * _dims(hs, [t for t in names[0] if t.startswith("[")])
        else:
            m.size = inner.size
        m.align = inner.align
        return
    fp = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\(", code)
    if fp:  # a function pointer member
        m.names, m.size, m.align = [fp.group(1)], 4, 4
        m.pointer_refs |= readable_headers.refs_of(code) - {fp.group(1)}
        return
    words, toks = _words(TOKEN.findall(code))
    ref, base = _ref(words), None
    first = None
    for part in _commas(" ".join(toks)):
        ptoks = TOKEN.findall(part)
        names = [t for t in ptoks if re.fullmatch(r"[A-Za-z_]\w*", t)]
        if len(names) != 1 or "(" in ptoks:
            raise Unsized(part)
        if ptoks.count("*"):
            s, a = 4, 4
        else:
            base = base or _type_info(hs, words, seen)
            s, a = base
        s *= _dims(hs, [t for t in ptoks if t.startswith("[")])
        m.names.append(names[0])
        if ref:
            (m.pointer_refs if ptoks.count("*") else m.value_refs).add(ref)
        if first is None:
            first = (s, a)
        elif first != (s, a):
            raise Unsized("mixed declarators")
    if first is None:
        raise Unsized(code)
    m.count = len(m.names)  # `u32 a, b;`: consecutive, no padding between them (same type)
    m.size, m.align = first[0] * m.count, first[1]


def _layout_of(hs, item, code_all, text_all, seen):
    brace = code_all.find("{")
    end = _close(code_all, brace)
    lay = Layout()
    lay.item, lay.head, lay.tail = item, text_all[:brace + 1], text_all[end:]
    lay.union = re.search(r"\bunion\s*(?:[A-Za-z_]\w*\s*)?$", code_all[:brace]) is not None
    lay.members, pos, align = [], 0, 1
    for text, code in _split_members(text_all[brace + 1:end], code_all[brace + 1:end]):
        if not code.strip():
            continue
        m = Member(text)
        _member(hs, m, text, code, seen)
        m.align = min(m.align, PACK)
        m.offset = 0 if lay.union else -(-pos // m.align) * m.align
        pos = max(pos, m.size // m.count) if lay.union else m.offset + m.size
        align = max(align, m.align)
        lay.members.append(m)
    if not lay.members:
        raise Unsized("empty")
    lay.size, lay.align = -(-pos // align) * align, align
    return lay


def layout(hs, item, seen=frozenset()):
    """The Layout of a struct or union item (cached on the headers), or None when it cannot be computed."""
    cache = hs.__dict__.setdefault("_lean_layouts", {}) if hasattr(hs, "__dict__") else {}
    if item.order in cache:
        return cache[item.order]
    code = mask(item.text)[0]
    head = code[:code.find("{")] if "{" in code else ""
    if not re.search(r"\b(struct|union)\b", head) or item.order in seen:
        return None
    try:
        lay = _layout_of(hs, item, code, item.text, seen | {item.order})
    except (Unsized, StopIteration, IndexError, ValueError):
        lay = None
    cache[item.order] = lay
    return lay


def pad_line(offset, length):
    return f"    u8 _pad_0x{offset:x}[0x{length:x}];"


def kept(lay, keep):
    """keep (member indexes), plus a most-aligned member when the kept ones would lower the struct's alignment."""
    keep = set(keep) if not lay.union else set(range(len(lay.members)))
    if max((lay.members[i].align for i in keep), default=1) < lay.align:
        keep.add(next(i for i, m in enumerate(lay.members) if m.align == lay.align))
    return keep


def body(lay, keep):
    """The member lines of a projection: the kept members (kept()) and the padding between them."""
    lines, pos = [], 0
    for i, m in enumerate(lay.members):
        if i not in keep:
            continue
        if m.offset > pos:
            lines.append(pad_line(pos, m.offset - pos))
        lines.append(m.text)
        pos = m.offset + m.size
    if lay.size > pos:
        lines.append(pad_line(pos, lay.size - pos))
    return lines


def project(lay, keep):
    """The struct's text with only the members whose indexes are in keep, and padding for the rest (same size,
    alignment and offsets of what it shows)."""
    return lay.head + "\n" + "\n".join(body(lay, kept(lay, keep))) + "\n" + lay.tail.lstrip()

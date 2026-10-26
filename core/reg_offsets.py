"""Member offsets raw.c reaches through a register (core/readable_lean.lean_set, after batch 25's added_offsets).

SH-4 has no displacement above 0x3c (0x7f for r0 forms), so the original loads a bigger member offset into r0 from
the literal pool (mov.w @(disp,PC),r0) and steps it: raw.c writes `r0 = 0x1018u; wr32(p + r0, v); r0 = r0 + 4u;
wr32(p + r0, w);`, or reads the pool word itself (`r0 = (u32)(s32)(s16)rd16(0x8c02f0b6u);`). Only the first value is
a literal (int_literals) and only the step is an added literal (added_offsets): the members at 0x101c, 0x1020, ...
were left out of the projected struct and Haiku wrote magic offsets (PS2 S04 8c02ec74, 8c02f022, 8c02ae40).

The original also forms a derived base and reads displacements from it: `r4 = r5 + 0x0eu; wr16(r4 + 4u, 0u);` is
member 0x12 of what r5 points at, a sum no literal holds (PS2 S03 8c05ef00, 8c05bb64 wrote `(u8 *)&g_flow + K`).

reached() follows each variable (a register or a named local: `off_ac`) through straight-line text: set to a
constant or a pool word, stepped (`x = x +/- K`, `x += K`), or derived from another (`y = x + K`). Every value it
holds where an rd/wr address adds it (`p + x`, `x + p`) is a member offset; an rd/wr at a derived base plus a
displacement (`rd16(y + 4u)`) is the derived offset plus it, or, from a constant address (`r5 = 0x0c54213cu`), an
address for lean_select.global_offsets. Any other write forgets the variable. Branches are not followed: an extra
value only shows one more member.
An rd/wr covers its width: `wr32(flow + 4u, 0u)` reaches the u8 members at 5, 6 and 7 too (Spawn S06 GameFlow's
bytes, cleared by two word stores, were padding). An address that adds literals in a row
(`wrf32(base + 0x12c + 0x1c, ..)`, TC 8c03c954) reaches their sums (+0x148); added_offsets counts each alone.
A derived base plus a literal taken as a value (stored, passed: `r8 = r14 + 0xa8u; wr32(sp + 4u, r8 + 0x1cu);`, the
address of GameObj.parts at 0xc4 in PS2 8c02e14e) is a member too; a variable's own definition is not.
A sum of literals, known values and variables sets a variable as a step does, in any order (`r14 = 0x38f8u + A`,
PS2 8c0b4bfe's BossBState), a pool word read inside it too. So an indexed array member is reached:
`addr = (u32)(s32)(s16)rd16(0x8c14b4f4u) + r14 + r13; rd32(addr)` (GameObj.parts[i] at 0xc4 in PS2 8c14b3b4, which
wrote `(u8 *)spark + 0xc4 + i * 4`) or `r4 = r11 + r14 + 0x8u` (Spawn 8c03bae0). The base is the one derived base
among the variables, or else an untracked pointer; the other variables are indexes, counted as 0 (the first element).
"""
import re

from .compare import IMAGE_BASE

NUM = r"(0[xX][0-9a-fA-F]+|\d+)[uU]?"
VAR = r"([A-Za-z_]\w*)"
DECL = r"^\s*(?:(?:const\s+)?[A-Za-z_]\w*\s+)?"  # `u32 r5 = ...;` declares and sets
SET = re.compile(rf"{DECL}{VAR}\s*=\s*{NUM}\s*;")
POOL = re.compile(rf"{DECL}{VAR}\s*=\s*(?:\(u32\)\(s32\)\(s(16)\)rd16|(rd32))"
                  r"\s*\(\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*\)\s*;")
INLINE = re.compile(r"\(u32\)\(s32\)\(s16\)rd16\s*\(\s*0[xX]([0-9a-fA-F]{8})[uU]?\s*\)")  # a mov.w pool word
TERM = r"(?:\(\w+\)\s*)?(?:[A-Za-z_]\w*|(?:0[xX][0-9a-fA-F]+|\d+)[uU]?)"
SUM = re.compile(rf"{DECL}{VAR}\s*=\s*({TERM}(?:\s*\+\s*{TERM})+)\s*;")
STEP = re.compile(rf"{DECL}{VAR}\s*=\s*(?:\(\w+\)\s*)?{VAR}\s*([+-])\s*{NUM}\s*;")
INC = re.compile(rf"^\s*{VAR}\s*([+-])=\s*{NUM}\s*;")  # `r2 += 8u;` (Spawn 8c0135a0 steps its stores so)
WRITE = re.compile(rf"{DECL}{VAR}\s*(?:=(?!=)|\+=|-=)")
ACCESS = re.compile(r"\b(?:rd|wr)(8|16|32|f32)\s*\(")
SIZE = {"8": 1, "16": 2, "32": 4, "f32": 4}
AT = re.compile(rf"^\s*(?:\(\w+\s*\*?\)\s*)?{VAR}\s*(?:\+\s*{NUM})?\s*$")  # `p`, `p + 4u`
TAIL = re.compile(rf"\+\s*{NUM}\s*$")  # an address ending in a literal: `p + 0x44u`
CONST = re.compile(r"^\s*(?:\(\w+\s*\*?\)\s*)?0[xX]([0-9a-fA-F]{8})[uU]?\s*$")
ADDED = (re.compile(r"\+\s*([A-Za-z_]\w*)\b"), re.compile(r"\b([A-Za-z_]\w*)\s*\+"))  # `p + x`, `x + p`
LIMIT = 0x100000  # an offset, not an address or a big constant
CHAIN = re.compile(r"(?:\+\s*(?:0[xX][0-9a-fA-F]+|\d+)[uU]?\s*){2,}(?![\w*/%<>.])")  # `+ 0x12c + 0x1c`
LIT = re.compile(r"0[xX][0-9a-fA-F]+|\d+")
VALUE = re.compile(rf"\b{VAR}\s*\+\s*{NUM}(?![\w*/%<>.\[(])")  # `r8 + 0x1cu` as a value: an address taken
KEYWORDS = {"return", "else", "case", "goto"}


def _int(text):
    return int(text, 16) if text[:2].lower() == "0x" else int(text)


def _pool(image, addr, size, signed):
    off = addr - IMAGE_BASE
    if image is None or not 0 <= off <= len(image) - size:
        return None
    v = int.from_bytes(image[off:off + size], "little")
    return v - 0x10000 if signed and v & 0x8000 else v


def _inline(image, m):
    v = _pool(image, int(m.group(1), 16), 2, True)
    return m.group(0) if v is None or v < 0 else f"0x{v:x}u"


def _sum(dst, terms, vals, based):
    """dst = a sum of literals, known values, a pointer (a derived base if one is) and indexes (counted as 0)."""
    k, ptrs = 0, []
    for t in terms:
        t = re.sub(r"^\(\w+\)\s*", "", t.strip())
        if re.fullmatch(NUM, t):
            k += _int(t.rstrip("uU"))
        elif t in vals:
            k += vals[t]
        else:
            ptrs.append(t)
    bases = [p for p in ptrs if p in based]
    known = None if len(bases) > 1 or dst in ptrs and dst not in based else based[bases[0]] if bases else 0
    vals.pop(dst, None)
    based.pop(dst, None)
    if not ptrs:
        vals[dst] = k & 0xFFFFFFFF
    elif known is not None and 0 <= known + k < LIMIT:
        based[dst] = known + k


def workspace_image(text):
    """The workspace binary when raw.c reads a pool word (POOL), else None."""
    if not (INLINE.search(text) or any(POOL.match(ln) for ln in text.splitlines())):
        return None
    from . import paths
    try:
        return paths.config()["binary_path"].read_bytes()
    except (OSError, KeyError, TypeError):
        return None


def accesses(line):
    """(bytes, address text) of every rd/wr on the line, nested ones too."""
    for m in ACCESS.finditer(line):
        depth, i = 0, m.end()
        while i < len(line) and not (depth == 0 and line[i] in ",)"):
            depth += {"(": 1, ")": -1}.get(line[i], 0)
            i += 1
        yield SIZE[m.group(1)], line[m.end():i]


def reached(text, image=None):
    """(member offsets, addresses) raw.c text reaches through a variable (image: the binary, for pool words):
    offsets it adds to an address, displacements from a derived base, and addresses from a constant base."""
    from .lint import strip_comments
    vals, based, out, addrs = {}, {}, set(), set()  # vals: a known value; based: an unknown pointer plus K
    for line in strip_comments(text).splitlines():
        for size, arg in accesses(line):
            here, at = set(), set()
            chained = False
            for m in CHAIN.finditer(arg):
                parts = [_int(x) for x in LIT.findall(m.group(0))]
                here.update(sum(parts[:i]) for i in range(2, len(parts) + 1))
                chained = m.end() >= len(arg.rstrip())
            t = TAIL.search(arg)
            if t and not chained:
                here.add(_int(t.group(1)))
            here.update(vals[r] for r in {r for pat in ADDED for r in pat.findall(arg)} if r in vals)
            m = AT.match(arg)
            if m:
                d = _int(m.group(2)) if m.group(2) else 0
                if m.group(1) in based:
                    here.add(based[m.group(1)] + d)
                elif vals.get(m.group(1), 0) >= LIMIT:
                    at.add(vals[m.group(1)] + d)
                elif m.group(1) not in vals:
                    here.add(d)  # an untracked pointer plus d: `wr32(flow, 0u)` covers members 0..3
            c = CONST.match(arg)
            if c:
                at.add(int(c.group(1), 16))
            out.update(o + i for o in here for i in range(size))  # a wide access covers the members inside it
            addrs.update((a + i) & 0xFFFFFFFF for a in at for i in range(size))
        if image is not None:  # a pool word inside a definition is its value (the read itself stays an address)
            line = INLINE.sub(lambda p: _inline(image, p), line)
        m, inc = STEP.match(line), INC.match(line)
        s = None if inc or m else SUM.match(line)
        if not (inc or m and m.group(1) not in KEYWORDS or s):  # an address taken, not a variable's own definition
            out.update(based[v.group(1)] + _int(v.group(2)) for v in VALUE.finditer(line) if v.group(1) in based)
        if inc or m and m.group(1) not in KEYWORDS:
            dst, src, sign, k = (inc.group(1), inc.group(1), *inc.group(2, 3)) if inc else m.group(1, 2, 3, 4)
            k = _int(k) * (1 if sign == "+" else -1)
            if src in vals:
                vals[dst] = (vals[src] + k) & 0xFFFFFFFF
                based.pop(dst, None)
            elif (dst != src or src in based) and 0 <= based.get(src, 0) + k < LIMIT:
                based[dst] = based.get(src, 0) + k  # a derived base: what src points at, plus k
                vals.pop(dst, None)
            else:
                vals.pop(dst, None)
                based.pop(dst, None)
            continue
        if s and s.group(1) not in KEYWORDS:
            _sum(s.group(1), s.group(2).split("+"), vals, based)
            continue
        m = SET.match(line) or POOL.match(line)
        v = None
        if m and m.re is SET:
            v = _int(m.group(2))
        elif m:
            v = _pool(image, int(m.group(4), 16), 2 if m.group(2) else 4, bool(m.group(2)))
        if v is not None:
            vals[m.group(1)] = v & 0xFFFFFFFF
            based.pop(m.group(1), None)
            continue
        w = WRITE.match(line)
        if w:
            vals.pop(w.group(1), None)
            based.pop(w.group(1), None)
    return {v for v in out if v < LIMIT}, addrs


def offsets(text, image=None):
    """The member offsets raw.c text reaches through a variable (reached's first half)."""
    return reached(text, image)[0]

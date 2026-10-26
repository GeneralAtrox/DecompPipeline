"""Member offsets of a struct local in the original's own frame (core/readable_lean.lean_set, after reg_offsets).

raw.c addresses the original's frame from the entry r15: a variable set to it (`e = 0x8cffe000u;`) and stepped
(`b = e - 0x88u; rr = b - 4u;`), or a frame constant itself. A struct local the original passes by address
(`call_gen(0x8c057010u, rr + 0x24u, ...)`: a SpriteDesc at r15 + 0x24) is filled through frame literals
(`wrf32(rr + 0x34u, 1.25f)`): its member offsets are those literals less the struct's base, a difference no literal
holds, so the lean projection left the members out and Haiku wrote `*(f32 *)((u8 *)&sprite + 0x10)` (Spawn S07
8c0160a0, 8c014500, 8c0146ba).

offsets() follows the frame variables through straight-line text (any other write forgets one) and takes every frame
address raw.c names: a frame constant, or a frame variable plus or minus a literal. A base is one that escapes (not
the address of an rd/wr, nor a frame variable's own definition); every frame address in [base, base + SPAN) gives
the member offset address - base, with every byte a wide store covers. An extra offset only shows one more member.
"""
import re

from .compare import SP

NUM = r"(0[xX][0-9a-fA-F]+|\d+)[uU]?"
DEF = re.compile(rf"^\s*(?:\w+\s+)?(\w+)\s*=\s*(?:\(\w+\)\s*)?(?:(\w+)\s*(?:([-+])\s*{NUM})?)\s*;")
WRITE = re.compile(r"^\s*(?:\w+\s+)?(\w+)\s*(?:=(?!=)|\+=|-=)")
EXPR = re.compile(rf"\b(?:0[xX]((?i:{SP >> 16:04x})[0-9a-fA-F]{{4}})[uU]?(?:\s*([-+])\s*{NUM})?"  # `0x8cffdf98u + 0x5c`
                  rf"|([A-Za-z_]\w*)\s*([-+])\s*{NUM})(?!\s*[*<>/%\w])")
ACCESS = re.compile(r"\b(?:rd|wr)(8|16|32|f32)\s*\(\s*(?:\(\w+\)\s*)?$")
SIZE = {"8": 1, "16": 2, "32": 4, "f32": 4}
SPAN = 0x200  # a struct local's size, as far as its members go


def _int(text):
    return int(text, 16) if text[:2].lower() == "0x" else int(text)


def offsets(text, lo=None):
    """The member offsets raw.c text writes into struct locals of the original's own frame (lo: the lowest frame
    address, readable_stack.frame_lo())."""
    from .lint import strip_comments
    from .readable_stack import STACK_HI, frame_lo
    lo = frame_lo() if lo is None else lo
    vals, named, bases = {}, set(), set()
    for line in strip_comments(text).splitlines():
        d = DEF.match(line)
        own = None
        for m in EXPR.finditer(line):
            if m.group(1):
                k = _int(m.group(3)) if m.group(2) else 0
                a = (int(m.group(1), 16) + (k if m.group(2) != "-" else -k)) & 0xFFFFFFFF
            elif m.group(4) in vals:
                k = _int(m.group(6))
                a = (vals[m.group(4)] + (k if m.group(5) == "+" else -k)) & 0xFFFFFFFF
            else:
                continue
            if not lo <= a < STACK_HI:
                continue
            if d and m.start() == d.start(2):
                own = a  # a frame variable's definition (the entry r15 too), not an escape
                continue
            if a < SP:
                acc = ACCESS.search(line[:m.start()])
                named.update(a + i for i in range(SIZE[acc.group(1)] if acc else 1))  # a wide store's bytes too
                if not acc:
                    bases.add(a)
        if d:
            if own is not None:
                vals[d.group(1)] = own
            elif d.group(2) in vals and not d.group(3):
                vals[d.group(1)] = vals[d.group(2)]
            else:
                vals.pop(d.group(1), None)
            continue
        w = WRITE.match(line)
        if w:
            vals.pop(w.group(1), None)
    return {a - k for k in bases for a in named if k <= a < k + SPAN}

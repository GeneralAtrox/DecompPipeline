"""Jump tables in the readable pass (core/rtx/lift).

The translator writes a jump table as a switch over the code addresses it reaches: `r1 = (u32)(s32)(s16)rd16(r2 +
TABLE); sw_ = r1 + BASE; switch (sw_) { case 0x8c05ccde: ... default: tc_call(sw_) }`. Reading the table from the
image gives what each index reaches, so the switch can be over the index: `switch (r2) { case 0: case 4: ... }`.
The default goes: it stands for an index outside the table, a jump no valid vector makes (the oracle refuses a
target that is not code), and the judge decides on the whole. A label the table does not reach leaves the switch as it
was, and the caller refuses it.

Nearest existing: core/rtx/flow (constant folding over the body) - it has no image to read, and lift.py is over its size
cap (it may only shrink)."""
import functools

from .cast import Assign, Bin, Block, Call, Case, Cast, Num, Switch, Var
from .flow import U32, is_int, linear

WIDTH = {"rd8": 1, "rd16": 2, "rd32": 4}
SIGNED = {"s8": 8, "s16": 16}


@functools.lru_cache(maxsize=4)
def _load(path):
    return path.read_bytes()


def image_reader():
    """read(address, width): the little-endian word of the game's image, or None outside it."""
    from .. import compare, paths
    data = _load(paths.config()["binary_path"])

    def read(addr, width):
        off = addr - compare.IMAGE_BASE
        return int.from_bytes(data[off:off + width], "little") if 0 <= off <= len(data) - width else None
    return read


def table_load(e):
    """(index expression, table address, width, sign bits or None, mask or None) of a table load, else None."""
    mask = sign = None
    if isinstance(e, Bin) and e.op == "&" and is_int(e.b):
        mask, e = e.b.v, e.a
    while isinstance(e, Cast):
        sign, e = SIGNED.get(e.type, sign), e.e
    if not (isinstance(e, Call) and e.fn in WIDTH):
        return None
    index, table = linear(e.args[0])
    return (index, table, WIDTH[e.fn], sign, mask) if index is not None else None


def targets(load, base, read, labels):
    """{target address: [index values reaching it]}, walking the table while it reaches a label."""
    _, table, width, sign, mask = load
    out, off = {}, 0
    while True:
        word = read(table + off, width)
        if word is None:
            return out
        if sign and word >> (sign - 1):
            word -= 1 << sign
        target = ((word & mask if mask is not None else word) + base) & U32
        if target not in labels:
            return out
        out.setdefault(target, []).append(off)
        off += width


def rewrite_one(sw, load, base, read):
    """Turn sw (over the code addresses) into a switch over the index. False when a label is not in the table."""
    stmts = sw.body.body if isinstance(sw.body, Block) else sw.body
    labels = {s.v.v for s in stmts if isinstance(s, Case) and s.v is not None and is_int(s.v)}
    reach = targets(load, base, read, labels)
    if not labels or set(reach) != labels:
        return False
    out, in_default = [], False
    for s in stmts:
        if isinstance(s, Case):
            in_default = s.v is None
            if not in_default:
                out.extend(Case(Num(i)) for i in reach[s.v.v])
        elif not in_default:
            out.append(s)
    stmts[:] = out
    sw.e = load[0]
    return True


def rewrite(body, read=None):
    """Every `R = table load; sw_ = R + BASE; switch (sw_)` of the body whose table reaches all its labels."""
    from .lift import all_stmts
    read = read or image_reader()
    for stmts, k, s in reversed(list(all_stmts(body.body))):
        if not (isinstance(s, Switch) and isinstance(s.e, Var) and s.e.name == "sw_" and k >= 2):
            continue
        add, load = stmts[k - 1], stmts[k - 2]
        if not (isinstance(add, Assign) and add.lhs == s.e and isinstance(load, Assign)):
            continue
        reg, base = linear(add.rhs)
        table = table_load(load.rhs)
        if table and reg == load.lhs and rewrite_one(s, table, base, read):
            del stmts[k - 2:k]

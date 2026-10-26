"""Path facts for core/param_unread.py: what a path learned from zero tests of memory words, and frame offsets.

A path that took `tst rX,rX; bt/bf` on a word loaded from outside the frame knows whether the word is zero until a
store outside the frame, a call or a write to the base register; a later branch on the same word then takes only
its side (TC Anim_SeekTime tests +0x930 twice). binds: (register, key) — the register holds the word at key; vals:
(key, zero). A key is (base register, offset, mnemonic).
"""
import re

from . import sh4_asm, stack_uninit

T_USERS = {"bt", "bf", "bt/s", "bf/s", "movt", "addc", "subc", "negc", "rotcl", "rotcr", "div1"}
T_WRITERS = re.compile(r"^(cmp/\w+|tst(\.b)?|fcmp/\w+|ftst|clrt|sett|shll|shlr|shal|shar|rotl|rotr|addv|subv|"
                       r"div0s|div0u|dt|tas\.b)$")

COND = {"bt": True, "bt/s": True, "bf": False, "bf/s": False}  # taken when T is 1 (`tst rX,rX`: rX == 0)
MAX_PATHS = 16  # fact sets kept apart per instruction; past it a path goes on with no facts (it prunes nothing)


def mem_key(x, op, st):
    """(base register, offset, mnemonic) of a load from @rB / @(disp,rB) / @(r0,rB) (r0 a known constant) outside
    the frame, else None."""
    m = stack_uninit.MEM.match(op)
    if not m or m.group(1) or m.group(3):
        return None
    base = m.group(2) or m.group(5) or m.group(7)
    alias = st[1] if st else {}
    if base == "r15" or alias.get(base) is not None:
        return None
    off = int(m.group(4), 0) if m.group(4) else 0
    if m.group(7):
        if "c:r0" not in alias:
            return None
        off = alias["c:r0"]
    return base, off, x.mn


def written(x):
    """Registers instruction x writes (destination, @rB+ and @-rB)."""
    out = set()
    for o in x.ops:
        m = stack_uninit.MEM.match(o)
        if m and (m.group(1) or m.group(3)):
            out.add(m.group(2))
        out.update(r for r in sh4_asm.regs_in(o) if sh4_asm.uses(x, r)[1])
    return out


def facts(x, st, binds, vals):
    """(binds, vals) after x. binds: (register, key) — the register holds the word at key; vals: (key, zero) — the
    word at key is (not) zero. A store outside the frame forgets both; writing a register forgets what it holds or
    is the base of."""
    w = written(x)
    if x.ops and len(x.ops) > 1 and x.ops[-1].startswith("@") and not sh4_asm.NO_WRITE.match(x.mn) and \
            slot(x, x.ops[-1], st) is None:
        return frozenset(), frozenset()
    binds = frozenset(b for b in binds if b[0] not in w and b[1][0] not in w)
    vals = frozenset(v for v in vals if v[0][0] not in w)
    if x.mn in ("mov.l", "mov.w", "mov.b") and len(x.ops) == 2 and not x.ops[1].startswith("@"):
        key = mem_key(x, x.ops[0], st)
        if key:
            binds |= {(x.ops[1], key)}
    return binds, vals


def cond_key(insns, br, binds):
    """The key whose zero test decides the conditional branch at br (`tst rX,rX` just before it, rX bound), else
    None."""
    for k in range(br - 1, max(-1, br - 5), -1):
        y = insns[k]
        if y.mn in sh4_asm.DELAYED or y.mn in COND:
            return None
        if T_WRITERS.match(y.mn):
            if y.mn != "tst" or len(y.ops) != 2 or y.ops[0] != y.ops[1]:
                return None
            r = y.ops[0]
            if any(r in written(insns[j]) for j in range(k + 1, br)):
                return None
            return next((key for reg, key in binds if reg == r), None)
    return None


def slot(x, op, st):
    """Entry-relative frame offset of a memory operand (pre-decrement applied), else None."""
    m = stack_uninit.MEM.match(op) if x.mn != "mova" else None
    if not m or st is None:
        return None
    sp, alias = st[0], st[1]
    reg = m.group(2) or m.group(5) or m.group(7)
    b = sp if reg == "r15" else alias.get(reg)
    if b is None:
        return None
    if m.group(7):
        if "c:r0" not in alias:
            return None
        b += alias["c:r0"]
    if m.group(1):
        b -= stack_uninit._size(x, [r for o in x.ops for r in sh4_asm.regs_in(o) if not o.startswith("@")])
    return b + (int(m.group(4), 0) if m.group(4) else 0)

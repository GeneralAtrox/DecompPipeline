"""Code addresses that function code uses as values but that are no function (batch 27; TC 0x8c0811fe, the pad read
function Lib_InputInit stores in g_padReadFn).

Why discovery misses them: Ghidra makes functions at call targets; TcOrphans (catalog rebuild) also makes them for
decoded code no function owns, for never-decoded branch targets and for the entries of pointer tables (6 or more
pointer words). A routine whose address only sits in one literal-pool word, loaded and stored or passed, is never
decoded, so no rule sees it: its bytes stay undefined and TcPrepare calls the address a "global".

candidates() finds them from the packets' listings: a literal-pool load annotated `(global X)` whose register is then
used as a value (stored, compared, moved or passed to a call) and never as an address before it is written again,
with X even, inside the loaded image and outside every function body. tools/data_funcs.py then asks Ghidra
(tools/ghidra_tools/TcEntryCheck.java, read-only) whether X disassembles as a subroutine, and lists the result; the
coordinator adds the chosen ones (`--add`: project.json catalog_overrides "entry", plus extra_functions outside the
game ranges), never in bulk.
"""
import bisect
import re

from core import sh4_asm

LOOKAHEAD = 16


def _use(insns, i, reg):
    """'value', 'address' or None: how the register loaded at i is used before it is written again."""
    value = False
    for x in insns[i + 1:i + 1 + LOOKAHEAD]:
        mem = [o for o in x.ops if o.startswith("@")]
        if any(reg in sh4_asm.regs_in(o) for o in mem):
            return "address"
        rd, wr = sh4_asm.uses(x, reg)
        if rd:
            value = True
        if x.mn in sh4_asm.CALLS and re.fullmatch(r"r[4-7]", reg):
            return "value"
        if wr and not rd:
            break
    return "value" if value else None


def bodies(path):
    """Sorted [(lo, hi)] of state/function_bodies.txt ("entry lo hi", hi inclusive)."""
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.split()
            if len(t) == 3:
                rows.append((int(t[1], 16), int(t[2], 16)))
    return sorted(rows)


def inside(rows, a):
    """Is a inside one of the (disjoint) body ranges?"""
    k = bisect.bisect_right(rows, (a, 1 << 40)) - 1
    return k >= 0 and rows[k][0] <= a <= rows[k][1]


def candidates(packets_dir, image_lo, image_hi, body_rows, functions):
    """{addr: [(user function, load address), ...]} of value-used code-region addresses that are no function."""
    out = {}
    for d in sorted(p for p in packets_dir.iterdir() if p.is_dir()):
        text = sh4_asm.read_packet(d)
        insns = sh4_asm.parse(text) if text else []
        for x in insns:
            pv = x.pool_value()
            if not pv or pv[1] != "global" or not x.ops or not re.fullmatch(r"r\d+", x.ops[-1]):
                continue
            if pv[0] >> 29 not in (0, 4, 5):  # RAM in its P0, P1 or P2 form
                continue
            a = (pv[0] & 0x1FFFFFFF) | 0x80000000
            if a % 2 or not image_lo <= a < image_hi or a in functions or inside(body_rows, a):
                continue
            if _use(insns, x.idx, x.ops[-1]) == "value":
                out.setdefault(f"{a:08x}", []).append((d.name, f"{x.addr:08x}"))
    return out


ADDABLE = ("valid", "code")  # TcEntryCheck verdicts that may become a function entry


def refused(verdicts, addrs):
    """[(addr, why)] of the addresses --add must not write: one TcEntryCheck did not find to disassemble as a
    subroutine (invalid), typed data, already a function, inside one, or not checked."""
    out = []
    for a in addrs:
        v, d = verdicts.get(a, ("unchecked", ""))
        if v not in ADDABLE:
            out.append((a, f"{v} {d}".strip()))
    return out

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

tables() finds the same kind of address held in a data table of functions (core/mid_entries' table rule) instead of a
literal pool: a handler table whose other words are function starts, often in the 0x0c mirror form (PS2's object
handlers 0x0c06b31c, 0x0c06b352 between 0x0c06aed0 and 0x0c06b356). TcOrphans' pointer-table rule did not make them,
and mid_entries reports only table words inside a body.
"""
import bisect
import re
import struct

from core import mid_entries, sh4_asm

LOOKAHEAD = 16
NEAR = 0x100  # a table word outside every body is taken as code when it is this close to a body (after or before)


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
    """Sorted [(lo, hi)] of state/function_bodies.txt ("entry lo hi", hi exclusive, as TcCatalog writes it)."""
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.split()
            if len(t) == 3:
                rows.append((int(t[1], 16), int(t[2], 16)))
    return sorted(rows)


def inside(rows, a):
    """Is a inside one of the (disjoint) body ranges? The first byte after a body is not (the next function's start
    when two functions touch: PS2's 8c04f506 right after 8c04f458's body)."""
    k = bisect.bisect_right(rows, (a, 1 << 40)) - 1
    return k >= 0 and rows[k][0] <= a < rows[k][1]


def tables(image, base, body_rows, starts, pools=frozenset()):
    """{addr: [("table", word address)]}: words of data tables of functions (mid_entries.TABLE_RUN code addresses in a
    row, at least half of them function starts; the words outside every body and every literal pool the listings
    load) that point outside every body, less than NEAR bytes after a body's end or before a body's start, even and
    no function start."""
    ends = [hi for _, hi in body_rows]
    los = [lo for lo, _ in body_rows]
    out = {}
    run = []

    def near(a):
        k = bisect.bisect_right(ends, a) - 1
        j = bisect.bisect_right(los, a)
        return not inside(body_rows, a) and ((k >= 0 and a - ends[k] < NEAR) or (j < len(los) and los[j] - a < NEAR))

    def close():
        if len(run) >= mid_entries.TABLE_RUN and 2 * sum(a in starts for _, a in run) >= len(run):
            for at, a in run:
                if a not in starts and not inside(body_rows, a):
                    out.setdefault(f"{a:08x}", []).append(("table", f"{at:08x}"))
        run.clear()

    for i, w in enumerate(struct.unpack_from(f"<{len(image) // 4}I", image, 0)):
        at, a = base + 4 * i, mid_entries.p1(w)
        if (not inside(body_rows, at) and at not in pools and a is not None and a % 2 == 0
                and base <= a < base + len(image) and (a in starts or inside(body_rows, a) or near(a))):
            run.append((at, a))
        else:
            close()
    close()
    return out


def candidates(packets_dir, image_lo, image_hi, body_rows, functions):
    """{addr: [(user function, load address), ...]} of value-used code-region addresses that are no function."""
    out = {}
    if not packets_dir.is_dir():  # nothing prepared yet: no listings to read
        return out
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

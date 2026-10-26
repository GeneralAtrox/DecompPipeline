"""Second entry points the catalog folded into another function: code that is entered at an address inside a
function's body, not at its start (r15; TC 8c05f40c).

Why discovery misses them: Ghidra makes a function at each call target it decodes, but a body it has already given
to a function stays there. TC's 8c05f198 swaps its two arguments and branches to 8c05f40c, so Ghidra made
8c05f40c..8c05f504 a second body range of 8c05f198; the collision table at 8c0a7c80 calls 8c05f40c directly. TcOrphans
makes functions only for code no function owns (core/data_funcs.py covers addresses outside every body), so the
entry never became a function: the port has no host function there and the call returns 0.

find() lists every address A inside a body range of function F (state/function_bodies.txt, "entry lo hi", hi
exclusive), A not a function start, that something enters as a function:
- call: a bsr, or a jsr the listing resolves (`; call A`), anywhere in the game;
- pool: a literal-pool word holding A, loaded by a function other than F (a function pointer passed, stored or
  called);
- branch: a bra or jmp from a function other than F to A (a tail call into F's middle).
  For pool and branch, "other than F" means by the packet and by the body file; a packet can also list a block the
  body file gives to another function (code two functions share), and a branch or a pool-loaded jmp to a block the
  packet lists is not an entry.
- table: a word of a data table (outside every body and every literal pool a listing loads) of at least TABLE_RUN code addresses, at least half of them
  function starts (a table of functions; a switch table of one function's labels has no starts);
- prologue: a body range of F other than the one at F's start, entered from F by a bra, that begins by saving a
  callee-saved register or PR on the stack (its own function's prologue: the TC case, from 8c05f198 itself).
  Alone this is a "merged body": the compiler's separate function, reached only through F, which the port runs as
  part of F. needs_override() is true only with evidence of another rule: something outside F enters it.
Addresses are folded to their P1 form (0x8c...); project.json catalog_overrides "entry" and "not_entry" are left out
(already decided). Each result lists its evidence; tools/mid_entries.py reports them as "needs an entry override".
"""
import bisect
import re
import struct

from . import sh4_asm

TABLE_RUN = 4  # code-address words in a row that make a table
MERGED = "body range of"  # the prologue rule's evidence


def needs_override(evidence):
    """Entered from outside its function by some rule (not only the prologue rule)."""
    return any(not e.startswith(MERGED) for e in evidence)


def p1(v):
    """The P1 (0x8c...) form of an SH-4 RAM address, or None for an address outside RAM's P0/P1/P2 forms."""
    return (v & 0x1FFFFFFF) | 0x80000000 if v >> 29 in (0, 4, 5) else None


class Bodies:
    """The function body ranges: owner(a) is the function whose body holds a."""

    def __init__(self, text, starts):
        rows = []
        for line in text.splitlines():
            t = line.split()
            if len(t) == 3:
                rows.append((int(t[1], 16), int(t[2], 16), int(t[0], 16)))
        self.rows = sorted(rows)
        self.lows = [r[0] for r in self.rows]
        self.starts = set(starts)

    def owner(self, a):
        k = bisect.bisect_right(self.lows, a) - 1
        return self.rows[k][2] if k >= 0 and self.rows[k][0] <= a < self.rows[k][1] else None

    def chunks(self, entry):
        return [(lo, hi) for lo, hi, e in self.rows if e == entry]


def _prologue(image, base, a):
    """Does the code at a start by pushing r8..r14 or PR (mov.l Rm,@-r15 / sts.l PR,@-r15)?"""
    off = a - base
    if not 0 <= off <= len(image) - 2:
        return False
    w = struct.unpack_from("<H", image, off)[0]
    return w == 0x4F22 or ((w & 0xFF0F) == 0x2F06 and 8 <= (w >> 4) & 0xF <= 14)


def _jumps_with(insns, x):
    """Is the register x loads used by a jmp before it is written again (a long jump, not a function pointer)?"""
    reg = x.ops[-1] if x.ops else ""
    for y in insns[x.idx + 1:x.idx + 9]:
        if y.mn == "jmp" and y.ops and y.ops[0] == f"@{reg}":
            return True
        rd, wr = sh4_asm.uses(y, reg)
        if wr and not rd:
            return False
    return False


def from_listings(listings, bodies):
    """{a: [evidence]} from the call, pool and branch rules over {function entry: Insn list}."""
    out = {}

    def hit(a, why):
        if a is not None and a not in bodies.starts and bodies.owner(a) is not None:
            out.setdefault(a, []).append(why)

    for entry, insns in listings.items():
        listed = {x.addr for x in insns}
        for x in insns:
            mine = (entry, bodies.owner(x.addr))  # a packet can list code the body file gives to another function
            t = x.call_target()
            if x.mn in sh4_asm.CALLS and (t or x.branch_target() is not None):
                a = p1(int(t, 16)) if t else p1(x.branch_target())
                hit(a, f"call from {entry:08x} at {x.addr:08x}")
                continue
            pv = x.pool_value()
            if pv:
                a = p1(pv[0])
                if a is not None and a % 2 == 0 and bodies.owner(a) not in (None, *mine) and not (
                        a in listed and _jumps_with(insns, x)):
                    hit(a, f"pool word loaded by {entry:08x} at {x.addr:08x}")
                continue
            if x.mn in ("bra", "jmp"):
                a = p1(x.branch_target()) if x.branch_target() is not None else (p1(int(t, 16)) if t else None)
                if a is not None and a not in listed and bodies.owner(a) not in (None, *mine):
                    hit(a, f"branch from {entry:08x} at {x.addr:08x}")
    return out


def from_prologues(listings, bodies, image, base):
    """{a: [evidence]}: a function's later body range entered by its own bra and starting with a prologue."""
    out = {}
    for entry, insns in listings.items():
        later = {lo for lo, _ in bodies.chunks(entry) if lo != entry}
        for x in insns:
            a = p1(x.branch_target()) if x.mn == "bra" and x.branch_target() is not None else None
            if a in later and a not in bodies.starts and _prologue(image, base, a):
                out.setdefault(a, []).append(f"{MERGED} {entry:08x} with its own prologue, branched to at "
                                             f"{x.addr:08x}")
    return out


def pool_words(listings):
    """The addresses of the literal-pool words the listings load (mov.l/mov.w @(disp,PC)): not data tables."""
    out = set()
    for insns in listings.values():
        for x in insns:
            if x.mn in ("mov.l", "mov.w") and x.pool_value() and x.ops:
                m = re.fullmatch(r"0x([0-9a-f]{8})", x.ops[0])
                if m:
                    out.add(int(m.group(1), 16) & ~3)
    return out


def from_tables(image, base, bodies, gaps=(), pools=frozenset()):
    """{a: [evidence]}: non-start words of data tables of functions (outside every body and every literal pool)."""
    out = {}
    n = len(image) // 4
    words = struct.unpack_from(f"<{n}I", image, 0)
    run = []

    def close():
        code = [(at, a) for at, a in run]
        if len(code) >= TABLE_RUN and 2 * sum(a in bodies.starts for _, a in code) >= len(code):
            for at, a in code:
                if a not in bodies.starts:
                    out.setdefault(a, []).append(f"table word {at:08x} (a table of {len(code)} code addresses at "
                                                 f"{code[0][0]:08x})")
        run.clear()

    for i, w in enumerate(words):
        at = base + 4 * i
        a = p1(w)
        inside_code = bodies.owner(at) is not None or any(lo <= at < hi for lo, hi in gaps)
        if not inside_code and at not in pools and a is not None and a % 2 == 0 and (a in bodies.starts or bodies.owner(a) is not None):
            run.append((at, a))
        else:
            close()
    close()
    return out


def find(listings, bodies, image, base, decided=(), gaps=()):
    """{a: [evidence]} of every rule, without the decided addresses (catalog_overrides entry and not_entry)."""
    out = {}
    for part in (from_listings(listings, bodies), from_prologues(listings, bodies, image, base),
                 from_tables(image, base, bodies, gaps, pool_words(listings))):
        for a, why in part.items():
            if f"{a:08x}" not in decided:
                out.setdefault(a, []).extend(why)
    return dict(sorted(out.items()))


def listings_of(packets_dir):
    """{function entry: Insn list} from the packets' asm listings."""
    out = {}
    for d in sorted(p for p in packets_dir.iterdir() if p.is_dir()) if packets_dir.is_dir() else []:
        if re.fullmatch(r"[0-9a-f]{8}", d.name):
            text = sh4_asm.read_packet(d)
            if text:
                out[int(d.name, 16)] = sh4_asm.parse(text)
    return out

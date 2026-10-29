"""junkout_scan.py: the SH-4 functions whose harness the tail-exit rule (junk-out) can alter, found without Ghidra.
selective_restamp --change junkout re-prepares exactly these.

TcStrict.walk (the r1..r7 read after a mocked call) counts a function's own exported r4..r7 (TcOut: TcStrict.exitLive)
as read where a path leaves: before, only at a return; now also at a jump out of the extent (a tail call `jmp @r3`, a
`bra` or branch to another function, a fall-on into the next one, a jump Ghidra knows no target of). Without that, a
mocked call's junk could land in rK and reach the exit through a tail call: the expected RO was junk no C can make
(Spawn fe 8c4b1d0e: junk of call 2 in r4, then `jmp @r3`). Two things read the walk, and only with exitLive set:
the junk mask at a mocked call (TcStrict.junk) and the callee hand-backs a function reads (TcOut.outs: r_out, K
lines). So a harness can differ only when the function exports some rK (meta.json "r_exports", or regout's (b) set
for a function without one: regout's static exports miss some, e.g. Spawn ovl 8c5a2460) and a walk from one of its
call sites differs between "exits at returns only" and "exits at returns and jumps out", on r4..r7.
The view is a superset: every exporter's exit_live is taken as all of r4..r7, branches and fall-ons out of the extent
count as exits, and no project's mock_junk flag is consulted (the sample proves the rest).

Nearest existing: core/regout.py - its walk already counts jumps out as exits (the new model) and its scan lists every
function TcOut writes into, not the ones this rule changes; this reuses its decoder, extent, walk and export scan and
only compares the walk under the old and new exits.
"""
import json
import pathlib
import sys

from core import regout


class Old(regout.Code):
    """TcStrict.walk before: a path ends at a jump out with nothing read; the exit only at rts/rte."""

    def succ(self, f, a, ext):
        nx, ends = super().succ(f, a, ext)
        return nx, ends and self.insn(a).op in ("rts", "rte")


class New(regout.Code):
    """TcStrict.walk now: also a conditional branch out of the extent, or a fall-on past it, is an exit."""

    def succ(self, f, a, ext):
        nx, ends = super().succ(f, a, ext)
        i = self.insn(a)
        nxt = a + 4 if i.delayed else a + 2
        if i.op in regout.BRANCHES:
            return nx, ends or any(x not in ext for x in (i.ea, nxt))
        if not ends and not nx and i.op not in regout.JUMPS:
            return nx, True
        return nx, ends


def exporters(ws):
    """The functions whose harness meta.json has "r_exports" (TcOut.prepare's own answer)."""
    out = set()
    for m in pathlib.Path(ws, "harness").glob("*/meta.json"):
        try:
            if json.loads(m.read_text(encoding="utf-8")).get("r_exports"):
                out.add(int(m.parent.name, 16))
        except ValueError:
            continue
    return out


def scan(image, base, bodies, callees=None, exported=()):
    """{function: [call site whose walk differs]} over regout's exporters and those given."""
    _a, b, _why = regout.scan(image, base, bodies, callees)
    exporters = (b | set(exported)) & set(bodies)
    old, new = Old(image, base, bodies, callees), New(image, base, bodies, callees)
    out = {}
    for f in sorted(exporters):
        for a, _t, tail in new.sites(f):
            start = a + 4 if tail or new.insn(a).delayed else a + 2
            if (old.walk(f, start, regout.R4_7) ^ new.walk(f, start, regout.R4_7)) & regout.R4_7:
                out.setdefault(f, []).append(a)
    return out


def candidates(ws):
    """The addresses (8 hex digits) of the workspace's functions the rule can alter."""
    return {f"{x:08x}" for x in scan(*regout.load(ws), exporters(ws))}


if __name__ == "__main__":
    for w in sys.argv[1:]:
        found = scan(*regout.load(w), exporters(w))
        print(f"{w}: {len(found)}")
        for f, sites in found.items():
            print(f"{f:08x}", " ".join(f"{s:08x}" for s in sites))

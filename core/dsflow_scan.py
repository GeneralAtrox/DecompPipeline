"""Which functions the delay-slot fall-on change (ds-flow: TcTail.slotEntry/exits, TcCont's slot fall-ons) can alter,
found from the packet asm alone (tools/selective_restamp.py --change dsflow). Pure text: no Ghidra, no core.paths.

A branch (bt, bf, bt/s, bf/s, bra) whose target is another branch's delay slot runs the slot as a plain instruction
and then the next one, the fall-on (PS2 8c189204 `bf 0x8c189208` into the slot of `8c189206 bra`: 8c18920a). Before
the change Ghidra's flow and TcTail.extent stopped at the slot, so the fall-on is missing from the listing TcPrepare
writes; the oracle refuses a run that gets there ("executes code outside its body at 8c18920a"). The change adds it
to the extent (and TcCont decodes it and gives it to the branch's function), so only a packet with such a branch
whose fall-on is not in its listing can differ: a branch whose fall-on is listed already (a bt/s slot, or code the
body reaches some other way) leaves the extent as it was. Their callers are candidates too: TcTflag, TcOut and TcSum
read a callee's extent (a `; call` note in the caller's listing names it).
Nearest existing: core/tflag_scan.py (tf-scan, the same kind of finder for another change) - it follows T across
calls, not branch targets, so it has nothing to reuse here beyond the shape; the asm parsing is core/sh4_asm's.
"""
import pathlib
import sys

from .sh4_asm import parse, read_packet

BRANCHES = {"bt", "bf", "bt/s", "bf/s", "bra"}
OWNERS = {"bra", "bsr", "braf", "bsrf", "jmp", "jsr", "rts", "rte", "bt/s", "bf/s"}  # sh4_asm.DELAYED


def slot_branches(insns):
    """[(branch, slot, fall-on, listed)] for each branch in the listing into another branch's delay slot; listed:
    the fall-on is in the listing already (the change leaves the extent as it was)."""
    at = {x.addr: x for x in insns}
    out = []
    for x in insns:
        if x.mn not in BRANCHES or x.slot or x.call_target():
            continue
        t = x.branch_target()
        s, own = at.get(t), at.get((t or 0) - 2)
        if s is None or not s.slot or own is None or own.mn not in OWNERS or own.addr == x.addr:
            continue
        out.append((x.addr, t, t + 2, (t + 2) in at))
    return out


def dsflow_scan(packets, every=False):
    """{function: [(branch, slot, fall-on, listed)]} over a packets folder: the functions whose listing has a branch
    into a delay slot with the fall-on missing (every=True: listed ones too)."""
    out = {}
    for d in sorted(pathlib.Path(packets).iterdir()) if pathlib.Path(packets).is_dir() else []:
        text = read_packet(d) if d.is_dir() else None
        hits = [h for h in (slot_branches(parse(text)) if text else []) if every or not h[3]]
        if hits:
            out[d.name.lower()] = hits
    return out


def dsflow_candidates(packets):
    """The functions the change can alter (--change dsflow): the hits and the functions whose listing calls one."""
    hits = set(dsflow_scan(packets))
    out = set(hits)
    for d in sorted(pathlib.Path(packets).iterdir()) if hits and pathlib.Path(packets).is_dir() else []:
        text = read_packet(d) if d.is_dir() else None
        if text and any(x.call_target() in hits for x in parse(text)):
            out.add(d.name.lower())
    return out


def main(argv):
    """python -m core.dsflow_scan PACKETS... : one line per hit (function, branch, slot, fall-on, listed or MISSING)."""
    for p in argv:
        res = dsflow_scan(p, every=True)
        miss = sorted(f for f, hs in res.items() if any(not h[3] for h in hs))
        print(f"# {p}: {len(miss)} function(s) with a fall-on missing, {len(res) - len(miss)} with it listed")
        for f in sorted(res):
            for b, s, n, listed in res[f]:
                print(f"{f}\t{b:08x}\t{s:08x}\t{n:08x}\t{'listed' if listed else 'MISSING'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

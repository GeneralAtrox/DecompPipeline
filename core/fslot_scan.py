"""Which functions the TcLive tail-call fix can alter, found from the packet asm alone (tools/selective_restamp.py
--change fslot). Pure text: no Ghidra, no core.paths.

A float instruction whose p-code depends on FPSCR (fmov on SZ, PR on the arithmetic) has a branch inside it. In a delay
slot that branch is part of the owner's p-code, and TcLive.Deltas took it for a tail call and stopped there, so what the
function stores after the slot was missing from its meta: V8 8c070dc0's sq_callee said r4 4-2f, not 4-2f,34-3f, its
caller's judged C resent stale header bytes over the UVs, and no menu text drew. So a function with a float instruction
in any delay slot can have new facts, and the functions that call one read them. A superset: not every such slot's
owner counts as a tail, and most facts come out the same.
Nearest existing: core/dsflow_scan.py (the same kind of finder, hits and their callers) - that one looks for branches
into a delay slot, and this looks at what a slot holds; the asm parsing is core/sh4_asm's for both.
"""
import pathlib

from .sh4_asm import parse, read_packet


def float_slots(insns):
    """The addresses of the float instructions in delay slots (mnemonic f...; sh4_asm marks a slot's)."""
    return [x.addr for x in insns if x.slot and x.mn.startswith("f")]


def fslot_candidates(packets):
    """The functions the fix can alter (--change fslot): those with a float instruction in a delay slot and their
    direct callers (a caller's listing names its callee in a `; call` note). No listings is an error, not an empty
    answer: Spawn's ovl workspace has no packets folder, and an empty list would refresh every function it should name."""
    folders = sorted(pathlib.Path(packets).iterdir()) if pathlib.Path(packets).is_dir() else []
    listings = {d.name.lower(): parse(read_packet(d)) for d in folders if d.is_dir() and read_packet(d)}
    if not listings:
        raise SystemExit(f"--change fslot: no packet listings in {packets}, so its candidates cannot be named")
    hits = {a for a, insns in listings.items() if float_slots(insns)}
    return hits | {a for a, insns in listings.items() if any(x.call_target() in hits for x in insns)}

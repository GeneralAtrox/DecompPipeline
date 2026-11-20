"""The function list Ghidra's setup creates for an Xbox 360 title (ghidra_scripts/TcSetup.java reads it).

The setup runs no auto-analysis of its own (the title has about 88,000 functions and no symbols): TcSetup
disassembles and creates exactly the functions listed here, with the bodies the catalog gives (`.pdata` lengths, or
up to the next entry), so every packet, oracle run and catalog row sees the same boundaries as the offline catalog.

The list is plain text, one item per line, beside the binary (TcSetup finds it there):
    F <addr> <size>             a function [addr, addr + size), named fn_<addr>
    H <kind> <lo> <hi>          a register save/restore helper family, one function __<kind> over [lo, hi)
    L <name> <addr>             a label inside a helper family: one entry (__savegprlr_14, ...)
    I <thunk> <library> <ord>   an import's 16-byte call thunk, a function __imp_<library>_<ord>
All numbers are hex. Helper families and thunks are always listed; their callers run them inline (helpers) or
mock them (thunks).

Bodies that share words (catfeed.py's kit entries: a mid entry inside its parent, a hole's body running on into the
next function) cannot all be Ghidra functions at once: place() keeps the roots, then the catalogue's own functions,
then the kit entries, and leaves out any function that overlaps one kept before it.

Proof mode (--proof proof_set.json): only the proof's functions and the callees they reach in `depth` calls (4 by
default: a callee's prototype reads its own callees' inputs three levels down, TcBase.prototypeOf), plus every
helper and the thunks those functions call. Without --proof: every catalog function.

usage: python ghidra_setup.py <catalog_inputs.json> <out.txt> [--proof proof_set.json] [--depth N]
"""
import argparse
import bisect
import json
import re
import sys


def closure(funcs, roots, depth):
    """The addresses reachable from roots in at most `depth` calls or tail branches (roots included)."""
    have = set(roots)
    frontier = set(roots)
    for _ in range(depth):
        nxt = set()
        for a in frontier:
            f = funcs.get(a)
            if f:
                nxt.update(t for t in f["callees"] + f["tail_calls"] if t in funcs and t not in have)
        have |= nxt
        frontier = nxt
    return have


KIT = ("kit_hole", "kit_mid", "kit_leaf")   # catfeed.py's entries: bodies that may share words with another function


class Overlap(ValueError):
    """Two roots whose bodies share words: Ghidra holds one function per word, so they go in different setups."""


def _hits(spans, starts, lo, hi):
    """Does [lo, hi) overlap one of the sorted, disjoint spans?"""
    i = bisect.bisect_right(starts, lo) - 1
    return (i >= 0 and spans[i][1] > lo) or (i + 1 < len(spans) and spans[i + 1][0] < hi)


def place(funcs, roots=()):
    """The functions whose bodies Ghidra can hold together, and the ones dropped: (kept, dropped), lists of catalog rows.
    Ghidra makes one function per word, and catfeed.py's kit entries (a mid entry inside its parent, a hole's leaf body
    running on into the next function) share words with others. Roots come first (two overlapping roots: Overlap),
    then the catalogue's own functions, then the kit entries, in address order; a function that overlaps one already
    kept is dropped. Without kit entries or roots this keeps every function, as before."""
    roots = set(roots)

    def span(f):
        a = int(f["addr"], 16)
        return a, a + f["size"]

    def disjoint(rows):
        s = sorted((span(f), f) for f in rows)
        for (p, pf), (q, qf) in zip(s, s[1:]):
            if q[0] < p[1]:
                raise Overlap(f"roots {pf['addr']} and {qf['addr']} share words: set them up apart")
        return [x for x, _ in s]

    first = [f for f in funcs if f["addr"] in roots]
    rs = disjoint(first)
    rstarts = [lo for lo, _ in rs]
    kept, dropped = list(first), []
    for f in funcs:   # the catalogue's own functions do not overlap one another (pdata, ppcscan's added entries)
        if f["addr"] in roots or f.get("source") in KIT:
            continue
        (dropped if _hits(rs, rstarts, *span(f)) else kept).append(f)
    held = sorted(span(f) for f in kept)
    hstarts = [lo for lo, _ in held]
    last = 0
    for f in sorted((f for f in funcs if f.get("source") in KIT and f["addr"] not in roots), key=span):
        lo, hi = span(f)
        if lo < last or _hits(held, hstarts, lo, hi):
            dropped.append(f)
        else:
            kept.append(f)
            last = hi
    return sorted(kept, key=lambda f: int(f["addr"], 16)), dropped


def lines(cat, chosen=None, roots=(), dropped=None):
    """The setup list's lines for the catalog `cat`, every function or only the addresses in `chosen`. Functions that
    share words with a root or a kept function are left out (place); `dropped`, a list, receives them."""
    helpers = [(kind, lo, hi) for kind, lo, hi in cat["helper_ranges"]]

    def in_helper(lo, hi):
        return any(lo < h_hi and h_lo < hi for _, h_lo, h_hi in helpers)

    out, thunks = [], set()
    rows = [f for f in cat["functions"] if chosen is None or f["addr"] in chosen]
    for f in rows:
        thunks.update(int(t, 16) for t in f["imports"])
    rows = [f for f in rows if not (f["kind"] == "frame_helper" or in_helper(int(f["addr"], 16), int(f["addr"], 16) + f["size"]))]
    rows, lost = place(rows, roots)
    if dropped is not None:
        dropped.extend(lost)
    for f in rows:
        a = int(f["addr"], 16)
        out.append((a, f"F {a:08x} {f['size']:x}"))
    for kind, lo, hi in helpers:
        out.append((lo, f"H {kind} {lo:08x} {hi:08x}"))
    for name, a in cat["helpers"].items():
        if not name.endswith("ambiguous"):
            out.append((a, f"L __{name} {a:08x}"))
    for e in cat["imports"]:
        if e["thunk"] and (chosen is None or e["thunk"] in thunks):
            lib = re.sub(r"[^A-Za-z0-9]+", "_", e["library"]).strip("_")
            out.append((e["thunk"], f"I {e['thunk']:08x} {lib} {e['ordinal']:x}"))
    order = {"H": 0, "L": 1, "I": 2, "F": 3}
    return [t for _, t in sorted(out, key=lambda x: (x[0], order[x[1][0]]))]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("catalog")
    ap.add_argument("out")
    ap.add_argument("--proof")
    ap.add_argument("--depth", type=int, default=4)
    a = ap.parse_args(argv[1:])
    with open(a.catalog, encoding="utf-8") as fh:
        cat = json.load(fh)
    chosen = None
    if a.proof:
        with open(a.proof, encoding="utf-8") as fh:
            proof = json.load(fh)
        roots = [p["addr"] if isinstance(p, dict) else p for p in proof["functions"]]
        funcs = {f["addr"]: f for f in cat["functions"]}
        missing = [r for r in roots if r not in funcs]
        if missing:
            print(f"ghidra_setup: proof functions not in the catalog: {', '.join(missing)}")
            return 1
        chosen = closure(funcs, roots, a.depth)
    else:
        roots = []
    dropped = []
    out = lines(cat, chosen, roots, dropped)
    if dropped:
        print(f"ghidra_setup: {len(dropped)} function(s) left out: they share words with a root or a kept function")
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# DecompPipeline Xbox 360 setup list (tools/ghidra_setup.py)\n" + "\n".join(out) + "\n")
    kinds = {k: sum(1 for t in out if t[0] == k) for k in "FHLI"}
    print(f"ghidra_setup: {kinds['F']} functions, {kinds['H']} helper families ({kinds['L']} entries), "
          f"{kinds['I']} import thunks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

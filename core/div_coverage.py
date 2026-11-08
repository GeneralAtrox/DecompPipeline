"""What the divergence scenarios ran of the game: functions entered, branch sides taken (decomp.py divergence).

Each fast reference (port/tools/ref_fast.py record) keeps coverage.txt: the start PC of every block Flycast's dynarec
compiled in that run (research.BlockCoveragePath), in P1 form. The dynarec ends a block at every branch and compiles
a block the first time the guest enters it, so a PC in the set is code that ran:
- a function is entered when one of its body ranges (state/function_bodies.txt) holds a block start;
- a conditional branch (bt, bf, bt/s, bf/s) at PC in an entered function: its taken side ran when its target is a
  block start, its fall-through side when the address after it (after the delay slot for bt/s, bf/s) is one. A
  target another branch also jumps to counts as taken for both (the set does not say which branch got there).
The report (state/coverage.json): per scenario the functions entered; across all of them the scope's functions no
scenario entered, the branch sides none took, and the figures: functions entered / functions in scope, and branch
sides taken / branch sides in entered functions. Zero divergence proves the code it covers; the rest is checked only
by the judge's vectors, so each gap is a request for a scenario (a function never entered) or a targeted oracle
vector (a side never taken).

Nearest existing: core/coverage.py and core/call_coverage.py - they measure what the oracle's test vectors reach; this reads
what the game itself ran in the divergence scenarios, from Flycast's compiled blocks.
"""
import bisect
import json

from . import mid_entries

CONDITIONAL = {"bt": 2, "bf": 2, "bt/s": 4, "bf/s": 4}  # the fall-through address, after the branch


def read_pcs(path):
    """The block-start PCs in a coverage.txt (an empty set when there is none)."""
    if not path.exists():
        return set()
    return {int(t, 16) for t in path.read_text(encoding="ascii").split() if t}


class Owners:
    """The function that holds an address, from the body ranges."""

    def __init__(self, bodies):
        self.rows = sorted(bodies.rows)
        self.lows = [r[0] for r in self.rows]

    def of(self, a):
        k = bisect.bisect_right(self.lows, a) - 1
        return self.rows[k][2] if k >= 0 and self.rows[k][0] <= a < self.rows[k][1] else None


def entered(pcs, owners):
    """The functions (entry addresses) whose bodies hold a block start of pcs."""
    return {f for f in (owners.of(p) for p in pcs) if f is not None}


def branch_sides(insns):
    """[(branch pc, 'taken'|'fall-through', side address)] of a function listing's conditional branches."""
    out = []
    for x in insns:
        step = CONDITIONAL.get(x.mn)
        target = x.branch_target()
        if step and target is not None:
            out.append((x.addr, "taken", target))
            out.append((x.addr, "fall-through", x.addr + step))
    return out


def report(by_scenario, bodies, listings, in_scope):
    """The coverage report (module notes) from {scenario: block-start PCs}, Bodies, {entry: Insn list} and a
    predicate on function entries."""
    owners = Owners(bodies)
    per, union = {}, set()
    for name, pcs in by_scenario.items():
        pcs = {mid_entries.p1(p) for p in pcs} - {None}
        union |= pcs
        per[name] = sorted(f"{f:08x}" for f in entered(pcs, owners) if in_scope(f))
    scope = sorted({e for _, _, e in bodies.rows if in_scope(e)})
    hit = entered(union, owners)
    never = [f for f in scope if f not in hit]
    sides, missed = 0, []
    for f in scope:
        if f not in hit:
            continue
        for pc, side, at in branch_sides(listings.get(f, [])):
            sides += 1
            if mid_entries.p1(at) not in union:
                missed.append({"function": f"{f:08x}", "branch": f"{pc:08x}", "side": side, "to": f"{at:08x}"})
    return {"scenarios": {n: {"functions": len(v), "entered": v} for n, v in per.items()},
            "functions": {"in_scope": len(scope), "entered": len(scope) - len(never),
                          "never": [f"{f:08x}" for f in never]},
            "branch_sides": {"in_entered": sides, "taken": sides - len(missed), "never": missed},
            "figure": {"functions": round((len(scope) - len(never)) / len(scope), 4) if scope else None,
                       "branch_sides": round((sides - len(missed)) / sides, 4) if sides else None}}


def summary(rep):
    f, b = rep["functions"], rep["branch_sides"]
    return (f"coverage: {f['entered']}/{f['in_scope']} functions entered, {b['taken']}/{b['in_entered']} branch sides "
            f"taken in them; {len(f['never'])} functions and {len(b['never'])} branch sides no scenario reaches")


def write(path, rep):
    path.write_text(json.dumps(rep, indent=1), encoding="utf-8")


def from_workspace(paths, refs):
    """The report for the reference folders refs ({scenario: folder}) against the workspace's catalog and listings."""
    starts = [int(f["addr"], 16) for f in paths.read_json(paths.CATALOG)["functions"]]
    bodies = mid_entries.Bodies(paths.FUNCTION_BODIES.read_text(encoding="utf-8"), starts)
    sc = paths.scope()
    listings = {e: v for e, v in mid_entries.listings_of(paths.PACKETS).items() if f"{e:08x}" in sc}
    by = {n: read_pcs(r / "coverage.txt") for n, r in refs.items() if (r / "coverage.txt").exists()}
    return report(by, bodies, listings, lambda e: f"{e:08x}" in sc)


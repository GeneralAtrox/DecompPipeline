"""Group game functions into 'systems' (likely subsystems / source files) from the call graph.

Edges: direct calls and function pointers (hubs called by more than 25 functions are ignored), globals
shared by at most 15 functions, and address neighbours (a source file's functions are linked together).
Louvain community detection (networkx) on that weighted graph; the result is deterministic (seed 1).
Only used to order work and report progress: every function is still judged in isolation.
"""
import bisect
import collections
import json

from . import filelock, paths

GRAPH = paths.STATE / "graph.tsv"
SYSTEMS = paths.STATE / "systems.json"


def read_graph():
    rows = {}
    for line in GRAPH.read_text(encoding="utf-8").splitlines(True):
        p = line.rstrip("\n").split("\t")
        split = lambda s: {x for x in s.split(",") if x}  # noqa: E731
        rows[p[0]] = {"calls": split(p[1]), "fptrs": split(p[2]), "globs": split(p[3]),
                      "strs": [s for s in p[4].split("|") if s]}
    return rows


def build(functions, resolution=2.0):
    """functions: {addr: catalog entry} for the game region. Writes and returns state/systems.json."""
    import networkx as nx
    from networkx.algorithms import community

    rows = read_graph()
    game = [a for a in functions if a in rows]
    gset = set(game)
    callers = collections.Counter(c for a in game for c in rows[a]["calls"] if c in gset)
    g = nx.Graph()
    g.add_nodes_from(game)

    def bump(a, b, w):
        g.add_edge(a, b, weight=g.get_edge_data(a, b, {"weight": 0})["weight"] + w)

    for a in game:
        for c in rows[a]["calls"] | rows[a]["fptrs"]:
            if c in gset and c != a and callers[c] <= 25:
                bump(a, c, 3)
    users = collections.defaultdict(list)
    for a in game:
        for glob in rows[a]["globs"]:
            users[glob].append(a)
    for us in users.values():
        if 1 < len(us) <= 15:
            us.sort()
            for i, x in enumerate(us):
                for y in us[i + 1:]:
                    bump(x, y, 2.0 / (len(us) - 1))
    order = sorted(game, key=lambda a: int(a, 16))
    for x, y in zip(order, order[1:]):
        bump(x, y, 1.0)
    comms = community.louvain_communities(g, weight="weight", resolution=resolution, seed=1)
    comms = sorted(comms, key=lambda c: min(int(a, 16) for a in c))
    out = []
    for i, members in enumerate(comms, 1):
        strs = collections.Counter(s for a in members for s in rows[a]["strs"])
        src = [s.lstrip(".\\") for s, _ in strs.most_common() if s.lower().endswith(".c")]
        hint = src[0] if src else next((s for s, _ in strs.most_common() if len(s) >= 6), "")
        out.append({"id": f"S{i:02d}", "hint": hint[:28], "functions": sorted(members)})
    with filelock.locked(SYSTEMS):
        paths.write_json(SYSTEMS, {"resolution": resolution, "systems": out})
    return out


def extend(functions):
    """Add game functions found after the systems were built to the existing systems, keeping every id and
    member (designs and names refer to them). Votes: call/pointer edges either way 3, a shared global 1, each
    address neighbour 1. Writes and returns the systems; new functions with no vote at all get none."""
    with filelock.locked(SYSTEMS):  # read-modify-write: retire and tools/assign_systems.py write it too
        return _extend(functions)


def _extend(functions):
    rows = read_graph()
    data = paths.read_json(SYSTEMS)
    sys_of = {a: s["id"] for s in data["systems"] for a in s["functions"]}
    new = sorted((a for a in functions if a in rows and a not in sys_of), key=lambda a: int(a, 16))
    callers = collections.defaultdict(set)
    for a, r in rows.items():
        for c in r["calls"] | r["fptrs"]:
            callers[c].add(a)
    users = collections.defaultdict(set)
    for a, r in rows.items():
        for glob in r["globs"]:
            users[glob].add(a)
    for _ in range(3):  # new functions next to or calling each other: repeat as votes appear
        order = sorted(sys_of, key=lambda a: int(a, 16))
        keys = [int(a, 16) for a in order]
        added = {}
        for a in new:
            if a in sys_of:
                continue
            votes = collections.Counter()
            for c in rows[a]["calls"] | rows[a]["fptrs"] | callers[a]:
                if c in sys_of:
                    votes[sys_of[c]] += 3
            for glob in rows[a]["globs"]:
                if len(users[glob]) <= 15:
                    votes.update(sys_of[u] for u in users[glob] if u in sys_of)
            i = bisect.bisect_left(keys, int(a, 16))
            for j in (i - 1, i):
                if 0 <= j < len(order):
                    votes[sys_of[order[j]]] += 1
            if votes:
                added[a] = max(sorted(votes), key=lambda s: votes[s])
        if not added:
            break
        sys_of.update(added)
    for s in data["systems"]:
        s["functions"] = sorted(a for a, sid in sys_of.items() if sid == s["id"])
    paths.write_json(SYSTEMS, data)
    return data["systems"], sum(1 for a in new if a in sys_of)


def load():
    """{addr: system id} and the list of systems, or empty if not built yet."""
    data = paths.read_json(SYSTEMS)
    if not data:
        return {}, []
    return {a: s["id"] for s in data["systems"] for a in s["functions"]}, data["systems"]


def summary(systems, functions, ledger):
    """Per-system progress rows for the UI."""
    rows = []
    for s in systems:
        el = [a for a in s["functions"] if functions.get(a, {}).get("tier") not in (None, "excluded")]
        st = collections.Counter(ledger.get(a, {}).get("status", "queued") for a in el)
        rows.append({"id": s["id"], "hint": s["hint"], "eligible": len(el), "matched": st["matched"],
                     "failed": st["failed"] + st["error"] + st["skipped"],
                     "active": st["preparing"] + st["running"] + st["waiting"]})
    return rows


def dumps_debug(systems):
    return json.dumps([{k: s[k] for k in ("id", "hint")} | {"n": len(s["functions"])} for s in systems], indent=1)

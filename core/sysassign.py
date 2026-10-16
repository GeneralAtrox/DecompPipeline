"""Put every game function that is in no system into a system, without touching existing ids or members.

Systems (state/systems.json) are built once (core/systems.build) and then designed by hand: their ids and members
are referenced by designs, names and headers, so this only ever adds. A function found later (a catalog rebuild,
a split, a seed) is placed by evidence, strongest first, repeating until nothing changes (a function placed in one
round gives evidence to its neighbours in the next):

  call      call or function-pointer edges either way with members (hub callees with more than HUB callers ignored,
            as in systems.build); the most edges wins;
  global    globals shared with members (globals used by at most GLOBAL_USERS functions); the most wins;
  enclosed  the nearest assigned functions below and above it in address order are in the same system (a source
            file's functions are contiguous);
  ties      the system with a member nearest in address (address adjacency), then the lower id.

Edges come from state/graph.tsv (TcGraph) and from each function's harness (or packet) meta.json `callees` and
`globals`, so functions the graph export predates still get their calls. What is left has no affinity at all:
it is grouped into clusters (contiguous in address order, joined by calls among themselves), and each cluster
becomes a new system numbered after the highest id. With attach=True such a cluster joins the system of the
function just before it in address order instead (tier "adjacent").

Extra functions (project.json "extra_functions", core/scope.py: library code in scope, e.g. a studio's engine) are
placed in a second pass with the same rules over the extras alone: votes come only from systems that hold extras,
edges only between extras, and "address order" is the catalog's, broken wherever a function that is not an extra
(SDK code) lies between two extras. So the hand-designed game systems never change. Extras with no affinity to an
existing extra system are grouped as core/systems.build groups a game (Louvain communities of their own call graph:
calls 3, shared globals, adjacent neighbours 1; seed 1; resolution EXTRA_RESOLUTION 1.0, as 2.0 splits a few
hundred functions into many tiny systems), not into call-connected clusters, which in a library chain into one huge
group (TC's 313 engine functions: one of 239); each community becomes a new system (hint "extra").

Pure: takes a workspace path. Writes systems.json (under its file lock) and records every placement in
state/systems_assigned.json.
"""
import bisect
import collections
import datetime
import json
import pathlib
import re

from . import filelock, retire, scope
from .scope import game_functions, game_ranges, in_scope, scope_functions  # noqa: F401  (defined in core/scope.py)

SYSTEMS = "state/systems.json"
ASSIGNED = "state/systems_assigned.json"
HUB = 25
GLOBAL_USERS = 15
EXTRA_HINT = "extra"
EXTRA_RESOLUTION = 1.0


def unassigned(ws):
    """(functions in scope, game and extra, in no system; systems.json exists)."""
    data = retire._read(ws, SYSTEMS)
    if not data:
        return [], False
    members = {a for s in data.get("systems") or [] for a in s.get("functions") or []}
    return [a for a in scope_functions(ws) if a not in members], True


def extras_order(ws, extras):
    """The extras in catalog address order, with None wherever a catalog function that is not an extra lies between
    two of them (a break: they are not neighbours in the binary)."""
    xs, out = set(extras), []
    for a in sorted(retire.functions(ws), key=lambda a: int(a, 16)):
        if a in xs:
            out.append(a)
        elif out and out[-1] is not None:
            out.append(None)
    while out and out[-1] is None:
        out.pop()
    return out


def edges(ws, game):
    """({addr: callees and pointer targets}, {addr: globals}, number of game functions missing from graph.tsv)."""
    ws = pathlib.Path(ws)
    mirror = retire.mirrors(ws)
    calls, globs = collections.defaultdict(set), collections.defaultdict(set)
    graph = ws / "state" / "graph.tsv"
    seen = set()
    if graph.exists():
        for line in graph.read_text(encoding="utf-8").splitlines():
            p = line.split("\t") + ["", "", "", ""]
            a = retire.canonical(p[0], mirror)
            seen.add(a)
            calls[a] |= {retire.canonical(x, mirror) for x in (p[1] + "," + p[2]).split(",") if x}
            globs[a] |= {retire.canonical(x, mirror) for x in p[3].split(",") if x}
    for a in game:
        for d in ("harness", "packets"):
            m = ws / d / a / "meta.json"
            if m.exists():
                meta = json.loads(m.read_text(encoding="utf-8"))
                calls[a] |= {retire.canonical(c["addr"], mirror) for c in meta.get("callees") or []
                             if isinstance(c, dict) and c.get("addr")}
                globs[a] |= {retire.canonical(g, mirror) for g in meta.get("globals") or [] if isinstance(g, str)}
                break
    return calls, globs, sum(1 for a in game if a not in seen)


def _next_ids(systems, n):
    ids = [s["id"] for s in systems]
    m = [re.fullmatch(r"([A-Za-z_]*)(\d+)", i) for i in ids]
    prefix = next((x.group(1) for x in m if x), "S")
    width = max((len(x.group(2)) for x in m if x), default=2)
    top = max((int(x.group(2)) for x in m if x), default=0)
    return [f"{prefix}{top + k:0{width}d}" for k in range(1, n + 1)]


def plan(ws, attach=False):
    """{"assign": {addr: {"system", "how"}}, "new": [{"id", "functions"}], "before": n unassigned,
    "missing_graph": n, "systems": n existing, "extras": n of the unassigned that are extras} or None when the
    project has no systems.json yet. Game functions first, then the extras (their own pass: module docstring)."""
    data = retire._read(ws, SYSTEMS)
    if not data:
        return None
    out = {"assign": {}, "new": [], "before": 0, "missing_graph": 0, "systems": len(data.get("systems") or []),
           "extras": 0}
    game = game_functions(ws)
    _pass(ws, data, game, game, attach, "", out)
    extras = scope.extra_functions(ws)
    if extras:
        before = out["before"]
        _pass(ws, data, extras, extras_order(ws, extras), attach, EXTRA_HINT, out)
        out["extras"] = out["before"] - before
    return out


def _pass(ws, data, funcs, order, attach, hint, out):
    """Place the functions of funcs in no system, by evidence among funcs only. order: funcs in address order, with
    None where neighbours are not adjacent (core/sysassign.extras_order). Adds to out."""
    gset = set(funcs)
    sys_of = {a: s["id"] for s in data.get("systems") or [] for a in s.get("functions") or [] if a in gset}
    todo = [a for a in funcs if a not in sys_of]
    out["before"] += len(todo)
    if not todo:
        return
    calls, globs, missing = edges(ws, funcs)
    out["missing_graph"] += missing
    callers = collections.defaultdict(set)
    for a in funcs:
        for c in calls[a]:
            if c in gset and c != a:
                callers[c].add(a)
    near = collections.defaultdict(set)  # call/pointer neighbours, hubs ignored
    for a in funcs:
        for c in calls[a]:
            if c in gset and c != a and len(callers[c]) <= HUB:
                near[a].add(c)
                near[c].add(a)
    users = collections.defaultdict(set)
    for a in funcs:
        for g in globs[a]:
            users[g].add(a)
    pos = {a: i for i, a in enumerate(order) if a is not None}

    def side(i, step):
        """The system of the nearest assigned function from position i in direction step, up to a break."""
        j = i + step
        while 0 <= j < len(order) and order[j] is not None:
            if order[j] in sys_of:
                return sys_of[order[j]]
            j += step
        return None

    def bracket(a):
        return side(pos[a], -1), side(pos[a], 1)

    def nearest(a, cands):
        x = int(a, 16)
        members = collections.defaultdict(list)
        for m, s in sys_of.items():
            if s in cands:
                members[s].append(int(m, 16))
        dist = {}
        for s, ms in members.items():
            ms.sort()
            i = bisect.bisect_left(ms, x)
            dist[s] = min(abs(ms[j] - x) for j in (i - 1, i) if 0 <= j < len(ms))
        return min(sorted(cands), key=lambda s: dist.get(s, 1 << 40))

    def votes(a, tier):
        v = collections.Counter()
        if tier == "call":
            v.update(sys_of[c] for c in near[a] if c in sys_of)
        elif tier == "global":
            for g in globs[a]:
                if len(users[g]) <= GLOBAL_USERS:
                    v.update(sys_of[u] for u in users[g] if u in sys_of and u != a)
        else:
            lo, hi = bracket(a)
            if lo is not None and lo == hi:
                v[lo] = 1
        return v

    changed = True
    while changed:
        changed = False
        for tier in ("call", "global", "enclosed"):
            while True:
                add = {}
                for a in todo:
                    if a not in sys_of:
                        v = votes(a, tier)
                        if v:
                            top = max(v.values())
                            add[a] = nearest(a, {s for s in v if v[s] == top})
                if not add:
                    break
                changed = True
                for a, s in add.items():
                    sys_of[a] = s
                    out["assign"][a] = {"system": s, "how": tier}
    left = [a for a in todo if a not in sys_of]
    clusters = _communities(order, pos, left, near, users) if hint == EXTRA_HINT else _clusters(order, pos, left, near)
    if attach:  # join the system just before (else after) the cluster; only a project with no members makes new ones
        for cl in [c for c in clusters if any(bracket(c[0]))]:
            lo, hi = bracket(cl[0])
            for a in cl:
                out["assign"][a] = {"system": lo or hi, "how": "adjacent"}
        clusters = [c for c in clusters if not any(bracket(c[0]))]
    if clusters:
        for cl, sid in zip(clusters, _next_ids((data.get("systems") or []) + out["new"], len(clusters))):
            out["new"].append({"id": sid, "hint": hint, "functions": cl})
            for a in cl:
                out["assign"][a] = {"system": sid, "how": "new"}


def _communities(order, pos, left, near, users, resolution=EXTRA_RESOLUTION):
    """The functions left, grouped by Louvain communities (core/systems.build's weights) of the graph among them."""
    import networkx as nx
    from networkx.algorithms import community

    lset = set(left)
    g = nx.Graph()
    g.add_nodes_from(left)

    def bump(a, b, w):
        g.add_edge(a, b, weight=g.get_edge_data(a, b, {"weight": 0})["weight"] + w)

    for a in left:
        for c in sorted(near[a] & lset):
            if a < c:  # near is symmetric: each edge once
                bump(a, c, 3)
    for us in users.values():
        us = sorted(u for u in us if u in lset) if len(us) <= GLOBAL_USERS else []
        for i, x in enumerate(us):
            for y in us[i + 1:]:
                bump(x, y, 2.0 / (len(us) - 1))
    for a in left:
        nxt = order[pos[a] + 1] if pos[a] + 1 < len(order) else None
        if nxt in lset:
            bump(a, nxt, 1.0)
    comms = community.louvain_communities(g, weight="weight", resolution=resolution, seed=1)
    return sorted((sorted(c, key=lambda a: int(a, 16)) for c in comms), key=lambda c: int(c[0], 16))


def _clusters(game, pos, left, near):
    """Functions with no affinity, grouped: address-contiguous runs (no assigned function between), joined by calls."""
    parent = {a: a for a in left}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    lset = set(left)
    for a in left:
        nxt = game[pos[a] + 1] if pos[a] + 1 < len(game) else None
        if nxt in lset:
            parent[find(nxt)] = find(a)
        for c in near[a] & lset:
            parent[find(c)] = find(a)
    groups = collections.defaultdict(list)
    for a in left:
        groups[find(a)].append(a)
    return sorted((sorted(g, key=lambda a: int(a, 16)) for g in groups.values()), key=lambda g: int(g[0], 16))


def apply(ws, attach=False):
    """Plan and write under the systems.json lock (so the plan sees the file as written). Returns the plan."""
    ws = pathlib.Path(ws)
    with filelock.locked(ws / SYSTEMS):
        p = plan(ws, attach)
        if not p or not p["assign"]:
            return p
        data = retire._read(ws, SYSTEMS)
        for s in data["systems"]:
            add = [a for a, x in p["assign"].items() if x["system"] == s["id"]]
            if add:
                s["functions"] = sorted(set(s["functions"]) | set(add), key=lambda a: int(a, 16))
        data["systems"] += p["new"]
        filelock.write_json(ws / SYSTEMS, data)
    at = datetime.datetime.now().isoformat(timespec="seconds")
    filelock.update_json(ws / ASSIGNED, lambda d: d.update({a: {**x, "at": at} for a, x in p["assign"].items()}), {})
    return p


def report(p):
    """Report lines for a plan."""
    if p is None:
        return ["no state/systems.json: build the systems first (decomp.py systems)"]
    lines = [f"{p['before']} game function(s) in no system (of {p['systems']} existing systems)"]
    if p.get("extras"):
        lines[0] = (f"{p['before'] - p['extras']} game function(s) and {p['extras']} extra function(s) "
                    f"(project.json extra_functions) in no system (of {p['systems']} existing systems)")
    if not p["assign"]:
        return lines + ["nothing to assign"]
    how = collections.Counter(x["how"] for x in p["assign"].values())
    lines.append("placed by: " + ", ".join(f"{k} {how[k]}" for k in ("call", "global", "enclosed", "adjacent", "new")
                                             if how[k]))
    if p["missing_graph"]:
        lines.append(f"{p['missing_graph']} function(s) missing from state/graph.tsv (calls taken from their "
                     "harness/packet meta.json)")
    per = collections.Counter(x["system"] for x in p["assign"].values())
    new_ids = {s["id"] for s in p["new"]}
    lines.append("added per existing system: " + (" ".join(f"{s}+{n}" for s, n in sorted(per.items())
                                                           if s not in new_ids) or "none"))
    for s in p["new"]:
        lines.append(f"new system {s['id']}{' (extra)' if s['hint'] == EXTRA_HINT else ''} ({len(s['functions'])}): "
                     f"{' '.join(s['functions'])}")
    return lines

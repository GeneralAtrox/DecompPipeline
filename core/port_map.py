"""Port readiness map: what a workspace's records say about one function, and the original's call tree from an entry.

A function is one of:
  ready           raw C and readable C both pass on record, and it has real names
  needs_raw       no matched raw C on record (library code, hardware functions and refused rows included; `why` says)
  needs_readable  raw C is matched; readable C is missing, stale, failing or still machine-named
  kit             the kit replaces it: a module of port/sdk or port/runtime the game turns on defines its registry name
  native          the game's own port code replaces it (core/port_map_native says how it is bound)
  outside         a call goes there, but this workspace's catalog has no such function (an overlay, another workspace)

The walk does not descend through kit and native functions, whose originals no longer run, except from the entry
itself: the tree asked for is the original's. A game.json `wrap` keeps the original running, so a wrapped function
keeps its decomp state, and the walk goes on through it.

Ready is what the records say, never a judgement of the file: a row can be forged. A status alone is not accepted (a
PASS attempt or a verify time, the files on disk, and nothing changed after the judge saw it), and tools/port_map.py
judge re-judges a function without recording anything. A harness that moved since the raw match is a flag, not a
verdict, because a re-prepare does not rewrite the ledger.

Call edges come from core/port_map_edges.py. The tree is never complete by itself: `completeness` counts indirect call
sites, jump tables, functions with no edge data and every disagreement between edge sources.

Pure (no core.paths): takes a workspace path. Reads only.

Nearest existing: core/sysassign.py (reads the same edges, for placing a function in a system, and has no states) and
core/final_audit.py (whole-game verdict that needs core.paths and runs builds) - neither walks from an entry or says
what one function still needs. Each game's boot_closure.tsv came from one-off scripts that are not in this repository.
"""
import collections
import datetime
import hashlib
import json
import pathlib
import re

from .port_map_edges import edge_gaps, edges
from .raw_names import mask, proto_name

READY, NEEDS_RAW, NEEDS_READABLE, KIT, NATIVE, OUTSIDE = "ready", "needs_raw", "needs_readable", "kit", "native", "outside"
STATES = (READY, NEEDS_READABLE, NEEDS_RAW, KIT, NATIVE, OUTSIDE)
HARNESS_FILES = ("vectors.txt", "expected.txt")
WEAK_PROOF = ("matched_low_coverage", "weak_harness")
CHANGED_GRACE_S = 120  # a passing file is copied before its row is written

RAW_FN = re.compile(r"\bfn_([0-9a-f]{8})\b")
MACHINE_NAME = re.compile(r"^(?:fn|FUN|sub|thunk_FUN)_|_[0-9a-f]{8}$")


def _json(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _text(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def text_sha(path):
    """sha256 of a text file with CRLF as LF (a checkout on Windows must not read as a change); None: no file."""
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return None


def file_sha(path):
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _rows(data):
    return (data or {}).get("functions", data) or {}


class Workspace:
    """A project workspace's catalog, ledgers, names and graph, read once."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        if not (self.path / "project.json").is_file():
            raise ValueError(f"not a project workspace (no project.json): {self.path}")
        self.project = _json(self.path / "project.json") or {}
        catalog = _rows(_json(self.path / "state" / "catalog.json"))
        rows = catalog if isinstance(catalog, list) else list(catalog.values())
        self.catalog = {e["addr"]: e for e in rows if isinstance(e, dict) and e.get("addr")}
        self.ledger_found = (self.path / "state" / "ledger.json").is_file()
        self.ledger = _rows(_json(self.path / "state" / "ledger.json"))
        self.readable = _rows(_json(self.path / "state" / "readable.json"))
        verdicts = _rows(_json(self.path / "state" / "translator_verdicts.json"))
        self.translator_pass = {a: v == "PASS" for a, v in verdicts.items()}
        self.names, self.proto = {}, {}
        for f in sorted((self.path / "readable" / "names").glob("*.json")):
            for a, e in ((_json(f) or {}).get("functions") or {}).items():
                name = proto_name(str((e or {}).get("proto", "")))
                if name:
                    self.names[a.lower()] = name
                    self.proto[a.lower()] = e["proto"]
        self.by_name = {n: a for a, n in self.names.items()}
        self.graph = {}
        for ln in (_text(self.path / "state" / "graph.tsv") or "").splitlines():
            p = ln.split("\t")
            if len(p) >= 3:
                self.graph[p[0]] = ({x for x in p[1].split(",") if x}, {x for x in p[2].split(",") if x})
        self.ranges = [(int(a, 16), int(b, 16)) for a, b in self.project.get("game_ranges") or []]
        if not self.ranges and self.project.get("game_end"):
            self.ranges = [(0, int(self.project["game_end"], 16))]

    def function(self, token):
        """A catalog function for an address word, through the SH-4 P0 mirror of cached RAM (0x0c.. for 0x8c..)."""
        token = token.lower()
        if token in self.catalog:
            return token
        mirror = f"{int(token, 16) | 0x80000000:08x}"
        return mirror if mirror in self.catalog else None

    def resolve(self, entry):
        """An address, fn_ADDR or a registry name to an address. Raises ValueError when it is none of them."""
        text = str(entry).strip()
        m = re.fullmatch(r"(?:0x|fn_)?([0-9a-fA-F]{8})", text)
        if m:
            found = self.function(m.group(1))
            if not found:
                raise ValueError(f"{text} is not a function in this workspace's catalog")
            return found
        if text in self.by_name:
            return self.by_name[text]
        raise ValueError(f"unknown entry: {text} (no registry name; give an address)")

    def in_game(self, addr):
        return any(lo <= int(addr, 16) < hi for lo, hi in self.ranges) if self.ranges else True

    def harness(self, addr):
        return self.path / "harness" / addr

    def raw_file(self, addr):
        return self.path / "matched" / f"{addr}.c"

    def readable_file(self, addr):
        return self.path / "readable" / "src" / f"{addr}.c"

    def meta(self, addr):
        return _json(self.harness(addr) / "meta.json") or _json(self.path / "packets" / addr / "meta.json")

    def fingerprint(self, addr):
        """What a function's port code stands on; the drift check compares this to a recorded one."""
        row, rd = self.ledger.get(addr) or {}, self.readable.get(addr) or {}
        return {"raw": text_sha(self.raw_file(addr)), "readable": text_sha(self.readable_file(addr)),
                "vectors": file_sha(self.harness(addr) / "vectors.txt"),
                "expected": file_sha(self.harness(addr) / "expected.txt"),
                "raw_status": row.get("status"), "raw_verdict": row.get("verdict_detail"),
                "readable_status": rd.get("status"), "readable_verified": rd.get("verified") or rd.get("finished")}


def _local(stamp):
    try:
        return datetime.datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def raw_state(ws, addr):
    """(raw C passes on record, why not, flags)."""
    row = ws.ledger.get(addr)
    if not ws.ledger_found:
        return False, "this workspace has no state/ledger.json", []
    if not row:
        return False, "no ledger row", []
    if row.get("status") != "matched":
        return False, f"ledger status {row.get('status')}", []
    if not ws.raw_file(addr).is_file():
        return False, "the ledger says matched, but matched/ADDR.c is missing", []
    if not str(row.get("verdict_detail") or "").startswith("PASS"):
        return False, f"the ledger says matched without a PASS on record ({row.get('verdict_detail')})", []
    if not (ws.harness(addr) / "meta.json").is_file():
        return False, "the ledger says matched, but it has no harness to be judged on", []
    flags = []
    recorded = row.get("harness_sha256") or {}
    if any(recorded.get(n) and recorded[n] != file_sha(ws.harness(addr) / n) for n in HARNESS_FILES):
        flags.append("harness_moved_since_match")
    proof = row.get("proof")
    if isinstance(proof, dict):
        proof = proof.get("state")
    if proof in WEAK_PROOF:
        flags.append(proof)
    return True, "", flags


def readable_state(ws, addr):
    """(readable C passes on record with real names, why not, flags)."""
    row = ws.readable.get(addr)
    path = ws.readable_file(addr)
    if not row:
        return False, "a readable file with no ledger row" if path.is_file() else "no readable C", []
    if row.get("status") != "readable":
        return False, f"readable status {row.get('status')}" + (f": {row['note'][:100]}" if row.get("note") else ""), []
    if not path.is_file():
        return False, "the row says readable, but readable/src/ADDR.c is missing", []
    attempts = row.get("attempts") or []
    if not (row.get("verified") or any(a.get("verdict") == "PASS" for a in attempts)):
        return False, "the row says readable with no PASS attempt and no verify time", []
    stamps = [t for t in (_local(row.get("verified")), _local(row.get("finished"))) if t is not None]
    if stamps and path.stat().st_mtime > max(stamps) + CHANGED_GRACE_S:
        return False, "readable/src/ADDR.c changed after the judge last saw it", []
    name = ws.names.get(addr)
    if not name:
        return False, "machine names: no registry name", []
    if MACHINE_NAME.search(name):
        return False, f"machine names: {name}", []
    if row.get("readable_by") == "machine":
        return False, "machine names: no agent has named this file's locals", []
    code = mask(_text(path) or "")[0]
    named_raw = sorted({a for a in RAW_FN.findall(code) if a in ws.names and not MACHINE_NAME.search(ws.names[a])})
    if named_raw:
        return False, f"machine names: uses fn_{named_raw[0]} for a function the registry has named", []
    flags = ["calls_unnamed_function"] if RAW_FN.search(code) else []
    if not any(a.get("verdict") == "PASS" for a in attempts[-1:]) and attempts:
        flags.append("last_attempt_not_pass")
    return True, "", flags


def decomp_state(ws, addr):
    """(state among ready, needs_raw, needs_readable; why; flags) from the records alone."""
    entry = ws.catalog.get(addr)
    if entry is None:
        return OUTSIDE, "not a function in this workspace's catalog", []
    flags = [] if ws.in_game(addr) else ["library"]
    ok, why, more = raw_state(ws, addr)
    flags += more
    if not ok:
        if entry.get("hw_refs") or entry.get("sys_ops"):
            flags.append("hardware")
        if ws.translator_pass.get(addr):
            flags.append("translator_passed_not_in_ledger")
        return NEEDS_RAW, why, flags
    ok, why, more = readable_state(ws, addr)
    flags += more
    return (READY, "", flags) if ok else (NEEDS_READABLE, why, flags)


def walk(ws, entry, binds=None, pointers=True, max_depth=None):
    """The tree from entry, every function once at its shallowest depth: call edges first, then what only a pointer
    or the text reaches. {"entry", "rows": [...], "summary", "completeness"}. binds: core/port_map_native.Bindings."""
    root = ws.resolve(entry)
    rows, gaps, counts = {}, [], collections.Counter()
    indirect = {}

    def visit(start, kinds):
        """Breadth first over `kinds` of edge from start: (addr, depth, via, reached)."""
        todo = collections.deque(start)
        while todo:
            addr, depth, via, reached = todo.popleft()
            if addr in rows:
                continue
            row = classify(ws, addr, binds)
            row.update(depth=depth, via=via, reached=reached)
            rows[addr] = row
            if row["state"] == OUTSIDE or (row["state"] in (KIT, NATIVE) and addr != root):
                counts["cut_at_replacement"] += row["state"] != OUTSIDE
                row["_edges"] = None
                continue
            found = edges(ws, addr)
            row["_edges"] = found
            gaps.extend(edge_gaps(ws, addr, found))
            n = (ws.catalog.get(addr) or {}).get("indirect_calls") or 0
            row["indirect_calls"] = max(n, len(found["indirect"]))
            if row["indirect_calls"]:
                indirect[addr] = {"count": row["indirect_calls"], "sites": found["indirect"]}
            counts["jump_tables"] += (ws.catalog.get(addr) or {}).get("jump_tables") or 0
            if max_depth is not None and depth >= max_depth:
                counts["cut_at_depth"] += 1
                continue
            for kind in kinds:
                for t in sorted(found[kind]):
                    if t not in rows:
                        todo.append((ws.function(t) or t, depth + 1, addr, "call" if kind == "calls" else
                                     "pointer" if kind == "refs" else "text"))

    visit([(root, 0, None, "entry")], ("calls",))
    if pointers:  # what the call edges did not reach, and everything that reaches in turn
        for addr, row in list(rows.items()):
            if row.get("_edges"):
                for kind, label in (("refs", "pointer"), ("text", "text")):
                    start = [(ws.function(t) or t, row["depth"] + 1, addr, label) for t in sorted(row["_edges"][kind])]
                    visit(start, ("calls", "refs", "text"))
    for row in rows.values():
        row.pop("_edges", None)
    ordered = sorted(rows.values(), key=lambda r: (r["depth"], int(r["addr"], 16)))
    by_kind = collections.Counter(g["kind"] for g in gaps)
    return {"entry": root, "name": ws.names.get(root), "rows": ordered, "summary": summarise(ordered),
            "completeness": {"complete": False,
                             "why": "indirect calls and pointers held only in data tables are not edges here",
                             "indirect_call_sites": sum(v["count"] for v in indirect.values()),
                             "functions_with_indirect_calls": indirect,
                             "jump_tables": counts["jump_tables"],
                             "cut_at_replacement": counts["cut_at_replacement"],
                             "cut_at_depth": counts["cut_at_depth"],
                             "edge_gaps": dict(by_kind), "gaps": gaps}}


def classify(ws, addr, binds=None):
    """One function's row. The decomp's own state is kept beside what the port does with it."""
    state, why, flags = decomp_state(ws, addr)
    row = {"addr": addr, "name": ws.names.get(addr), "state": state, "decomp": state, "why": why, "flags": flags,
           "bytes": (ws.catalog.get(addr) or {}).get("bytes") or 0}
    if binds is None:
        return row
    native = binds.native.get(addr) or []
    if native:
        row["native"] = [dict(b) for b in native]
    if any(b["kind"] != "wrap" and b["defined"] for b in native):
        row["state"], row["why"] = NATIVE, ", ".join(sorted({f"{b['fn']} ({b['file']})" for b in native}))
    else:
        for b in native:
            if not b["defined"]:
                flags.append(f"game.json binds {b['fn']} but no source defines it")
            elif b["kind"] == "wrap":
                flags.append(f"wrapped by {b['fn']}")
        name = ws.names.get(addr)
        where = binds.kit_addr.get(addr) or (binds.kit.get(name) if name else None)
        if where and where["on"]:
            row["state"], row["why"] = KIT, where["where"]
        elif where:
            flags.append(f"kit has it in {where['where']}, which this game does not turn on")
        if addr in binds.cited_only:
            flags.append("cited_not_bound")
    return row


def summarise(rows):
    """Per state: functions and bytes; flags counted."""
    out = {s: {"functions": 0, "bytes": 0} for s in STATES}
    flags = collections.Counter()
    for r in rows:
        out[r["state"]]["functions"] += 1
        out[r["state"]]["bytes"] += r.get("bytes") or 0
        flags.update(f.split(" ")[0] if f.startswith(("wrapped", "kit", "game.json")) else f for f in r["flags"])
    return {"states": out, "flags": dict(flags), "functions": len(rows),
            "reached": dict(collections.Counter(r["reached"] for r in rows))}

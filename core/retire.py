"""Retire the artefacts of addresses that are no longer functions: move them to retired/<kind>/, never delete.

A function leaves the catalog when a catalog rule changes (decomp.py catalog / seed / setup: TcOverride, TcOrphans,
TcPrune...). Everything the pipeline keyed by its address stays behind unless it is retired: its ledger row, its
systems.json membership, harness, packet, matched C, readable file and readable ledger row, builds, runs... An
artefact is an orphan when its address is not a function of state/catalog.json (SDK functions included).

Pure functions on a workspace folder (no core.paths), so tools/retire.py, decomp.py, tools/doctor.py, the server and
the tests share them. A names entry (readable/names/*.json) for a non-function moves only when nothing else refers to
it: a tail entry that current functions call by its own address is a name, not an orphan (reported as kept).
"""
import datetime
import json
import pathlib
import re
import shutil

from . import filelock

KEY = re.compile(r"^(?:rd_)?([0-9a-f]{8})(?=$|[._])")  # ADDR, ADDR.c, ADDR.note, ADDR_x, rd_ADDR
# Folders whose entries are keyed by address: (kind, path in the workspace). project.json "artefact_dirs":
# {kind: path} adds more for one project.
DIRS = (("harness", "harness"), ("packets", "packets"), ("matched", "matched"), ("matched_stale", "matched_stale"),
        ("work", "work"), ("runs", "runs"), ("sandbox", "sandbox"), ("build", "build"), ("import", "import"),
        ("hints", "oracle/hints"), ("readable_src", "readable/src"), ("agent_notes", "agent_notes"))
LEDGER, READABLE, SYSTEMS, ESCALATE = "state/ledger.json", "state/readable.json", "state/systems.json", \
    "state/escalate.json"
REMOVED_RECORDS = ("state/ledger_removed.json", "state/pruned_ledger.json")  # rows of functions removed by hand
MANIFEST = "retired/manifest.json"


def _read(ws, rel, default=None):
    p = pathlib.Path(ws) / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _write(ws, rel, data):
    filelock.write_json(pathlib.Path(ws) / rel, data)  # atomic; callers hold filelock.locked around read-modify-write


def functions(ws):
    """Every function address of the catalog. Refuses a missing or empty catalog (everything would look orphaned)."""
    cat = _read(ws, "state/catalog.json")
    if not cat or not cat.get("functions"):
        raise RuntimeError(f"{ws}: no functions in state/catalog.json; nothing can be retired against it")
    return {f["addr"] for f in cat["functions"]}


def mirrors(ws):
    """target.json "address_mirrors" ({top byte: canonical top byte}, e.g. a RAM mirror): an address another form of
    which is a function names that function."""
    target = (_read(ws, "project.json", {}) or {}).get("target")
    t = pathlib.Path(__file__).resolve().parents[1] / "targets" / str(target) / "target.json"
    return (json.loads(t.read_text(encoding="utf-8")).get("address_mirrors") or {}) if target and t.exists() else {}


def canonical(addr, mirror):
    return mirror.get(addr[:2], addr[:2]) + addr[2:]


def artefact_dirs(ws):
    extra = (_read(ws, "project.json", {}) or {}).get("artefact_dirs") or {}
    return list(DIRS) + sorted(extra.items())


def _item(kind, addr, where, **more):
    return {"kind": kind, "addr": addr, "from": where, **more}


def scan(ws, funcs=None):
    """{"items": [orphan artefacts], "names_kept": [non-function names something refers to]}, read-only."""
    ws = pathlib.Path(ws)
    funcs = functions(ws) if funcs is None else funcs
    items = []
    for kind, rel in artefact_dirs(ws):
        d = ws / rel
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            m = KEY.match(p.name)
            if m and m.group(1) not in funcs:
                items.append(_item(kind, m.group(1), f"{rel}/{p.name}"))
    for addr in sorted(((_read(ws, LEDGER) or {}).get("functions") or {})):
        if addr not in funcs:
            items.append(_item("ledger", addr, LEDGER))
    for addr in sorted(_read(ws, READABLE) or {}):
        if addr not in funcs:
            items.append(_item("readable_ledger", addr, READABLE))
    for s in (_read(ws, SYSTEMS) or {}).get("systems") or []:
        for addr in s.get("functions", []):
            if addr not in funcs:
                items.append(_item("systems", addr, SYSTEMS, system=s.get("id")))
    for addr in sorted(_read(ws, ESCALATE) or {}):
        if addr not in funcs:
            items.append(_item("escalate", addr, ESCALATE))
    names = [(f, a, e) for f, a, e in _names_entries(ws) if a not in funcs]
    mirror = mirrors(ws)
    kept = [{"addr": a, "file": f, "name": _proto_name(e.get("proto", "")), "referenced_by": [], "refs": 0,
             "alias_of": canonical(a, mirror)} for f, a, e in names if canonical(a, mirror) in funcs]
    names = [(f, a, e) for f, a, e in names if canonical(a, mirror) not in funcs]
    if names:
        orphan_addrs = {i["addr"] for i in items}
        refs = _references(ws, funcs)
        for f, addr, e in names:
            name = _proto_name(e.get("proto", ""))
            by = sorted(refs["addr"].get(addr, set()) | (refs["name"].get(name, set()) if name else set()))
            if by:
                kept.append({"addr": addr, "file": f, "name": name, "referenced_by": by[:12], "refs": len(by),
                             "was_function": addr in orphan_addrs})
            else:
                items.append(_item("names", addr, f"readable/names/{f}", name=name))
    return {"items": items, "names_kept": kept}


def _names_entries(ws):
    d = pathlib.Path(ws) / "readable" / "names"
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        for addr, e in (json.loads(f.read_text(encoding="utf-8")).get("functions") or {}).items():
            out.append((f.name, addr.lower().removeprefix("0x").rjust(8, "0"), e if isinstance(e, dict) else {}))
    return out


def _proto_name(proto):
    m = re.search(r"\b([A-Za-z_]\w*)\s*\(", proto)
    return m.group(1) if m else None


def _references(ws, funcs):
    """Who refers to what, among current functions only: {"addr": {addr: {who}}, "name": {identifier: {who}}} from
    harness metas (callees, data functions: every 8-hex-digit word), readable/src files and hand-written headers."""
    ws = pathlib.Path(ws)
    by_addr, by_name = {}, {}
    for meta in (ws / "harness").glob("*/meta.json") if (ws / "harness").is_dir() else []:
        owner = meta.parent.name
        if owner not in funcs:
            continue
        for a in set(re.findall(r"\b([0-9a-f]{8})\b", meta.read_text(encoding="utf-8", errors="replace"))):
            if a != owner:
                by_addr.setdefault(a, set()).add(f"harness/{owner}")
    texts = [(f"readable/src/{p.name}", p) for p in sorted((ws / "readable" / "src").glob("*.c"))
             if p.stem in funcs] if (ws / "readable" / "src").is_dir() else []
    inc = ws / "readable" / "include"
    texts += [(p.relative_to(ws).as_posix(), p) for p in sorted(inc.rglob("*.h")) if p.name != "game.h"] \
        if inc.is_dir() else []
    for who, p in texts:
        t = p.read_text(encoding="utf-8", errors="replace")
        for w in set(re.findall(r"[A-Za-z_]\w*", t)):
            by_name.setdefault(w, set()).add(who)
        for a in set(re.findall(r"\b(?:0x)?([0-9a-fA-F]{8})\b", t)):
            by_addr.setdefault(a.lower(), set()).add(who)
    return {"addr": by_addr, "name": by_name}


def reasons(ws, addrs):
    """{addr: why it is no longer a function}: the clauses of a removal record's "why" that name it (state/
    ledger_removed.json {"why", "rows"}, or a bare {addr: row} file such as pruned_ledger.json), an earlier retire,
    the known_runs.tsv run that now explains its bytes; else "not in state/catalog.json"."""
    ws = pathlib.Path(ws)
    out = {a: [] for a in addrs}
    for rel in REMOVED_RECORDS:
        d = _read(ws, rel)
        if not isinstance(d, dict):
            continue
        rows, why = (d.get("rows") or {}, d.get("why") or "") if "rows" in d else (d, "")
        for a in out:
            if a in rows or a in why:
                clauses = [c.strip() for c in re.split(r";\s*", why) if a in c]
                out[a].append(f"{pathlib.Path(rel).name}: " + (" / ".join(clauses) or why or "row recorded there"))
    known = ws / "state" / "known_runs.tsv"
    if known.exists():
        for n, line in enumerate(known.read_text(encoding="utf-8").splitlines(), 1):
            p = line.split("\t")
            if line.startswith("#") or len(p) < 5:
                continue
            lo, hi = int(p[0], 16), int(p[1], 16)
            for a in out:
                if lo <= int(a, 16) < hi:
                    out[a].append(f"known_runs.tsv line {n}: {p[2]} run {p[0]}..{p[1]} ({p[4][:160]})")
    bodies = ws / "state" / "function_bodies.txt"  # "entry lo hi" per body range (TcCatalog)
    if bodies.exists():
        for line in bodies.read_text(encoding="utf-8").splitlines():
            p = line.split()
            for a in out:
                if len(p) == 3 and int(p[1], 16) <= int(a, 16) < int(p[2], 16):
                    out[a].append(f"its bytes are now in the body of function {p[0]} (function_bodies.txt)")
    for a in [a for a, v in out.items() if not v]:  # no record: the newest ledger backup that still had its row
        for b in sorted((ws / "state").glob("ledger.before-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            row = (json.loads(b.read_text(encoding="utf-8")).get("functions") or {}).get(a)
            if row is not None:
                out[a].append(f"its ledger row was removed by hand (last in state/{b.name}, status "
                              f"{row.get('status')})")
                break
    return {a: "; ".join(v) or "not in state/catalog.json" for a, v in out.items()}


def _dest(ws, kind, name):
    d = pathlib.Path(ws) / "retired" / kind
    d.mkdir(parents=True, exist_ok=True)
    dst, n = d / name, 1
    while dst.exists():  # the same address retired again later: keep both
        dst, n = d / f"{name}.{n}", n + 1
    return dst


def apply(ws, plan, note=None, funcs=None):
    """Move every item of plan (scan()) to retired/<kind>/ and append the manifest; returns the records. JSON entries
    (ledger rows, readable rows, systems membership, names, escalations) are written to retired/<kind>/ADDR.json and
    removed from their file. Never touches an address that is a current function."""
    ws = pathlib.Path(ws)
    funcs = functions(ws) if funcs is None else funcs
    items = [i for i in plan["items"] if i["addr"] not in funcs]
    if not items:
        return []
    why = reasons(ws, sorted({i["addr"] for i in items}))
    at = datetime.datetime.now().isoformat(timespec="seconds")
    records = []

    def record(i, to, entry=None):
        rec = {"at": at, "kind": i["kind"], "addr": i["addr"], "from": i["from"],
               "to": to.relative_to(ws).as_posix(), "reason": ((note + "; ") if note else "") + why[i["addr"]]}
        if entry is not None:
            to.write_text(json.dumps({**rec, "entry": entry}, indent=2), encoding="utf-8")
        records.append(rec)

    for i in items:
        if i["from"] in (LEDGER, READABLE, SYSTEMS, ESCALATE) or i["kind"] == "names":
            continue
        src = ws / i["from"]
        if src.exists():
            dst = _dest(ws, i["kind"], src.name)
            shutil.move(str(src), str(dst))
            record(i, dst)
    by_file = {}
    for i in items:
        if i["from"] in (LEDGER, READABLE, ESCALATE) or i["kind"] in ("systems", "names"):
            by_file.setdefault(i["from"], []).append(i)
    for rel, group in sorted(by_file.items()):
        with filelock.locked(ws / rel):  # the server, refine runs and tools write these files too
            _retire_entries(ws, rel, group, record)
    with filelock.locked(ws / MANIFEST):
        _write(ws, MANIFEST, _read(ws, MANIFEST, []) + records)
    return records


def _retire_entries(ws, rel, group, record):
    """Remove group's entries from the JSON file rel (read fresh under its lock), recording each."""
    data = _read(ws, rel)
    addrs = {i["addr"] for i in group}
    if rel == SYSTEMS:
        for s in data.get("systems") or []:
            s["functions"] = [a for a in s.get("functions", []) if a not in addrs]
    table = data.get("functions") if rel == LEDGER or group[0]["kind"] == "names" else data
    for i in group:
        entry = {"system": i.get("system")} if rel == SYSTEMS else None
        if table is not None and rel != SYSTEMS:
            key = next((k for k in list(table) if k.lower().removeprefix("0x").rjust(8, "0") == i["addr"]), None)
            entry = table.pop(key) if key is not None else None
        record(i, _dest(ws, i["kind"], f"{i['addr']}.json"), entry if entry is not None else {})
    _write(ws, rel, data)


def summary(plan):
    """Lines: per kind, how many orphans and which addresses; the names kept and why."""
    by_kind = {}
    for i in plan["items"]:
        by_kind.setdefault(i["kind"], []).append(i["addr"])
    lines = [f"  {k:16s} {len(v):4d}  " + " ".join(sorted(set(v))) for k, v in sorted(by_kind.items())]
    for k in plan["names_kept"]:
        lines.append(f"  kept name {k['addr']} {k['name']} ({k['file']}): " + (
            f"another form of function {k['alias_of']} (address_mirrors): key it by {k['alias_of']} to apply it"
            if k.get("alias_of") else f"referenced by {k['refs']} ({', '.join(k['referenced_by'][:4])})"))
    return lines

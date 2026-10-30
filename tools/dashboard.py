"""dashboard.py: one page showing every game's decompilation progress, unit by unit (a treemap, as ByteMatch shows).

usage: python tools/dashboard.py [--projects-dir DIR] [--board FILE] [--out FILE] [--no-open] [--no-refresh]
       python tools/dashboard.py add|remove FOLDER     (agents: put a game folder on this machine's page, or take it off)
       python tools/dashboard.py list
  Dashboard.bat (DecompPipeline's root) runs the first form and opens the page in the default browser.

The page shows only the projects an agent added (docs/DASHBOARD.md): the game an agent sets up, and an existing game
only when the user asks for it. Nothing is searched for. `add` lists the game folder's workspaces (project.json +
state/catalog.json, here and in each mirror) in %LOCALAPPDATA%/DecompPipeline/dashboard/projects.json; run it again
when the game gains a workspace. A unit is one system of state/systems.json (functions without one: their 64 KB
address range, "8c18xxxx"); each function is sized by its bytes (state/catalog.json) and coloured by its ledger row,
state/readable.json and, with no ledger row, the translator's latest verdict (state/translator_verdicts.json). Writes
only the list (add/remove) and --out (default: index.html next to the list), one self-contained page with the data.

Two files the agents keep (docs/DASHBOARD.md has their format): GAME/progress.json, the game's port milestones
(builds, boots, playable, matches the original), what is being done now and next, and its open bugs (a game made
outside the pipeline adds its console, framework and own road of steps); and the work board (config key "board", else
PROJECTS_DIR/board.json): each round of work, what it does, its state, where it runs and which games it unblocks.
config.local.json may add "dashboard_mirrors" (folders laid out like the projects folder, e.g. another machine's live
copies; the newest copy of each workspace wins) and "dashboard_refresh" (a command run first, bounded at 600 s).

Nearest existing: tools/scorecard.py - per-console gates as text (definition of done); it has no per-function or
per-unit view and no page. Its workspace walk (scorecard_data.discover) is reused by `add`. ui/index.html is one
running server's worker monitor, not a view across games.
"""
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
import scorecard_data as sd  # noqa: E402

TOOL = sd.TOOL
from core import scope, toolcfg  # noqa: E402

SPEC = TOOL / "tools" / "scorecard" / "spec.json"
PAGE = TOOL / "tools" / "dashboard" / "page.html"
# states, in the page's legend order: readable C passes, raw C matched, agents/translator tried, refused, not started,
# and a hardware function not proven (the port kit replaces it: counted apart, not in the percentages)
STATES = ["readable", "matched", "progress", "blocked", "todo", "kit"]


def load(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def state_of(row, rd, verdict=None):
    """One function's state from its ledger row, its readable.json row and, for one the ledger has not tried, the
    translator's latest verdict (state/translator_verdicts.json: PASS is raw C proven, any other is tried)."""
    st = (row or {}).get("status") or "queued"
    if st == "matched":
        return "readable" if (rd or {}).get("status") == "readable" else "matched"
    if st in ("skipped", "error", "unsupported"):
        return "blocked"
    if st in ("failed", "running", "stopped") or (row or {}).get("attempts"):
        return "progress"
    return ("matched" if verdict == "PASS" else "progress") if verdict else "todo"


def workspace(ws):
    """{name, units: [[unit name, [[addr, bytes, state index, source], ...]], ...], counts} for one workspace: every
    function of its decompile scope (core/scope), so one with no ledger row yet counts as not done, plus any other
    ledger row. A function that touches hardware (core/coverage.excluded_functions) and is not proven is the port kit's:
    state "kit", counted apart. A workspace without a ledger (judged by the translator only, or not started) lists its
    catalog's functions."""
    led = (load(ws / "state" / "ledger.json", {}) or {}).get("functions", {})
    cat = load(ws / "state" / "catalog.json", {}) or {}
    fs = [f for f in cat.get("functions", []) if isinstance(f, dict) and "addr" in f]
    size = {f["addr"]: f.get("bytes") or 0 for f in fs}
    hw = {f["addr"] for f in fs if f.get("hw_refs") or f.get("sys_ops") or f.get("hw")}  # hw: a mirror's flag
    if led:
        try:
            led = {**{a: {"status": "skipped", "source": "hardware"} if a in hw else None
                      for a in scope.scope_functions(ws)}, **led}
        except (OSError, ValueError):  # a scope the copy cannot read (e.g. an extras file a mirror lacks)
            pass
    verdicts = (load(ws / "state" / "translator_verdicts.json", {}) or {}).get("functions", {})
    rds = load(ws / "state" / "readable.json", {}) or {}
    rds = rds.get("functions", rds) if isinstance(rds, dict) else {}
    systems = (load(ws / "state" / "systems.json", {}) or {}).get("systems", [])
    unit_of = {}
    for s in systems:
        name = s.get("id", "?") + (f" {s['hint']}" if s.get("hint") else "")
        for a in s.get("functions", []):
            unit_of.setdefault(a, name)
    units, counts = {}, {k: [0, 0] for k in STATES}
    for addr, row in (led or dict.fromkeys(size)).items():
        k = state_of(row, rds.get(addr), verdicts.get(addr))
        if addr in hw and k not in ("readable", "matched"):
            k = "kit"
        b = size.get(addr) or 2
        src = (row or {}).get("source") or ""
        units.setdefault(unit_of.get(addr) or f"{addr[:4]}xxxx", []).append([addr, b, STATES.index(k), src])
        counts[k][0] += 1
        counts[k][1] += b
    st = ws / "state"
    stamp = st / "ledger_mtime.txt"  # a mirror carries the copy's own time
    when = float(stamp.read_text()) if stamp.is_file() else max(
        (st / n).stat().st_mtime for n in ("ledger.json", "translator_verdicts.json", "catalog.json") if (st / n).is_file())
    harn = ws / "harness"
    if harn.is_dir():
        built = sum(1 for d in os.scandir(harn) if (pathlib.Path(d.path) / "meta.json").is_file())
    else:  # a mirror carries the count
        built = int((st / "harness_count.txt").read_text()) if (st / "harness_count.txt").is_file() else 0
    return {"units": sorted([u, sorted(fs)] for u, fs in units.items()), "counts": counts, "harnesses": built,
            "updated": datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M"), "_t": when}


def project_list():
    """This machine's dashboard projects (docs/DASHBOARD.md): only what an agent added with `add`. Nothing is found
    by itself: an agent adds the game it sets up, and an existing game only when the user asks for it."""
    return pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "DecompPipeline" / "dashboard" / \
        "projects.json"


def find_workspaces(folder, mirrors=()):
    """The workspaces (project.json + state/catalog.json) of one game folder, here and at the same place in each
    mirror, as paths relative to the folder."""
    found = set()
    for base in [folder, *mirrors]:
        found |= {ws.relative_to(base).as_posix() for ws in sd.discover(base, need="state/catalog.json")[0]}
    return sorted(found)


def add(folder, pdir, mirrors=()):
    """Add (or refresh) one game folder on this machine's dashboard: its workspaces are listed now, so the page never
    searches. A game with a workspace and no progress.json gets a starter one; a game with no workspace needs a
    progress.json with a "console" first (docs/DASHBOARD.md). Returns the entry, or exits saying what is missing."""
    folder = pathlib.Path(folder).resolve()
    if not folder.is_dir():
        sys.exit(f"{folder}: no such folder")
    rel = folder.relative_to(pdir.resolve()) if folder.is_relative_to(pdir.resolve()) else None
    wss = find_workspaces(folder, [pathlib.Path(m) / rel for m in mirrors] if rel is not None else [])
    pr = folder / "progress.json"
    if not wss and not (load(pr, {}) or {}).get("console"):
        sys.exit(f"{folder}: no workspace (project.json + state/catalog.json) and no progress.json with a console")
    if not pr.exists():
        pr.write_text(json.dumps({
            "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "milestones": {"builds": None, "boots": None, "playable": None, "matches": None},
            "now": "Set up from the disc; the functions are catalogued.",
            "next": "Prepare each function's tests, then the translator and the judge.", "bugs": []}, indent=1) + "\n",
            encoding="utf-8")
    lst = project_list()
    keep = [e for e in (load(lst, {}) or {}).get("projects", []) if pathlib.Path(e["folder"]) != folder]
    entry = {"folder": str(folder), "workspaces": wss}
    lst.parent.mkdir(parents=True, exist_ok=True)
    lst.write_text(json.dumps({"projects": keep + [entry]}, indent=1) + "\n", encoding="utf-8")
    return entry


def remove(folder):
    lst = project_list()
    folder = pathlib.Path(folder).resolve()
    old = (load(lst, {}) or {}).get("projects", [])
    keep = [e for e in old if pathlib.Path(e["folder"]) != folder]
    lst.write_text(json.dumps({"projects": keep}, indent=1) + "\n", encoding="utf-8")
    return len(old) - len(keep)


def collect(projects, pdir, mirrors=()):
    """One row per listed workspace, read from the game folder or from the same place in a mirror (a folder laid out
    like the projects folder, e.g. another machine's live copies), the newest copy winning; one row for a listed game
    with no workspace (its progress.json "road" stands in for the steps)."""
    spec = load(SPEC, {}) or {}
    name_of = {c.get("target"): c.get("name", k) for k, c in spec.get("consoles", {}).items()}
    rows = []
    for e in projects:
        folder = pathlib.Path(e["folder"])
        pr = load(folder / "progress.json", {}) or {}
        rel = folder.relative_to(pdir) if folder.is_relative_to(pdir) else None
        bases = [(folder, "here")] + ([(pathlib.Path(m) / rel, "mirror") for m in mirrors] if rel is not None else [])
        common = {"game": folder.name, "port": (folder / "port" / "game.json").is_file(), "progress": pr}
        got = 0
        for part in e.get("workspaces", []):
            best = None
            for base, where in bases:
                ws = base / part
                if (ws / "state" / "catalog.json").is_file():
                    w = {"console": name_of.get((load(ws / "project.json", {}) or {}).get("target"),
                                                pr.get("console") or "?"),
                         "part": part, "where": where, **common, **workspace(ws)}
                    best = w if best is None or w["_t"] > best["_t"] else best
            if best:
                rows.append(best)
                got += 1
        if not got:
            when = (folder / "progress.json").stat().st_mtime if (folder / "progress.json").is_file() else 0
            rows.append({"console": pr.get("console") or "?", "part": "main", "where": "here", **common, "units": [],
                         "counts": {k: [0, 0] for k in STATES}, "harnesses": 0, "_t": when,
                         "updated": datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M")})
    return sorted(rows, key=lambda w: (w["console"], w["game"], w["part"]))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cfg = toolcfg.load(TOOL)
    mirrors = cfg.get("dashboard_mirrors") or ()
    if argv[:1] in (["add"], ["remove"], ["list"]):
        ap = argparse.ArgumentParser(prog="dashboard.py " + argv[0])
        if argv[0] != "list":
            ap.add_argument("folder", help="the game's own folder (the one that holds its decomp/ or its progress.json)")
        ap.add_argument("--projects-dir")
        a = ap.parse_args(argv[1:])
        pdir = pathlib.Path(a.projects_dir or cfg.get("projects_dir") or TOOL.parent)
        if argv[0] == "add":
            e = add(a.folder, pdir, mirrors)
            print(f"added {e['folder']}: {len(e['workspaces'])} workspace(s) {e['workspaces']}")
        elif argv[0] == "remove":
            print(f"removed {remove(a.folder)} entr(y/ies) -> {project_list()}")
        else:
            for e in (load(project_list(), {}) or {}).get("projects", []):
                print(e["folder"], e.get("workspaces", []))
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects-dir")
    ap.add_argument("--out")
    ap.add_argument("--board", help="the work board (default: config key board, else PROJECTS_DIR/board.json)")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-refresh", action="store_true", help="skip config dashboard_refresh")
    a = ap.parse_args(argv)
    pdir = pathlib.Path(a.projects_dir or cfg.get("projects_dir") or TOOL.parent)
    out = pathlib.Path(a.out) if a.out else project_list().with_name("index.html")
    if cfg.get("dashboard_refresh") and not a.no_refresh:  # e.g. pull another machine's live copies into a mirror
        try:
            subprocess.run(cfg["dashboard_refresh"], timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            print("refresh skipped:", e)
    games = collect((load(project_list(), {}) or {}).get("projects", []), pdir, mirrors)
    for g in games:
        g.pop("_t")
    board = load(a.board or cfg.get("board") or pdir / "board.json", {}) or {}
    data = {"states": STATES, "games": games, "work": board.get("work", []), "board_updated": board.get("updated"),
            "made": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    page = PAGE.read_text(encoding="utf-8").replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    n = sum(len(fs) for g in games for _, fs in g["units"])
    print(f"{len({g['game'] for g in games})} game(s), {n} functions -> {out}")
    if not games:
        print("No projects on this machine's dashboard yet: an agent adds one with python tools/dashboard.py add FOLDER")
    if not a.no_open:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())

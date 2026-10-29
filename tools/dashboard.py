"""dashboard.py: one page showing every game's decompilation progress, unit by unit (a treemap, as ByteMatch shows).

usage: python tools/dashboard.py [--projects-dir DIR] [--board FILE] [--out FILE] [--no-open] [--no-refresh]
  Dashboard.bat (DecompPipeline's root) runs this and opens the page in the default browser.

The workspaces are the scorecard's (tools/scorecard/spec.json "consoles" roots, under config key projects_dir, else
DecompPipeline's parent): every folder with project.json + state/ledger.json is found, so a new game needs no
registration beyond its folder being under a console root (add the root to spec.json for a new game folder). A unit is
one system of state/systems.json (functions without one: their 64 KB address range, "8c18xxxx"); each function is sized by its bytes
(state/catalog.json) and coloured by its ledger row and state/readable.json. Read-only: writes only --out (default
%LOCALAPPDATA%/DecompPipeline/dashboard/index.html), one self-contained page with the data inside.

Two files the agents keep (docs/DASHBOARD.md has their format): GAME/progress.json, the game's port milestones
(builds, boots, playable, matches the original), what is being done now and next, and its open bugs; and the work
board (config key "board", else PROJECTS_DIR/board.json): each round of work, what it does, its state, where it runs
and which games it unblocks. Everything else on the page is read from the workspaces. config.local.json may add
"dashboard_mirrors" (folders laid out like the projects folder, e.g. another machine's live copies; the newest copy of
each workspace wins) and "dashboard_refresh" (a command run first, bounded at 600 s, that refreshes them).

Nearest existing: tools/scorecard.py - per-console gates as text (definition of done); it has no per-function or
per-unit view and no page. Its discovery (scorecard_data.discover, game_root) is reused here. ui/index.html is one
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
from core import toolcfg  # noqa: E402

SPEC = TOOL / "tools" / "scorecard" / "spec.json"
PAGE = TOOL / "tools" / "dashboard" / "page.html"
# states, in the page's legend order: readable C passes, raw C matched, agents/translator tried, refused, not started
STATES = ["readable", "matched", "progress", "blocked", "todo"]


def load(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def state_of(row, rd):
    """One function's state from its ledger row and its readable.json row."""
    st = (row or {}).get("status") or "queued"
    if st == "matched":
        return "readable" if (rd or {}).get("status") == "readable" else "matched"
    if st in ("skipped", "error", "unsupported"):
        return "blocked"
    if st in ("failed", "running", "stopped") or (row or {}).get("attempts"):
        return "progress"
    return "todo"


def workspace(ws):
    """{name, units: [[unit name, [[addr, bytes, state index, source], ...]], ...], counts} for one workspace."""
    led = (load(ws / "state" / "ledger.json", {}) or {}).get("functions", {})
    cat = load(ws / "state" / "catalog.json", {}) or {}
    size = {f["addr"]: f.get("bytes") or 0 for f in cat.get("functions", []) if isinstance(f, dict) and "addr" in f}
    rds = load(ws / "state" / "readable.json", {}) or {}
    rds = rds.get("functions", rds) if isinstance(rds, dict) else {}
    systems = (load(ws / "state" / "systems.json", {}) or {}).get("systems", [])
    unit_of = {}
    for s in systems:
        name = s.get("id", "?") + (f" {s['hint']}" if s.get("hint") else "")
        for a in s.get("functions", []):
            unit_of.setdefault(a, name)
    units, counts = {}, {k: [0, 0] for k in STATES}
    for addr, row in led.items():
        k = state_of(row, rds.get(addr))
        b = size.get(addr) or 2
        src = (row or {}).get("source") or ""
        units.setdefault(unit_of.get(addr) or f"{addr[:4]}xxxx", []).append([addr, b, STATES.index(k), src])
        counts[k][0] += 1
        counts[k][1] += b
    st = ws / "state"
    when = float((st / "ledger_mtime.txt").read_text()) if (st / "ledger_mtime.txt").is_file()         else (st / "ledger.json").stat().st_mtime
    harn = ws / "harness"
    if harn.is_dir():
        built = sum(1 for d in os.scandir(harn) if (pathlib.Path(d.path) / "meta.json").is_file())
    else:  # a mirror carries the count
        built = int((st / "harness_count.txt").read_text()) if (st / "harness_count.txt").is_file() else 0
    return {"units": sorted([u, sorted(fs)] for u, fs in units.items()), "counts": counts, "harnesses": built,
            "updated": datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M"), "_t": when}


def collect(projects_dir, mirrors=()):
    """Every workspace under the spec's console roots, in the projects folder and in each mirror of it (a folder with
    the same layout, e.g. another machine's live copies); the newest ledger of each (game, part) wins. progress.json
    and port/game.json are always read from the projects folder's own game folder."""
    spec = load(SPEC, {}) or {}
    best = {}
    for key, con in spec.get("consoles", {}).items():
        for root in con.get("roots", []):
            for base in [pathlib.Path(projects_dir), *map(pathlib.Path, mirrors)]:
                found, _ = sd.discover(base / root)
                for ws in found:
                    g = sd.game_root(ws)
                    if g == ws and ws.name in ("decomp", "research"):  # a mirror has no port/ to find the game by
                        g = ws.parent
                    part = ws.relative_to(g).as_posix() if ws != g else "main"
                    home = pathlib.Path(projects_dir) / g.relative_to(base)
                    w = {"console": con.get("name", key), "game": g.name, "part": part, "home": g.relative_to(base),
                         "where": "mirror" if base != pathlib.Path(projects_dir) else "here",
                         "port": (home / "port" / "game.json").is_file(),
                         "progress": load(home / "progress.json", {}) or {}, **workspace(ws)}
                    k = (w["game"], part)
                    if k not in best or w["_t"] > best[k]["_t"]:
                        best[k] = w
    homes = {(pathlib.Path(projects_dir) / g).resolve() for g in {w["home"] for w in best.values()}}
    for w in own_projects(projects_dir, homes):
        best[(w["game"], "main")] = w
    for w in best.values():
        w.pop("home")
    return sorted(best.values(), key=lambda w: (w["console"], w["game"], w["part"]))


def own_projects(projects_dir, homes, depth=3):
    """Games made some other way (their own port, research only, not on DecompPipeline yet): any folder within
    `depth` levels of the projects folder whose progress.json has a "console", outside the workspaces' game folders.
    Its "road" (docs/DASHBOARD.md) stands in for the steps the page would otherwise read from a workspace."""
    base = pathlib.Path(projects_dir)
    for top, dirs, names in os.walk(base):
        t = pathlib.Path(top)
        dirs[:] = [] if len(t.relative_to(base).parts) >= depth else             [d for d in dirs if not d.startswith((".", "DecompPipeline")) and d not in ("External", "node_modules")]
        if "progress.json" not in names or t.resolve() in homes:
            continue
        pr = load(t / "progress.json", {}) or {}
        if not isinstance(pr, dict) or not pr.get("console"):
            continue
        when = (t / "progress.json").stat().st_mtime
        yield {"console": pr["console"], "game": pr.get("game") or t.name, "part": "main", "where": "here",
               "port": False, "progress": pr, "units": [], "counts": {k: [0, 0] for k in STATES}, "harnesses": 0,
               "updated": datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M"), "_t": when, "home": ""}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects-dir")
    ap.add_argument("--out")
    ap.add_argument("--board", help="the work board (default: config key board, else PROJECTS_DIR/board.json)")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-refresh", action="store_true", help="skip config dashboard_refresh")
    a = ap.parse_args(argv)
    cfg = toolcfg.load(TOOL)
    pdir = pathlib.Path(a.projects_dir or cfg.get("projects_dir") or TOOL.parent)
    base = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "DecompPipeline" / "dashboard"
    out = pathlib.Path(a.out) if a.out else base / "index.html"
    if cfg.get("dashboard_refresh") and not a.no_refresh:  # e.g. pull another machine's live copies into a mirror
        try:
            subprocess.run(cfg["dashboard_refresh"], timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            print("refresh skipped:", e)
    games = collect(pdir, cfg.get("dashboard_mirrors") or ())
    for g in games:
        g.pop("_t")
    board = load(a.board or cfg.get("board") or pdir / "board.json", {}) or {}
    data = {"states": STATES, "games": games, "work": board.get("work", []), "board_updated": board.get("updated"),
            "made": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    page = PAGE.read_text(encoding="utf-8").replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    n = sum(len(fs) for g in games for _, fs in g["units"])
    print(f"{len(games)} workspace(s), {n} functions -> {out}")
    if not a.no_open:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())

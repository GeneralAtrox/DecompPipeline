"""Call-site coverage of a project: which call sites no valid vector reaches, why, and re-preparing with the reach pass.

usage:
  python tools/call_coverage.py --project DIR report [--all] [--top N] [--json FILE] [--list FILE] [--check]
  python tools/bounded.py 14400 python tools/call_coverage.py --project DIR reach [ADDR... | @FILE] [--max N] [--slots N] [--dry]

report  Ranked list (most unreached sites first) of the matched functions (--all: every function with a harness
        summary) whose call sites no valid vector reaches, each site with the reach pass's reason, and the project
        figure (core/call_coverage.py). --list FILE writes the matched functions with an untried site (a harness made
        before the reach pass) for `reach @FILE`. --check exits 1 while such a function exists.
reach   Servers stopped. Re-prepares matched functions with the current oracle (the reach pass, TcReach.java, on by
        target.json "reach_calls"), config "ghidra_parallel" (or --slots N) at once in Ghidra slots, at most --max (default 400)
        per run: the listed ones, else every one with an untried site. The old harness is kept in
        state/reach_backup/ADDR/. Each one's matched raw C is then judged on its new harness: a FAIL means the
        matched C differs from the original at a call (or on a path) no vector reached before. The FAILs go to
        state/reach_failed.txt (requeue them: tools/requeue.py --project DIR --seed NOTE @state/reach_failed.txt),
        the passes and the before/after counts to state/reach_run.json. Exit 0 when every judged raw C passes.
"""
import argparse
import json
import pathlib
import shutil
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import expand_addrs, server_state, use_project  # noqa: E402


def cmd_report(a):
    from core import call_coverage as cc, paths
    addrs = (sorted(p.name for p in paths.HARNESS.iterdir() if (p / "oracle_summary.json").exists())
             if a.all else cc.matched_functions())
    rows, f = cc.report(addrs, top=a.top)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"figure": f, "functions": rows}, indent=1), encoding="utf-8")
    todo = cc.untried({x: r for x, r in rows.items() if x in set(cc.matched_functions())})
    if a.list:
        pathlib.Path(a.list).write_text("".join(x + "\n" for x in todo), encoding="utf-8")
        print(f"{len(todo)} matched function(s) with an untried site written to {a.list}")
    return 1 if a.check and todo else 0


def cmd_reach(a, ws):
    if not a.dry and server_state(ws, timeout=3) is not None:
        sys.exit("the project's server is running: stop it first (prepare needs the master Ghidra project)")
    from core import call_coverage as cc, cascade, ghidra, paths, refine
    cfg = paths.config()
    if a.slots:
        cfg["ghidra_parallel"] = a.slots  # this run only (slot copies are made on first use)
    if not int((cfg.get("reach_calls") or {}).get("runs") or 0):
        sys.exit('the reach pass is off (config "reach_calls" runs 0): re-preparing would change nothing')
    matched = set(cc.matched_functions())
    listed = expand_addrs(a.addrs)
    before = cc.site_rows(listed or sorted(matched))
    todo = [x for x in listed if x in matched] if listed else cc.untried(before)
    skipped = [x for x in listed if x not in matched]
    todo = todo[:max(0, a.max)]
    workers = max(1, int(cfg.get("ghidra_parallel", 1)))
    print(f"reach: {len(todo)} matched function(s) to re-prepare ({workers} Ghidra slot(s) at once)"
          + (f"; {len(skipped)} listed one(s) not matched, skipped" if skipped else "") + (" [dry]" if a.dry else ""),
          flush=True)
    if a.dry or not todo:
        return 0
    backup = paths.STATE / "reach_backup"

    def prepare(addr):
        dest = backup / addr
        if not dest.exists() and (paths.HARNESS / addr).exists():
            shutil.copytree(paths.HARNESS / addr, dest)
        with ghidra.slot(cfg) as i:
            ghidra.prepare(cfg, addr, slot_index=i)

    def judge(x):
        try:
            return refine.harness_stale(cfg, x)
        except refine.infra.InfraError as e:
            return str(e)

    out = cascade.reprepare_all(todo, prepare, judge, workers=workers, log=lambda m: print(m, flush=True))
    after = cc.site_rows(out["prepared"])
    rows = {x: {"before": len(before.get(x, {}).get("unreached", [])), "after": len(after.get(x, {}).get("unreached",
            [])), "found": after.get(x, {}).get("found", []), "fails": out["failed"].get(x)} for x in out["prepared"]}
    log = paths.STATE / "reach_run.json"
    old = paths.read_json(log) or {}
    old.update(rows)
    log.write_text(json.dumps(old, indent=1, sort_keys=True), encoding="utf-8")
    failed = sorted(out["failed"])
    (paths.STATE / "reach_failed.txt").write_text("".join(x + "\n" for x in failed), encoding="utf-8")
    gained = sum(r["before"] - r["after"] for r in rows.values())
    print(f"reach: {len(out['prepared'])} re-prepared, {len(out['errors'])} prepare error(s); {gained} call site(s) "
          f"newly reached; {len(failed)} matched raw C FAIL the new harness (state/reach_failed.txt)"
          + (f": {' '.join(failed)}" if failed else ""), flush=True)
    return 1 if failed or out["errors"] else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--all", action="store_true")
    r.add_argument("--top", type=int)
    r.add_argument("--json")
    r.add_argument("--list")
    r.add_argument("--check", action="store_true")
    x = sub.add_parser("reach")
    x.add_argument("addrs", nargs="*")
    x.add_argument("--max", type=int, default=400)
    x.add_argument("--slots", type=int)
    x.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    ws = use_project(a.project)
    return cmd_report(a) if a.cmd == "report" else cmd_reach(a, ws)


if __name__ == "__main__":
    sys.exit(main())

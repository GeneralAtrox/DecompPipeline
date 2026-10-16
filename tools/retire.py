"""Retire every artefact keyed by an address that is not a current catalog function: move it to retired/<kind>/ in
the project (never delete) and append retired/manifest.json with what moved, from where, and why.

usage: python tools/retire.py --project DIR [--dry] [--note TEXT] [--force] [--max N]
  --dry    list what would move, change nothing
  --note   prefixed to every manifest reason (e.g. "catalog pass 2026-10-14")
  --force  run although the project's server answers or readable.json has rows marked "running" (a refine run, or one
           that was killed), or more than --max addresses would be retired
  --max    refuse when more distinct addresses than this would be retired (default: max(50, 5% of the catalog)): a
           broken catalog must not empty the workspace
Kinds: harness, packets, matched, matched_stale, work, runs, sandbox (ADDR and rd_ADDR), build (raw and readable
builds), import, hints (oracle/hints), readable_src, ledger rows, readable ledger rows, systems.json membership,
escalate.json entries, and readable/names entries nothing else refers to (others are reported as kept). Idempotent:
a second run finds nothing. Exit 0; 1 when refused.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import server_state, workspace  # noqa: E402
from core import retire  # noqa: E402  (pure: no core.paths)


def busy(ws):
    """Why retiring now could race a writer, or None."""
    if server_state(ws):
        return "the project's server is running (stop it first: the ledger is its file)"
    rd = retire._read(ws, retire.READABLE) or {}
    running = sorted(a for a, r in rd.items() if (r or {}).get("status") == "running")
    if running:
        return (f"state/readable.json has {len(running)} row(s) marked running ({' '.join(running[:6])}): a refine "
                "run is writing it, or one was killed (then pass --force)")
    return None


def run(ws, dry=False, note=None, force=False, max_addrs=None, log=print):
    """Scan and (unless dry) retire. Returns (plan, records, refusal or None)."""
    funcs = retire.functions(ws)
    plan = retire.scan(ws, funcs)
    addrs = sorted({i["addr"] for i in plan["items"]})
    log(f"{ws}: {len(plan['items'])} orphan artefact(s) of {len(addrs)} address(es) that are not functions "
        f"({len(funcs)} catalog functions); {len(plan['names_kept'])} non-function name(s) kept (referenced)")
    for line in retire.summary(plan):
        log(line)
    cap = max_addrs if max_addrs is not None else max(50, len(funcs) // 20)
    if dry or not plan["items"]:
        return plan, [], None
    why = busy(ws)
    if not why and len(addrs) > cap:
        why = f"{len(addrs)} addresses would be retired, more than --max {cap} (is the catalog complete?)"
    if why and not force:
        log(f"REFUSED: {why}")
        return plan, [], why
    records = retire.apply(ws, plan, note=note, funcs=funcs)
    log(f"retired {len(records)} artefact(s) of {len({r['addr'] for r in records})} address(es); manifest "
        f"{retire.MANIFEST}")
    return plan, records, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--note")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max", type=int, dest="max_addrs")
    a = ap.parse_args()
    _, _, refused = run(workspace(a.project), dry=a.dry, note=a.note, force=a.force, max_addrs=a.max_addrs)
    sys.exit(1 if refused else 0)


if __name__ == "__main__":
    main()

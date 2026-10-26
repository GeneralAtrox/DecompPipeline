"""Requeue finished functions (after a checker fix): by a regex on verdict_detail, or by address.

usage: python tools/requeue.py --project DIR --note TEXT (--rx REGEX | ADDR ... | @FILE) [--status S,S] [--dry]
       python tools/requeue.py --project DIR --stale-skips [--dry]
  --rx       requeue functions whose status is in --status (default skipped,failed) and whose verdict_detail
             matches REGEX (re.search)
  ADDR/@FILE explicit addresses (any finished status, matched included); @FILE has one or more per line
  --stale-skips  the finished functions whose blocker kind a landing fixed after they were judged (target.json
             blocker_kinds "fixed_at", core/stale_skips.py), one requeue per kind with its own note; `serve` does
             the same at start. Never a function in the excluded tier (hardware or system ops) or out of scope
  --dry      list what would be requeued, change nothing
  --seed T   start each re-match from the function's current matched C (or matched_stale/): copied to import/ADDR.c
             with T as import/ADDR.note, so the job judges it first (attempt 0) and an agent fixes only what the new
             harness finds. For a re-match after new hints: unseeded, port phase C's Effect_SpawnEmitter (2438 bytes)
             failed 4 attempts from scratch; seeded it passed on attempt 1 for $0.02.
  --stale    with --seed: seed from matched_stale/ first (the C before an earlier re-match), to get it back when that
             re-match was forced by a faulty harness (TC 8c0144da: a mutated jump table, fixed in TcCfg.isConst)
Without --seed, the job still judges the function's matched_stale/ C first (core/job.py: a PASS needs no agent
run; a FAIL starts attempt 1 from it). --seed only adds the note, or with --stale the older C.
When the project's server answers on its port, the request goes through its /api/requeue (the server owns the
ledger). Otherwise the ledger is changed directly, the same way the server does it (status queued, a "requeued"
note, matched C moved to matched_stale/), after a backup to state/ledger.before-requeue-<time>.json.
Functions being worked on (waiting, preparing, running) or already queued are left alone either way.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import api, expand_addrs, project_json, server_state, use_project  # noqa: E402

ACTIVE = ("waiting", "preparing", "running", "queued")


def select(ledger, rx, statuses, addrs):
    F = ledger["functions"]
    if rx is not None:
        return sorted(a for a, r in F.items()
                      if r.get("status") in statuses and rx.search(r.get("verdict_detail") or ""))
    return [a for a in addrs if a in F]


def requeue_offline(ws, ledger, addrs, note):
    """orch.Orchestrator.requeue on the ledger file (no server is running)."""
    from core import filelock, paths
    if server_state(ws, timeout=3) is not None:  # a server came up since the first look: do not race it
        sys.exit("a server is running now; nothing changed (run again to go through its API)")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    done = []
    with filelock.locked(paths.LEDGER):  # re-read under the lock: the ledger as it is now, not as first read
        shutil.copyfile(paths.LEDGER, paths.STATE / f"ledger.before-requeue-{stamp}.json")
        ledger.clear()
        ledger.update(paths.read_json(paths.LEDGER))
        for a in addrs:
            r = ledger["functions"].get(a)
            if not r or r.get("status") in ACTIVE:
                continue
            r["requeued"] = f"{time.strftime('%Y-%m-%d %H:%M')}: was {r['status']}; {note}"
            r["status"] = "queued"
            r.pop("matched_file", None)
            done.append(a)
        paths.write_json(paths.LEDGER, ledger)
    for a in done:
        m = paths.MATCHED / f"{a}.c"
        if m.exists():  # matched/ only holds C that passes the current checker; the job judges this first
            (ws / "matched_stale").mkdir(exist_ok=True)
            os.replace(m, ws / "matched_stale" / f"{a}.c")
    return done


def seed(ws, addrs, text, stale_first=False):
    """Copy each function's matched C (else matched_stale/; the other way round with stale_first) to import/ADDR.c
    with the note; returns those seeded."""
    out = []
    for a in addrs:
        dirs = ("matched_stale", "matched") if stale_first else ("matched", "matched_stale")
        src = next((f for f in (ws / d / f"{a}.c" for d in dirs) if f.exists()), None)
        if src:
            (ws / "import").mkdir(exist_ok=True)
            shutil.copyfile(src, ws / "import" / f"{a}.c")
            (ws / "import" / f"{a}.note").write_text(text.strip() + "\n", encoding="utf-8")
            out.append(a)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--note")
    ap.add_argument("--stale-skips", action="store_true")
    ap.add_argument("--rx")
    ap.add_argument("--status", default="skipped,failed")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--seed", metavar="NOTE")
    ap.add_argument("--stale", action="store_true")
    ap.add_argument("addrs", nargs="*")
    a = ap.parse_args()
    from core import oplog  # the manual operator steps record (docs/DEFINITION_OF_DONE.md gate 2)
    if not a.dry:
        oplog.begin("requeue.py", [oplog.workspace(a.project)])
    if a.stale and not a.seed:
        sys.exit("--stale needs --seed")
    if (bool(a.rx) + bool(a.addrs) + a.stale_skips) != 1:
        sys.exit("give one of --rx REGEX, addresses (ADDR ... / @FILE) or --stale-skips")
    if bool(a.note) == a.stale_skips:
        sys.exit("--note is needed with --rx or addresses; --stale-skips writes one note per blocker kind")
    ws = use_project(a.project)
    rx = re.compile(a.rx) if a.rx else None
    statuses = set(a.status.split(","))
    ledger = json.loads((ws / "state" / "ledger.json").read_text(encoding="utf-8"))
    if a.stale_skips:
        from core import paths, stale_skips
        groups = stale_skips.by_note(stale_skips.stale(ledger["functions"], paths.config().get("blocker_kinds") or [],
                                                       stale_skips.takeable()))
    else:
        groups = {a.note: select(ledger, rx, statuses, expand_addrs(a.addrs))}
    addrs = [x for g in groups.values() for x in g]
    st = server_state(ws)
    mode = "server" if st else "offline"
    print(f"{project_json(ws).get('name')}: {len(addrs)} function(s) to requeue ({mode})")
    for x in addrs:
        r = ledger["functions"][x]
        print(f"  {x}  {r.get('status'):8}  {(r.get('verdict_detail') or '')[:100]}")
    if not addrs or a.dry:
        return 0
    if a.seed:
        src = "matched_stale/ C" if a.stale else "matched C"
        print(f"seeded {len(seed(ws, addrs, a.seed, a.stale))} from their {src} (import/)")
    done = []
    for note, group in groups.items():
        if st:
            done += api(project_json(ws)["port"], "/api/requeue", {"addrs": group, "note": note})["requeued"]
        else:
            done += requeue_offline(ws, ledger, group, note)
    print(f"requeued {len(done)}")
    return 0


if __name__ == "__main__":
    from core import oplog
    sys.exit(oplog.main(main))

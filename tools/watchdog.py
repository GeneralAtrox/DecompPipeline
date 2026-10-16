"""Bounded pipeline watchdog: poll the running servers once a minute and exit early, with the reason, as soon as
something needs action (a server unreachable, usage-blocked, finished, short of workers, or its loop off).

usage: python tools/watchdog.py --project DIR [--project DIR ...] [--workers N] [--minutes M] [--interval S]
  --workers   target live workers per server (default: each project's "workers" setting)
  --minutes   how long to watch before exiting 0 (default 60); always bounded, never open-ended
Exit codes: 0 no issue for the whole period, 1 an issue (printed after "ACTION:").
An issue must persist for two polls (a worker may be between functions), except unreachable and finished.
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import TOOL, api, project_json, workspace  # noqa: E402
from core import toolcfg  # noqa: E402  (common puts DecompPipeline/ on sys.path)


def target_workers(ws, override):
    if override is not None:
        return override
    cfg = toolcfg.load(TOOL)  # config.json + config.local.json
    return int(project_json(ws).get("workers", cfg.get("workers", 1)))


def check(servers):
    issues, line = [], []
    for name, port, want in servers:
        try:
            s = api(port, "/api/state", timeout=20)
        except OSError as e:
            issues.append(f"{port} unreachable ({name}): {e}")
            continue
        o, u = s["overview"], s["usage"]
        live = len([w for w in s["workers"] if not w["stopping"]])
        line.append(f"{port} w{live} m{o['matched']} r{o['remaining']}")
        if u.get("blocked_until"):
            issues.append(f"{port} usage blocked until {u['blocked_until']} ({u.get('status')})")
        elif not s["queue"] and not s["busy"]:  # queue drained (failed/skipped keep their reasons)
            issues.append(f"{port} finished: matched {o['matched']} failed {o['failed']} skipped {o['skipped']}")
        elif live < want and o["remaining"] > live:
            issues.append(f"{port} has {live} workers, target {want}")
        elif not s.get("loop"):
            issues.append(f"{port} loop is off")
    return issues, " | ".join(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", action="append", required=True)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--interval", type=float, default=60)
    a = ap.parse_args()
    servers = []
    for d in a.project:
        ws = workspace(d)
        cfg = project_json(ws)
        servers.append((cfg.get("name"), int(cfg["port"]), target_workers(ws, a.workers)))
    end = time.time() + a.minutes * 60
    strikes = {}
    while time.time() < end:
        issues, line = check(servers)
        print(time.strftime("%H:%M"), line, flush=True)
        now = {" ".join(i.split(" ")[:2]): i for i in issues}
        strikes = {k: strikes.get(k, 0) + 1 for k in now}
        hard = [now[k] for k, n in strikes.items() if n >= 2 or "unreachable" in now[k] or "finished" in now[k]]
        if hard:
            print("ACTION:", *hard, sep="\n  ", flush=True)
            return 1
        time.sleep(max(1.0, min(a.interval, end - time.time())))
    print(f"OK: no issues for {a.minutes:g} minutes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Queue wait of the other sessions' broker tickets, sampled every 0.5 s for S seconds or until LOG.stop exists. A
ticket's wait runs from the time in its name to the last sample that still saw it. Tickets of the PIDs in PIDFILES
(the proof runners', comma-separated) are counted apart as "mine". Writes one JSON line per finished ticket, then a
summary line.
usage: waitwatch.py LOG S [PIDFILES]"""
import json
import os
import pathlib
import statistics
import sys
import time

log, secs = pathlib.Path(sys.argv[1]), float(sys.argv[2])
pidfiles = [pathlib.Path(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else []
stop = log.with_name(log.name + ".stop")
d = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker"
seen, done, end = {}, [], time.time() + secs
with open(log, "a", encoding="utf-8") as f:
    while time.time() < end and not stop.exists():
        now = time.time()
        mine = set()
        for pf in pidfiles:
            try:
                mine.add(int(pf.read_text()))
            except (OSError, ValueError):
                pass
        names = {q.stem[2:] for q in d.glob("t_*.json")}
        for n in names:
            seen[n] = now
        for n in [n for n in seen if n not in names]:
            prio, ns, pid = n.split("_")[:3]
            rec = {"prio": int(prio), "pid": int(pid), "mine": int(pid) in mine,
                   "wait_s": round(seen.pop(n) - int(ns) / 1e9, 2), "end": round(now)}
            done.append(rec)
            f.write(json.dumps(rec) + "\n")
            f.flush()
        time.sleep(0.5)
    others = [r["wait_s"] for r in done if not r["mine"]]
    f.write(json.dumps({"summary": True, "secs": round(secs), "others": len(others),
                        "median_s": statistics.median(others) if others else None,
                        "p90_s": sorted(others)[int(0.9 * (len(others) - 1))] if others else None,
                        "max_s": max(others) if others else None,
                        "still_waiting": len(seen)}) + "\n")

"""Prepare the queue's next functions while the workspace is idle, so its workers later reuse current harnesses
(config "harness_reuse", core/job.reusable_harness) instead of waiting on Ghidra. Functions expected to prepare
slowly (core/longtail) go first: prefetch hides a short prepare behind an agent, not a 300 s one.

usage: python tools/idle_prepare.py --project DIR [--ahead N] [--slots K] [--minutes M] [--ram-stop PCT]
                                    [--out FILE.tsv] [--dry]
  --ahead N      the queue's next N functions, in claim order (default 40)
  --slots K      Ghidra slots for this run (default config "ghidra_parallel")
  --minutes M    start no prepare after M minutes (default 60); the ones running then finish
  --ram-stop P   at P% of RAM in use, kill the running prepares and stop (default 90)
  --out          a TSV of the results (tools/prepare_list.py's columns; state also "stopped")
  --dry          list the order, prepare nothing
Refuses when harness_reuse is off for the project (nothing prepared here would be used), while its server answers,
or while another idle prepare holds it (core/idle_guard). Stops, killing its Ghidra runs, when the server starts, at
the RAM stop, or when a worker or prefetch anywhere waits for a machine-wide Ghidra token it holds (config
"ghidra_broker" on: its prepares ask at priority "idle", the lowest, and give way). A killed prepare is of a function with no current harness, so nothing current is lost. It never
starts or resumes workers and never writes the ledger. Exit 0 when it ran to the end, 2 when stopped early.
"""
import argparse
import concurrent.futures
import sys
import threading
import time

import prepare_list  # puts the DP root on sys.path
from common import server_state, use_project


def plan(cfg, functions, rows, priority, reusable, hist, ahead):
    """The functions to prepare, in order: the next `ahead` of the queue without a current harness, the ones
    expected to be slow first (core/longtail.order)."""
    from core import longtail, orch
    nxt = orch.open_order(functions, rows, priority)[:ahead]
    return longtail.order([a for a in nxt if not reusable(a)], hist)


def run(todo, prepare, stop, deadline, workers, log=print):
    """prepare(addr, stop) for each of todo, `workers` at a time, until stop is set or time passes deadline
    (monotonic). Returns the result rows (state "stopped" for the ones not prepared)."""
    def one(a):
        if stop.is_set() or time.monotonic() > deadline:
            return prepare_list.row(a, "stopped")
        try:
            meta, summary = prepare(a, stop)
        except Exception as e:  # noqa: BLE001  (one bad prepare must not stop the others)
            if stop.is_set():
                return prepare_list.row(a, "stopped", detail="killed by the stop")
            msg = str(e).splitlines()[0][:200] if str(e) else ""
            return prepare_list.row(a, "error", detail=f"{type(e).__name__}: {msg}")
        return prepare_list.row(a, "prepared", meta, summary)

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for n, rw in enumerate(ex.map(one, todo), 1):
            rows.append(rw)
            log(f"  [{n}/{len(todo)}] {rw['addr']}: {rw['state']}" + (f" {rw['detail']}" if rw["detail"] else ""))
    return rows


def prepare_one(cfg, addr, stop_flag):
    """One prepare at the broker's "idle" priority: a worker or prefetch kept waiting by it sets stop_flag, which
    kills this run and stops the rest."""
    from core import ghidra
    with ghidra.slot(cfg, stop_flag=stop_flag, prio="idle") as i:
        return ghidra.prepare(cfg, addr, slot_index=i, stop_flag=stop_flag)


def warm_slots(cfg):
    """Bring every read-only slot copy up to date before the prepares start. A copy is refreshed only while the master
    (slot 0) is free, and with every thread re-taking slot 0 as soon as it ends, the refresh waited for all of them
    (r15 proof: 23 of 24 prepares ran on slot 0, one after another)."""
    from core import ghidra
    for i in range(1, int(cfg.get("ghidra_parallel", 1))):
        with ghidra.slot_master():
            ghidra._refresh_copy(i)


def watch(state, ws, stop, ram_stop, log, every=2.0):
    """Set stop when the server asks (core/idle_guard), answers on its port, or RAM reaches ram_stop. A thread."""
    from core import idle_guard
    while not stop.is_set():
        ram = idle_guard.ram_pct() or 0
        why = ("the server asked" if idle_guard.stop_requested(state)
               else "the server is running" if server_state(ws, timeout=2) is not None
               else f"RAM at {ram:.0f}%" if ram >= ram_stop else None)
        if why:
            log(f"stopping: {why}")
            stop.set()
            return
        stop.wait(every)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--ahead", type=int, default=40)
    ap.add_argument("--slots", type=int)
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--ram-stop", type=float, default=90)
    ap.add_argument("--out")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    from core import oplog  # the manual operator steps record (docs/DEFINITION_OF_DONE.md gate 2)
    if not a.dry:
        oplog.begin("idle_prepare.py", [oplog.workspace(a.project)])
    ws = use_project(a.project)
    from core import ghidra, idle_guard, job, longtail, orch, paths
    cfg = paths.config()
    if not cfg.get("harness_reuse"):
        sys.exit("harness_reuse is off for this project: a harness prepared ahead would not be used")
    if server_state(ws, timeout=3) is not None:
        sys.exit("the project's server is running: idle prepare only runs while it is stopped")
    if a.slots:
        cfg["ghidra_parallel"] = a.slots  # this run only
    rows = (paths.read_json(paths.LEDGER) or {}).get("functions", {})
    functions = orch.scoped_functions(paths.read_json(paths.CATALOG) or {"functions": []}, paths.scope(), cfg)
    hist = longtail.history(paths.STATE)
    todo = plan(cfg, functions, rows, cfg["priority"], lambda x: job.reusable_harness(cfg, x), hist, a.ahead)
    slow = sum(hist.get(x, 0) >= longtail.LONG_S for x in todo)
    print(f"next {a.ahead} in the queue: {len(todo)} without a current harness, {slow} expected slow (first)",
          flush=True)
    if a.dry:
        for x in todo:
            print(f"  {x}  {hist.get(x, '?')} s last time")
        return 0
    if not idle_guard.acquire(paths.STATE):
        sys.exit("another idle prepare holds this workspace")
    stop = threading.Event()
    try:
        threading.Thread(target=watch, args=(paths.STATE, ws, stop, a.ram_stop, print), daemon=True).start()
        warm_slots(cfg)
        out = run(todo, lambda addr, s: prepare_one(cfg, addr, s), stop, time.monotonic() + 60 * a.minutes, int(cfg.get("ghidra_parallel", 1)),
                  log=lambda m: print(m, flush=True))
    finally:
        stop.set()
        idle_guard.release(paths.STATE)
    if a.out:
        prepare_list.write_tsv(a.out, out)
    n = {s: sum(r["state"] == s for r in out) for s in ("prepared", "stopped", "error")}
    print(f"{n['prepared']} prepared, {n['stopped']} stopped, {n['error']} error(s)")
    return 2 if n["stopped"] else 0


if __name__ == "__main__":
    from core import oplog
    sys.exit(oplog.main(main, ok=(0, 2)))  # 2: it gave way (the server, the RAM stop): nobody is needed

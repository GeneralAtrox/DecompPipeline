"""Prepare a list of functions in Ghidra ahead of the workers, several Ghidra slots at once, and print each one's
oracle figures. With config "harness_reuse" on, a worker then reuses the harness without opening Ghidra
(core/job.reusable_harness), so a large worker pool never waits on Ghidra slots. Functions whose harness is already current are left alone.

usage: python tools/prepare_list.py --project DIR ADDR|@FILE... [--slots N] [--serve] [--out FILE.tsv] [--dry]
           [--seed-offset N] [--rows FILE.tsv] [--kill-s S]
       python tools/prepare_list.py --project DIR --harness-stale [ADDR|@FILE...] [--slots N] [--serve] [--out FILE.tsv]
           [--dry]
  @FILE      one or more addresses per line ('#' comments); keep long hex lists off the command line
  --harness-stale  also every function the readable ledger marks harness_stale (refine / verify: its matched raw C
             fails its harness, so the harness needs a prepare): the list an operator copied out by hand
  --slots N  Ghidra slots for this run instead of config "ghidra_parallel" (slot 0 is the master project, the rest
             are read-only copies, made on first use)
  --serve    prepare on read-only slot copies 1..N (N = --slots or "ghidra_parallel"), each through its long-lived
             Ghidra (core/ghidra_server, config "ghidra_serve" enabled for this run): one JVM start and one queue turn
             per copy instead of per function, the same harness bytes (reprepare_stale --serve's rule). The master's
             queued marks wait for its next prepare
  --out      a TSV of the results: addr, state (current / prepared / refused / timeout / error), signature,
             unsupported, dev_valid, hidden_valid, blocks_hit_all, blocks_total, block_pct, detail; each row is appended the moment
             its prepare ends (a run cut short keeps what finished), then the whole file is rewritten in input order
  --dry      list which functions would be prepared, prepare nothing
  --rows     appends one line per function as soon as its prepare ends (not at the run's end): addr, state (prepared /
             refused / timeout / error), the kill limit it ran under (--kill-s, '' for the config's), detail. A run
             killed part way keeps every line written (tools/wave_game.py's translate step reads them: a timeout is not
             prepared again at the same limit)
  --kill-s S one function's prepare is killed after S s instead of config "ghidra_kill_s"/"ghidra_timeout_s" (this run
             only; the oracle facts, and so the harness bytes, are unchanged: only the kill comes sooner). The kill
             limit is part of a refusal's key, so a prepare killed at S s is tried again at a longer limit
Servers stopped: it refuses while the project's server answers (prepare uses the master Ghidra project and writes
harness/). Resumable, so a run cut short (tools/bounded.py) leaves only the rest:
- a prepared harness is current (core/job.reusable_harness);
- a refused prepare (TcPrepare's early "unsupported": meta.json only, so never a current harness) or one killed at the
  kill timeout is a current refusal in state/prepare_refusals.json (Refusals), keyed on today's oracle stamp and the
  kill timeout, and is not prepared again while both are unchanged;
- callees first: a caller's callee records take its callees' harness signatures when it is prepared
  (ghidra.prepare: callee_drift.adopt) and stay current only while they are equal (oracle_stamp.current), so a caller
  prepared before a callee of the same list went stale when the callee landed. A caller waits for the callees its
  last meta.json names; one whose new meta.json names a callee still to come is checked again after it, and
  re-prepared only if it drifted;
- each result is logged (and appended to --out) when it ends, not held behind slower ones in input order.
A served Ghidra that fails to start (core/ghidra_server.StartError: a project path Ghidra refuses, a project owned by
another user) stops the run: the functions not yet started are listed as errors, not tried, instead of each starting a
JVM that fails the same way.
A prepare killed at its kill limit is state "timeout" in the run it ends in, a current refusal from then on.
Exit 0 when every function has a current harness or a current refusal, 1 when a prepare failed otherwise.
"""
import argparse
import os
import collections
import concurrent.futures
import datetime
import pathlib
import re
import socket
import sys
import threading

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import expand_addrs, server_state, use_project  # noqa: E402

COLUMNS = ("addr", "state", "signature", "unsupported", "dev_valid", "hidden_valid", "blocks_hit_all", "blocks_total",
           "block_pct", "detail")


def row(addr, state, meta=None, summary=None, detail=""):
    s = summary or {}
    total, hit = s.get("blocks_total"), s.get("blocks_hit_all")
    pct = round(100.0 * hit / total, 1) if total else ""
    return {"addr": addr, "state": state, "signature": (meta or {}).get("signature", ""),
            "unsupported": (meta or {}).get("unsupported") or "", "dev_valid": s.get("dev_valid", ""),
            "hidden_valid": s.get("hidden_valid", ""), "blocks_hit_all": "" if hit is None else hit,
            "blocks_total": "" if total is None else total, "block_pct": pct, "detail": detail}


REFUSALS = "prepare_refusals.json"  # under the project's state/
TIMEOUT = re.compile(r"\bGhidra timeout\b|timed out", re.I)  # core/ghidra and core/ghidra_server: the kill limit hit


class Refusals:
    """Prepares that end the same way while their inputs are unchanged: refused by TcPrepare before any vector
    ("unsupported", meta.json only: the harness is never current, so every run prepared it again) or killed at the
    kill timeout ("Ghidra timeout"). Keyed on what decides that: today's oracle stamp (core/oracle_stamp: the scripts,
    the facts with the oracle deadline taken from ghidra_timeout_s, hints, vector counts, oracle_limits, the catalog
    and so the function's body, binary, Ghidra install) and the kill timeout of its run config (ghidra_kill_s, else
    ghidra_timeout_s). Any change makes it eligible again (CLAUDE.md: never re-run what failed or timed out with the
    same limits). A stop, a lock or a crash is never recorded. Each record is written the moment its prepare ends."""

    def __init__(self, cfg, path=None, stamper=None):
        from core import filelock, paths
        self.cfg, self.path, self.stamper, self.fl = cfg, pathlib.Path(path or paths.STATE / REFUSALS), stamper, filelock
        self.lock = threading.Lock()
        try:
            self.recs = filelock.read_json(self.path, {}) or {}
        except (OSError, ValueError):
            self.recs = {}

    def key(self, addr):
        from core import oracle_fresh, oracle_limits
        with self.lock:
            if self.stamper is None:
                self.stamper = oracle_fresh.Stamper(self.cfg)
        rc = oracle_limits.run_cfg(self.cfg, addr)
        return {"stamp": self.stamper.today(addr),
                "kill_s": int(rc.get("ghidra_kill_s") or rc.get("ghidra_timeout_s") or 0)}

    def current(self, addr):
        """The recorded refusal's detail while addr's inputs are the recorded ones, else None."""
        rec = self.recs.get(addr)
        if not rec:
            return None
        try:
            key = self.key(addr)
        except Exception:  # noqa: BLE001  (inputs unreadable: prepare as before)
            return None
        return (rec.get("detail") or rec.get("kind")) if all(rec.get(k) == v for k, v in key.items()) else None

    def record(self, addr, kind, detail):
        try:
            key = self.key(addr)
        except Exception:  # noqa: BLE001  (nothing to key it on: prepared again next time)
            return
        rec = dict(key, kind=kind, detail=detail, host=socket.gethostname(),
                   when=datetime.datetime.now().isoformat(timespec="seconds"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fl.update_json(self.path, lambda d: d.__setitem__(addr, rec), default={})
        with self.lock:
            self.recs[addr] = rec


def callee_addrs(meta):
    return [c["addr"] for c in (meta or {}).get("callees") or [] if isinstance(c, dict) and c.get("addr")]


def last_callees(addr):
    """The callees addr's last meta.json names (harness, else packet; none before its first prepare)."""
    from core import paths
    for d in (paths.HARNESS, paths.PACKETS):
        meta = paths.read_json(d / addr / "meta.json")
        if isinstance(meta, dict):
            return callee_addrs(meta)
    return []


def run(cfg, addrs, prepare, reusable, workers, log=print, on_row=None, refusals=None, callees=None):
    """Prepare every addr without a current harness or a current refusal, workers at a time, callees first (see the
    module's docstring). prepare(addr) -> (meta, summary) takes a Ghidra slot itself; reusable(addr) -> (meta,
    summary) or None; refusals: Refusals (default the project's; False: none); callees(addr) -> the addrs it calls
    as last known (default last_callees). on_row(row) gets each result the moment it ends. Returns the result rows in
    addrs' order."""
    refusals = Refusals(cfg) if refusals is None else refusals
    callees = last_callees if callees is None else callees
    rows, todo = {}, []
    for a in addrs:
        got = reusable(a)
        why = None if got else refusals and refusals.current(a)
        if got:
            rows[a] = row(a, "current", *got)
        elif why:
            rows[a] = row(a, "refused", detail=f"refused earlier with the same inputs: {why}")
        else:
            todo.append(a)
    log(f"{len(addrs)} function(s): {sum(r['state'] == 'current' for r in rows.values())} current, "
        f"{sum(r['state'] == 'refused' for r in rows.values())} refused earlier with the same inputs, "
        f"{len(todo)} to prepare ({workers} slot(s))")

    halt = []  # the first server start failure (core/ghidra_server.StartError): the rest are not tried

    def one(a):
        if halt:
            return row(a, "error", detail=f"not tried: {halt[0]}"), None
        try:
            meta, summary = prepare(a)
        except Exception as e:  # noqa: BLE001  (one bad prepare must not stop the others)
            from core.ghidra_server import StartError
            msg = str(e).splitlines()[0][:200] if str(e) else ""
            detail = f"{type(e).__name__}: {msg}"
            if isinstance(e, StartError) and not halt:
                halt.append(detail)
                log(f"stopping: the Ghidra server failed to start ({detail}); the rest are not tried")
            if TIMEOUT.search(msg):  # killed at the kill timeout: same limits, same end
                if refusals:
                    refusals.record(a, "timeout", detail)
                return row(a, "timeout", detail=detail), None
            return row(a, "error", detail=detail), None
        if meta.get("unsupported") and not summary:  # refused before any vector: meta.json only, never current
            if refusals:
                refusals.record(a, "unsupported", meta["unsupported"])
            return row(a, "refused", meta, summary), meta
        return row(a, "prepared", meta, summary), meta

    def again(a):  # its callees landed after it: re-prepared only if its callee records drifted
        got = reusable(a)
        return (row(a, "prepared", *got), None) if got else one(a)

    todo_set, first = set(todo), set()
    wait = {a: {c for c in callees(a) if c in todo_set and c != a} for a in todo}
    users, late_users, late = collections.defaultdict(set), collections.defaultdict(set), {}
    for a, cs in wait.items():
        for c in cs:
            users[c].add(a)
    ready = collections.deque((a, False) for a in todo if not wait[a])
    queued = {a for a, _ in ready}
    n = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        running = {}
        while True:
            while ready and len(running) < max(1, workers):
                a, rerun = ready.popleft()
                running[ex.submit(again if rerun else one, a)] = (a, rerun)
            if not running:
                left = [a for a in todo if a not in queued]  # only a call cycle waits on itself: start its first
                if not left:
                    break
                queued.add(left[0])
                ready.append((left[0], False))
                continue
            done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                a, rerun = running.pop(f)
                rw, meta = f.result()
                rows[a] = rw
                if not rerun:
                    n += 1
                    first.add(a)
                    pending = {c for c in callee_addrs(meta) if c in todo_set and c != a and c not in first}
                    if pending and rw["state"] == "prepared":  # a callee its last meta.json did not name
                        late[a] = pending
                        for c in pending:
                            late_users[c].add(a)
                    for u in users.pop(a, ()):
                        wait[u].discard(a)
                        if not wait[u] and u not in queued:
                            queued.add(u)
                            ready.append((u, False))
                    for u in late_users.pop(a, ()):
                        late[u].discard(a)
                        if not late[u]:
                            ready.append((u, True))
                log(f"  [{n}/{len(todo)}{' again' if rerun else ''}] {rw['addr']}: {rw['state']}"
                    + (f" blocks {rw['blocks_hit_all']}/{rw['blocks_total']} ({rw['block_pct']}%)"
                       if rw["blocks_total"] != "" else "")
                    + (f" unsupported: {rw['unsupported']}" if rw["unsupported"] else "")
                    + (f" {rw['detail']}" if rw["detail"] else ""))
                if on_row:
                    on_row(rw)
    return [rows[a] for a in addrs]


def write_tsv(path, rows):
    clean = lambda v: str(v).replace("\t", " ").replace("\n", " ")  # noqa: E731
    lines = ["\t".join(COLUMNS)] + ["\t".join(clean(r[c]) for c in COLUMNS) for r in rows]
    pathlib.Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_row(path, rw, kill_s):
    """--rows: one tab-separated line, flushed at once: addr, state, kill limit ('' = the config's), detail."""
    detail = " ".join(str(rw["detail"]).split())[:300]
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write("\t".join([rw["addr"], rw["state"], str(kill_s or ""), detail]) + "\n")


def harness_stale():
    """The functions the readable ledger (core/refine) marks harness_stale, in address order."""
    from core import refine
    return sorted(a for a, r in refine.load_ledger().items() if r.get("status") == "harness_stale")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("addrs", nargs="*")
    ap.add_argument("--harness-stale", action="store_true")
    ap.add_argument("--slots", type=int)
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--seed-offset", type=lambda x: int(x, 0), default=0,
                    help="developer only: add N to every function's vector seed (core/ghidra.vector_seed); a harness "
                         "already current is not re-prepared, so delete it first")
    ap.add_argument("--rows")
    ap.add_argument("--kill-s", type=int, default=0)
    a = ap.parse_args()
    if a.seed_offset:
        os.environ["DP_SEED_OFFSET"] = str(a.seed_offset)
    from core import oplog  # the manual operator steps record (docs/DEFINITION_OF_DONE.md gate 2)
    if not a.dry:
        oplog.begin("prepare_list.py", [oplog.workspace(a.project)])
    ws = use_project(a.project)
    addrs = expand_addrs(a.addrs)
    if a.harness_stale:
        addrs = list(dict.fromkeys(addrs + harness_stale()))
    if not addrs:
        ap.error("no addresses (ADDR, @FILE or --harness-stale)")
    if not a.dry and server_state(ws, timeout=3) is not None:
        sys.exit("the project's server is running: stop it first (prepare needs the master Ghidra project)")
    from core import ghidra, job, paths
    cfg = paths.config()
    if a.slots:
        cfg["ghidra_parallel"] = a.slots  # this run only
    if a.kill_s:
        cfg["ghidra_kill_s"] = a.kill_s  # this run only: the kill, not the oracle's budget (core/ghidra_server.kill_timeout)
    workers = max(1, int(cfg.get("ghidra_parallel", 1)))
    if a.serve:  # this run only: slots 1..N served; slot 0, the master, unused
        cfg["ghidra_parallel"] = workers + 1
        cfg["ghidra_serve"] = {**(cfg.get("ghidra_serve") or {}), "enabled": True}
    known = {f["addr"] for f in (paths.read_json(paths.CATALOG) or {}).get("functions", [])}
    unknown = [x for x in addrs if x not in known]
    if unknown:
        sys.exit(f"{len(unknown)} address(es) are not catalog functions: {' '.join(unknown[:10])}")

    def prepare(addr):
        with ghidra.slot(cfg, first=1 if a.serve else 0) as i:
            return ghidra.prepare(cfg, addr, slot_index=i)

    def reusable(addr):
        return job.reusable_harness(cfg, addr)

    refusals = Refusals(cfg)
    if a.dry:
        todo = [x for x in addrs if not reusable(x)]
        refused = [x for x in todo if refusals.current(x)]
        print(f"{len(addrs)} function(s): {len(addrs) - len(todo)} current, {len(refused)} refused earlier with the "
              f"same inputs, {len(todo) - len(refused)} to prepare [dry]")
        return 0
    if a.out:  # each row as it ends: a run cut short keeps what finished
        pathlib.Path(a.out).write_text("\t".join(COLUMNS) + "\n", encoding="utf-8")

    def on_row(r):
        if a.out:
            with open(a.out, "a", encoding="utf-8") as f:
                f.write("\t".join(str(r[c]).replace("\t", " ").replace("\n", " ") for c in COLUMNS) + "\n")
        if a.rows:  # wave_game's short lines, with the kill limit
            append_row(a.rows, r, a.kill_s)
    rows = run(cfg, addrs, prepare, reusable, workers, log=lambda m: print(m, flush=True), on_row=on_row,
               refusals=refusals)
    if a.out:
        write_tsv(a.out, rows)
    bad = [r for r in rows if r["state"] == "error"]
    print(f"{sum(r['state'] == 'prepared' for r in rows)} prepared, {sum(r['state'] == 'current' for r in rows)} "
          f"already current, {sum(r['state'] == 'refused' for r in rows)} refused, "
          f"{sum(r['state'] == 'timeout' for r in rows)} killed at the limit, {len(bad)} error(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    from core import oplog
    sys.exit(oplog.main(main))

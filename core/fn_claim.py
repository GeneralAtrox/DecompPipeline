"""One worker per function (batch 21): a cross-process claim per function, and refine.py run's attempt cycle under it.

TC's prod4 round got 48 addresses twice: two workers shared sandbox/rd_ADDR and build/ADDR/rd_preflight, deleted each
other's files, and the ledger recorded `error` ([Errno 2] ... work.c) and `blocked` (LNK1181 ... protos.obj).

- claim(addr, purpose): core/filelock.claim on state/fnlocks/ADDR.lock, held by `run` for the whole attempt cycle
  (raw check, preflight, sandbox, agent, judge, ledger write), and by `verify`, `sync-names` and a manual `check`
  while they build or judge that function. A rival waits `function_lock_wait_s` (config, default 15) and then skips
  the function with a `busy` note naming the owner; a busy function's ledger row is never written.
  The agent's own check.py (`check --agent`) runs inside the owner's cycle and takes no claim.
- run_one: claim, then decide again from the fresh row whether the function still needs this run (another run may
  have finished it while this one waited in its queue), then refine.refine. An infrastructure error (core/infra:
  an OSError, or an InfraError from a refused tool-written file) is retried once with the attempts left; a second one
  is recorded as `infra_error` (a redo keeps the old file's verdict, with redo_failed outcome infra_error).
- run_all: refine.py run's batch. Once the plan's limit has rejected a session (core/usage) it starts no more functions:
  V8's run went through 268 of them in 52 minutes, each failing in seconds. A passing 429 or 529 does not stop it.
"""
import concurrent.futures
import os
import sys
import threading
import time

from . import build_prune, busylog, filelock, infra, paths, readable, refine, usage

LOCKS = paths.STATE / "fnlocks"
Busy = filelock.Busy


def wait_s(cfg):
    return float(cfg.get("function_lock_wait_s", 15))


def claim(addr, purpose, wait=15.0):
    """The claim of one function (context manager); raises Busy (str: the owner) after `wait` seconds."""
    owner = {"purpose": purpose, "command": " ".join(os.path.basename(x) if i == 0 else x
                                                     for i, x in enumerate(sys.argv))[:200]}
    return filelock.claim(LOCKS / f"{addr}.lock", wait, owner)


def skip_reason(addr, row, redo, since=None):
    """Why `refine.py run` leaves addr alone, or None: a readable file exists (not stale) and no --redo; failed and no
    --redo; or (since: this run's start) another process finished it during this run."""
    status = row.get("status")
    if since and (row.get("finished") or "") >= since and status not in ("running", None):
        return f"finished by another run during this one ({status} at {row.get('finished')})"
    if (readable.SRC / f"{addr}.c").exists() and not (redo or status == "stale"):
        return "has a readable file (use --redo)"
    if status == "failed" and not redo:
        return "failed before (use --redo)"
    return None


def record_infra(addr, note):
    """The status of a cycle that ended on a second infrastructure error: never blocked, failed or error."""
    kept = refine.kept_status(addr, refine.load_ledger().get(addr, {}))
    if kept:
        return refine.redo_not_passed(addr, kept, infra.STATUS, note)
    refine.update(addr, status=infra.STATUS, finished=refine.now(), note=note[:300])
    return f"{infra.STATUS}: {note} (not a verdict: run it again)"


def cycle(cfg, addr, attempts, log, model=None, effort=None, draft="off"):
    """refine.refine, retried once on an infrastructure error with the attempts it has left (at least one). draft: the
    machine draft first (core/draft_run: off, adopt, start)."""
    from . import draft_run
    with busylog.busy(busylog.slot(), "j"):  # the machine draft: builds and judges, no agent
        start = draft_run.first(cfg, addr, draft, log)
    if start and start[0] == "adopted":
        return start[1]
    left = attempts
    for n in (1, 2):
        before = len(refine.load_ledger().get(addr, {}).get("attempts", []))
        try:
            return refine.refine(cfg, addr, model=model, max_attempts=left, log=log, effort=effort,
                                 start=start[1] if start and n == 1 else None)
        except refine.PassNotRecorded:
            raise
        except Exception as e:  # noqa: BLE001  (anything else goes to refine.py run's record_error)
            if not infra.is_infra_exc(e):
                raise
            note = f"infrastructure error ({type(e).__name__}): {e}"
            if n == 2:
                log(f"{note}; second time: recorded as {infra.STATUS}")
                return record_infra(addr, note)
            used = len(refine.load_ledger().get(addr, {}).get("attempts", [])) - before
            left = max(1, left - used)
            log(f"{note}; retrying once ({left} attempt(s) left)")
    raise AssertionError("unreachable")


def run_one(cfg, addr, attempts, log, redo=False, since=None, model=None, effort=None, wait=None, draft="off"):
    """One function of `refine.py run` under its claim. Returns the run log's status text."""
    try:
        with claim(addr, "run", wait_s(cfg) if wait is None else wait):
            why = skip_reason(addr, refine.load_ledger().get(addr, {}), redo, since)
            if why:
                log(f"skipped: {why}")
                return f"skipped ({why})"
            status = cycle(cfg, addr, attempts, log, model=model, effort=effort, draft=draft)
            build_prune.after_job(cfg, addr, log)  # under the claim: its build outputs
            return status
    except Busy as e:
        log(f"{e}; not run, its ledger row is untouched")
        return f"busy (not run): {e}"


LIMIT_EXIT = 3  # refine.py run stopped on the plan's limit


def run_all(a, cfg, todo, since=None):
    """One worker per function: run_one holds the function's claim for its whole cycle (a function another process
    holds is skipped as busy, its row untouched) and retries an infrastructure error once. Exits LIMIT_EXIT when the
    plan's limit left functions not started; their rows are untouched."""
    build_prune.maybe_sweep(cfg, log=lambda m: print(m, flush=True))  # by age and size, when due
    lock = threading.Lock()

    def one(addr):
        def log(msg):
            with lock:
                print(f"[{addr}] {msg}", flush=True)
        if usage.Usage().blocked_until():
            return addr, None
        try:
            return addr, run_one(cfg, addr, a.attempts, log, redo=a.redo, since=since, model=a.model,
                                 effort=a.effort, draft=a.draft or cfg.get("readable_draft", "start"))
        except Exception as e:  # one bad function must not stop the batch (an old file keeps its verdict)
            try:  # after a pass wrote readable/src (PassNotRecorded) the status is readable, never error
                return addr, refine.record_error(addr, str(e), passed=isinstance(e, refine.PassNotRecorded))
            except Exception as e2:  # the ledger itself cannot be written: the row stays; verify repairs it
                return addr, (f"error: {e}; the ledger write failed too ({type(e2).__name__}: {e2}); "
                              f"run `refine.py verify {addr}`")

    n = max(1, min(a.workers, len(todo)))  # the threads the pool will start: each a worker (core/busylog)
    busylog.start_slots(n)
    not_run = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
            for addr, status in ex.map(one, todo):
                if status is None:
                    not_run += 1
                else:
                    print(f"== {addr}: {status}", flush=True)
    finally:
        busylog.end_slots(n)
    if not_run:
        until = usage.Usage().blocked_until()
        at = time.strftime("%a %H:%M", time.localtime(until)) if until else "now"
        print(f"PLAN LIMIT: stopped; {not_run} function(s) not started, their rows untouched. "
              f"It resets {at}; run the same command again then.", flush=True)
        sys.exit(LIMIT_EXIT)

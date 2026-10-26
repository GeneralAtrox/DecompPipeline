"""Per-worker busy/idle log (docs/DEFINITION_OF_DONE.md gate 3, "idle share"): of the time a worker exists, the share
it spends on real work. A record only: nothing in the pipeline reads it back (tools/scorecard.py does).

Each process appends to a file of its own, state/worker_busy/PID-START.log (no lock, no rival writer), one short line
per event: "<unix time, 0.1 s> <worker> <code>". Codes: "+" the worker started, "-" it ended; a letter starts real
work: p a Ghidra prepare (after its slot wait), t the translator, a an agent session, j a judge or build (the
official check, a readable check, a readable preflight or machine draft); "." ends it. Everything else a worker does
while it exists is idle: waiting for a Ghidra slot, a prefetch, a rate or usage limit or a claim, and the bookkeeping
between steps. Workers: core/orch's (W1, W2...; W0 runs `decomp.py investigate`) and `refine.py run`'s (R0, R1...).
A process that dies leaves its workers open: tools/scorecard_records.busy_totals leaves those lives out (their idle
time after the last event is unknown) and counts them. DP_NO_RECORDS=1 turns the log off (as core/oplog's).
"""
import contextlib
import os
import threading
import time

from . import paths

DIR = "worker_busy"
_NAME = f"{os.getpid()}-{int(time.time())}.log"
_LOCK = threading.Lock()
_SLOT = threading.local()
_SLOTS = {"next": 0, "run": 0}


def off():
    return os.environ.get("DP_NO_RECORDS") == "1"


def event(worker, code):
    """Append one event; never raises (a record must not stop the work)."""
    if worker is None or off():
        return
    line = f"{time.time():.1f} {worker} {code}\n"
    d = paths.STATE / DIR
    try:
        with _LOCK:
            try:
                f = open(d / _NAME, "a", encoding="ascii")
            except FileNotFoundError:
                d.mkdir(exist_ok=True)
                f = open(d / _NAME, "a", encoding="ascii")
            with f:
                f.write(line)
    except OSError:
        pass


@contextlib.contextmanager
def busy(worker, code):
    """Real work of `worker` (a key from of() or slot(); None records nothing) for the block's duration. Nested in
    another span of the same worker on this thread: part of that one, nothing recorded."""
    open_ = _SLOT.__dict__.setdefault("open", set())
    if worker is None or worker in open_:
        yield
        return
    open_.add(worker)
    event(worker, code)
    try:
        yield
    finally:
        open_.discard(worker)
        event(worker, ".")


def of(w):
    """The key of a core/orch Worker."""
    return f"W{getattr(w, 'id', '?')}"


def start_slots(n):
    """`refine.py run`: its n worker threads exist from here (R0..Rn-1); slot() names each thread on first use."""
    with _LOCK:
        _SLOTS["next"], _SLOTS["run"], _SLOTS["n"] = 0, _SLOTS["run"] + 1, n
    for i in range(n):
        event(f"R{i}", "+")


def end_slots(n):
    for i in range(n):
        event(f"R{i}", "-")
    with _LOCK:
        _SLOTS["n"] = 0


def slot():
    """This thread's key in the current `refine.py run` (None outside one: nothing is recorded)."""
    if not _SLOTS.get("n"):
        return None
    if getattr(_SLOT, "run", None) != _SLOTS["run"]:
        with _LOCK:
            _SLOT.run, _SLOT.key = _SLOTS["run"], f"R{_SLOTS['next']}"
            _SLOTS["next"] += 1
    return _SLOT.key

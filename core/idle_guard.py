"""The idle-time prepare job (tools/idle_prepare.py) and the server never use a workspace's Ghidra at the same time.

The job holds state/idle_prepare.json ({"pid", "started"}) while it runs and refuses to start while the server
answers. A server starting (Orchestrator.__init__) writes state/idle_prepare.stop and waits, bounded, until the job
has killed its Ghidra runs and removed its file. Those runs prepare functions with no current harness, so a killed
one loses nothing that was current. A file whose process is gone is stale and is removed.
"""
import ctypes
import json
import os
import time

from . import filelock

HOLD = "idle_prepare.json"
STOP = "idle_prepare.stop"


def holder(state):
    """The running job's {"pid", "started"}, or None (no file, unreadable, or its process gone: then removed)."""
    f = state / HOLD
    try:
        h = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(h, dict) and filelock.pid_alive(int(h.get("pid") or 0)):
        return h
    f.unlink(missing_ok=True)
    return None


def acquire(state):
    """Hold the workspace for this process (False: another job holds it). Clears a stop left from before."""
    if holder(state) is not None:
        return False
    state.mkdir(parents=True, exist_ok=True)
    (state / STOP).unlink(missing_ok=True)
    filelock.write_atomic(state / HOLD, json.dumps({"pid": os.getpid(), "started": time.time()}))
    return True


def release(state):
    (state / HOLD).unlink(missing_ok=True)
    (state / STOP).unlink(missing_ok=True)


def stop_requested(state):
    return (state / STOP).exists()


def end_job(state, log=None, timeout=120):
    """Ask a running job to stop and wait for it (the server, before it can start workers). True when none runs
    (any more); False when it still held the workspace after `timeout` seconds."""
    h = holder(state)
    if h is None:
        return True
    (state / STOP).write_text(str(os.getpid()), encoding="utf-8")
    if log:
        log(f"idle prepare (PID {h['pid']}) is running: asked it to stop")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if holder(state) is None:
            (state / STOP).unlink(missing_ok=True)
            return True
        time.sleep(0.5)
    if log:
        log(f"WARNING: idle prepare (PID {h['pid']}) did not stop within {timeout} s")
    return False


def ram_pct():
    """Physical memory in use, percent (None when unknown)."""
    try:
        if os.name == "nt":
            class Mem(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("total", "avail", "ptotal", "pavail", "vtotal", "vavail", "x")]
            m = Mem()
            m.len = ctypes.sizeof(Mem)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None
            return 100.0 * (m.total - m.avail) / m.total
        total = os.sysconf("SC_PHYS_PAGES")
        return 100.0 * (total - os.sysconf("SC_AVPHYS_PAGES")) / total
    except (OSError, ValueError, AttributeError):
        return None

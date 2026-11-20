"""The wave drivers' machine-wide controls (tools/wave.py, tools/wave_game.py), read by core/doctor.py too: the STOP
file `wave.py stop` writes, and the HOLD the re-stamp wave writes in the Ghidra broker's folder (core/ghidra_broker).

A STOP stops the waves running when it was written. One a wave finds when it starts was meant for the waves before
it: that wave ignores it (started, stop_asked) and never deletes it, since a wave still running may not have read it
yet. The wave's HOLD names its pass, its writer and the writer's pid. A hold attempt that fails takes its HOLD away
again (unhold: nothing owns it, and every Ghidra run would wait on it); a STOP keeps it, so a resumed wave goes on
behind it, until `wave.py release`.

Nearest existing: core/ghidra_broker.py - it only reads the HOLD, to make Ghidra runs wait (held); the STOP file, who
wrote the HOLD and whether that process still runs are the wave drivers', and tools/wave.py is a script core/doctor.py
cannot import.
"""
import json
import os
import pathlib
import time

from . import filelock

STATE_DIR = pathlib.Path(os.environ.get("LOCALAPPDATA", ".")) / "DecompPipeline" / "wave"
STOP_FILE = STATE_DIR / "STOP"
HOLD = "HOLD"  # core/ghidra_broker.HOLD: the file in the broker's folder
_seen = {}  # str(STOP path): that file as this process's wave found it when it started (started)


def stop_mark(path):
    """The STOP file at path as it is now: (mtime_ns, text), or None without one. write_stop writes a new text each
    time (a random part), so a second stop differs from the first even within one clock tick."""
    try:
        return path.stat().st_mtime_ns, path.read_text(encoding="utf-8")
    except OSError:
        return None


def started(path):
    """A wave starts: a STOP file at path now was written for the waves before it, so this process ignores it."""
    _seen[str(path)] = stop_mark(path)


def stop_asked(path):
    """A STOP written since this process's wave started (started): any STOP, when nothing called started."""
    now = stop_mark(path)
    return now is not None and now != _seen.get(str(path))


def write_stop(path):
    """`wave.py stop`: every wave running now stops; one that starts later ignores this STOP."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time():.6f} pid {os.getpid()} {os.urandom(4).hex()}\n", encoding="utf-8")


def _record(text):
    try:
        rec = json.loads(text) if text and text.strip() else {}
    except ValueError:
        return {}
    return rec if isinstance(rec, dict) else {}


def hold_info(d):
    """The HOLD in the broker folder d: its record (pass, by, since, pid; {} for one that names none), with "alive"
    (whether that pid runs) when it has a pid; None without a HOLD."""
    try:
        rec = _record((d / HOLD).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError:
        rec = {}
    if rec.get("pid") is not None:
        rec["alive"] = filelock.pid_alive(rec["pid"])
    return rec


def write_hold(d, key, by="tools/wave.py"):
    """The wave's HOLD in folder d (pass key, writer, time, pid). Returns the HOLD's text before, None without one:
    unhold puts that back."""
    try:
        before = (d / HOLD).read_text(encoding="utf-8")
    except FileNotFoundError:
        before = None
    filelock.write_atomic(d / HOLD, json.dumps({"pass": key, "by": by, "since": time.time(), "pid": os.getpid()}))
    return before


def unhold(d, key, before, log):
    """A hold attempt failed: the HOLD it wrote (pass key) goes, or the one another writer had there before
    (write_hold's before) comes back. A HOLD that no longer names key is someone else's and stays."""
    if (hold_info(d) or {}).get("pass") != key:
        return
    if before is not None and _record(before).get("pass") != key:
        filelock.write_atomic(d / HOLD, before)
        log(f"hold failed: the HOLD that was in {d} before is back")
    else:
        (d / HOLD).unlink(missing_ok=True)
        log(f"hold failed: HOLD removed ({d / HOLD}); no Ghidra run waits on it")


def release(d, log):
    """`wave.py release` (and a failed live test, and --release-at-end): the HOLD in folder d goes, whoever wrote it."""
    p = d / HOLD
    if p.exists():
        p.unlink(missing_ok=True)
        log(f"HOLD removed ({p})")

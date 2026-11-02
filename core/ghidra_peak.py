"""The Ghidra broker's one-JVM peak and its throttle note (core/ghidra_broker.by_ram sizes every JVM by the peak).

peak(): the largest Ghidra JVM seen, kept in the broker folder's jvm_peak.json ({"mb", "pid", "at"}), peak_mb until the first
sample. It EXPIRES: a record older than peak_ttl_s (config "ghidra_broker", default one hour) is dropped, and the next JVM
measured becomes the peak, unless a JVM at 90% or more of it is still running (then the record's time is refreshed: an
outlier that is still alive keeps counting while it lives). Before this a single 5,990 MB outlier on MAGNUS (normal JVMs are
150-400 MB) left room for one JVM on a 16 GB machine, so 8 slots ran as about 1, for as long as the file existed. The RAM
stop line (stop_at, stop_line) stays the safety whatever the peak says.

throttle_note(): the sentence a broker says (ram_log.jsonl's line, and once on stderr per change, say()) when what it grants
is far below the slots configured, so a card cannot report "6 slots running" when the broker allows 1.
Nearest existing: core/ghidra_broker.py - peak() lived there; that file is over its size cap (it may only shrink), so the
peak, its expiry and the note live here and the broker imports them.
"""
import contextlib
import json
import os
import sys
import time

PEAK = "jvm_peak.json"
TTL_S = 3600          # a peak not seen again for this long expires (config "peak_ttl_s")
ALIVE = 0.9           # a running JVM at this share of the peak keeps it alive
REFRESH_S = 60        # a live outlier's record is rewritten at most this often
_said = [""]


def peak(b, d, mem, now=None):
    """One JVM's peak MB: the largest seen in folder d's jvm_peak.json, raised by mem ({pid: MB}), expired after
    peak_ttl_s unless a JVM near it still runs; b's peak_mb until the first sample. Called under the folder's lock."""
    now = time.time() if now is None else now
    f = d / PEAK
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
        seen = float(rec["mb"])
        at = float(rec.get("at") or f.stat().st_mtime)
    except (OSError, ValueError, KeyError, TypeError):
        rec, seen, at = None, 0.0, now
    top = max(mem.items(), key=lambda kv: kv[1], default=(0, 0.0))
    alive = seen > 0 and top[1] >= ALIVE * seen
    if seen > 0 and not alive and now - at > float(b.get("peak_ttl_s", TTL_S)):
        seen = 0.0  # an old outlier, and no JVM near it now
        with contextlib.suppress(OSError):
            f.unlink()
    if top[1] > seen or (alive and now - at > REFRESH_S):
        seen = max(seen, top[1])
        pid = top[0] if top[1] >= seen else (rec or {}).get("pid", top[0])
        with contextlib.suppress(OSError):
            f.write_text(json.dumps({"mb": round(seen, 1), "pid": pid, "at": round(now, 1)}), encoding="utf-8")
    return seen if seen > 0 else float(b["peak_mb"])


def ceiling(b, ram, cores=None):
    """The most Ghidra JVMs by_ram may ever allow: slots, or max_jvms (0: cores / 4), or burst under burst_below."""
    n = max(int(b["slots"]), int(b["max_jvms"]) or (cores or os.cpu_count() or 4) // 4)
    return max(n, int(b["burst"])) if ram is not None and ram < b["burst_below"] else n


def throttle_note(b, n, ceil, peak_mb, total, ram):
    """"" unless the broker allows far fewer JVMs (n) than it is configured for (ceil): at most half, and two fewer."""
    if ceil - n < 2 or n * 2 > ceil:
        return ""
    return (f"ghidra_broker: {n} of {ceil} Ghidra JVM slots are usable now (RAM {ram:.0f}%"
            + (f" of {total / 1024:.0f} GB" if total else "") + f"; each JVM is counted at {peak_mb:.0f} MB, the largest "
            f"seen in the last {float(b.get('peak_ttl_s', TTL_S)) / 3600:.1f} h in {PEAK})")


def say(note):
    """The note on stderr, once per change (a process asks the broker many times a minute)."""
    if note and note != _said[0]:
        print(note, file=sys.stderr, flush=True)
    _said[0] = note

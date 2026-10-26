"""How long a function's prepare is expected to take, from what its earlier prepares took (long-tail prepares).

Prepares of LONG_S seconds or more are 1-2% of prepares but 31-44% of prepare time on TC and Spawn (their
events.log), mostly oracle work bound by its deadline, and prefetch cannot hide them behind one agent. History
predicts them exactly under the same scripts (two fresh runs of 500 TC functions: the same 6 slow in both) and less
well across script changes (TC events.log: 2 of 12 slow before were slow again), so it orders work ahead of time
(tools/idle_prepare.py) and never decides whether a function is prepared.

Sources, the newest prepare of each function wins:
- state/prepare_timing.jsonl (and its rotated .1): each ok record's phases.total (core/prepare_timing);
- state/events.log, for prepares before prepare timing: "[Wn] ADDR: preparing in Ghidra" to that worker's next line
  about ADDR. The log's times have no date: a step back of more than 10 minutes starts a new day.
"""
import json
import re

LONG_S = 120
_LINE = re.compile(r"^(\d\d):(\d\d):(\d\d)  \[W(\d+)\] ([0-9a-f]{8})([: #].*)$")
_START = ": preparing in Ghidra"


def _from_events(path):
    out, open_, day, last = {}, {}, 0, None
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with f:
        for raw in f:
            m = _LINE.match(raw.rstrip("\n"))
            if not m:
                continue
            s = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            if last is not None and s < last - 600:
                day += 1
            last, t = s, day * 86400 + s
            w, a = m[4], m[5]
            if m[6].startswith(_START):
                open_[w] = (a, t)
            elif w in open_ and open_[w][0] == a:
                out[a] = t - open_.pop(w)[1]
    return out


def _from_timing(state):
    out = {}
    for name in ("prepare_timing.jsonl.1", "prepare_timing.jsonl"):  # older first: the newest record wins
        try:
            lines = (state / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            total = (r.get("phases") or {}).get("total")
            if r.get("ok") and r.get("addr") and isinstance(total, (int, float)):
                out[r["addr"]] = float(total)
    return out


def history(state):
    """{addr: seconds of its latest prepare} from the workspace's state folder."""
    return {**_from_events(state / "events.log"), **_from_timing(state)}


def order(addrs, hist, long_s=LONG_S):
    """addrs with the ones expected to take long_s or more first, slowest first; the rest keep their order."""
    slow = sorted((a for a in addrs if hist.get(a, 0) >= long_s), key=lambda a: -hist[a])
    return slow + [a for a in addrs if hist.get(a, 0) < long_s]

"""The frame lists of the performance report: the worst frames with what ran in each, and where in the replay the
frames over the bar cluster. Split from perf_report.py, which works out the numbers these read.

Nearest existing: port/tools/perf_report.py - the analysis itself; these two lists are its last step and were moved
out to keep it under the file-size cap.
"""
import collections

WAITING = "waiting"


def worst_frames(all_frames, by_tick, entries, profiles, limit=8):
    out, seen = [], set()
    for r in sorted(all_frames, key=lambda r: -r["make_us"]):
        if r["tick"] in seen:
            continue
        seen.add(r["tick"])
        funcs, secs, bound = collections.Counter(), collections.Counter(), collections.Counter()
        for key, w, sec in by_tick.get(r["tick"], ()):
            e, _th, b, blocked, _sys = entries[key]
            bound[b] += w
            if b != WAITING:
                funcs[e["name"] + (f" (blocked: {blocked})" if blocked else "")] += w
                if sec:
                    secs[sec] += w
        total, allw = sum(funcs.values()) or 1.0, sum(bound.values()) or 1.0
        near = [t for v, t in profiles[r["run"]]["log"]["stamped"] if r["vblank"] - 120 <= v <= r["vblank"] + 5][-3:]
        out.append({"tick": r["tick"], "vblank": r["vblank"], "run": r["run"], "make_ms": r["make_us"] / 1000,
                    "tick_ms": r["tick_us"] / 1000, "samples": len(by_tick.get(r["tick"], ())),
                    "bound": {k: v / allw for k, v in bound.items()},
                    "running": [[n, v / total] for n, v in funcs.most_common(5)],
                    "sections": [[n, v / (sum(secs.values()) or 1.0)] for n, v in secs.most_common(4)], "game_was": near})
        if len(out) >= limit:
            break
    return out


def over_list(over_rows, profiles, limit=40):
    """The frames over budget grouped into stretches of the replay, with what the log says the game was doing."""
    groups = []
    for r in sorted(over_rows, key=lambda r: (r["run"], r["vblank"])):
        g = groups[-1] if groups else None
        if g and g["run"] == r["run"] and r["vblank"] - g["to_vblank"] <= 120:
            g["to_vblank"], g["frames"], g["worst_ms"] = r["vblank"], g["frames"] + 1, max(g["worst_ms"], r["make_us"] / 1000)
        else:
            groups.append({"run": r["run"], "from_vblank": r["vblank"], "to_vblank": r["vblank"], "frames": 1,
                           "worst_ms": r["make_us"] / 1000})
    for g in groups:
        stamped = profiles[g["run"]]["log"]["stamped"]
        g["game_was"] = [t for v, t in stamped if g["from_vblank"] - 300 <= v <= g["to_vblank"]][-2:]
    groups.sort(key=lambda g: -g["frames"])
    return groups[:limit]

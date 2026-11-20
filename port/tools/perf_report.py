"""The analysis behind port/tools/perf.py: samples, frame rows and logs in, the numbers of the report out.

Reads what base/perf.c writes (--perf-samples, --play-frames) and a port.map (perf_map.py). Ticks are split into
those made within the bar, those just over it (the band up to 8 ms, which decides a p99 near the bar) and those far
over, and every cost is given for each, so the report says what was running in the frames that missed and not only on
average. Time blocked in the system is named by what it waits on. Nothing here knows a game or a console.

Nearest existing: tools/scorecard_runtime.py - reads only the log's `play verdict` line, no per-frame or per-function
view; the hand-made lists (PowerStone2 late19 REWRITE_LIST.md, Spawn FRAMETIME.md) were built by one-off scripts.
"""
import array
import collections
import csv
import re
import struct

from perf_frames import WAITING, over_list, worst_frames
from perf_map import CORE, KIT, LIB, NATIVE, RAW, READABLE

BAR_US, BAND_US = 6670, 8000
CLASSES = ("normal", "band", "far")
GPU, CPU = "graphics driver or GPU", "CPU"
WAIT_KINDS = (  # what a thread inside a system call is waiting on, from the function in the exe that made the call
    (r"present|latency|show_|swap|flip", "present / display"),
    (r"pace|vblank|sleep|timer|idle|pump", "frame pacing"),
    (r"gdfs|file|fopen|fread|read_|disc|cd_|gd_|load", "file read"),
    (r"lock|mutex|critical", "a lock"),
    (r"pack|worker|thread|join|event|band|copy_helper", "a worker thread"),
)
PACING = ("present / display", "frame pacing")
# One-off work a kit logs: it belongs before play, so a benchmark counts it, marks its frames and gives the numbers with
# and without them. A kit's own line for it is `once: <kind> <ms> ms <what>`; the patterns catch lines kits already write.
ONCE_LINE = re.compile(r"\bonce: ([a-z][a-z -]*?) ([\d.]+) ms")
ONCE_KINDS = (
    (r"shader.*(compil|built|creat)|d3dcompile|compil.*shader", "shader compile"),
    (r"(texture|pack|model|sound bank).*(first use|loaded|decoded|built)", "first-use load"),
    (r"\bopen(ed|s|ing)? .*\.(cpk|afs|pak|bin|wmv|sfd|adx|xma|pvr)\b", "file open"),
)
GRAPHICS = re.compile(r"d3d|gfx|render|pvr|draw|tex|shader|vdp", re.I)
# What a swap is expected to give back, as a share of the item's own time: (low, high, where the range comes from).
GAINS = {
    RAW: (0.30, 0.70, "native rewrite of translated C: accessors inlined gave 28%, the ARM7 interpreter to native 70% (docs/RUNTIME_TARGET.md)"),
    READABLE: (0.20, 0.60, "native rewrite of readable C: still runs on guest memory and the call glue"),
    KIT: (0.50, 1.00, "replaced natively, or moved off the frame's thread (then all of it leaves the frame)"),
    LIB: (0.50, 1.00, "library or system time: cached, or moved off the frame at its caller"),
    CORE: (0.20, 0.60, "Core code: ordinary optimisation, or off the frame"),
    NATIVE: (0.10, 0.40, "already native: ordinary optimisation only"),
}


def read_samples(path):
    """{'period_us', 'sections': [names], 'rows': [(t_us, tick, vblank, thread, section, offset, in_system, stack)],
    'busy': [whether the thread had run since the last sample, per row (meta bit 7; True in older files)]}"""
    data = open(path, "rb").read()
    magic, size, period, nsec, nwords = struct.unpack_from("<5I", data, 0)
    if magic != 0x31465250:
        raise ValueError(f"{path}: not a perf sample file")
    pos = 20
    sections = [data[pos + 32 * i:pos + 32 * i + 32].split(b"\0")[0].decode("ascii", "replace") for i in range(nsec)]
    pos += 32 * nsec
    w = array.array("I")
    w.frombytes(data[pos:pos + 4 * nwords])
    rows, busy, i, n = [], [], 0, len(w)
    while i + 5 <= n:
        meta = w[i + 3]
        depth = (meta >> 16) & 0xff
        where = w[i + 4]
        rows.append((w[i], w[i + 1], w[i + 2], meta & 0x7f, (meta >> 8) & 0xff, where & 0x7fffffff,
                     bool(where & 0x80000000), tuple(w[i + 5:i + 5 + depth])))
        busy.append(bool(meta & 0x80))
        i += 5 + depth
    if busy and not any(busy):
        busy = [True] * len(busy)  # a file from before the bit: every sample counts as run
    return {"period_us": period, "sections": sections, "rows": rows, "busy": busy, "image_size": size}


def read_frames(path):
    with open(path, newline="") as f:
        return [{k: int(v) for k, v in r.items()} for r in csv.DictReader(f)]


def pct(values, p):
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(p * len(v) + 0.999999) - 1))]


def once_summary(events, frames, start=0):
    """({kind: {'count', 'ms'}}, the vertical blanks whose frames carry one-off work, how many events have no blank)."""
    kinds, marked, unplaced = {}, set(), 0
    last = max((r["vblank"] for r in frames), default=None)
    for e in events:
        v = e["vblank"]
        if v is not None and (v < start or (last is not None and v > last)):
            continue
        k = kinds.setdefault(e["kind"], {"count": 0, "ms": 0.0})
        k["count"], k["ms"] = k["count"] + 1, k["ms"] + e["ms"]
        if v is None:
            unplaced += 1
        else:
            marked |= {v, v + 1}  # the frame it ran in is shown at that blank or the next
    return kinds, marked, unplaced


# Frames the kit marks as a loading screen (flag 1: over 256 KB read from the disc in half a second) are left out, as
# the log's verdict does. perf.py --count-loading sets 0: a stretch the kit marks as loading throughout (a streamed
# movie, Toy Commander's front end) is then timed like any other.
LOADING = 1


def frame_stats(frames, bar_us=BAR_US, skip=()):
    """The benchmark numbers of one run, from its frame rows (loading screens left out, as the log's verdict does);
    skip: vertical blanks left out too (the frames that carried one-off work)."""
    judged = [r for r in frames if not r["flags"] & LOADING and r["vblank"] not in skip]
    make = [r["make_us"] for r in judged]
    late = sum(max(0, (r["gap_us"] * 144 + 500000) // 1000000 - 1) for r in judged)
    over = [r for r in judged if r["make_us"] > bar_us]
    return {"frames": len(judged), "loading_frames": len(frames) - len(judged), "kit_loading": sum(r["flags"] & 1 for r in frames),
            "make_ms": {"median": pct(make, 0.5) / 1000, "p99": pct(make, 0.99) / 1000, "worst": max(make, default=0) / 1000},
            "late": late, "late_per_1000": 1000 * late / len(judged) if judged else 0.0,
            "over": len(over), "over_per_1000": 1000 * len(over) / len(judged) if judged else 0.0}


def stats_from_log(log):
    """frame_stats' shape from the log's own lines, for a build that has no --play-frames (None if it has neither)."""
    m = re.search(r"make_ms ([\d.]+)/([\d.]+)/([\d.]+)", log["summary"])
    v = re.search(r"late (\d+) of (\d+) shown \(([\d.]+) per 1000", log["verdict"])
    if not m or not v:
        return None
    return {"frames": int(v.group(2)), "loading_frames": 0, "late": int(v.group(1)), "late_per_1000": float(v.group(3)),
            "make_ms": {"median": float(m.group(1)), "p99": float(m.group(2)), "worst": float(m.group(3))},
            "over": None, "over_per_1000": None, "from_log": True}


def log_lines(path):
    """The log's own verdict lines, the sampler's cost line, and the stamped lines by vertical blank."""
    out = {"summary": "", "verdict": "", "perf": "", "held_ms": None, "stamped": [], "out_of_step": "", "once": [], "timers": [],
           "timer_frames": None}
    try:
        text = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return out
    for ln in text:
        for key in ("play summary:", "play verdict:", "perf:"):
            if key in ln and (key != "perf:" or "samples of the game thread" in ln):
                out[key.split()[-1].rstrip(":")] = ln[ln.index(key):].strip()
        t = re.search(r"perf: timer (\w+) (.+?): ([\d.]+) ms in (\d+) calls, longest ([\d.]+) ms"
                      r"(?:; ([\d.]+) ms in frames within the bar, ([\d.]+) ms in frames over it)?", ln)
        if t:  # base/perf.c's named timers: measured, where a sample cannot name the caller; then split by frame
            out["timers"].append([t.group(1), t.group(2), float(t.group(3)), int(t.group(4)), float(t.group(5))]
                                 + ([float(t.group(6)), float(t.group(7))] if t.group(6) else []))
        f = re.search(r"perf: timer frames: (\d+) within the bar, (\d+) over it", ln)
        if f:
            out["timer_frames"] = [int(f.group(1)), int(f.group(2))]
        if "replay out of step" in ln and not out["out_of_step"]:
            out["out_of_step"] = ln.strip()[:200]
        m = re.match(r"\[\S+ \+[\d.]+s v(\d+)\] (.*)", ln)
        once = ONCE_LINE.search(ln)
        kind = once.group(1).strip() if once else next((k for pat, k in ONCE_KINDS if re.search(pat, ln, re.I)), None)
        if kind:  # vblank None: the kit wrote it outside the game log, so its frame cannot be marked
            ms = once or re.search(r"([\d.]+) ms", ln)
            out["once"].append({"vblank": int(m.group(1)) if m else None, "kind": kind,
                                "ms": float(ms.group(2 if once else 1)) if ms else 0.0})
        if m and not m.group(2).startswith(("gamelog:", "P1 ", "P2 ", "P3 ", "P4 ")):
            out["stamped"].append((int(m.group(1)), m.group(2)[:140]))
    m = re.search(r"held ([\d.]+) ms in all", out["perf"])
    out["held_ms"] = float(m.group(1)) if m else None
    return out


def bench(runs):
    """runs: [{'side', 'stats' (frame_stats), 'cpu', ...}]. Per side the mean and the run-to-run spread; a difference
    between two sides is called real only when it is larger than the larger spread."""
    out = {"sides": {}, "runs": runs}
    for side in sorted({r["side"] for r in runs}):
        mine = [r["stats"] for r in runs if r["side"] == side and r.get("stats")]
        row = {"runs": len(mine)}
        for key in ("median", "p99", "worst"):
            v = [s["make_ms"][key] for s in mine]
            row[key] = {"mean": sum(v) / len(v) if v else 0.0, "min": min(v, default=0.0), "max": max(v, default=0.0),
                        "spread": max(v, default=0.0) - min(v, default=0.0)}
        v = [s["late_per_1000"] for s in mine]
        row["late_per_1000"] = {"mean": sum(v) / len(v) if v else 0.0, "min": min(v, default=0.0), "max": max(v, default=0.0)}
        mine = [r for r in runs if r["side"] == side and r.get("stats")]
        if any(r.get("once") for r in mine):  # one-off work in the window: per run on average, and the numbers without it
            kinds = sorted({k for r in mine for k in r.get("once", {})})
            row["once"] = {k: {key: sum(r.get("once", {}).get(k, {}).get(key, 0) for r in mine) / len(mine) for key in ("count", "ms")}
                           for k in kinds}
            row["once_frames"] = sum(r.get("once_frames", 0) for r in mine) / len(mine)
            row["once_unplaced"] = sum(r.get("once_unplaced", 0) for r in mine) / len(mine)
            wo = [r["stats_without"] for r in mine if r.get("stats_without")]
            if wo:
                row["without_once"] = {key: sum(s["make_ms"][key] for s in wo) / len(wo) for key in ("median", "p99", "worst")}
                row["without_once"]["late_per_1000"] = sum(s["late_per_1000"] for s in wo) / len(wo)
        out["sides"][side] = row
    if len(out["sides"]) == 2:
        a, b = (out["sides"][s] for s in sorted(out["sides"]))
        out["difference"] = {}
        for key in ("median", "p99"):
            d = b[key]["mean"] - a[key]["mean"]
            noise = max(a[key]["spread"], b[key]["spread"])
            out["difference"][key] = {"b_minus_a_ms": d, "noise_ms": noise,
                                      "real": abs(d) > noise and min(a["runs"], b["runs"]) >= 2}
    return out


def wait_kind(name):
    return next((k for pat, k in WAIT_KINDS if re.search(pat, name, re.I)), "a system call")


def bound_of(entry, thread, section, sections, in_system):
    """(CPU | GPU | WAITING, what it waits on or None) for one sample."""
    if thread == 0 and section < len(sections) and sections[section].lower().startswith("wait"):
        return WAITING, "frame pacing"
    if not in_system:
        return CPU, None
    if entry["kind"] == "system time":
        return CPU, "a system call"
    kind = wait_kind(entry["name"])
    if kind in PACING and entry["tier"] not in (RAW, READABLE):
        return WAITING, kind
    if kind == "a system call" and (GRAPHICS.search(entry["name"]) or "graphics" in entry["kind"]):
        return GPU, "the graphics driver"
    return CPU, kind  # a file read, a lock, a worker: the frame's own time, blocked


def fix_for(f):
    """Which rule applies to a cost (the user's rules, 2026-11-11): move off the frame, cache, or native rewrite."""
    if f.get("blocked_on") in ("file read", "a worker thread", "a lock"):
        return f"move off the frame: {f['blocked_on']} on the frame's thread; do it on a worker, ahead of need"
    if f.get("bound") == GPU:
        return "GPU or driver time: fewer or cheaper draws, uploads ahead of need; check against the small-window run"
    if f["tier"] in (RAW, READABLE):
        return "native rewrite, with the translated route kept selectable as the answer key (proven by replay)"
    if f["tier"] == KIT:
        if "sound" in f["kind"] or "media" in f["kind"]:
            return "move off the frame's thread, or native rewrite of its hot loops (old model kept selectable)"
        if "graphics" in f["kind"]:
            return "cache what repeats each frame (parsed lists, decoded textures), then native replacement of the emulated path"
        if "translated-code" in f["kind"]:
            return "goes with native rewrites of its callers (direct calls, host memory); until then inline or cache the lookup"
        return "native replacement of the emulated part, or off the frame's thread"
    if f["tier"] == LIB:
        return "cache the result or move the call off the frame, at the function that calls it"
    return "ordinary optimisation"


class _Acc:
    def __init__(self):
        self.d = {c: collections.Counter() for c in CLASSES}

    def add(self, c, key, w):
        self.d[c][key] += w

    def keys(self):
        return set().union(*self.d.values())

    def per(self, c, key, nticks):
        if c == "over":
            n = nticks["band"] + nticks["far"]
            return (self.d["band"][key] + self.d["far"][key]) / n if n else 0.0
        return self.d[c][key] / nticks[c] if nticks[c] else 0.0


def analyse(profiles, mp, bar_us=BAR_US, band_us=BAND_US, calls=None, call_blanks=0, top=40):
    """profiles: [{'samples': read_samples(), 'frames': read_frames(), 'log': log_lines()}], runs of one build over
    one replay (ticks line up, so they pool). mp: perf_map.Map. calls: {guest address: count} of a counting pass."""
    self_ms, count, incl_ms, layer_ms, section_ms, wait_ms, bound_ms = (_Acc() for _ in range(7))
    nticks = {c: 0 for c in CLASSES}
    entries, by_tick, all_frames, weights = {}, collections.defaultdict(list), [], []
    for run_i, p in enumerate(profiles):
        frames, rows, sections = p["frames"], p["samples"]["rows"], p["samples"]["sections"]
        size = p["samples"].get("image_size") or 0x7ffffff0
        make = collections.Counter()
        for r in frames:
            if not r["flags"] & LOADING:
                make[r["tick"]] = max(make[r["tick"]], r["make_us"], 1)
        cls = {t: "far" if m > band_us else "band" if m > bar_us else "normal" for t, m in make.items()}
        all_frames += [dict(r, run=run_i) for r in frames if not r["flags"] & LOADING]
        seen = {r[1] for r in rows}
        for t in seen & set(cls):
            nticks[cls[t]] += 1
        for thread in (0, 1):
            mine = [r for r in rows if r[3] == thread]
            if len(mine) < 2:
                continue
            w = (mine[-1][0] - mine[0][0]) / 1000.0 / (len(mine) - 1)  # ms of that thread's time one sample stands for
            weights.append(w)
            tname = "game thread" if thread == 0 else "thread showing frames"
            for _t, tick, _vblank, _th, section, off, in_system, stack in mine:
                c = cls.get(tick)
                if not c:
                    continue
                if off >= size:  # the sampler could not name a caller (base/perf.c): placed by the game's call stack
                    under = mp.guest(stack[-1]) if stack else None
                    what = "in the kernel, caller not known" if off == 0x7ffffffe else "in the system, no caller in the exe"
                    e = {"rva": -2 - (stack[-1] if stack else 0), "addr": None, "tier": LIB, "layer": "Core", "object": "",
                         "name": f"({what}" + (f"; under {under['name']})" if under else ")"),
                         "kind": "system time" if off == 0x7ffffffe else "system", "folded": 0}
                else:
                    e = mp.at(off) or {"rva": -1, "name": "(outside the exe)", "addr": None, "tier": LIB, "layer": "Core",
                                       "kind": "system", "object": "", "folded": 0}
                bound, blocked = bound_of(e, thread, section, sections, in_system)
                key = (e["rva"], thread, bound, in_system)
                entries[key] = (e, thread, bound, blocked, in_system)
                self_ms.add(c, key, w)
                count.add(c, key, 1)
                layer_ms.add(c, (tname, "waiting / pacing" if bound == WAITING else e["layer"]), w)
                bound_ms.add(c, bound, w)
                if in_system or bound == WAITING:
                    wait_ms.add(c, (tname, blocked or "a system call", e["name"]), w)
                if thread == 0:
                    section_ms.add(c, sections[section] if section < len(sections) else "(no section)", w)
                    for addr in set(stack):
                        incl_ms.add(c, addr, w)
                by_tick[tick].append((key, w, sections[section] if thread == 0 and section < len(sections) else ""))
    nticks["over"] = nticks["band"] + nticks["far"]
    use = "over" if nticks["over"] else "normal"
    work = sum(self_ms.per(use, k, nticks) for k in entries if k[2] != WAITING) or 1.0
    funcs = []
    for key, (e, thread, bound, blocked, in_system) in entries.items():
        addr = e["addr"]
        unsure = bool(in_system) and bound != WAITING  # busy inside the system: the stack may name the wrong caller
        f = {"name": e["name"] + (f" (+{e['folded']} folded into the same code)" if e["folded"] else "")
             + (" [caller uncertain]" if unsure else ""), "caller_uncertain": unsure,
             "address": f"{addr:08x}" if addr is not None else None, "tier": e["tier"], "kind": e["kind"], "layer": e["layer"],
             "thread": "game" if thread == 0 else "show", "in_system_call": in_system, "bound": bound, "blocked_on": blocked,
             "object": e["object"]}
        for c in CLASSES + ("over",):
            f["self_ms_" + c] = self_ms.per(c, key, nticks)
            f["samples_" + c] = int(count.d["band"][key] + count.d["far"][key] if c == "over" else count.d[c][key])
        f["excess_ms"] = f["self_ms_over"] - f["self_ms_normal"] if nticks["over"] else 0.0
        f["excess_band_ms"] = f["self_ms_band"] - f["self_ms_normal"] if nticks["band"] else 0.0
        f["cost_ms"] = f["self_ms_" + use]
        f["share_of_work"] = f["cost_ms"] / work if bound != WAITING else 0.0
        if addr is not None and thread == 0 and addr in incl_ms.keys():
            f["inclusive_ms_normal"] = max(incl_ms.per("normal", addr, nticks), f["self_ms_normal"])
            f["inclusive_ms_over"] = max(incl_ms.per("over", addr, nticks), f["self_ms_over"])
        if calls is not None and addr is not None and call_blanks:
            f["calls_per_tick"] = calls.get(addr, 0) / call_blanks
        f["fix"] = fix_for(f)
        funcs.append(f)
    funcs.sort(key=lambda f: -f["cost_ms"])
    make = [r["make_us"] for r in all_frames]
    stats = {"median": pct(make, 0.5) / 1000, "p99": pct(make, 0.99) / 1000, "worst": max(make, default=0) / 1000}
    over_rows = [r for r in all_frames if r["make_us"] > bar_us]
    mean_over = sum(r["make_us"] for r in over_rows) / len(over_rows) / 1000 if over_rows else 0.0
    need = {"p99_cut_ms": max(0.0, stats["p99"] - bar_us / 1000), "mean_over_cut_ms": max(0.0, mean_over - bar_us / 1000)}

    def table(acc, n=None):
        rows = [[*(k if isinstance(k, tuple) else (k,)), *(acc.per(c, k, nticks) for c in CLASSES + ("over",))] for k in acc.keys()]
        return sorted(rows, key=lambda r: -(r[-1] or r[-4]))[:n]

    band_frames = sum(1 for r in all_frames if bar_us < r["make_us"] <= band_us)
    band = {"frames": band_frames, "of_over_frames": band_frames / len(over_rows) if over_rows else 0.0,
            "ticks": nticks["band"], "p99_in_band": bar_us < stats["p99"] * 1000 <= band_us,
            "from_ms": bar_us / 1000, "to_ms": band_us / 1000,
            "functions_extra": [[f["name"], f["tier"], f["thread"], f["self_ms_normal"], f["self_ms_band"], f["excess_band_ms"],
                                 f["samples_band"], f["fix"]]
                                for f in sorted(funcs, key=lambda f: -f["excess_band_ms"])[:12] if f["excess_band_ms"] > 0]}
    return {"bar_ms": bar_us / 1000, "class_used": use, "ticks": nticks, "make_ms": stats, "need": need,
            "sample_ms": sum(weights) / len(weights) if weights else 0.0, "band": band,
            "bound": table(bound_ms), "layers": table(layer_ms), "sections": table(section_ms), "waits": table(wait_ms, 20),
            "functions": funcs[:top], "functions_total": len(funcs), "swaps": swaps(funcs, need, use),
            "worst_frames": worst_frames(all_frames, by_tick, entries, profiles), "over_frames": over_list(over_rows, profiles)}


def swaps(funcs, need, use, limit=15):
    """The ranked swap list: the costs a rewrite, a cache or a move off the frame can take away, largest first."""
    out, covered = [], 0.0
    target = need["p99_cut_ms"]
    for f in funcs:
        if f["bound"] == WAITING or f["tier"] not in GAINS or f["cost_ms"] <= 0 or f.get("caller_uncertain"):
            continue
        low, high, why = GAINS[f["tier"]]
        n = f["samples_" + use]
        sure = "high" if n >= 300 else "medium" if n >= 80 else "low"
        covered += f["cost_ms"] * (low + high) / 2
        out.append({"rank": len(out) + 1, "name": f["name"], "address": f["address"], "what": f["tier"], "kind": f["kind"],
                    "thread": f["thread"], "blocked_on": f["blocked_on"], "fix": f["fix"],
                    "cost_ms_per_frame": f["cost_ms"], "excess_ms_in_over_frames": f["excess_ms"],
                    "share_of_frame_work": f["share_of_work"], "share_of_needed_cut": f["cost_ms"] / target if target else None,
                    "gain_ms": [f["cost_ms"] * low, f["cost_ms"] * high], "gain_basis": why,
                    "confidence": sure, "samples": n, "cumulative_gain_mid_ms": covered,
                    "reaches_bar_here": bool(target) and covered >= target})
        if len(out) >= limit:
            break
    return out

r"""Why each frame was late: one timed run of a segment, and every late frame and long tick named by its cause.

    py -3.12 port/tools/perf.py segment --a BUILD_DIR --exe game.exe --out OUT_DIR --state FILE [--state-at 120]
        [--warmup 600] [--sample-us 500] [--symbols FILE] [--signed-in] -- <the game's own arguments, its stop>
    py -3.12 port/tools/perf.py segment --report OUT_DIR [--a BUILD_DIR --exe game.exe --symbols FILE]   (again, from kept files)

The run is the launcher's play path on the real clock (a window; --signed-in starts it in the machine's signed-in
session, which a window over ssh needs). It loads the state at --state-at, measures from --warmup blanks after the load
(--perf-from load+N: the game log's play summary and verdict count from there too) and keeps three files: the frame rows,
the tick rows with the kit's parts (base/perf_trace.c) and the samples (base/perf.c). With --replay REC and --from V it
measures a replay from blank V instead. On the main laptop it runs only as MAIN_LAPTOP_OK=final-timing.

The report (late-<stamp>.md, late-<stamp>.html with the frame-time chart, late-<stamp>.json) gives the verdict against
the bar, the late frames and long ticks grouped by cause, worst first, with the game's function names and whether each
was translated or interpreted, and the share of the game thread that was work, waiting or the system's. A late frame is
one whose gap spans more than one 144 slot; its cause is what took the most time on the game's thread in the gap (left
out: the pacing wait, where the kit draws in-between frames on time), or else on the thread that shows frames.

Nearest existing: perf.py all/bench/profile - pools runs and ranks functions by tick, but has no per-frame cause, no
state-load segment and no tick parts; perf_report.py's worst frames list what ran in the ten worst only. This reuses
both (run_exe, Map, read_samples, frame_stats).
"""
import argparse
import collections
import csv
import datetime
import html
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import perf_report as pr  # noqa: E402

SLOT_US = 1000000 / 144
MAIN_LAPTOP = os.environ.get("DP_MAIN_LAPTOP", os.environ.get("DP_TARGET_MACHINE", ""))  # the user's laptop: only the final confirm runs there
PACING = "wait: pacing to the blank"


def read_ticks(path):
    if not pathlib.Path(path).exists():
        return []
    with open(path, newline="") as f:
        return [{k: int(v) for k, v in r.items()} for r in csv.DictReader(f)]


def read_symbols(path):
    """{original address: name} from lines holding an 8-digit hex address and a name (either order)."""
    out = {}
    if not path or not pathlib.Path(path).exists():
        return out
    for ln in open(path, encoding="utf-8", errors="replace"):
        m = re.search(r"\b(?:0x)?([0-9a-fA-F]{8})\b[\s,=:]+([A-Za-z_][\w@$.]*)", ln) or re.search(
            r"([A-Za-z_][\w@$.]*)[\s,=:]+(?:0x)?([0-9a-fA-F]{8})\b", ln)
        if m:
            a, n = (m.group(1), m.group(2)) if re.fullmatch(r"[0-9a-fA-F]{8}", m.group(1)) else (m.group(2), m.group(1))
            out.setdefault(int(a, 16), n)
    return out


class Namer:
    def __init__(self, mp=None, syms=None):
        self.mp, self.syms = mp, syms or {}

    def guest(self, addr):
        return self.syms.get(addr) or (self.mp.guest(addr)["name"] if self.mp else f"fn_{addr:08x}")

    def host(self, off):
        e = self.mp.at(off) if self.mp else None
        return e["name"] if e else f"port.exe+{off:x}"


def category(s, busy, sections, nm):
    """(cause, function) for one sample."""
    _t, _tick, _vb, thread, section, off, in_system, stack = s
    sec = sections[section] if section < len(sections) else ""
    who = "game thread" if thread == 0 else "show thread"
    if in_system:
        if off == 0x7ffffffe:
            return f"OS: {who} busy inside the kernel", ""
        name = nm.host(off)
        kind = pr.wait_kind(name)
        if thread == 0 and kind == "frame pacing":  # the kit sleeping to the blank (pace_sleep_us): waiting, never a cause
            return PACING, name
        return (f"OS: {who} blocked ({kind})" if not busy else f"OS: {who} busy in a system call"), name
    if not busy and not sec.startswith("wait") and s[1]:  # held, not in the system, yet it had not run: descheduled
        return f"OS: {who} not running (Windows ran something else)", ""
    if thread != 0:
        return f"show thread: {nm.host(off)}", ""
    if sec.startswith(("wait", "kit")):
        return sec, (nm.guest(stack[-1]) if stack and sec.startswith("wait: game") else "")
    if sec.startswith("game"):
        return sec, (nm.guest(stack[-1]) if stack else nm.host(off))
    return ("game: " + (sec or "code")), (nm.guest(stack[-1]) if stack else nm.host(off))


def tick_cause(t, names):
    """With no samples: the largest of a tick's own parts (Saturn: game, polling, the chips)."""
    parts = {n: t[n] for n in names if n not in ("work", "wait_pace", "wait_poll")}  # waits are not in the work
    if not parts:
        return "a long tick (no parts)", ""
    k = max(parts, key=parts.get)
    return f"tick part: {k}", ""


def analyse(frames, ticks, samples, nm, bar_us=pr.BAR_US):
    rows = samples["rows"] if samples else []
    busy = samples.get("busy") if samples else None
    sections = samples["sections"] if samples else []
    period = samples["period_us"] if samples else 0
    times = [s[0] for s in rows]
    import bisect

    def window(t0, t1, thread):
        i, j = bisect.bisect_right(times, t0), bisect.bisect_right(times, t1)
        return [(rows[k], busy[k] if busy else True) for k in range(i, j) if rows[k][3] == thread]

    part_names = [k for k in (ticks[0].keys() if ticks else []) if k not in ("t_us", "tick", "vblank")]
    tick_t = [t["t_us"] for t in ticks]
    stats = pr.frame_stats(frames, bar_us)
    events, groups = [], {}

    def blame(t0, t1, what, f=None):
        cost = collections.Counter()
        for s, b in window(t0, t1, 0):
            c = category(s, b, sections, nm)
            if c[0] != PACING:
                cost[c] += period
        if not cost:
            for s, b in window(t0, t1, 1):
                c = category(s, b, sections, nm)
                if not c[0].startswith("OS: show thread blocked (present"):
                    cost[c] += period
        if cost:
            return cost.most_common(1)[0][0], cost
        i, j = bisect.bisect_right(tick_t, t0), bisect.bisect_right(tick_t, t1 + SLOT_US)
        if i < j:
            return tick_cause(max(ticks[i:j], key=lambda t: t.get("work", 0)), part_names), cost
        if f and f.get("present_us", 0) > SLOT_US / 2:
            return ("show thread: present took long", ""), cost
        return ("unknown (no samples, no tick rows: run with --sample-us)", ""), cost

    for f in frames:
        if f["flags"] & pr.LOADING:
            continue
        slots = (f["gap_us"] * 144 + 500000) // 1000000
        if slots > 1 or f["make_us"] > bar_us:
            cause, cost = blame(f["t_us"] - f["gap_us"], f["t_us"], "frame", f)
            ev = {"kind": "late frame" if slots > 1 else "over the bar", "vblank": f["vblank"], "t_us": f["t_us"],
                  "gap_ms": f["gap_us"] / 1000, "make_ms": f["make_us"] / 1000, "late_slots": max(0, slots - 1),
                  "cause": cause[0], "function": cause[1],
                  "costs": [[c[0], c[1], v / 1000] for c, v in cost.most_common(4)]}
            events.append(ev)
    prev = None
    for t in ticks:
        if prev is not None and t.get("work", 0) > bar_us:
            cause, cost = blame(prev["t_us"], t["t_us"], "tick")
            events.append({"kind": "long tick", "vblank": t["vblank"], "t_us": t["t_us"], "work_ms": t["work"] / 1000,
                           "parts": {k: t[k] / 1000 for k in part_names}, "cause": cause[0], "function": cause[1],
                           "costs": [[c[0], c[1], v / 1000] for c, v in cost.most_common(4)]})
        prev = t
    for ev in events:
        g = groups.setdefault((ev["cause"], ev["function"]), {"cause": ev["cause"], "function": ev["function"], "late_frames": 0,
                                                               "late_slots": 0, "over_bar": 0, "long_ticks": 0, "worst_ms": 0.0,
                                                               "vblanks": []})
        if ev["kind"] == "late frame":
            g["late_frames"] += 1
            g["late_slots"] += ev["late_slots"]
        elif ev["kind"] == "over the bar":
            g["over_bar"] += 1
        else:
            g["long_ticks"] += 1
        g["worst_ms"] = max(g["worst_ms"], ev.get("gap_ms") or ev.get("work_ms") or 0)
        if len(g["vblanks"]) < 8:
            g["vblanks"].append(ev["vblank"])
    order = sorted(groups.values(), key=lambda g: (-g["late_slots"], -g["over_bar"], -g["long_ticks"], -g["worst_ms"]))
    share = collections.Counter()
    for k, s in enumerate(rows):
        if s[3] == 0:
            c = category(s, busy[k] if busy else True, sections, nm)[0]
            share["waiting" if c.startswith("wait") else "the system (OS)" if c.startswith("OS") else "work"] += 1
    n = sum(share.values())
    tick_share = {}
    if ticks and "work" in part_names:
        tot = {k: sum(t[k] for t in ticks) for k in part_names}
        allt = tot["work"] + tot.get("wait_pace", 0) + tot.get("wait_poll", 0)
        if allt:
            tick_share = {k: tot[k] / allt for k in part_names}
    tops = collections.Counter()
    for ev in events:
        for c, fn, ms in ev["costs"]:
            if fn:
                tops[(fn, c)] += ms
    return {"stats": stats, "events": events, "groups": order, "share": {k: v / n for k, v in share.items()} if n else {},
            "tick_share": tick_share, "top_functions": [[fn, c, round(ms, 2)] for (fn, c), ms in tops.most_common(15)],
            "samples": len(rows), "period_us": period, "frames": len(frames), "ticks": len(ticks)}


def verdict(st):
    ok = st["make_ms"]["p99"] <= pr.BAR_US / 1000 + 1e-9 and st["late"] * 1000 <= st["frames"]
    return (f"{'pass' if ok else 'fail'}: make p99 {st['make_ms']['p99']:.2f} ms (bar 6.67), late {st['late']} of {st['frames']} "
            f"shown ({st['late_per_1000']:.2f} per 1000, bar 1), worst make {st['make_ms']['worst']:.2f} ms")


def render_md(rep):
    a, m = rep["analysis"], rep["meta"]
    out = [f"# Late frames: {m['name']} on {m['machine']} ({m['when']})", "",
           f"Segment: {m['segment']}. Output {m['size']}. Sampling every {a['period_us'] or '-'} us ({a['samples']} samples); "
           f"{a['frames']} frame rows, {a['ticks']} tick rows.", "", f"**Verdict:** {verdict(a['stats'])}." +
           ("" if m["machine"] == MAIN_LAPTOP else " (Not the user's laptop: compare builds only.)"), ""]
    if rep.get("log_verdict"):
        out += [f"Game log: `{rep['log_verdict']}`", ""]
    if a["share"]:
        out.append("**The game thread's time (samples):** " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(a["share"].items(), key=lambda kv: -kv[1])) + ".")
    if a["tick_share"]:
        out.append("**By the kit's tick parts:** " + ", ".join(f"{k} {v:.1%}" for k, v in a["tick_share"].items()) + ".")
    out += ["", "## Late frames and long ticks by cause, worst first", "",
            "| Cause | Function | Late frames | Late slots | Over the bar | Long ticks | Worst ms | At vertical blanks |",
            "|---|---|---|---|---|---|---|---|"]
    for g in a["groups"]:
        out.append(f"| {g['cause']} | {g['function'] or '-'} | {g['late_frames']} | {g['late_slots']} | {g['over_bar']} | "
                   f"{g['long_ticks']} | {g['worst_ms']:.1f} | {', '.join(map(str, g['vblanks']))} |")
    if a["top_functions"]:
        out += ["", "## Functions in the late windows (ms of samples)", "", "| Function | Where | ms |", "|---|---|---|"]
        out += [f"| {fn} | {c} | {ms:.1f} |" for fn, c, ms in a["top_functions"]]
    worst = sorted(a["events"], key=lambda e: -(e.get("gap_ms") or e.get("work_ms") or 0))[:12]
    out += ["", "## The worst events", "", "| Vertical blank | Event | ms | Cause | Function | Next costs |", "|---|---|---|---|---|---|"]
    for e in worst:
        out.append(f"| {e['vblank']} | {e['kind']} | {e.get('gap_ms') or e.get('work_ms'):.1f} | {e['cause']} | {e['function'] or '-'} | "
                   + "; ".join(f"{c} {fn} {ms:.1f}" for c, fn, ms in e["costs"][1:]) + " |")
    out += ["", "Notes: " + " ".join(rep.get("notes", [])), ""]
    return "\n".join(out)


def render_html(rep):
    a = rep["analysis"]
    fr = rep["frames"]
    n, top = max(1, len(fr)), 40.0
    colours = {}
    pal = ["#d1495b", "#edae49", "#00798c", "#30638e", "#8d5a97", "#6a994e", "#bc6c25", "#495057"]
    for i, g in enumerate(a["groups"][:8]):
        colours[(g["cause"], g["function"])] = pal[i]
    late = {e["t_us"]: e for e in a["events"] if e["kind"] != "long tick"}
    w, h = 1200, 360
    pts = " ".join(f"{i * w / n:.1f},{h - min(f['gap_us'] / 1000, top) * h / top:.1f}" for i, f in enumerate(fr))
    mk = " ".join(f"{i * w / n:.1f},{h - min(f['make_us'] / 1000, top) * h / top:.1f}" for i, f in enumerate(fr))
    dots = "".join(f'<circle cx="{i * w / n:.1f}" cy="{h - min(f["gap_us"] / 1000, top) * h / top:.1f}" r="3.5" fill="'
                   f'{colours.get((late[f["t_us"]]["cause"], late[f["t_us"]]["function"]), "#888")}"><title>vb {f["vblank"]}: '
                   f'{html.escape(late[f["t_us"]]["cause"])} {html.escape(late[f["t_us"]]["function"])}</title></circle>'
                   for i, f in enumerate(fr) if f["t_us"] in late)
    y = lambda ms: h - ms * h / top  # noqa: E731
    legend = "".join(f'<li><span style="background:{colours.get((g["cause"], g["function"]), "#888")}"></span>{html.escape(g["cause"])} '
                     f'{html.escape(g["function"] or "")}: {g["late_frames"]} late, {g["over_bar"]} over the bar, {g["long_ticks"]} long ticks</li>'
                     for g in a["groups"][:8])
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Late frames</title>
<style>:root{{--bg:#fff;--fg:#222;--grid:#ccc}}@media (prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#ddd;--grid:#444}}}}
body{{background:var(--bg);color:var(--fg);font:14px system-ui;margin:16px}}svg{{width:100%;height:auto}}
li span{{display:inline-block;width:10px;height:10px;margin-right:6px}}</style></head><body>
<h3>{html.escape(rep['meta']['name'])}: {html.escape(verdict(a['stats']))}</h3>
<svg viewBox="0 0 {w} {h}"><line x1="0" x2="{w}" y1="{y(6.94):.1f}" y2="{y(6.94):.1f}" stroke="var(--grid)"/>
<line x1="0" x2="{w}" y1="{y(6.67):.1f}" y2="{y(6.67):.1f}" stroke="var(--grid)" stroke-dasharray="4"/>
<polyline points="{mk}" fill="none" stroke="#00798c" stroke-width="1" opacity="0.6"/>
<polyline points="{pts}" fill="none" stroke="var(--fg)" stroke-width="1"/>{dots}</svg>
<p>Gap before each shown frame (line, ms, cut at {top:.0f}) and its make time (blue); grid lines 6.94 ms (one 144 slot) and 6.67 ms
(the make bar, dashed). Dots: late frames, coloured by cause.</p><ul>{legend}</ul></body></html>"""


def build_cmd(a, out):
    cmd = [str(pathlib.Path(a.a) / a.exe)] + list(a.game_args)
    if a.state:
        dd = next((cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x == "--data-directory"), None)
        if not dd:
            dd = str(out / "data")
            cmd += ["--data-directory", dd]
        (pathlib.Path(dd) / "states").mkdir(parents=True, exist_ok=True)
        shutil.copy2(a.state, pathlib.Path(dd) / "states" / f"slot{a.slot}.state")
        cmd += ["--state-load", f"{a.state_at}:{a.slot}", "--perf-from", f"load+{a.warmup}"]
    else:
        cmd += ["--replay", a.replay, "--perf-from", str(a.from_blank)]
    cmd += ["--play-summary", "on", "--record", "off", "--size", a.size, "--log", str(out / "run.log"),
            "--play-frames", str(out / "run.frames.csv")]
    if a.sample_us:
        cmd += ["--perf-sample", str(a.sample_us), "--perf-samples", str(out / "run.samples.bin")]
    return cmd


def run_signed_in(cmd, out, timeout):
    """A window over ssh cannot show: the run is started as a task in the machine's signed-in session, ended by its
    task (schtasks /end) if it overruns."""
    bat = out / "run.cmd"
    bat.write_text("@echo off\r\n" + subprocess.list2cmdline(cmd) + f"\r\necho %errorlevel% > \"{out / 'rc.txt'}\"\r\n", encoding="ascii")
    (out / "rc.txt").unlink(missing_ok=True)
    tn = f"perfseg_{os.getpid()}"
    subprocess.run(["schtasks", "/create", "/tn", tn, "/tr", str(bat), "/sc", "once", "/st", "23:59", "/it", "/f"], capture_output=True, timeout=60)
    subprocess.run(["schtasks", "/run", "/tn", tn], capture_output=True, timeout=60)
    t = time.time()
    while not (out / "rc.txt").exists() and time.time() - t < timeout:
        time.sleep(5)
    rc = (out / "rc.txt").read_text().strip() if (out / "rc.txt").exists() else "timeout"
    if rc == "timeout":
        subprocess.run(["schtasks", "/end", "/tn", tn], capture_output=True, timeout=60)
    subprocess.run(["schtasks", "/delete", "/tn", tn, "/f"], capture_output=True, timeout=60)
    return rc


def main(argv):
    ap = argparse.ArgumentParser(prog="perf.py segment", description=__doc__.split("\n")[0])
    ap.add_argument("--a", help="the build folder"), ap.add_argument("--exe", default="port.exe")
    ap.add_argument("--out", help="the run's folder (in .work)"), ap.add_argument("--report", help="re-report a kept run's folder")
    ap.add_argument("--name", default="game"), ap.add_argument("--state"), ap.add_argument("--state-at", type=int, default=120)
    ap.add_argument("--slot", type=int, default=9), ap.add_argument("--warmup", type=int, default=600, help="blanks after the load")
    ap.add_argument("--replay"), ap.add_argument("--from", dest="from_blank", type=int, default=0)
    ap.add_argument("--sample-us", type=int, default=500, help="0: the trace only (frame and tick rows)")
    ap.add_argument("--size", default="2560x1600"), ap.add_argument("--symbols", help="original address and name per line")
    ap.add_argument("--signed-in", action="store_true", help="start the run in the signed-in session (a window over ssh)")
    ap.add_argument("--timeout", type=int, default=600), ap.add_argument("--keep", action="store_true")
    ap.add_argument("game_args", nargs="*")
    a = ap.parse_args(argv)
    out = pathlib.Path(a.report or a.out or ap.error("--out or --report"))
    out.mkdir(parents=True, exist_ok=True)
    notes = []
    if not a.report:
        if not a.a or not (a.state or a.replay):
            ap.error("--a and --state (or --replay) are needed")
        if platform.node().upper() == MAIN_LAPTOP and os.environ.get("MAIN_LAPTOP_OK") != "final-timing":
            sys.exit("refused: the user's laptop runs only the final confirm, as MAIN_LAPTOP_OK=final-timing; compare on build box A or build box B")
        cmd = build_cmd(a, out) + ["--seconds", str(a.timeout)]  # the kit's own watchdog, under the tool's
        if a.signed_in:
            rc = run_signed_in(cmd, out, a.timeout + 120)
        else:
            from perf import ABOVE_NORMAL, run_exe  # perf.py dispatches here; imported again as a module, without running main
            rc = run_exe(cmd, str(pathlib.Path(a.a)), out / "run.out.txt", a.timeout + 120, ABOVE_NORMAL)
        (out / "segment.json").write_text(json.dumps({"cmd": cmd, "exit": rc, "name": a.name, "state": a.state, "warmup": a.warmup,
                                                      "replay": a.replay, "from": a.from_blank, "size": a.size}, indent=1))
        notes.append(f"Run exit {rc}.")
    seg = json.loads((out / "segment.json").read_text()) if (out / "segment.json").exists() else {}
    frames = pr.read_frames(out / "run.frames.csv") if (out / "run.frames.csv").exists() else []
    ticks = read_ticks(out / "run.ticks.csv")
    samples = pr.read_samples(out / "run.samples.bin") if (out / "run.samples.bin").exists() else None
    mp = None
    if a.a and (pathlib.Path(a.a) / a.exe).with_suffix(".map").exists():
        from perf_map import Map
        mp = Map((pathlib.Path(a.a) / a.exe).with_suffix(".map"))
    else:
        notes.append("No map beside the exe: kit and system functions are named by offset (build with /MAP).")
    log = pr.log_lines(out / "run.log")
    if not frames:
        sys.exit(f"no frame rows in {out} (the run failed or the build has no --play-frames): see run.log")
    an = analyse(frames, ticks, samples, Namer(mp, read_symbols(a.symbols)))
    held = log.get("held_ms")
    if held is not None and frames:
        notes.append(f"The sampler held the threads {held:.0f} ms over {(frames[-1]['t_us'] - frames[0]['t_us']) / 1e6:.0f} s (its cost).")
    seg_text = (f"state {pathlib.Path(seg.get('state') or '').name} loaded, from {seg.get('warmup')} blanks after the load"
                if seg.get("state") else f"replay {pathlib.Path(seg.get('replay') or '').name} from blank {seg.get('from')}")
    rep = {"meta": {"name": seg.get("name", a.name), "machine": platform.node().upper(), "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "segment": seg_text, "size": seg.get("size", a.size)}, "analysis": an, "log_verdict": log["verdict"],
           "notes": notes, "frames": frames}
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"late-{stamp}.md").write_text(render_md(rep), encoding="utf-8")
    (out / f"late-{stamp}.html").write_text(render_html(rep), encoding="utf-8")
    (out / f"late-{stamp}.json").write_text(json.dumps({k: v for k, v in rep.items() if k != "frames"}, indent=1), encoding="utf-8")
    print(f"{rep['meta']['name']}: {verdict(an['stats'])}")
    print("share: " + ", ".join(f"{k} {v:.0%}" for k, v in an["share"].items()) + (
        "; ticks: " + ", ".join(f"{k} {v:.1%}" for k, v in an["tick_share"].items()) if an["tick_share"] else ""))
    for g in an["groups"][:8]:
        print(f"  {g['late_frames']:3d} late ({g['late_slots']} slots), {g['over_bar']} over, {g['long_ticks']} long ticks, "
              f"worst {g['worst_ms']:.1f} ms: {g['cause']} {g['function']}")
    print(f"report: {out / f'late-{stamp}.md'}")
    if not a.keep and not a.report:  # the samples are the run's bulk; the rows and the report stay
        (out / "run.samples.bin").unlink(missing_ok=True)
    return 0

r"""The Core's performance toolkit: benchmark a port build, find where its frame time goes, and rank what to swap.

One command for any game on any console kit, from one recorded replay (docs/PERF_TOOLKIT.md):

    py -3.12 port/tools/perf.py all --name Spawn --a BUILD_DIR [--b OTHER_BUILD_DIR] --replay REC.ptrec --stop-at N
        [--fast-until V] --out OUT_DIR [--log-dir GAME_LOG_DIR] -- <the game's own arguments>

BUILD_DIR holds port.exe and port.map (--exe NAME for a kit that names them otherwise). It runs, without a window and on the real clock:
  bench    the builds in interleaved order (A B B A A B; A A A A for one build): make time and late frames per run,
           the run-to-run spread, and whether a difference is outside it;
  profile  build A with the sampler on (base/perf.c), a few runs pooled, then one fixed-clock pass counting calls;
  report   perf-<date>.md and perf-<date>.json in OUT_DIR and in the game's log folder.
A game's standard scenarios are templates cut from the user's own recordings (perf_template.py):
    perf.py template --name Spawn --scenario front-end --a BUILD_DIR --replay REC.ptrec --end-at-log "main menu"
        --template-out GAME/perf --out OUT_DIR -- <the game's own arguments>
    perf.py all --template GAME/perf/front-end.json --a BUILD_DIR --out OUT_DIR -- <the game's own arguments>
A template is replayed once on the fixed clock first; a build that no longer follows it is refused (`check` does only that).
Every run is bounded and ended by its own process id if it overruns. Samples are deleted once the report is written
(--keep keeps them). `report --out OUT_DIR --keep` runs rebuild the report from kept files.

Nearest existing: the per-game timing drivers (Spawn .work/spawn-finish/lcc/drive7.py, Power Stone 2
.work/f8/hud8/lc/pairs.py, Toy Commander .work/coop/ftime/ftime.py) - each is tied to one game's launcher paths, none
attributes time to functions or writes a report; this replaces them.
"""
import argparse
import csv
import datetime
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import perf_report as pr  # noqa: E402
import perf_template as pt  # noqa: E402
from perf_machine import clock_mhz, cpu_snapshot, machine_state, others_since, quiet  # noqa: E402
from perf_map import Map  # noqa: E402
from perf_text import render  # noqa: E402

BELOW_NORMAL, ABOVE_NORMAL, NO_WINDOW = 0x4000, 0x8000, 0x08000000


def run_exe(cmd, cwd, out, timeout, priority=BELOW_NORMAL):
    """The run's exit code, or 'timeout' after its whole tree was ended by its process id. Checks run below normal;
    timed runs at the launcher's play priority (above normal, base/host_main.cpp), which a scripted run does not set."""
    with open(out, "w") as o:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=o, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=priority | NO_WINDOW)
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=60)
            p.wait(timeout=60)
            return "timeout"


def play_args(a, tag, out, size=None, cost=True):
    """The arguments of a timed run: the launcher's play path with no window (or --window), on the real clock from
    --fast-until. The sound device is opened as in play, so its put is timed; mixing ahead of the clock stays off, since
    the replay already mixes what the recorded run mixed ahead, and more would change what the game sees."""
    shown = ["--window", a.window] if a.window else ["--no-window"]
    audio = [] if "--audio" in a.game_args else ["--audio", "on", "--audio-ahead", "off"]
    args = list(a.game_args) + audio + shown + ["--size", size or a.size, "--fps", "144", "--record", "off", "--replay", str(a.replay),
                                "--stop-at", str(a.stop_at), "--seconds", str(a.timeout), "--game-log", "on",
                                "--play-summary", "on", "--log", str(out / f"{tag}.log"),
                                "--play-frames", str(out / f"{tag}.frames.csv"), "--perf-from", str(a.fast_until)]
    args += ["--tick-cost", "on"] if cost else []  # the kit's section timer and its --prof cannot both be on
    if "--present" not in args:  # the launcher's play path gives a play run this; a scripted run gets no defaults,
        args += ["--present", "auto"]  # and without it a game with in-between frames is timed at its tick rate
    if a.fast_until and not a.from_boot:  # a fixed-clock run is scripted: no play defaults (base/host_main.cpp)
        args += ["--fixed-clock", "1", "--fast-until", str(a.fast_until)]
    return args


def one_run(a, side, tag, out, extra=(), size=None, cost=True):
    build = pathlib.Path(a.a if side == "A" else a.b)
    cpu = quiet(a.max_cpu, a.quiet_wait)
    cmd = [str(build / a.exe)] + play_args(a, tag, out, size, cost) + list(extra)
    if a.plant and side == "A":
        cmd += ["--perf-plant", str(a.plant)]
    if a.plant_sys and side == "A":
        cmd += ["--perf-plant-sys", str(a.plant_sys)]
    t0, snap = time.time(), cpu_snapshot()
    code = run_exe(cmd, str(build), out / f"{tag}.out.txt", a.timeout + 120, ABOVE_NORMAL)
    others = others_since(snap, time.time() - t0)  # the machine's other work while this run was timed
    row = {"side": side, "tag": tag, "exit": code, "cpu": cpu, "seconds": round(time.time() - t0, 1), "mhz": (clock_mhz() or [None])[0], "others": others}
    frames, log = out / f"{tag}.frames.csv", pr.log_lines(out / f"{tag}.log")
    row["log_summary"], row["log_verdict"], row["held_ms"] = log["summary"], log["verdict"], log["held_ms"]
    row["timers"], row["timer_frames"] = log["timers"], log["timer_frames"]
    text = (out / f"{tag}.log").read_text(errors="replace") if (out / f"{tag}.log").exists() else ""
    row["hidden"] = "the window is hidden" in text  # Present then only tests the window: its timer is not a real present
    row["out_of_step"] = log["out_of_step"]  # the replay no longer fits this build: its numbers are of another game
    rows = pr.read_frames(frames) if frames.exists() else []
    row["ticks"] = len({r["tick"] for r in rows})
    stats = pr.frame_stats(rows) if rows else pr.stats_from_log(log)
    row["once"], marked, row["once_unplaced"] = pr.once_summary(log["once"], rows, a.fast_until)
    if rows and marked:
        row["stats_without"] = pr.frame_stats(rows, skip=marked)
        row["once_frames"] = sum(1 for r in rows if r["vblank"] in marked)
    if stats:  # a build from before --play-frames has only its log's lines
        row["stats"] = stats
    print(f"{tag}: exit {code}, load {cpu:.0f}%, others during {others['share']:.0%}, " + (f"make p99 {row['stats']['make_ms']['p99']:.2f} ms, "
          f"late {row['stats']['late_per_1000']:.2f} per 1000" if "stats" in row else "no frame rows"), flush=True)
    return row


def do_bench(a, out, state):
    order = a.runs or ("ABBAAB" if a.b else "AAAA")
    state["bench_runs"] = [one_run(a, side, f"bench{i}{side}", out) for i, side in enumerate(order)]
    if a.small_size and a.small_size != a.size:  # does make time move with the output size? Then the card is in it.
        state["small_runs"] = [one_run(a, "A", f"small{i}", out, size=a.small_size) for i in range(2)]


def do_profile(a, out, state):
    state["profile_runs"] = [one_run(a, "A", f"prof{i}", out, ["--perf-sample", str(a.sample_us), "--perf-samples",
                                                               str(out / f"prof{i}.samples.bin")])
                             for i in range(a.profile_runs)]
    # Graphics card time per blank: the kit's timer queries flush each frame, so this pass is read for that column only.
    state["gpu_run"] = one_run(a, "A", "gpu", out, ["--prof", "on", "--prof-from", str(a.fast_until),
                                                    "--prof-csv", str(out / "gpu.csv")], cost=False)
    # Exact call counts: a fixed-clock pass, so counting never changes a timed run. Kits without the counter skip it.
    build = pathlib.Path(a.a)
    cmd = [str(build / a.exe)] + list(a.game_args) + [
        "--headless", "--fixed-clock", "1", "--record", "off", "--replay", str(a.replay), "--stop-at", str(a.stop_at),
        "--prof", "on", "--prof-from", str(a.fast_until), "--prof-calls", str(out / "calls.txt"), "--log", str(out / "calls.log")]
    state["calls_exit"] = run_exe(cmd, str(build), out / "calls.out.txt", a.timeout + 120)


def read_calls(path):
    calls, blanks = {}, 0
    if not path.exists():
        return None, 0
    for ln in open(path):
        if ln.startswith("#"):
            blanks = int(ln.split("over")[1].split()[0])
        elif ln.strip():
            addr, n = ln.split()
            calls[int(addr, 16)] = calls.get(int(addr, 16), 0) + int(n)
    return calls, blanks


def read_gpu(path):
    """{vertical blank: graphics card ms} from the kit's --prof-csv ({} where the kit has no timer queries)."""
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        rows = {int(r["vblank"]): int(r["gpu"]) / 1000 for r in csv.DictReader(f) if r.get("gpu")}
    return rows if any(rows.values()) else {}


def resolution(a, state, side):
    """Two runs at a small output size against the benchmark's: whether make time goes with the size."""
    small = [r["stats"]["make_ms"] for r in state.get("small_runs", []) if r.get("stats") and r["exit"] == 0 and not r.get("out_of_step")]
    if not small:
        return None
    s = {k: sum(m[k] for m in small) / len(small) for k in ("median", "p99")}
    full = {k: side[k]["mean"] for k in ("median", "p99")}
    d = {k: full[k] - s[k] for k in s}
    if d["median"] > max(side["median"]["spread"], 0.3) or d["p99"] > max(side["p99"]["spread"], 0.3):
        verdict = (f"**Make time goes with the output size** (median {d['median']:+.2f} ms, p99 {d['p99']:+.2f} ms at full size): "
                   "that part is the graphics card or per-pixel work, and code swaps on the processor will not take it away.")
    else:
        verdict = (f"**Make time does not move with the output size** (median {d['median']:+.2f} ms, p99 {d['p99']:+.2f} ms, inside the "
                   "noise): the frames over the bar are bound by the processor or by waiting, not by the graphics card.")
    return {"small_size": a.small_size, "full_size": a.size, "small": s, "full": full, "difference_ms": d, "verdict": verdict}


def do_report(a, out, state):
    notes, rep = [], {"bar_ms": pr.BAR_US / 1000}
    rep["meta"] = {"name": a.name, "machine": platform.node(), "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "size": a.size, "replay": pathlib.Path(a.replay).name, "stop_at": a.stop_at, "from": a.fast_until,
                   "builds": {"A": str(a.a), "B": str(a.b or "")}, "verdict_machine": a.verdict_machine, "plant_us": a.plant, "plant_sys_us": a.plant_sys,
                   "sample_us": a.sample_us, "from_boot": a.from_boot, "scenario": a.scenario, "scenario_shows": a.shows, "machine_state": machine_state()}
    mhz = [r["mhz"] for r in state.get("bench_runs", []) + state.get("profile_runs", []) if r.get("mhz")]
    if mhz:
        rep["meta"]["machine_state"]["run_mhz"] = [min(mhz), max(mhz)]
    notes.append("**Rows marked [caller uncertain] are time a thread spent busy inside the system. On a 32-bit build the sampler "
                 "can give that time to the wrong function, so they are never in the swap list; the named timers below measure "
                 "the Core's own system calls instead. Sampled sizes: on build box A a planted 3.0 ms function read 3.04 ms "
                 "against 3.01 ms by its own timer in one run, and 3.83 ms in an earlier, busier run (not explained): check the "
                 "machine load of each run before trusting a size.**")
    every = state.get("bench_runs", []) + state.get("profile_runs", []) + state.get("small_runs", [])
    timers, ticks, split = {}, 0, [0, 0]  # split: shown frames made within the bar, over it (perf.c, all frames)
    for r in state.get("profile_runs", []):
        if r.get("out_of_step") or not r.get("ticks"):
            continue
        ticks += r["ticks"]
        split = [a + b for a, b in zip(split, r.get("timer_frames") or [0, 0])]
        for row in r.get("timers", []):
            t = timers.setdefault((row[0], row[1]), [0.0, 0, 0.0, 0.0, 0.0])
            t[0], t[1], t[2] = t[0] + row[2], t[1] + row[3], max(t[2], row[4])
            t[3], t[4] = (t[3] + row[5], t[4] + row[6]) if len(row) > 6 else (t[3], t[4])
    rep["timers"] = [{"thread": k[0], "name": k[1], "ms_per_tick": v[0] / ticks, "calls_per_tick": v[1] / ticks, "longest_ms": v[2],
                      "within_ms_per_frame": v[3] / split[0] if split[0] else None, "over_ms_per_frame": v[4] / split[1] if split[1] else None}
                     for k, v in sorted(timers.items(), key=lambda kv: -kv[1][0])]
    rep["timer_frames"] = split
    if rep["timers"]:
        notes.append("Measured by named timers (not sampled), per tick: " + "; ".join(
            f"{t['thread']} thread {t['name']} {t['ms_per_tick']:.2f} ms in {t['calls_per_tick']:.1f} calls (longest {t['longest_ms']:.2f} ms)"
            + (f", per frame {t['within_ms_per_frame']:.2f} within the bar / {t['over_ms_per_frame']:.2f} over it"
               if t["within_ms_per_frame"] is not None and t["over_ms_per_frame"] is not None else "")
            for t in rep["timers"]) + "." + (f" Frames within the bar {split[0]}, over it {split[1]} (profile runs, loading frames too)."
                                              if split[1] else ""))
    named = {t["name"] for t in rep["timers"]}
    if state.get("profile_runs") and "present" not in named:
        notes.append("Present and the wait to show are not timed: with no window there is no swap chain, so nothing is presented "
                     "(perf.py --window WxH times them, in a run started from the machine's signed-in session).")
    if any(r.get("hidden") for r in state.get("profile_runs", [])):
        notes.append("**The window was hidden in a profile run: its presents were test presents, so the present timer is not a real one.**")
    if not a.from_boot and a.fast_until:
        notes.append("Runs fast-forward on a fixed clock to the start, which makes them scripted runs: the Game menu's defaults "
                     "(clearer art such as a clear HUD) are off, unlike the launcher's play run. --from-boot runs them as play runs.")
    kit_loading = [r["stats"].get("kit_loading", 0) for r in every if r.get("stats")]
    if a.count_loading and any(kit_loading):
        notes.append(f"**Frames the kit marks as a loading screen (disc reads over 512 KB/s) are timed here like any other "
                     f"(--count-loading): {min(kit_loading)} to {max(kit_loading)} per run.**")
    busy = [f"{r['tag']} {r['others']['share']:.0%}" + (f" ({', '.join(n for n, _ in r['others']['top'])})" if r["others"]["top"] else "")
            for r in every if r.get("others")]
    if busy:
        notes.append("Other processes' share of all the processors during each run (a run that shared the machine is slower "
                     "for it; timed runs are above normal, as the launcher's): " + "; ".join(busy) + ".")
    lost = [f"{r['tag']} ({r['out_of_step'][:90]})" for r in every if r.get("out_of_step")]
    if lost:
        notes.append("**The replay went out of step with this build, so these runs are left out: " + "; ".join(lost)
                     + ". Record the scenario again on this build (perf.py template).**")
    for key in ("bench_runs", "profile_runs"):
        state[key] = [r for r in state.get(key, []) if not r.get("out_of_step")]
    bench_runs = [r for r in state.get("bench_runs", []) if r.get("stats") and r["exit"] == 0]
    failed = [r["tag"] for r in state.get("bench_runs", []) + state.get("profile_runs", []) if r["exit"] != 0 or not r.get("stats")]
    if failed:
        notes.append("Runs that did not end cleanly or wrote no frame rows, left out: " + ", ".join(failed) + ".")
    if bench_runs:
        rep["bench"] = pr.bench(bench_runs)
        if "A" in rep["bench"]["sides"]:
            rep["resolution"] = resolution(a, state, rep["bench"]["sides"]["A"])
    gpu = read_gpu(out / "gpu.csv")
    if gpu:
        v = list(gpu.values())
        rep["gpu"] = {"blanks": len(v), "median_ms": pr.pct(v, 0.5), "p99_ms": pr.pct(v, 0.99), "worst_ms": max(v)}
    elif state.get("gpu_run"):
        notes.append("No graphics card time: this kit has no timer queries yet (only the Dreamcast kit does); the output size check stands in.")
    profiles = []
    for r in state.get("profile_runs", []):
        binf = out / f"{r['tag']}.samples.bin"
        if r.get("stats") and binf.exists():
            profiles.append({"samples": pr.read_samples(binf), "frames": pr.read_frames(out / f"{r['tag']}.frames.csv"),
                             "log": pr.log_lines(out / f"{r['tag']}.log")})
    if profiles:
        calls, blanks = read_calls(out / "calls.txt")
        if calls is None:
            notes.append("No call counts: the counting pass wrote none (this kit has no call counter, or the pass failed).")
        rep["analysis"] = pr.analyse(profiles, Map((pathlib.Path(a.a) / a.exe).with_suffix(".map")), calls=calls, call_blanks=blanks)
        for w in rep["analysis"]["worst_frames"]:
            if w["vblank"] in gpu:
                w["gpu_ms"] = gpu[w["vblank"]]
        if not any(p["samples"]["sections"] for p in profiles):
            notes.append("This kit names no sections: the layer split comes from where each function's source lives only.")
        if bench_runs and "A" in rep["bench"]["sides"]:
            side, prof = rep["bench"]["sides"]["A"], [r["stats"]["make_ms"] for r in state["profile_runs"] if r.get("stats")]
            held = sum(r.get("held_ms") or 0 for r in state["profile_runs"])
            secs = sum(r["seconds"] for r in state["profile_runs"] if r.get("stats")) or 1
            rep["overhead"] = {"median_ms": sum(p["median"] for p in prof) / len(prof) - side["median"]["mean"],
                               "p99_ms": sum(p["p99"] for p in prof) / len(prof) - side["p99"]["mean"],
                               "spread_ms": side["p99"]["spread"], "held_ms": round(held, 1), "held_share": held / 1000 / secs}
    notes += [f"Template check, build {side}: {text}." for side, text in state.get("template", {}).items()]
    rep["notes"] = notes
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    text = render(rep)
    for folder in [out] + ([pathlib.Path(a.log_dir)] if a.log_dir else []):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"perf-{stamp}.md").write_text(text, encoding="utf-8")
        (folder / f"perf-{stamp}.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    if not a.keep:
        for f in list(out.glob("*.samples.bin")) + list(out.glob("*.frames.csv")) + list(out.glob("*.out.txt")) + list(out.glob("gpu.csv")):
            f.unlink()
    print(f"report: {out / f'perf-{stamp}.md'}")
    if rep.get("bench"):
        for side, s in rep["bench"]["sides"].items():
            print(f"  {side}: make median {s['median']['mean']:.2f} p99 {s['p99']['mean']:.2f} (spread {s['p99']['spread']:.2f}) "
                  f"late {s['late_per_1000']['mean']:.2f} per 1000")
        for key, d in rep["bench"].get("difference", {}).items():
            print(f"  {key}: B-A {d['b_minus_a_ms']:+.2f} ms, noise {d['noise_ms']:.2f}: {'real' if d['real'] else 'inside the noise'}")
    for s in rep.get("analysis", {}).get("swaps", [])[:6]:
        print(f"  swap {s['rank']}: {s['name'][:48]} [{s['what']}] {s['cost_ms_per_frame']:.2f} ms, gain "
              f"{s['gain_ms'][0]:.2f}-{s['gain_ms'][1]:.2f}, {s['confidence']}")
    return rep


def main():
    if sys.argv[1:2] == ["segment"]:  # why each frame was late, from a state-load or replay segment (perf_late.py)
        import perf_late
        sys.exit(perf_late.main(sys.argv[2:]))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=("all", "bench", "profile", "report", "template", "check"))
    ap.add_argument("--name", default="game")
    ap.add_argument("--a", help="build folder with port.exe and port.map")
    ap.add_argument("--b", help="a second build to compare with A")
    ap.add_argument("--exe", default="port.exe", help="the exe's name in a build folder; its map has the same name (xbox_run.exe)")
    ap.add_argument("--replay")
    ap.add_argument("--stop-at", type=int, default=0)
    ap.add_argument("--fast-until", type=int, default=0, help="run at full speed to this vertical blank, time from it")
    ap.add_argument("--out", required=True)
    ap.add_argument("--log-dir", help="the game's log folder: the report is copied there")
    ap.add_argument("--size", default="2560x1600")
    ap.add_argument("--from-boot", action="store_true", help="the whole replay on the real clock, a play run with the "
                    "launcher's defaults (Game menu defaults such as clearer art); timing still starts at --fast-until")
    ap.add_argument("--count-loading", action="store_true", help="time the frames the kit marks as a loading screen too")
    ap.add_argument("--window", default="", help="time in a real window of this size (WxH) instead of --no-window")
    ap.add_argument("--small-size", default="640x400", help="size of the two runs that show whether the card is in the frame ('' for none)")
    ap.add_argument("--scenario", default="", help="the name of what the replay shows, e.g. front-end")
    ap.add_argument("--shows", default="", help="template: one line on what the scenario shows")
    ap.add_argument("--template", help="a scenario file made by the template step: gives the replay, its start and end")
    ap.add_argument("--template-out", help="template: the game's folder for scenario files (private; listed in its LOCATIONS.md)")
    ap.add_argument("--end-at-log", help="template: the scenario ends --after-blanks after the first game log line matching this")
    ap.add_argument("--after-blanks", type=int, default=300)
    ap.add_argument("--setting", action="append", default=[], help="template: a game argument that changes timing; may repeat")
    ap.add_argument("--game-commit", default="")
    ap.add_argument("--kit-commit", default="")
    ap.add_argument("--strict", action="store_true", help="refuse a template whose checkpoint pictures differ too")
    ap.add_argument("--runs", help="bench order, e.g. ABBAAB")
    ap.add_argument("--profile-runs", type=int, default=3)
    ap.add_argument("--sample-us", type=int, default=500)
    ap.add_argument("--plant", type=int, default=0, help="test: a planted slow function of this many us a tick in A's runs")
    ap.add_argument("--plant-sys", type=int, default=0, help="test: the same, spent inside system calls (perf_planted_system_call)")
    ap.add_argument("--timeout", type=int, default=900, help="seconds one run may take")
    ap.add_argument("--max-cpu", type=float, default=35.0)
    ap.add_argument("--quiet-wait", type=int, default=300)
    ap.add_argument("--verdict-machine", default="")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("game_args", nargs="*", help="after --: the game's own arguments (disc, save folder, ...)")
    a = ap.parse_args()
    pr.LOADING = 0 if a.count_loading else 1
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    state_file = out / "runs.json"
    state = json.loads(state_file.read_text()) if state_file.exists() and a.step != "all" else {}
    if a.step == "template":
        if not (a.a and a.replay and a.scenario and a.template_out):
            ap.error("template needs --a, --replay, --scenario and --template-out")
        sys.exit(pt.make(a, run_exe, out))
    if a.template:
        try:
            tpl, rec = pt.load(a.template)
        except (ValueError, OSError) as e:
            sys.exit(f"refused: {e}")
        a.replay, a.stop_at, a.fast_until, a.scenario, a.shows = rec, tpl["end"], tpl["start"], tpl["scenario"], tpl["shows"]
        a.name = a.name if a.name != "game" else tpl["game"]
        a.game_args = list(a.game_args) + tpl["settings"]
        if a.step != "report":
            for side, build in (("A", a.a), ("B", a.b)):
                if build:
                    ok, text = pt.verify(tpl, rec, run_exe, pathlib.Path(build) / a.exe, a.game_args, out, a.timeout, a.strict)
                    print(f"template {tpl['scenario']} on {side}: {text}", flush=True)
                    state.setdefault("template", {})[side] = text
                    if not ok:
                        sys.exit(f"refused: {side} does not follow the template; a benchmark of it would time a different stretch of play. "
                                 "Cut the scenario again from a recording made on this build (perf.py template).")
    if a.step == "check":
        return
    if a.step != "report" and (not a.a or not a.replay or not a.stop_at):
        ap.error("--a, --replay and --stop-at are needed (or --template)")
    if a.step in ("all", "bench"):
        do_bench(a, out, state)
    if a.step in ("all", "profile"):
        do_profile(a, out, state)
    state_file.write_text(json.dumps(state, indent=1))
    if a.step in ("all", "report"):
        do_report(a, out, state)
    if shutil.disk_usage(out).free < 5 << 30:
        print("warning: under 5 GB free on this drive")


if __name__ == "__main__":
    main()

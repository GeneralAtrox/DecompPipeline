"""The regression run: every <game>/tests/*.script, one at a time, bounded, headless, with the fixed clock and tick
presenting, so each run repeats exactly. A script fails on
- an exit other than the watchdog's (a crash, a halt (tc_halt), a run-time check, the entry function returning);
- a log class missing from its baseline: a TODO line, a stub reached (STUB), or a new TA polygon class (ta: list);
- a frame it asks for that was not reached, that differs from its golden (frames.py), or that has no golden (nothing
  compared it: use `filled` for a frame that is not approved);
- a frame listed under `filled` with an empty edge band (frames.py filled(): a full-screen picture that drew only part of
  itself, as a movie clipped to its top strip), whether or not it has a golden;
- a pass that overflowed the renderer's autosort pool (pvr/pvr_oit.c): which translucent fragments it dropped
  depends on the GPU's order, so its frames do not repeat.
Log classes in the baseline that a run no longer reaches are reported, not failed.

    python port/tools/regress.py [--game DIR] [--only NAME,..] [--accept-log] [--accept-frames]
    (tools/build.py --regress runs it after building)

A script is one "key value" per line; '#' starts a comment:
    seconds 40                   the watchdog: the run ends after this many seconds
    frames 900,1500              frames (0 is the first presented) dumped and compared
    filled 10000,10500           frames (dumped too) that must have no empty edge band, with no golden needed
    input START@900,A@1500       scripted presses (several input lines are joined)
    args --vmu none              more port.exe arguments
Files, under <game>: tests/goldens/<script>/frame_NNNNNN.png (approved by the user), tests/baseline/<script>.txt
(the log classes), and per run build/regress/<script>/ (port.log, frames, a sheet for each mismatch).
--accept-log rewrites the baselines from this run. --accept-frames copies this run's frames over the goldens: only
for frames the user has approved.
Each folder's provenance.json (provenance.py) says where its files came from. A golden with no record, or not the file
its record names, fails. Accepting labels a file a native baseline, and over original evidence it fails. The last line
counts the frames compared against each: a native baseline catches a change and supports no fidelity claim.
"""
import argparse
import pathlib
import re
import shutil
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import frames  # noqa: E402
import game_config  # noqa: E402
import provenance  # noqa: E402
from build import run  # noqa: E402

WATCHDOG_EXIT = 2
COMPARED = {"original": 0, "native": 0}
CLASS = re.compile(r"^(TODO .*|STUB #\d+ .*|ta: list \d+: (?:polygon|sprite) vertex type \d+)")
UNSTABLE = re.compile(r"^pvr: a pass drew \d+ translucent fragments, past the autosort pool .*$", re.M)


def parse_script(path):
    s = {"seconds": 60, "frames": [], "filled": [], "input": [], "args": []}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        value = value.strip()
        if key == "seconds":
            s["seconds"] = int(value)
        elif key == "frames":
            s["frames"] += [int(v) for v in value.split(",") if v.strip()]
        elif key == "filled":
            s["filled"] += [int(v) for v in value.split(",") if v.strip()]
        elif key == "input":
            s["input"] += [v for v in value.split(",") if v.strip()]
        elif key == "args":
            s["args"] += value.split()
        else:
            raise SystemExit(f"{path}:{n}: unknown key {key!r}")
    return s


def log_classes(log_text):
    """The run's log classes, each once: frame numbers and first-call orders dropped."""
    out = set()
    for line in log_text.splitlines():
        m = CLASS.match(line)
        if m:
            c = re.sub(r" \(frame \d+\)$", "", m.group(1))
            out.add(re.sub(r"^STUB #\d+ ", "STUB ", c))
    return out


def unstable(log_text):
    """The log's lines saying a frame was drawn in a way that does not repeat (an autosort pool overflow)."""
    return UNSTABLE.findall(log_text)


def exit_reason(log_text):
    m = re.findall(r"^EXIT: (.*)$", log_text, re.M)
    return m[-1] if m else "no EXIT line"


def run_script(cfg, script, a):
    name = script.stem
    s = parse_script(script)
    game = cfg["dir"]
    out = game / "build" / (f"{a.build_dir}_regress" if a.build_dir else "regress") / name
    shutil.rmtree(out, ignore_errors=True)
    (out / "frames").mkdir(parents=True)
    cmd = [str(game / "build" / (a.build_dir or "x86") / "port.exe"), "--disc", str(cfg["disc"]), "--seconds", str(s["seconds"]),
           "--log", str(out / "port.log"), "--headless", "--fixed-clock", "1", "--present", "tick",
           "--dump-dir", str(out / "frames")]
    if cfg["fsca_table"]:
        cmd += ["--check-fsca", str(cfg["fsca_table"])]
    dumped = sorted(set(s["frames"]) | set(s["filled"]))
    if dumped:
        cmd += ["--dump-frames", ",".join(str(f) for f in dumped)]
    if s["input"]:
        cmd += ["--input", ",".join(s["input"])]
    cmd += s["args"]
    rc, _ = run(cmd, s["seconds"] + 60, cwd=out)
    log = (out / "port.log").read_text(encoding="utf-8", errors="replace") if (out / "port.log").exists() else ""
    fails, notes = [], []
    if rc != WATCHDOG_EXIT:
        fails.append(f"exit {rc}: {exit_reason(log)}")
    classes = log_classes(log)
    fails += [f"frames do not repeat: {line}" for line in unstable(log)]
    base_file = game / "tests" / "baseline" / f"{name}.txt"
    logs = provenance.Ledger(base_file.parent)
    if a.accept_log:
        try:
            logs.native(base_file.name, "regress.py --accept-log: this port's own log")
            base_file.parent.mkdir(parents=True, exist_ok=True)
            base_file.write_text("".join(c + "\n" for c in sorted(classes)), encoding="utf-8")
            logs.save()
        except provenance.Refused as e:
            fails.append(str(e))
    if base_file.exists() and logs.problem(base_file.name):
        fails.append(f"log baseline: {logs.problem(base_file.name)}")
    base = set(base_file.read_text(encoding="utf-8").splitlines()) if base_file.exists() else set()
    fails += [f"new log class: {c}" for c in sorted(classes - base)]
    notes += [f"no longer reached: {c}" for c in sorted(base - classes)]
    gdir = game / "tests" / "goldens" / name
    golds = provenance.Ledger(gdir.parent)
    for f in s["frames"]:
        png = out / "frames" / f"frame_{f:06d}.png"
        gold = gdir / png.name
        if not png.exists():
            fails.append(f"frame {f} not reached")
            continue
        key = f"{name}/{png.name}"
        if a.accept_frames:
            try:
                golds.native(key, "regress.py --accept-frames: this port's own frame, approved by the user")
                gdir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(png, gold)
                golds.save()
            except provenance.Refused as e:
                fails.append(f"frame {f}: {e}")
        if not gold.exists():
            fails.append(f"frame {f}: no golden")
            continue
        if golds.problem(key):
            fails.append(f"frame {f}: {golds.problem(key)}")
        COMPARED["original" if golds.fidelity(key) else "native"] += 1
        r = frames.compare(gold, png)
        if not r["identical"]:
            (fails if not r["pass"] else notes).append(f"frame {f}: {frames.describe(r)}")
        if not r["pass"]:
            frames.sheet(gold, png, out / f"mismatch_{f:06d}.png")
    for f in s["filled"]:
        png = out / "frames" / f"frame_{f:06d}.png"
        if not png.exists():
            fails.append(f"frame {f} not reached")
            continue
        empty = frames.filled(png)
        if empty:
            fails.append(f"frame {f}: empty edge band(s) {', '.join(empty)}")
    print(f"{'FAIL' if fails else 'pass'} {name}: {len(s['frames']) + len(s['filled'])} frame(s), {len(classes)} log class(es)")
    for line in fails + notes:
        print("    " + line)
    return not fails


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default=None, help="the folder with game.json (default: the current folder)")
    ap.add_argument("--only", default="", help="comma-separated script names")
    ap.add_argument("--accept-log", action="store_true")
    ap.add_argument("--accept-frames", action="store_true")
    ap.add_argument("--build-dir", default=None, help="test build/NAME/port.exe (build.py --build-dir)")
    a = ap.parse_args(argv)
    cfg = game_config.load(game_config.find_game(a.game))
    game_config.oplog().begin("port regress.py", [cfg["dir"]])
    if not cfg["disc"]:
        raise SystemExit("regress needs game.json disc")
    scripts = sorted((cfg["dir"] / "tests").glob("*.script"))
    if a.only:
        want = set(a.only.split(","))
        scripts = [p for p in scripts if p.stem in want]
    if not scripts:
        raise SystemExit(f"no scripts in {cfg['dir'] / 'tests'}")
    ok = sum(run_script(cfg, p, a) for p in scripts)
    print(f"regress: {ok} of {len(scripts)} script(s) pass; frames compared against original evidence {COMPARED['original']}, "
          f"against native baselines {COMPARED['native']} (no fidelity claim)")
    return 0 if ok == len(scripts) else 1


if __name__ == "__main__":
    sys.exit(game_config.oplog().main(main))

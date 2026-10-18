"""The regression run: every <game>/tests/*.script, one at a time, bounded, headless, with the fixed clock and tick
presenting, so each run repeats exactly. A script fails on
- an exit other than the watchdog's (a crash, a run-time check, the entry function returning);
- a log class missing from its baseline: a TODO line, a stub reached (STUB), or a new TA polygon class (ta: list);
- a frame it asks for that was not reached, or that differs from its golden (frames.py).
Log classes in the baseline that a run no longer reaches are reported, not failed.

    python port/tools/regress.py [--game DIR] [--only NAME,..] [--accept-log] [--accept-frames]
    (tools/build.py --regress runs it after building)

A script is one "key value" per line; '#' starts a comment:
    seconds 40                   the watchdog: the run ends after this many seconds
    frames 900,1500              frames (0 is the first presented) dumped and compared
    input START@900,A@1500       scripted presses (several input lines are joined)
    args --vmu none              more port.exe arguments
Files, under <game>: tests/goldens/<script>/frame_NNNNNN.png (approved by the user), tests/baseline/<script>.txt
(the log classes), and per run build/regress/<script>/ (port.log, frames, a sheet for each mismatch).
--accept-log rewrites the baselines from this run. --accept-frames copies this run's frames over the goldens: only
for frames the user has approved.
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
from build import run  # noqa: E402

WATCHDOG_EXIT = 2
CLASS = re.compile(r"^(TODO .*|STUB #\d+ .*|ta: list \d+: (?:polygon|sprite) vertex type \d+)")


def parse_script(path):
    s = {"seconds": 60, "frames": [], "input": [], "args": []}
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
    if s["frames"]:
        cmd += ["--dump-frames", ",".join(str(f) for f in s["frames"])]
    if s["input"]:
        cmd += ["--input", ",".join(s["input"])]
    cmd += s["args"]
    rc, _ = run(cmd, s["seconds"] + 60, cwd=out)
    log = (out / "port.log").read_text(encoding="utf-8", errors="replace") if (out / "port.log").exists() else ""
    fails, notes = [], []
    if rc != WATCHDOG_EXIT:
        fails.append(f"exit {rc}: {exit_reason(log)}")
    classes = log_classes(log)
    base_file = game / "tests" / "baseline" / f"{name}.txt"
    if a.accept_log:
        base_file.parent.mkdir(parents=True, exist_ok=True)
        base_file.write_text("".join(c + "\n" for c in sorted(classes)), encoding="utf-8")
    base = set(base_file.read_text(encoding="utf-8").splitlines()) if base_file.exists() else set()
    fails += [f"new log class: {c}" for c in sorted(classes - base)]
    notes += [f"no longer reached: {c}" for c in sorted(base - classes)]
    gdir = game / "tests" / "goldens" / name
    for f in s["frames"]:
        png = out / "frames" / f"frame_{f:06d}.png"
        gold = gdir / png.name
        if not png.exists():
            fails.append(f"frame {f} not reached")
            continue
        if a.accept_frames:
            gdir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(png, gold)
        if not gold.exists():
            notes.append(f"frame {f}: no golden yet")
            continue
        r = frames.compare(gold, png)
        if not r["identical"]:
            (fails if not r["pass"] else notes).append(f"frame {f}: {frames.describe(r)}")
        if not r["pass"]:
            frames.sheet(gold, png, out / f"mismatch_{f:06d}.png")
    print(f"{'FAIL' if fails else 'pass'} {name}: {len(s['frames'])} frame(s), {len(classes)} log class(es)")
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
    if not cfg["disc"]:
        raise SystemExit("regress needs game.json disc")
    scripts = sorted((cfg["dir"] / "tests").glob("*.script"))
    if a.only:
        want = set(a.only.split(","))
        scripts = [p for p in scripts if p.stem in want]
    if not scripts:
        raise SystemExit(f"no scripts in {cfg['dir'] / 'tests'}")
    ok = sum(run_script(cfg, p, a) for p in scripts)
    print(f"regress: {ok} of {len(scripts)} script(s) pass")
    return 0 if ok == len(scripts) else 1


if __name__ == "__main__":
    sys.exit(main())

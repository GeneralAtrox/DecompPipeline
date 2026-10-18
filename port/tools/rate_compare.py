"""The frame-rate comparison: one regression script's run (its input and arguments) shown first at the game's own
tick (--present tick, 30 or 60 frames a second) and then with in-between frames at the display's rate (--present
interp), so the two can be watched one after the other on the same display. Each run goes through the script's
start on the fixed clock, as fast as the host allows, then turns to real time at vertical blank --from
(--fast-until), so both show the same scene from there.

    python port/tools/rate_compare.py --script mission1 --from 10100 [--watch 40] [--fps N] [--only tick|interp]
                                      [--window WxH] [--headless] [--game DIR]

--watch: seconds of real time shown in each run, at least: the watchdog ends it, counting the fixed-clock part at a
cautious rate (closing the window ends a run sooner). --fps caps the in-between frames (default: the display's rate). --headless runs off-screen, for checking the tool; real-time counts then depend on the host.
The logs go to <game>/build/rate/<mode>/port.log; at the end each run's frame rate and in-between frame counts are
printed from them.
"""
import argparse
import re
import shutil
import sys

import regress
from build import run
import game_config

FAST_VBLANKS_PER_S = 400   # a cautious rate for the fixed-clock part, for the watchdog


def summary(log_text, start):
    """Frames a second presented after vertical blank start (the window's 5 s readouts), and frame_extra's report."""
    rates = [float(m[1]) for m in re.finditer(r"^gfx: ([\d.]+) frames a second presented .* vblank (\d+)\)$",
                                              log_text, re.M) if int(m[2]) > start]
    lines = []
    if rates:
        lines.append(f"presented: {min(rates):.1f} to {max(rates):.1f} frames a second "
                     f"(mean {sum(rates) / len(rates):.1f} over {len(rates)} readouts)")
    m = re.search(r"^frame_extra: (\d+) in-between frames after (\d+) rendered frames.*$", log_text, re.M)
    if m:
        lines.append(m[0])
    m = re.findall(r"^EXIT: (.*)$", log_text, re.M)
    lines.append("exit: " + (m[-1] if m else "no EXIT line"))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default=None, help="the folder with game.json (default: the current folder)")
    ap.add_argument("--script", required=True, help="a tests/NAME.script for the input and arguments")
    ap.add_argument("--from", dest="start", type=int, required=True, help="the vertical blank real time starts at")
    ap.add_argument("--watch", type=int, default=40, help="seconds of real time per run")
    ap.add_argument("--fps", type=int, default=0, help="cap on in-between frames a second (0: the display's rate)")
    ap.add_argument("--only", choices=("tick", "interp"), help="one run instead of both")
    ap.add_argument("--window", help="a window of this size (WxH) instead of full screen")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    cfg = game_config.load(game_config.find_game(a.game))
    if not cfg["disc"]:
        raise SystemExit("rate_compare needs game.json disc")
    s = regress.parse_script(cfg["dir"] / "tests" / f"{a.script}.script")
    seconds = a.start // FAST_VBLANKS_PER_S + 20 + a.watch
    for mode in [a.only] if a.only else ["tick", "interp"]:
        out = cfg["dir"] / "build" / "rate" / mode
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        cmd = [str(cfg["dir"] / "build" / "x86" / "port.exe"), "--disc", str(cfg["disc"]), "--seconds", str(seconds),
               "--log", str(out / "port.log"), "--fast-until", str(a.start), "--present", mode]
        if cfg["fsca_table"]:
            cmd += ["--check-fsca", str(cfg["fsca_table"])]
        if s["input"]:
            cmd += ["--input", ",".join(s["input"])]
        if mode == "interp":
            cmd += ["--fps", str(a.fps)]
        if a.headless:
            cmd += ["--headless"]
        elif a.window:
            cmd += ["--window", a.window]
        cmd += s["args"]
        print(f"{mode}: fast to vertical blank {a.start}, then {a.watch} s of real time (watchdog {seconds} s)",
              flush=True)
        rc, _ = run(cmd, seconds + 60, cwd=out)
        log = (out / "port.log").read_text(encoding="utf-8", errors="replace") if (out / "port.log").exists() else ""
        if "host: real time from vertical blank" not in log:
            print(f"    real time never started (exit {rc}): raise the watchdog or lower --from")
        for line in summary(log, a.start):
            print("    " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Benchmark scenarios per game: a named stretch of one of the user's own recordings, checked before it is timed.

A template is a small file kept in the game's own (private) folder beside a copy of the recording it cuts:

    <game>/perf/front-end.json    what it shows, the recording's name and hash, the build it was made on, the vertical
                                  blanks timing starts and ends at, the settings that change timing, and checkpoints
    <game>/perf/front-end.ptrec   the recording (inputs and timing; it may hold the save files that run read)

`perf.py template` makes one; `perf.py all --template FILE` times it. Before any timed run the template is replayed
once on the fixed clock with no window: if the build no longer follows the recording (base/session.c logs where pad
changes stop coming at their recorded vertical blank) the benchmark is refused with the place it diverged, because its
numbers would be of a different stretch of play. Frames hashed every 600 blanks are the checkpoints: a difference there
with the play still in step means the build draws a different picture, which is reported and not refused (--strict
refuses it).

Nearest existing: port/tools/replay_compare.py - proves two builds the same over whole recordings (its recording
reader is used here); it has no named, cut scenario and keeps no checkpoints to hold a later build against.
"""
import datetime
import hashlib
import json
import pathlib
import platform
import re
import shutil
import struct

import perf_report as pr
from replay_compare import inputs_only, sections

EVERY = 600  # vertical blanks between checkpoint frames


def blake(data):
    return hashlib.blake2b(data, digest_size=12).hexdigest()


def header(data):
    """The recording's own header text: the version and the arguments of the run that made it."""
    return data[12:12 + struct.unpack("<I", data[8:12])[0]].decode("utf-8", "replace")


def check_run(run, exe, game_args, settings, recording, stop, folder, timeout):
    """One fixed-clock pass with no window to vertical blank `stop`: exit code, checkpoint frame hashes, where the
    replay went out of step (or ""), and the game log's lines by vertical blank. Leaves nothing on disk."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    cmd = [str(exe)] + list(game_args) + list(settings)
    if "--data-directory" not in cmd:  # the run's own save folder: the recording gives the saves the game reads
        cmd += ["--data-directory", str(folder / "data")]
    cmd += ["--log", str(folder / "port.log"), "--dump-dir", str(folder), "--headless", "--fixed-clock", "1",
            "--seconds", str(timeout), "--size", "640x480", "--record", "off", "--present", "tick", "--stop-at", str(stop),
            "--dump-every", str(EVERY), "--game-log", "on", "--replay", str(recording)]
    code = run(cmd, str(pathlib.Path(exe).parent), folder / "out.txt", timeout + 120)
    frames = {p.name: blake(p.read_bytes()) for p in sorted(folder.rglob("frame*")) if p.is_file()}
    log = pr.log_lines(folder / "port.log")
    shutil.rmtree(folder, ignore_errors=True)
    return {"exit": code, "frames": frames, "out_of_step": log["out_of_step"], "stamped": log["stamped"]}


def make(a, run, out):
    """perf.py template: cut scenario a.scenario from recording a.replay on build a.a. Exit code."""
    rec = pathlib.Path(a.replay)
    data = rec.read_bytes()
    if data[:8] != b"PORTREC1":
        print(f"{rec}: not a session recording")
        return 3
    last = sections(data)["vblanks"]
    stop = min(a.stop_at or last, last)
    exe = pathlib.Path(a.a) / a.exe
    res = check_run(run, exe, a.game_args, a.setting, rec, stop, out / "template-check", a.timeout)
    if res["out_of_step"] or res["exit"] != 0:
        print(f"refused: this build does not replay {rec.name} (exit {res['exit']}). {res['out_of_step'] or 'see its log'}\n"
              "Use a recording made on this build, or record the scenario again from the game's Record entry.")
        return 3
    end = stop
    if a.end_at_log:  # where the scenario ends comes from the game's own log, not from a guess at a blank number
        hit = next((v for v, t in res["stamped"] if v >= a.fast_until and re.search(a.end_at_log, t, re.I)), None)
        if hit is None:
            seen = list(dict.fromkeys(re.sub(r"\d+", "N", t)[:70] for _, t in res["stamped"]))[:40]
            print(f"refused: no game log line matches {a.end_at_log!r} before vertical blank {stop}. Lines seen:\n  " + "\n  ".join(seen))
            return 3
        end = min(stop, hit + a.after_blanks)
        if end != stop:
            res = check_run(run, exe, a.game_args, a.setting, rec, end, out / "template-check", a.timeout)
    folder = pathlib.Path(a.template_out)
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"{a.scenario}.ptrec"
    if copy.resolve() != rec.resolve():
        shutil.copyfile(rec, copy)
    tpl = {"game": a.name, "scenario": a.scenario, "shows": a.shows, "recording": copy.name, "recording_blake": blake(data),
           "recording_from": rec.name, "recording_blanks": last, "recording_header": header(data)[:600],
           "recording_holds": inputs_only(data) or "inputs and timing only", "start": a.fast_until, "end": end,
           "settings": list(a.setting), "recorded_on": {"game": a.game_commit, "kit": a.kit_commit},
           "made_on": {"build": str(a.a), "machine": platform.node(), "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")},
           "checkpoint_every": EVERY, "checkpoints": res["frames"]}
    (folder / f"{a.scenario}.json").write_text(json.dumps(tpl, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"template: {folder / (a.scenario + '.json')}: {a.name} {a.scenario}, vertical blanks {a.fast_until} to {end} of {last}, "
          f"{len(res['frames'])} checkpoints; the recording holds {tpl['recording_holds']}")
    return 0


def load(path):
    """(template dict, recording path); raises ValueError if the recording beside it is not the one it was cut from."""
    path = pathlib.Path(path)
    tpl = json.loads(path.read_text(encoding="utf-8"))
    rec = path.parent / tpl["recording"]
    if not rec.exists() or blake(rec.read_bytes()) != tpl["recording_blake"]:
        raise ValueError(f"{rec}: missing, or not the recording {path.name} was cut from")
    return tpl, rec


def verify(tpl, rec, run, exe, game_args, out, timeout, strict=False):
    """(ok, one line saying what was found) for one build against a template."""
    res = check_run(run, exe, game_args, tpl["settings"], rec, tpl["end"], out / "template-check", timeout)
    if res["out_of_step"]:
        return False, f"out of step: {res['out_of_step']}"
    if res["exit"] != 0:
        return False, f"the replay ended with exit {res['exit']} before vertical blank {tpl['end']}"
    want, got = tpl["checkpoints"], res["frames"]
    wrong = sorted(n for n in want if got.get(n) != want[n])
    if not wrong:
        return True, f"in step, all {len(want)} checkpoints the same picture"
    text = (f"in step by pad timing, but {len(wrong)} of {len(want)} checkpoint frames differ from the build the template was made on, "
            f"the first {wrong[0]}: this build draws a different picture (or the play diverged without a pad change to show it)")
    return not strict, text

"""Lockstep check: the port against Flycast, frame by frame, from the same input.

Flycast plays the game from a scripted input (the port's --input syntax) with a Lua script (lockstep_emu.lua) that
reads the game's watched state (lockstep.spec) at every vertical blank, or where the port samples (game.json
lockstep_sample_pcs), and keeps each frame's last sample. The port replays those frames (runtime/lockstep.c): every
frame takes its length (the SDK timer's delta) and its pad from the paired emulator frame, and the port writes the
same watched state at each frame's end. The compare names the first frame where the two differ, and what differs.

    python tools/lockstep.py run --game DIR --input START@900,A@1500+10,R@6000+2000 --seconds 600
        [--emu FILE] [--build-dir x86-agent] [--out DIR] [--spec FILE]

`run` records the emulator frames (DIR/build/lockstep/emu.txt, or --emu FILE if it exists already: the emulator is
deterministic for the same input, so one recording serves every port build), runs the port and compares.
`emu`, `port` and `compare` run one step. Flycast runs without observation (unpaced); the port with
--fixed-clock 1, headless, no audio, no memory card (Flycast's controller has none), --present tick.

Pairing: the frame label is the game's copy of the SDK timer, read as each frame starts. The emulator half also
records each frame's draw (the TA's first block, read on the game's thread), and with lockstep_sample_pcs its step
(its frame words at its sample PC, also for several frames in one vertical blank; lockstep_scene_pcs: the SDK's
kmBeginScene, so frames count as the port's), so faster frames and loads are known (build_frames); the port's frames
take their timer values from those. lockstep_rtc_pcs (the SDK's RTC read): a frame drives the port with the RTC it read.
Samples are compared where both sides have one; a sample taken mid-frame or after a long frame (a disc load) is
counted apart. Words flagged time are reported apart; ptr words compare as null or not; - is a word outside RAM.

--watch NAME[J]@N (with port or run) logs each write the port makes to that word in port frame N, named from the
build's port.map (lockstep_watch.py): the function that wrote it and its likeliest callers. --baseline TRACE (a
reference build's trace, same recording) reports only what that build does not share: a change's first divergence.

Display lists: a recording made with vertex digests (hw_check.py emu: OUT/hw_emu.txt has D lines) is also compared on
what the game sends the PowerVR (tools/ta_digest.py): the port writes its lists (--hw-digest OUT/hw_port_BUILD.txt)
and each render is paired with its scene as hw_check pairs them. The first divergent render is named next to the RAM
verdict (exit 4 when the RAM agrees); a recording without digests is compared on the RAM only, and says so.
"""
import argparse
import os
import pathlib
import secrets
import struct
import subprocess
import sys
import time

import emu_capture as ec
from lockstep_frames import (build_frames, draws_path, drive_file, iter_frames, layout, read_frames, read_marks,
                             check_gates, sample_of, steps_path, word)
from lockstep_watch import exit_line, report_hits, watch_arg
from game_config import PIPELINE, load as load_game, oplog
from ta_digest import check as check_lists, has_digests

TOOLS = pathlib.Path(__file__).resolve().parent
BUTTONS = {"C": 0x1, "B": 0x2, "A": 0x4, "START": 0x8, "UP": 0x10, "DOWN": 0x20, "LEFT": 0x40, "RIGHT": 0x80,
           "Y": 0x200, "X": 0x400}
TRIGGERS = {"R": 255}
FLOAT_REL, FLOAT_ABS = 1e-4, 1e-6        # float words this close are rounding, not divergence
PRESS = 10                               # vertical blanks a press without +N lasts (the port's gfx_input.c)
LOAD_VBLANKS = 4                         # an emulator frame longer than this is a load: sampled mid-work
SET_APART_MAX = 0.05                     # more compared frames set apart than this: the check is blind (fails)
# The console Flycast plays: a VGA box, as the port is (sdk/sy_sys.c syCblCheck). Flycast's default is a TV cable,
# and a game sizes its screen margins by the cable (one game's HUD sat 14 and 30 px further in).
CONSOLE = ["config:Dreamcast.Cable=0"]


def parse_spec(path):
    items = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        f = line.split()
        if len(f) < 3 or f[0].startswith("#"):
            continue
        items.append({"name": f[0], "words": int(f[1], 0), "addr": f[2], "flags": set(f[3:])})
    return check_gates(items, path)


def parse_input(text):
    """START@900,A@1500+30,R@6000+2000 -> [(vblank, buttons, length, r trigger)]."""
    out = []
    for part in filter(None, (text or "").split(",")):
        name, _, rest = part.partition("@")
        at, _, length = rest.partition("+")
        name = name.upper()
        if name not in BUTTONS and name not in TRIGGERS:
            raise SystemExit(f"--input: unknown button {name}")
        out.append((int(at), BUTTONS.get(name, 0), int(length) if length else PRESS, TRIGGERS.get(name, 0)))
    return out


def flycast_exe(given=None):
    if given:
        return pathlib.Path(given)
    sys.path.insert(0, str(PIPELINE))
    from core import toolcfg  # noqa: E402
    return pathlib.Path(toolcfg.need(toolcfg.load(PIPELINE), "flycast"))


def recording_flycast(emu_path, name="LS_FLYCAST"):
    """The Flycast a recording was made with (emu_run writes it into the recording's Lua file), or None (a recording
    made before it did): a replay of the recording needs its lockstep hooks, which the toolcfg default may not have.
    name "LS_CABLE": the cable it played on (Dreamcast.Cable: "0" the VGA box, "default" Flycast's own, a TV cable)."""
    lua = pathlib.Path(emu_path).with_suffix(".lua")
    text = lua.read_text(encoding="utf-8", errors="replace") if lua.exists() else ""
    line = next((x for x in text.splitlines() if x.startswith(name + " = ")), None)
    return line.split("=", 1)[1].strip().strip('"') if line else None


def emu_run(game, spec, out, seconds, inputs, exe=None, hw=None, lua_vars=None, entries=(), drive=None, extra_lua=(),
            console=None):
    """Flycast from boot with lockstep_emu.lua for `seconds`; the frames go to `out`, and with hw the hardware check's
    renders to that file (hw_emu.lua, tools/hw_check.py). lua_vars: more Lua settings (name: Lua literal); entries:
    more -config entries (later ones win); drive(ctl, proc, deadline): runs the session instead of waiting
    (tools/reference.py); extra_lua: more Lua files appended last (tools/audio_emu.lua); console: in place of CONSOLE
    ([]: Flycast's own TV cable). Returns the wall time."""
    exe = flycast_exe(exe)
    cfg = load_game(game)
    out = pathlib.Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    lua, draws, steps = out.with_suffix(".lua"), draws_path(out), steps_path(out)
    table = ",".join("{%d,%d,%d,%d}" % i for i in inputs)
    pcs = lambda key: "{%s}" % ",".join("0x" + a for a in cfg[f"lockstep_{key.lower()}_pcs"])
    lua_vars = {**{f"LS_{k}_PCS": pcs(k) for k in ("SAMPLE", "RTC", "SCENE")}, **(lua_vars or {})}
    console = list(CONSOLE if console is None else console)
    cable = next((e[23:] for e in reversed(console + list(entries)) if e[:23] == "config:Dreamcast.Cable="), "default")
    lua.write_text(f'LS_FLYCAST = "{exe.resolve().as_posix()}"\nLS_CABLE = "{cable}"\n'  # (recording_flycast)
                   f'LS_SPEC = "{pathlib.Path(spec).resolve().as_posix()}"\nLS_OUT = "{out.as_posix()}"\n'
                   f'LS_DRAWS = "{draws.as_posix()}"\nLS_STEPS = "{steps.as_posix()}"\nLS_INPUT = {{{table}}}\n'
                   + (f'LS_HW = "{pathlib.Path(hw).resolve().as_posix()}"\n' if hw else "")
                   + "".join(f"{k} = {v}\n" for k, v in lua_vars.items())
                   + (TOOLS / "lockstep_emu.lua").read_text(encoding="utf-8")
                   + ((TOOLS / "hw_emu.lua").read_text(encoding="utf-8") if hw else "")
                   + "".join(pathlib.Path(x).read_text(encoding="utf-8") for x in extra_lua),
                   encoding="utf-8")
    for old in (out, draws, steps):
        old.unlink(missing_ok=True)
    port, token = ec.free_port(), secrets.token_hex(16)
    rel = os.path.relpath(lua, exe.parent).replace("\\", "/")
    # unpaced (no vsync; null audio without its real-time wait) and minimised without focus: the same frames, sooner
    base = [f"research:ControlPort={port}", f"research:ControlToken={token}", "config:rend.ThreadedRendering=no",
            "config:rend.Resolution=480", "config:rend.vsync=no", "audio:backend=null", f"config:LuaFileName={rel}",
            f"research:DreamcastRtcSeed={ec.RTC_SEED}", "input:device1.1=10", "input:device1.2=3",
            "config:Dreamcast.AutoLoadState=no"] + console
    keys = {e.partition("=")[0] for e in entries}
    config = [e for e in base if e.partition("=")[0] not in keys] + list(entries)
    t0 = time.monotonic()
    proc = subprocess.Popen([str(exe), "-config", ",".join(config), str(cfg["disc"])], cwd=str(exe.parent),
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=dict(os.environ, FLYCAST_RESEARCH_UNTHROTTLED_NULL_AUDIO="1"),
                            startupinfo=subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=7))
    ctl = ec.Control(port, token)
    try:
        ec.connect(ctl, proc, t0 + seconds + 30)
        if drive:
            drive(ctl, proc, t0 + seconds)
        while not drive and time.monotonic() - t0 < seconds and proc.poll() is None:
            time.sleep(1)
    finally:
        try:
            ctl.request("exit", timeout=10)
        except Exception:
            pass
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        ec.kill_tree(proc)
    return time.monotonic() - t0


def port_run(game, build_dir, spec, drive, trace, seconds, extra=()):
    cfg = load_game(game)
    bdir = pathlib.Path(game) / "build" / build_dir
    rdir = pathlib.Path(game) / "build" / f"{build_dir}_lockstep"
    rdir.mkdir(parents=True, exist_ok=True)
    cmd = [str(bdir / "port.exe"), "--disc", str(cfg["disc"]), "--seconds", str(seconds), "--log",
           str(rdir / "port.log"), "--headless", "--fixed-clock", "1", "--audio", "off", "--present", "tick",
           "--vmu", "none", "--settings", "none", "--record", "off", "--lockstep-spec", str(spec),
           "--lockstep", str(drive), "--lockstep-trace", str(trace), "--lockstep-end", "exit"] + list(extra)
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=str(rdir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        rc = proc.wait(timeout=seconds + 60)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=60)
        rc = -1
    return rc, time.monotonic() - t0, rdir / "port.log"


def fmt(w, it):
    if w is None:
        return "-"
    if "f" in it["flags"]:
        return "%08x(%g)" % (w, struct.unpack("<f", struct.pack("<I", w))[0])
    return "%08x" % w


def is_ram(w):
    return w is not None and 0x8c000000 <= w < 0x8d000000


def as_float(w, f=False):
    """The word as a float if it looks like one (|x| about 1e-8..1e10, or 0; any finite value in an f word), else
    None."""
    if w is None:
        return None
    if w & 0x7fffffff == 0:
        return 0.0
    e = (w >> 23) & 0xff
    return struct.unpack("<f", struct.pack("<I", w))[0] if 100 <= e <= 160 or (f and e < 255) else None


def float_gap(a, b, f=False, scale=0.0):
    """The relative gap of two float-looking words, None if they are not both floats; relative to scale when that is
    larger (a vector's length: a coordinate near zero rounds at the vector's scale)."""
    fa, fb = as_float(a, f), as_float(b, f)
    if fa is None or fb is None or (fa == 0.0 and fb == 0.0):
        return None
    gap = abs(fa - fb)
    return 0.0 if gap <= FLOAT_ABS else gap / max(abs(fa), abs(fb), scale)


def vector_scale(words, j, f):
    """The largest magnitude in word j's vector (its run of three) of an f item's words, 0.0 for other items."""
    if not f:
        return 0.0
    vals = [as_float(w, True) for w in words[j - j % 3:j - j % 3 + 3]]
    return max((abs(v) for v in vals if v is not None), default=0.0)


def differs(a, b, it, scale=0.0):
    """0 same; 1 different; 2 both RAM addresses (the two heaps are laid out apart); 3 floats within FLOAT_REL
    (SH-4 and x86 arithmetic round apart: fsca, fipr, ftrv). 2 and 3 are counted, not divergences."""
    if "ptr" in it["flags"]:
        return int((a in (None, 0)) != (b in (None, 0)))
    if a == b:
        return 0
    if is_ram(a) and is_ram(b):
        return 2
    gap = float_gap(a, b, "f" in it["flags"], scale)
    return 3 if gap is not None and gap <= FLOAT_REL else 1


def item_diffs(it, pw, pat, ew, eat, res=None):
    """[(j, port word, emulator word)] of the item's words that differ; res counts the pointer and float words."""
    f = "f" in it["flags"]
    pws = [word(pw[pat + j]) if pat + j < len(pw) else None for j in range(it["words"])]
    ews = [word(ew[eat + j]) if eat + j < len(ew) else None for j in range(it["words"])]
    out = []
    for j, (a, b) in enumerate(zip(pws, ews)):
        scale = max(vector_scale(pws, j, f), vector_scale(ews, j, f))
        d = differs(a, b, it, scale)
        if res is not None:
            res["pointers"] += d == 2
            if d == 3:
                res["floats"] += 1
                res["float_max"] = max(res["float_max"], float_gap(a, b, f, scale))
        if d == 1:
            out.append((j, a, b))
    return out


def compare(items, emu, frames, port, limit=12, base=None):
    """{frames, traced, last, driven, first, first_time, diffs}: the first port frame that differs outside the time
    words, and the first that differs only in them (last: the trace's last game frame; driven: the drive's frames). A
    sample taken mid-frame (its frames word not the draw's, nor one more as after a lockstep_sample_pcs PC: a load, a
    frame longer than the vertical blank) or after a long frame (LOAD_VBLANKS) is counted apart, and so is one that
    shows the port's frame before (drawn): the vertical blank came within the frame's draw on the console, before the
    draw wrote its words (not a step with scenes: its sample is the frame's). With base (another port run's trace from
    the same recording, a reference build), a frame whose differing items read as base's does is known: a change's own
    divergence is the first the reference does not share. Rows are read one by one."""
    elay, play = dict((it["name"], at) for it, at in layout(items, False)), layout(items, True)
    res = {"frames": 0, "first": None, "first_time": None, "diffs": [], "time_diffs": [], "pointers": 0,
           "floats": 0, "float_max": 0.0, "loads": 0, "load_diffs": 0, "drawn": 0, "known": 0,
           "driven": len(frames), "last": None}
    samples = sample_of(emu, frames)
    fat = next((at for it, at in layout(items, False) if "frames" in it["flags"]), None)
    rows, base_rows, prev, n = iter(port), iter(base or ()), None, -1
    for n, (label, gi, pw) in enumerate(rows):
        brow, before, prev, res["last"] = next(base_rows, None), prev, pw, gi
        if gi not in samples:
            continue  # not driven, or a frame no vertical blank sampled
        ei, draw, pw = samples[gi], frames[gi]["draw"], pw.split()
        before = before if len(draw or ()) < 8 else None  # a step with scenes: sampled at the frame's own point
        res["frames"] += 1
        ew = emu[ei][2].split()
        mid = draw is not None and fat is not None and (ew[fat] == "-" or (word(ew[fat]) - draw[1]) % 2**32 > 1)
        load = mid or ei == 0 or emu[ei][1] - emu[ei - 1][1] > LOAD_VBLANKS  # the first: sampled within the boot
        res["loads"] += load
        diffs, tdiffs, late = [], [], []
        for it, at in play:
            eat = elay[it["name"]]
            for j, a, b in item_diffs(it, pw, at, ew, eat, res):
                text = f"{it['name']}[{j}] port {fmt(a, it)} emu {fmt(b, it)}"
                (tdiffs if "time" in it["flags"] else diffs).append(text)
                if "time" not in it["flags"] and it not in late:
                    late.append(it)
        if diffs and load:
            res["load_diffs"] += 1
            continue
        if diffs and before is not None and not any(item_diffs(it, before.split(), at, ew, elay[it["name"]])
                                                    for it, at in play if it in late):
            res["drawn"] += 1
            continue
        if diffs and brow and not any(item_diffs(it, pw, at, brow[2].split(), at) for it, at in play if it in late):
            res["known"] += 1
            continue
        if diffs and res["first"] is None:
            res["first"] = (n, label, ei, emu[ei][1])
            res["diffs"] = diffs[:limit] + ([f"... {len(diffs) - limit} more"] if len(diffs) > limit else [])
            break
        if tdiffs and res["first_time"] is None:
            res["first_time"] = (n, label, ei, emu[ei][1])
            res["time_diffs"] = tdiffs[:limit]
    res["traced"] = n + 1
    for _, gi, _ in rows:
        res["traced"], res["last"] = res["traced"] + 1, gi
    return res


def report(res, emu, lists_differ=False):
    print(f"compared {res['frames']} port frames ({res['traced']} traced) with {len(emu)} emulator frames; "
          f"{res['pointers']} pointer words differ by heap layout only, {res['floats']} float words by rounding "
          f"(largest relative gap {res['float_max']:.2g}); {res['loads']} load or split frames, {res['load_diffs']} "
          f"of them differ (sampled mid-frame); {res['drawn']} sampled within the frame's draw (the frame before)"
          + (f"; {res['known']} as the baseline's" if res["known"] else ""))
    blind = res["frames"] >= 100 and res["loads"] > SET_APART_MAX * res["frames"]
    if blind:
        print(f"CHECK BLIND: {res['loads']} of {res['frames']} compared frames set apart, over "
              f"{SET_APART_MAX:.0%} (the emulator's sample point and the frames word disagree?)")
    if res["first_time"]:
        n, label, ei, vb = res["first_time"]
        print(f"first clock-only difference: port frame {n} (label {label:08x}), emulator frame {ei} (vblank {vb})")
        for d in res["time_diffs"]:
            print("   ", d)
    short = res["last"] is None or res["last"] < res["driven"] - 1
    if short:
        print(f"PORT STOPPED SHORT: its trace ends at game frame {res['last']} of the drive's {res['driven']} "
              f"(the port exited, halted or was cut before the recording's end: see its EXIT line)")
    if res["first"] is None:
        print(("no RAM divergence in the frames traced" if short else "no RAM divergence") + " (the display lists "
              "differ: below)" if lists_differ else "no divergence in the frames traced" if short else "no divergence")
        return 3 if short else 2 if blind else 0
    n, label, ei, vb = res["first"]
    print(f"FIRST DIVERGENT FRAME: port frame {n} (label {label:08x}), emulator frame {ei} (vblank {vb})")
    for d in res["diffs"]:
        print("   ", d)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["run", "emu", "port", "compare"])
    ap.add_argument("--game", required=True)
    ap.add_argument("--spec", default=None, help="default: GAME/lockstep.spec")
    ap.add_argument("--input", default="")
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--emu", default=None, help="the emulator frames (default OUT/emu.txt)")
    ap.add_argument("--out", default=None, help="default: GAME/build/lockstep")
    ap.add_argument("--build-dir", default="x86-agent")
    ap.add_argument("--flycast", default=None)
    ap.add_argument("--baseline", default=None, help="a reference port run's trace: report what it does not share")
    ap.add_argument("--watch", default=None, help="NAME[J]@N: log the writes to that word in port frame N")
    a, extra = ap.parse_known_args(argv)
    game = pathlib.Path(a.game).resolve()
    oplog().begin(f"port lockstep.py {a.step}", [game])
    spec = pathlib.Path(a.spec or game / "lockstep.spec").resolve()
    out = pathlib.Path(a.out or game / "build" / "lockstep").resolve()
    out.mkdir(parents=True, exist_ok=True)
    emu_path = pathlib.Path(a.emu or out / "emu.txt").resolve()
    items = parse_spec(spec)
    if a.step == "emu" or (a.step == "run" and not emu_path.exists()):
        wall = emu_run(game, spec, emu_path, a.seconds, parse_input(a.input), a.flycast)
        emu = read_frames(emu_path)
        played = (emu[-1][1] / 59.94) if emu else 0
        print(f"emulator: {len(emu)} frames, {played:.0f} s played in {wall:.0f} s")
        if a.step == "emu":
            return 0
    emu = read_frames(emu_path)
    frames = build_frames(emu, read_marks(emu_path))
    trace = out / f"port_{a.build_dir}.txt"
    hw_emu, hw_port = emu_path.with_name("hw_emu.txt"), out / f"hw_port_{a.build_dir}.txt"
    if a.step in ("run", "port"):
        drive = out / "drive.txt"
        drive_file(items, emu, frames, drive)
        extra = [x for x in extra if x != "--"]
        extra += ["--lockstep-watch", watch_arg(items, a.watch, layout)] if a.watch else []
        extra += ["--hw-digest", str(hw_port)] if has_digests(hw_emu) else []
        rc, wall, log = port_run(game, a.build_dir, spec, drive, trace, a.seconds, extra)
        print(f"port: exit {rc} ({exit_line(log)}), {wall:.0f} s; log {log}")
        if a.watch:
            report_hits(log, game / "build" / a.build_dir / "port.map")
    base = iter_frames(a.baseline) if a.baseline else None
    hw_rc, hw_lines = check_lists(hw_emu, hw_port, frames, out / f"lists_{a.build_dir}.txt")
    rc = report(compare(items, emu, frames, iter_frames(trace), base=base), emu, hw_rc == 4)
    print("\n".join(hw_lines))
    return rc or hw_rc or 0


if __name__ == "__main__":
    sys.exit(oplog().main(main))

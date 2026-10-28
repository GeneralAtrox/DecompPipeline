"""The emulator's half of the lockstep check (port/tools/lockstep_emu.lua) on a synthetic load window, run by a stock
Lua with a stand-in for FlycastRedux's API (memory, pc_hook, vertical blanks): several game frames end between two
vertical blanks, each at its sample PC, and one reads the RTC. Each frame is written with its own sample and step,
the RTC read is the frame's, and tools/lockstep.py then pairs every frame and still reports a one-word difference
planted inside the window (build_frames over the steps; compare; drive_file).
Only where a Lua interpreter is found (lua or lua5.4 on PATH, or $LUA): without one these tests skip, and NO_LUA says
so on stderr: a skip is not a pass.
Run: python -W error tests/test_lockstep_emu.py
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "port" / "tools"))

import lockstep  # noqa: E402
from lockstep_frames import build_frames, read_frames, read_marks  # noqa: E402

LUA = os.environ.get("LUA") or shutil.which("lua") or shutil.which("lua5.4")
SPEC = """label 1 8c100000 label time
frames 1 8c100004 frames time
x 1 8c100008
pad 13 8c100100 emu pad
rtc 2 a0710000 emu io rtc
"""
S, R, B = 0x8c06cc48, 0x8c0b42a6, 0x8c06c278  # a sample PC (the SDK's render), the RTC read, kmBeginScene
# The stand-in: guest memory as a table; pc_hook keeps the callback; the script below plays the game.
HARNESS = """mem = {}
flycast = {memory = {read32 = function(a) return mem[a] or 0 end},
           research = {pc_hook = function(pcs, fn) hook = fn end, subscribe = function() end},
           input = {pressButtons = function() end, releaseButtons = function() end, setAxis = function() end}}
dofile(LS_SCRIPT)
local function frame(label, frames, x) mem[0x8c100000] = label; mem[0x8c100004] = frames; mem[0x8c100008] = x end
frame(0x100, 1, 1); hook(%(B)d); hook(%(S)d)
flycast_callbacks.vblank()
-- a load window: frames 200, 300 (it reads the RTC) and 400 end before the next vertical blank, which comes within 500
frame(0x200, 2, 2); hook(%(B)d); hook(%(S)d)
frame(0x300, 3, 3); hook(%(B)d); mem[0xa0710004] = 0x1000; hook(%(R)d); mem[0xa0710004] = 0x1001; hook(%(S)d)
frame(0x400, 4, 4); hook(%(B)d); hook(%(S)d)
frame(0x500, 5, 99); hook(%(B)d)
flycast_callbacks.vblank()
mem[0x8c100008] = 5; hook(%(S)d)
frame(0x600, 6, 6)
flycast_callbacks.vblank()
flycast_callbacks.terminate()
""" % {"S": S, "R": R, "B": B}


class Console(unittest.TestCase):
    """Flycast plays the console the port is: a VGA box (sdk/sy_sys.c's syCblCheck). With Flycast's default TV cable
    a game sizes its screen margins for a television (Toy Commander's HUD 14 and 30 px further in)."""
    def test_vga_cable_as_the_port(self):
        sy = (TOOL / "port" / "sdk" / "sy_sys.c").read_text(encoding="utf-8")
        self.assertRegex(sy, r"s32 syCblCheck\(void\) \{ return SYE_CBL_VGA; \}")
        seen = []

        def popen(cmd, **_):
            seen.append(cmd)
            raise RuntimeError("stop")
        d = pathlib.Path(tempfile.mkdtemp(prefix="ls_cfg_"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        cfg = {"disc": d / "disc.cue", **{f"lockstep_{k}_pcs": [] for k in ("sample", "rtc", "scene")}}
        with mock.patch.object(lockstep, "load_game", return_value=cfg),                 mock.patch.object(lockstep.subprocess, "Popen", popen), self.assertRaises(RuntimeError):
            lockstep.emu_run(d, d / "spec.txt", d / "emu.txt", 1, [], exe=d / "flycast.exe")
        entries = seen[0][seen[0].index("-config") + 1].split(",")
        self.assertIn("config:Dreamcast.Cable=0", entries)


NO_LUA = "no Lua interpreter found (lua or lua5.4 on PATH, or $LUA): the lockstep_emu.lua tests are SKIPPED, not passed"
if not LUA:
    sys.stderr.write(f"*** {NO_LUA}\n")


@unittest.skipUnless(LUA, NO_LUA)
class LoadWindow(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="ls_emu_"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        spec, self.emu = self.dir / "spec.txt", self.dir / "emu.txt"
        spec.write_text(SPEC, encoding="ascii")
        script = self.dir / "emu.lua"
        script.write_text(f'LS_SPEC = "{spec.as_posix()}"\nLS_OUT = "{self.emu.as_posix()}"\n'
                          f'LS_STEPS = "{(self.dir / "emu_steps.txt").as_posix()}"\nLS_INPUT = {{}}\n'
                          f"LS_SAMPLE_PCS = {{{S}}}\nLS_RTC_PCS = {{{R}}}\nLS_SCENE_PCS = {{{B}}}\n"
                          + (TOOL / "port" / "tools" / "lockstep_emu.lua").read_text(encoding="utf-8"),
                          encoding="utf-8")
        run = self.dir / "run.lua"
        run.write_text(f'LS_SCRIPT = "{script.as_posix()}"\n' + HARNESS, encoding="ascii")
        subprocess.run([LUA, str(run)], check=True, timeout=60, capture_output=True)
        self.items = lockstep.parse_spec(spec)

    def test_every_frame_written_with_its_own_sample(self):
        rows = [(lab, vb, w.split()) for lab, vb, w in read_frames(self.emu)]
        self.assertEqual([(lab, vb, w[2]) for lab, vb, w in rows],
                         [(0x100, 1, "00000001"), (0x200, 1, "00000002"), (0x300, 1, "00000003"),
                          (0x400, 1, "00000004"), (0x500, 2, "00000005")])
        rtc = [int(w[-1], 16) for _, _, w in rows]
        self.assertEqual(rtc, [0, 0, 0x1000, 0, 0x1001])  # 300: its own read; the others: the vertical blank's
        self.assertEqual([d[:2] + d[7:] for d in read_marks(self.emu)], [(0x100 * k, k, 1) for k in range(1, 6)])

    def test_pairing_and_a_planted_difference_inside_the_window(self):
        emu = read_frames(self.emu)
        frames = build_frames(emu, read_marks(self.emu))
        self.assertEqual([(f["label"], f["jump"]) for f in frames], [(0x100 * k, None) for k in range(1, 6)])
        drive = self.dir / "drive.txt"
        lockstep.drive_file(self.items, emu, frames, drive)
        rtc = [int(line.split()[6], 16) for line in drive.read_text().splitlines()]
        self.assertEqual(rtc, [0, 0, 0x1000, 0, 0x1001])

        def port(x300):
            return ((f["label"], gi, "%08x %08x %08x" % (f["label"], gi + 1, x300 if gi == 2 else (gi + 1) % 5 or 5))
                    for gi, f in enumerate(frames))
        same = lockstep.compare(self.items, emu, frames, port(3))
        self.assertEqual((same["first"], same["frames"], same["load_diffs"]), (None, 5, 0))
        planted = lockstep.compare(self.items, emu, frames, port(33))
        self.assertEqual(planted["first"][:3], (2, 0x300, 2))
        self.assertEqual(planted["diffs"], ["x[0] port 00000021 emu 00000003"])


LOAD_SCENE = """mem = {}
flycast = {memory = {read32 = function(a) return mem[a] or 0 end},
           research = {pc_hook = function(pcs, fn) hook = fn end, subscribe = function() end},
           input = {pressButtons = function() end, releaseButtons = function() end, setAxis = function() end}}
dofile(LS_SCRIPT)
local function frame(label, frames, x) mem[0x8c100000] = label; mem[0x8c100004] = frames; mem[0x8c100008] = x end
frame(0x100, 1, 1); hook(%(B)d); hook(%(S)d)
flycast_callbacks.vblank()
-- a load that opens a movie: it begins the movie's first scene but reaches no sample PC before the next label
frame(0x200, 1, 2); mem[0x8c100004] = 2; hook(%(B)d)
flycast_callbacks.vblank()
frame(0x300, 3, 3); hook(%(B)d); hook(%(S)d)
flycast_callbacks.vblank()
frame(0x400, 4, 4)
flycast_callbacks.vblank()
flycast_callbacks.terminate()
""" % {"S": S, "B": B}


GATE = """mem = {}
flycast = {memory = {read32 = function(a) return mem[a] or 0 end},
           research = {pc_hook = function(pcs, fn) hook = fn end, subscribe = function() end},
           input = {pressButtons = function() end, releaseButtons = function() end, setAxis = function() end}}
dofile(LS_SCRIPT)
mem[0x8c100010] = 0xaa
for k, x in ipairs({1, 3, 2, 3, 4}) do
  mem[0x8c100000] = 0x100 * k; mem[0x8c100004] = k; mem[0x8c100008] = x
  flycast_callbacks.vblank()
end
flycast_callbacks.terminate()
"""


@unittest.skipUnless(LUA, NO_LUA)
class Gate(unittest.TestCase):
    """Flag if=ITEM:VALUE: the item is read only while that earlier item's word holds VALUE, else "-" (as the port's
    runtime/lockstep_spec.c); a gate on no earlier item stops the script."""

    def play(self, spec):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "spec.txt").write_text(spec, encoding="ascii")
        script = d / "emu.lua"
        script.write_text(f'LS_SPEC = "{(d / "spec.txt").as_posix()}"\nLS_OUT = "{(d / "emu.txt").as_posix()}"\n'
                          f"LS_INPUT = {{}}\n"
                          + (TOOL / "port" / "tools" / "lockstep_emu.lua").read_text(encoding="utf-8"),
                          encoding="utf-8")
        (d / "run.lua").write_text(f'LS_SCRIPT = "{script.as_posix()}"\n' + GATE, encoding="ascii")
        r = subprocess.run([LUA, str(d / "run.lua")], timeout=60, capture_output=True, text=True)
        return r, (read_frames(d / "emu.txt") if not r.returncode else None)

    def test_gated_item(self):
        r, rows = self.play(SPEC + "lvl 1 8c100010 if=x:3\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(lab, w.split()[2], w.split()[-1]) for lab, _, w in rows],
                         [(0x100, "00000001", "-"), (0x200, "00000003", "000000aa"), (0x300, "00000002", "-"),
                          (0x400, "00000003", "000000aa")])

    def test_a_gate_on_no_earlier_item_stops(self):
        for flag in ("if=lvl:3", "if=x[1]:3", "if=x"):
            r, _ = self.play(SPEC + f"lvl 1 8c100010 {flag}\n")
            self.assertNotEqual(r.returncode, 0, flag)


@unittest.skipUnless(LUA, "no Lua interpreter")
class LoadScene(unittest.TestCase):
    """A frame that begins a scene but reaches no sample PC (a load opening a movie) gets an unsampled step: its frame
    words read at the scene, then "1 1" (scene, unsampled); frames with a sample keep their steps as before."""

    def test_unsampled_step(self):
        d = pathlib.Path(tempfile.mkdtemp())
        try:
            spec, emu = d / "spec.txt", d / "emu.txt"
            spec.write_text(SPEC, encoding="ascii")
            script = d / "emu.lua"
            script.write_text(f'LS_SPEC = "{spec.as_posix()}"\nLS_OUT = "{emu.as_posix()}"\n'
                              f'LS_STEPS = "{(d / "emu_steps.txt").as_posix()}"\nLS_INPUT = {{}}\n'
                              f"LS_SAMPLE_PCS = {{{S}}}\nLS_RTC_PCS = {{{R}}}\nLS_SCENE_PCS = {{{B}}}\n"
                              + (TOOL / "port" / "tools" / "lockstep_emu.lua").read_text(encoding="utf-8"),
                              encoding="utf-8")
            run = d / "run.lua"
            run.write_text(f'LS_SCRIPT = "{script.as_posix()}"\n' + LOAD_SCENE, encoding="ascii")
            subprocess.run([LUA, str(run)], check=True, timeout=60, capture_output=True)
            self.assertEqual([r[0] for r in read_frames(emu)], [0x100, 0x200, 0x300])
            self.assertEqual([(m[0], m[1]) + m[7:] for m in read_marks(emu)],
                             [(0x100, 1, 1), (0x200, 2, 1, 1), (0x300, 3, 1)])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

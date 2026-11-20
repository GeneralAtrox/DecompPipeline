"""The game-owned renderer mode (port/base/native_game.h, port/native): the dummy native game in tests/fixtures/native_game
makes its own Direct3D 11 device and draws a known picture; the base's window path, frame dumps, game log, Copy agent
prompt, crash reporter and exit run through it. Headless, WARP and the fixed clock: no window, the same pixels on any
machine. Needs the 64-bit Visual Studio tools (config.local.json vcvars) and SDL3 for x64 (vcpkg, or already beside
the exe); skipped without them. The build machines run it, never the main laptop.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "port" / "tools"))
import build_native  # noqa: E402

FIXTURE = TOOL / "tests" / "fixtures" / "native_game"
OUT = pathlib.Path(os.environ.get("DP_NATIVE_OUT") or (TOOL / "tests" / ".work" / "native_game"))


def have_vc():
    try:
        build_native.vcvars64()
        return True
    except SystemExit:
        return False


def pe_exports(path):
    """The names an exe exports (its PE export directory), read without dumpbin or pefile."""
    data = pathlib.Path(path).read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec, optsize = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    dirs = opt + (112 if struct.unpack_from("<H", data, opt)[0] == 0x20B else 96)
    rva = struct.unpack_from("<I", data, dirs)[0]
    if not rva:
        return set()
    secs = [struct.unpack_from("<IIII", data, opt + optsize + i * 40 + 8) for i in range(nsec)]

    def off(r):
        for vsize, va, rawsize, raw in secs:
            if va <= r < va + max(vsize, rawsize):
                return raw + r - va
        raise ValueError(hex(r))
    count, names = struct.unpack_from("<I", data, off(rva) + 24)[0], struct.unpack_from("<I", data, off(rva) + 32)[0]
    out = set()
    for i in range(count):
        s = off(struct.unpack_from("<I", data, off(names) + 4 * i)[0])
        out.add(data[s:data.index(b"\0", s)].decode("ascii"))
    return out


def bmp(path):
    """(w, h, pixel(x, y) -> (r, g, b)) of a 24-bit bottom-up BMP."""
    data = pathlib.Path(path).read_bytes()
    off, w, h = struct.unpack_from("<I", data, 10)[0], *struct.unpack_from("<ii", data, 18)
    row = (w * 3 + 3) & ~3
    return w, h, lambda x, y: tuple(data[off + (h - 1 - y) * row + x * 3 + 2 - c] for c in range(3))


@unittest.skipUnless(os.name == "nt" and have_vc(), "no 64-bit Visual Studio tools")
class NativeGame(unittest.TestCase):
    exe = None

    @classmethod
    def setUpClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        cls.exe = OUT / "native_dummy.exe"
        if build_native.run(FIXTURE, cls.exe) != 0:
            raise AssertionError("the dummy did not build (see the output above)")

    def run_dummy(self, *args, timeout=120):
        d = pathlib.Path(tempfile.mkdtemp(dir=OUT))
        r = subprocess.run([str(self.exe), "--headless", "--warp", "--fixed-clock", "1", "--no-dialog", "--log", str(d / "run.log"),
                            "--seconds", "60", *[a.replace("{d}", str(d)) for a in args]], cwd=d, capture_output=True,
                           text=True, timeout=timeout)
        log = (d / "run.log").read_text(encoding="utf-8", errors="replace") if (d / "run.log").exists() else ""
        return d, r.returncode, log

    def tearDown(self):
        for d in OUT.glob("tmp*"):
            shutil.rmtree(d, ignore_errors=True)

    def test_picture_log_and_copy(self):
        d, code, log = self.run_dummy("--frames", "13", "--shot", "10,{d}\\f10.bmp", "--shot", "11,{d}\\f11.bmp",
                                      "--game-log", "on", "--input", "FIRE@3+2", "--scene-dump-at", "5", "--scene-dir", "{d}\\dumps")
        self.assertEqual(code, 0, log[-2000:])
        for line in ("gfx: the game's own Direct3D 11 device", "gfx: drawing on ", "gfx: output 640x480, frame 640x480 (headless)",
                     "native: frame 10 (tick 10) written to", "dummy: FIRE pressed (1)", "exit: frame limit (0) after 12 ticks"):
            self.assertIn(line, log)
        quads = {(160, 120): (255, 0, 0), (480, 120): (0, 255, 0), (160, 360): (0, 0, 255), (480, 360): (255, 255, 255)}
        for name, yellow, blue in (("f10", 57, 89), ("f11", 89, 57)):  # drawn straight (even) and handed over (odd)
            w, h, px = bmp(d / f"{name}.bmp")
            self.assertEqual((w, h), (640, 480))
            for (x, y), rgb in quads.items():
                self.assertEqual(px(x, y), rgb, (name, x, y))
            self.assertEqual((px(yellow, 240), px(blue, 240)), ((255, 255, 0), (0, 0, 255)), name)
        dumps = sorted((d / "dumps").glob("*.json"))
        self.assertTrue(dumps, "no Copy agent prompt dump")
        scene = dumps[0].read_text(encoding="utf-8")
        self.assertIn('"dummy_x": 20', scene)
        self.assertTrue(dumps[0].with_suffix(".bmp").exists())
        json.loads(scene)

    def test_gpu_exports(self):
        # hybrid laptops draw on the discrete GPU: every Core-built exe exports both (port/base/host_main.cpp)
        names = pe_exports(self.exe)
        self.assertIn("NvOptimusEnablement", names)
        self.assertIn("AmdPowerXpressRequestHighPerformance", names)

    def test_crash(self):
        d, code, log = self.run_dummy("--fault-at", "5", "--game-log", "on")
        self.assertNotEqual(code, 0)
        self.assertIn("fault-at: an integer divide by zero at vertical blank 5", log)
        self.assertIn("CRASH:", log)

    def test_menu_command_and_submenu(self):
        # Game > Dummy reset (a command) and Game > Dummy jump > Third (a submenu), picked as a click on them does
        d, code, log = self.run_dummy("--frames", "40", "--game-log", "on", "--menu-pick", "10:Game>Dummy reset",
                                      "--menu-pick", "20:game>dummy JUMP>Third", "--menu-pick", "30:Game>Dummy jump>Fourth")
        self.assertEqual(code, 0, log[-2000:])
        for line in ("menu: Game > Dummy reset (--menu-pick)", "dummy: reset", "menu: game > dummy JUMP > Third (--menu-pick)",
                     "dummy: jump to Third", "menu: --menu-pick Game > Dummy jump > Fourth: no such entry"):
            self.assertIn(line, log)
        # 40 frames, one tick each: reset at 10 (x 0), jump at 20 (x 320), then 19 or 20 ticks of 4
        self.assertRegex(log, r"dummy: end \(frame limit, 0\) at x 39[26]")

    def test_install_own(self):
        # the launcher's --setup-only and --verify-assets for a game that installs its own data (console_install)
        d = pathlib.Path(tempfile.mkdtemp(dir=OUT))
        (d / "disc.iso").write_text("DUMMY DISC\n", encoding="ascii")
        r = subprocess.run([str(self.exe), "--setup-only", "--no-dialog", "--data-directory", str(d / "work"), "--disc",
                            str(d / "disc.iso")], cwd=d, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((d / "work" / "assets" / "dummy" / "data.txt").read_text(encoding="ascii"), "DUMMY DISC\n")
        inst = json.loads((d / "work" / "installation.json").read_text(encoding="utf-8"))
        self.assertEqual(inst["assets"], "assets/dummy")
        r = subprocess.run([str(self.exe), "--verify-assets", "--no-dialog", "--assets", str(d / "work" / "assets" / "dummy")],
                           cwd=d, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([str(self.exe), "--setup-only", "--no-dialog", "--data-directory", str(d / "work2"), "--disc",
                            str(d / "missing.iso")], cwd=d, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 4)
        self.assertIn("error: the disc image", r.stderr)

    def test_launcher_json_settings_untouched(self):
        # A play run given a launcher's JSON settings file (catalogue settingsFile) keeps the Core's settings where every
        # game on the Core keeps them, data\settings.txt, and leaves the JSON byte for byte as it was.
        d = pathlib.Path(tempfile.mkdtemp(dir=OUT))
        doc = d / "settings.json"
        doc.write_bytes(b'{\n  "frame_rate": {"cap": 144},\n  "fullscreen": false\n}\n')
        before = doc.read_bytes()
        r = subprocess.run([str(self.exe), "--no-window", "--warp", "--no-dialog", "--data-directory", str(d / "data"),
                            "--log", str(d / "run.log"), "--settings", str(doc), "--frames", "30", "--menu-pick",
                            "10:Game>Dummy reset"], cwd=d, capture_output=True, text=True, timeout=120)
        log = (d / "run.log").read_text(encoding="utf-8", errors="replace") if (d / "run.log").exists() else ""
        self.assertEqual(r.returncode, 0, log[-2000:])
        self.assertIn("dummy: reset", log)
        self.assertEqual(doc.read_bytes(), before, "the launcher's JSON was written")
        own = d / "data" / "settings.txt"
        self.assertFalse((d / "core-settings.txt").exists())
        self.assertTrue(own.exists(), log[-2000:])
        self.assertIn("game.dummy-resets=1", own.read_text(encoding="utf-8"))

    def test_launcher_json_settings_missing(self):
        # A fresh install: the catalogue's settings.json does not exist yet. The Core still never makes it (the older
        # build, picked again to go back, could not load key=value lines) and keeps data\settings.txt.
        d = pathlib.Path(tempfile.mkdtemp(dir=OUT))
        doc = d / "settings.json"
        r = subprocess.run([str(self.exe), "--no-window", "--warp", "--no-dialog", "--data-directory", str(d / "data"),
                            "--log", str(d / "run.log"), "--settings", str(doc), "--frames", "30", "--menu-pick",
                            "10:Game>Dummy reset"], cwd=d, capture_output=True, text=True, timeout=120)
        log = (d / "run.log").read_text(encoding="utf-8", errors="replace") if (d / "run.log").exists() else ""
        self.assertEqual(r.returncode, 0, log[-2000:])
        self.assertFalse(doc.exists(), "the launcher's JSON was made")
        self.assertIn("game.dummy-resets=1", (d / "data" / "settings.txt").read_text(encoding="utf-8"))

    def make_ms(self, log):
        m = re.search(r"make_ms ([0-9.]+)/([0-9.]+)/([0-9.]+)", log)
        self.assertTrue(m, log[-2000:])
        return tuple(float(x) for x in m.groups())

    def test_make_holds_the_native_draw(self):
        # A native frame has no wait for a blank: the time from pg_native_frame to pg_native_present is make time. A 3 ms
        # draw shows in make p50 (before the fix every native draw was booked inside the tick: make was the tick alone).
        d, code, log = self.run_dummy("--frames", "60", "--play-summary", "on", "--dummy-draw-us", "3000")
        self.assertEqual(code, 0, log[-2000:])
        p50, p99, worst = self.make_ms(log)
        self.assertGreaterEqual(p50, 3.0, log[-1500:])
        self.assertGreaterEqual(p99, 3.0)

    def test_make_counts_a_tick_inside_the_frame_once(self):
        # Ticks between pg_native_frame and pg_native_present (Phantom Crash's loop): the span holds them, so make is
        # tick + draw (2 + 3 ms), not 7 (counted twice) and not 2 (the draw left out).
        d, code, log = self.run_dummy("--frames", "60", "--play-summary", "on", "--dummy-draw-us", "3000",
                                      "--dummy-tick-us", "2000", "--dummy-tick-inside", "1")
        self.assertEqual(code, 0, log[-2000:])
        p50, p99, worst = self.make_ms(log)
        self.assertGreaterEqual(p50, 5.0, log[-1500:])
        self.assertLess(p50, 6.5, log[-1500:])

    def test_make_adds_a_tick_before_the_frame(self):
        # Ticks before pg_native_frame (NHL Hitz's and Deathrow's loops): make is the tick plus the frame's span.
        d, code, log = self.run_dummy("--frames", "60", "--play-summary", "on", "--dummy-draw-us", "3000",
                                      "--dummy-tick-us", "2000")
        self.assertEqual(code, 0, log[-2000:])
        p50, p99, worst = self.make_ms(log)
        self.assertGreaterEqual(p50, 5.0, log[-1500:])
        self.assertLess(p50, 6.5, log[-1500:])

    def test_draw_split(self):
        # PG_DRAW_SPLIT logs the frame's spans at exit; unset, no line.
        env = dict(os.environ, PG_DRAW_SPLIT="1")
        d = pathlib.Path(tempfile.mkdtemp(dir=OUT))
        r = subprocess.run([str(self.exe), "--headless", "--warp", "--fixed-clock", "1", "--no-dialog", "--log", str(d / "run.log"),
                            "--seconds", "60", "--frames", "40", "--dummy-draw-us", "2000"], cwd=d, capture_output=True,
                           text=True, timeout=120, env=env)
        log = (d / "run.log").read_text(encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, log[-2000:])
        m = re.search(r"draw split: \d+ frames \(p50/p99/worst ms\) game ([0-9.]+)/.* copy .* present .* gpu_frame .* "
                      r"gpu_present .* outside .* frame .*; gpu samples (\d+)", log)
        self.assertTrue(m, log[-2000:])
        self.assertGreaterEqual(float(m.group(1)), 2.0)
        self.assertGreater(int(m.group(2)), 0)
        d, code, log = self.run_dummy("--frames", "10")
        self.assertNotIn("draw split:", log)

    def test_game_ends_itself(self):
        d, code, log = self.run_dummy("--dummy-ticks", "20")
        self.assertEqual(code, 0, log[-2000:])
        self.assertIn("dummy: end (the game ended, 0) at x 80", log)

    # ---- the game's own text field, mouse and menu commands (native_game.h, contract 2.17.0) ----
    def record_and_replay(self, game, live, frames=60, dump_at=56, shot=50):
        """A run with the game's options and the live input (injected, scripted, menu picks), its recording dumped at
        dump_at, then a replay of it with the game's options alone: (log, frame hash, recording bytes) of each."""
        out = []
        for args in ([*game, *live, "--scene-dump-at", str(dump_at), "--scene-dir", "{d}\\dumps"], None):
            if args is None:
                args = [*game, "--replay", str(rec)]
            d, code, log = self.run_dummy("--frames", str(frames), "--game-log", "on", "--shot", f"{shot},{{d}}\\f.bmp", *args)
            self.assertEqual(code, 0, log[-2000:])
            if not out:
                recs = sorted((d / "dumps").glob("*.ptrec"))
                self.assertTrue(recs, "no recording dumped")
                rec = recs[0]
            out.append((log, hashlib.sha256((d / "f.bmp").read_bytes()).hexdigest(), rec.read_bytes()))
        return out

    def test_text_typed_and_replayed(self):
        (log, shot, rec), (rlog, rshot, _) = self.record_and_replay(
            ["--dummy-text", "1"], ["--kbm-inject", r"TEXT=héllo\b\n@20"])
        self.assertIn('dummy: text submitted "héll"', log)
        self.assertIn(b"events1", rec[:4096], "the header's mark=events1")
        self.assertIn('dummy: text submitted "héll"', rlog)
        self.assertIn("session: replay uses text/mouse events", rlog)
        self.assertEqual(shot, rshot, "the replay's frame differs")

    def test_text_field_keeps_the_keys_not_the_script(self):
        d, code, log = self.run_dummy("--frames", "40", "--game-log", "on", "--kbm-inject", "Z@10", "--input", "FIRE@20+2")
        self.assertIn("dummy: FIRE pressed (2)", log)  # field shut: Z is A, and the script's A
        d, code, log = self.run_dummy("--frames", "40", "--game-log", "on", "--dummy-text", "1", "--kbm-inject", "Z@10",
                                      "--input", "FIRE@20+2")
        self.assertEqual(code, 0, log[-2000:])
        self.assertIn("dummy: FIRE pressed (1)", log)
        self.assertNotIn("dummy: FIRE pressed (2)", log)  # the field open: Z pressed nothing, the script's A did

    def test_mouse_look_and_focus(self):
        (log, shot, rec), (rlog, rshot, _) = self.record_and_replay(
            ["--dummy-mouse", "look"],
            ["--kbm-mouseleft", "a", "--kbm-inject", "MOUSEMOVE=10:-4@30+3,MOUSELEFT@36+2,FOCUSLOST@40,MOUSEMOVE=5:5@44+2,MOUSELEFT@47+2"])
        self.assertIn("dummy: mouse 30, -12, 1 left presses, captured 0", log)
        self.assertNotIn("dummy: FIRE pressed", log)  # the layout's left button (bound to A) presses nothing
        self.assertIn("input: the game's mouse let go", log)
        self.assertIn("dummy: mouse 30, -12, 1 left presses, captured 0", rlog)
        self.assertIn("session: replay uses text/mouse events", rlog)
        self.assertEqual(shot, rshot)

    def test_runtime_list_picked_by_path(self):
        (log, shot, rec), (rlog, rshot, _) = self.record_and_replay(
            [], ["--menu-pick", "20:Game>Dummy level>17-20>Level 20", "--menu-pick", "30:Game>Dummy level>Level 3",
                 "--menu-pick", "40:Game>Dummy level>Level 21"])
        for line in ("menu: Game > Dummy level > 17-20 > Level 20 (--menu-pick)", "dummy: menu tag 7 entry 19",
                     "menu: Game > Dummy level > Level 3 (--menu-pick)", "dummy: menu tag 7 entry 2",
                     "menu: --menu-pick Game > Dummy level > Level 21: no such entry"):
            self.assertIn(line, log)
        # taken at the next tick: the pick's log line comes before the game's
        self.assertLess(log.index("17-20 > Level 20 (--menu-pick)"), log.index("dummy: menu tag 7 entry 19"))
        self.assertNotIn("--menu-pick", rlog)
        for line in ("dummy: menu tag 7 entry 19", "dummy: menu tag 7 entry 2", "session: replay uses text/mouse events"):
            self.assertIn(line, rlog)
        self.assertEqual(shot, rshot)

    def test_five_section_recording_replays_as_before(self):
        # a run with player 2 and nothing of the game's own: the five-section file, no EVENTS, no events1 mark
        (log, shot, rec), (rlog, rshot, _) = self.record_and_replay([], ["--input", "FIRE@10+2,P2.A@12+2"])
        self.assertNotIn(b"events1", rec[:4096])
        self.assertNotIn("replay uses text/mouse events", rlog)
        self.assertIn("dummy: FIRE pressed (1)", rlog)
        self.assertEqual(shot, rshot)
        # and one an older kit wrote (1469f100e's dummy, the same arguments), when the fixture is there
        old = FIXTURE / "five_section_1469f100e.ptrec"
        if old.exists():
            want = json.loads(old.with_suffix(".json").read_text(encoding="utf-8"))
            d, code, log = self.run_dummy("--frames", "60", "--game-log", "on", "--shot", "50,{d}\\f.bmp", "--replay", str(old))
            self.assertEqual(code, 0, log[-2000:])
            self.assertEqual(hashlib.sha256((d / "f.bmp").read_bytes()).hexdigest(), want["shot"])
            self.assertIn(want["line"], log)
            self.assertNotIn("replay uses text/mouse events", log)


if __name__ == "__main__":
    unittest.main()

"""Old agent builds under <game>/build (port/tools/build_gc, run by build.py when it exits).
1. a configured build of the kit (CMakeCache.txt with PORT_GEN NAME_gen, build.ninja) unused for 5 days goes with its
   NAME_gen, NAME_run, NAME_regress, NAME_lockstep and NAME.log; a fresh one stays;
2. fresh means a file written inside it, or its .used stamp, even when the folder's own date is old;
3. x86, x86chk, gen, run, regress and reference stay however old, and so does the build build.py is running;
4. a folder that is not confirmed as a build stays: no CMakeCache, another PORT_GEN, a file;
5. a running process keeps its build (working folder, or --build-dir NAME on its command line), and an open file
   keeps it too (the rename is refused); both go once the process or the file is gone;
6. a junction inside a build is removed as a link: what it points at is untouched;
7. dry_run removes nothing; maybe_sweep runs once per interval, says one line, is off with DP_BUILD_GC=0, never
   raises, and build.py wires it in (--no-build-gc, not with --progress).
Synthetic folders. Run: python -W error tests/test_build_gc.py.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "port" / "tools"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

import build_gc  # noqa: E402

DAY = 86400.0
BUILD_PY = pathlib.Path(__file__).resolve().parents[1] / "port" / "tools" / "build.py"


def age(path, days, now):
    t = now - days * DAY
    path = pathlib.Path(path)
    for p in ([path] + list(path.rglob("*")) if path.is_dir() else [path]):
        os.utime(p, (t, t))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.build = pathlib.Path(self.tmp.name) / "port" / "build"
        self.build.mkdir(parents=True)
        self.now = time.time()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        self.tmp.cleanup()

    def make(self, name, days, gen=None, companions=("_gen", "_run", "_regress", "_lockstep"), log=True, cache=True):
        """build/NAME as a configured build, written `days` days ago, with companions."""
        d = self.build / name
        (d / "CMakeFiles").mkdir(parents=True)
        (d / "port.exe").write_bytes(b"x" * 1000)
        (d / "build.ninja").write_text("rule x\n")
        if cache:
            g = gen or f"{name}_gen"
            (d / "CMakeCache.txt").write_text(f"PORT_CHECKED:BOOL=OFF\nPORT_GEN:PATH=C:/somewhere/build/{g}\n")
        for s in companions:
            (self.build / (name + s)).mkdir()
            (self.build / (name + s) / "f.txt").write_bytes(b"y" * 100)
        if log:
            (self.build / f"{name}.log").write_text("log")
        age(d, days, self.now)
        for s in companions:
            age(self.build / (name + s), days, self.now)
        if log:
            age(self.build / f"{name}.log", days, self.now)
        return d

    def sweep(self, **kw):
        return build_gc.sweep(self.build, now=self.now, **kw)

    def left(self):
        return sorted(p.name for p in self.build.iterdir())


class Sweep(Case):
    def test_old_build_goes_with_its_companions_and_a_fresh_one_stays(self):
        self.make("x86-old", 6)
        self.make("x86-new", 1)
        done = self.sweep()
        self.assertEqual([n for n, _ in done], ["x86-old"])
        self.assertGreater(done[0][1], 1000)
        self.assertEqual(self.left(), ["x86-new", "x86-new.log", "x86-new_gen", "x86-new_lockstep", "x86-new_regress",
                                       "x86-new_run"])

    def test_a_fresh_file_inside_or_the_stamp_keeps_an_old_looking_build(self):
        a, b = self.make("x86-a", 9), self.make("x86-b", 9)
        (a / "CMakeFiles" / "new.obj").write_bytes(b"z")  # written now, the folder's date stays old
        os.utime(a, (self.now - 9 * DAY,) * 2)
        build_gc.touch_used(b)
        self.assertEqual(self.sweep(), [])
        self.assertIn("x86-a", self.left())
        self.assertIn("x86-b", self.left())

    def test_a_fresh_companion_keeps_the_build(self):
        self.make("x86-a", 9)
        (self.build / "x86-a_run" / "port.log").write_text("now")
        self.assertEqual(self.sweep(), [])

    def test_protected_names_and_the_running_build_stay(self):
        for n in ("x86", "x86chk", "gen", "run", "regress", "reference", "x86-cur"):
            self.make(n, 30)
        done = self.sweep(keep={"x86-cur"})
        self.assertEqual(done, [])
        self.assertTrue((self.build / "reference").is_dir() and (self.build / "x86-cur").is_dir())

    def test_what_is_not_confirmed_as_a_build_stays(self):
        (self.build / "look").mkdir()
        (self.build / "look" / "pic.png").write_bytes(b"p")
        self.make("nocache", 30, cache=False)
        self.make("other", 30, gen="elsewhere_gen")
        (self.build / "notes.txt").write_text("n")
        for p in ("look", "notes.txt"):
            age(self.build / p, 30, self.now)
        self.assertEqual(self.sweep(), [])
        self.assertEqual(self.left().count("nocache"), 1)
        self.assertTrue((self.build / "other").is_dir() and (self.build / "look").is_dir())

    def test_dry_run_removes_nothing(self):
        self.make("x86-old", 6)
        before = self.left()
        self.assertEqual([n for n, _ in self.sweep(dry_run=True)], ["x86-old"])
        self.assertEqual(self.left(), before)

    def test_days_decide(self):
        self.make("x86-a", 3)
        self.assertEqual(self.sweep(days=5), [])
        self.assertEqual([n for n, _ in self.sweep(days=2)], ["x86-a"])

    def test_a_junction_is_removed_as_a_link_and_its_target_stays(self):
        if os.name != "nt":
            self.skipTest("junctions are Windows")
        target = pathlib.Path(self.tmp.name) / "outside"
        target.mkdir()
        (target / "precious.txt").write_text("keep")
        d = self.make("x86-old", 8)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(d / "link"), str(target)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        age(target / "precious.txt", 0, self.now)
        self.assertEqual([n for n, _ in self.sweep()], ["x86-old"])
        self.assertFalse((self.build / "x86-old").exists())
        self.assertEqual((target / "precious.txt").read_text(), "keep")


class InUse(Case):
    def spawn(self, cwd, *extra):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *extra], cwd=cwd)
        self.procs.append(p)
        time.sleep(0.5)
        return p

    def test_a_process_working_in_a_build_keeps_it_until_it_ends(self):
        d = self.make("x86-busy", 8)
        p = self.spawn(d / "CMakeFiles")
        self.assertEqual(self.sweep(), [])
        self.assertTrue(d.is_dir())
        p.kill()
        p.wait()
        self.assertEqual([n for n, _ in self.sweep()], ["x86-busy"])

    def test_a_command_line_naming_the_build_keeps_it(self):
        d = self.make("x86-named", 8)
        self.spawn(self.tmp.name, "--build-dir", "x86-named")
        self.assertEqual(self.sweep(), [])
        self.assertTrue(d.is_dir())

    def test_a_longer_name_does_not_keep_a_shorter_one(self):
        self.make("x86-a", 8)
        self.spawn(self.tmp.name, "--build-dir", "x86-a2")
        self.assertEqual([n for n, _ in self.sweep()], ["x86-a"])

    @unittest.skipUnless(os.name == "nt", "Windows refuses to rename a folder with an open file")
    def test_an_open_file_keeps_the_build(self):
        d = self.make("x86-open", 8)
        with open(d / "port.exe", "rb"):
            self.assertEqual(self.sweep(), [])
            self.assertTrue(d.is_dir())
            self.assertTrue((d / "port.exe").is_file())
            self.assertFalse((self.build / ".gc-x86-open").exists())
        self.assertEqual([n for n, _ in self.sweep()], ["x86-open"])


class Wiring(Case):
    def test_maybe_sweep_once_per_interval_with_one_line(self):
        self.make("x86-old", 8)
        said = []
        with mock.patch.dict(os.environ, {"DP_BUILD_GC": "1"}):
            build_gc.maybe_sweep(self.build, {"x86"}, say=said.append)
            self.assertEqual(len(said), 1)
            self.assertIn("removed 1 agent build folder", said[0])
            self.assertIn("freed", said[0])
            self.make("x86-old2", 8)
            build_gc.maybe_sweep(self.build, {"x86"}, say=said.append)
        self.assertEqual(len(said), 1)  # inside the interval: nothing ran
        self.assertTrue((self.build / "x86-old2").is_dir())

    def test_nothing_removed_says_nothing(self):
        self.make("x86-new", 1)
        said = []
        build_gc.maybe_sweep(self.build, say=said.append)
        self.assertEqual(said, [])

    def test_off_switch(self):
        self.make("x86-old", 8)
        with mock.patch.dict(os.environ, {"DP_BUILD_GC": "0"}):
            build_gc.maybe_sweep(self.build)
        self.assertTrue((self.build / "x86-old").is_dir())

    def test_it_never_raises(self):
        said = []
        with mock.patch.object(build_gc, "sweep", side_effect=RuntimeError("boom")):
            build_gc.maybe_sweep(self.build, say=said.append)
        self.assertEqual(len(said), 1)
        self.assertIn("skipped", said[0])

    def test_a_missing_build_folder_is_fine(self):
        build_gc.maybe_sweep(self.build / "nope")
        self.assertEqual(build_gc.sweep(self.build / "nope"), [])

    def test_build_py_wires_it_in(self):
        text = BUILD_PY.read_text(encoding="utf-8")
        self.assertIn("--no-build-gc", text)
        self.assertIn("if not a.progress and not a.no_build_gc:", text)
        self.assertIn("atexit.register(build_gc.maybe_sweep, build, {bdir.name})", text)
        self.assertIn("build_gc.touch_used(bdir)", text)
        r = subprocess.run([sys.executable, str(BUILD_PY), "--help"], capture_output=True, text=True, timeout=120)
        self.assertIn("--no-build-gc", r.stdout)

    def test_the_kit_file_holds_no_game(self):
        text = pathlib.Path(build_gc.__file__).read_text(encoding="utf-8")
        for name in ("Toy", "Spawn", "Power", "Vigilante", "Lodoss"):
            self.assertNotIn(name, text)


if __name__ == "__main__":
    unittest.main()

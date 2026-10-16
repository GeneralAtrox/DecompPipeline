"""Fixtures for the header lint (core/header_lint.py, refine.py header) and the readable runs' validated header
snapshots (core/header_snapshot.py). Synthetic workspaces in a temp folder; no Ghidra or agent. The compiler is
faked except in Compile, which builds game.h for real when Visual Studio is installed (skipped otherwise).

usage: python tests/test_headers.py   (python tests/test_tooling.py runs these too)
"""
import atexit
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
_TMP = tempfile.TemporaryDirectory(prefix="headers_", dir=TOOL / "tests" / ".work")
TMP = pathlib.Path(_TMP.name)
PROJECT = {"target": "dreamcast_sh4", "ghidra_name": "fixture", "binary": "game.bin"}
if not (pathlib.Path(os.environ.get("DECOMP_PROJECT", "")) / "project.json").exists():  # alone: a project for paths
    (TMP / "project.json").write_text(json.dumps(PROJECT), encoding="utf-8")
    os.environ["DECOMP_PROJECT"] = str(TMP)
if str(TOOL) not in sys.path:
    sys.path.insert(0, str(TOOL))

from core import header_lint, header_snapshot, paths, readable, refine  # noqa: E402
from core import readable_check as rcheck  # noqa: E402

atexit.register(_TMP.cleanup)

ADDR = "8c010000"
TYPES = '#ifndef TYPES_H\n#define TYPES_H\n\n#include "sys/a.h"\n#include "sys/b.h"\n\n#endif\n'
A_H = "#ifndef A_H\n#define A_H\n\n/* Values of a thing. */\n#define FOO_A 1u\n#define FOO_B 2u\n\n#endif\n"
B_H = ("#ifndef B_H\n#define B_H\n\nstruct Later;\n\n/* A bar. */\ntypedef struct Bar {\n    u32 x;\n} Bar;\n\n"
       "typedef enum Mode {\n    MODE_IDLE,\n    MODE_RUN\n} Mode;\n\n#endif\n")
NAMES = {"functions": {ADDR: {"proto": "u32 Foo_Get(void)"}}, "globals": {"8c0c0000": {"decl": "u32 g_count"}}}
OK = lambda cfg, inc, bdir: []  # noqa: E731  (the faked game.h build: it builds)


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")


def make_ws(a_h=A_H, b_h=B_H):
    ws = pathlib.Path(tempfile.mkdtemp(prefix="ws", dir=TMP))
    put(ws / "project.json", PROJECT)
    put(ws / "readable/include/types.h", TYPES)
    put(ws / "readable/include/sys/a.h", a_h)
    put(ws / "readable/include/sys/b.h", b_h)
    put(ws / "readable/names/S01.json", NAMES)
    return ws


class WsCase(unittest.TestCase):
    """Points the readable stage and the snapshot store at a fresh workspace (and back afterwards)."""

    def setUp(self):
        self.ws = make_ws()
        rd = self.ws / "readable"
        self.saved = (readable.READABLE, readable.INCLUDE, readable.NAMES, header_snapshot.SNAPS,
                      header_snapshot.GOOD, header_snapshot.ACTIVE)
        readable.READABLE, readable.INCLUDE, readable.NAMES = rd, rd / "include", rd / "names"
        header_snapshot.SNAPS = self.ws / "state" / "readable_snapshots"
        header_snapshot.GOOD = header_snapshot.SNAPS / "last_good.json"
        header_snapshot.ACTIVE = None

    def tearDown(self):
        (readable.READABLE, readable.INCLUDE, readable.NAMES, header_snapshot.SNAPS, header_snapshot.GOOD,
         header_snapshot.ACTIVE) = self.saved

    def live(self, rel):
        return self.ws / "readable" / "include" / rel


class Lint(unittest.TestCase):
    def lint(self, a_h=A_H, b_h=B_H):
        return header_lint.lint(make_ws(a_h, b_h) / "readable" / "include")

    def test_clean(self):
        self.assertEqual(self.lint(), [])

    def test_duplicate_macro_names_both(self):
        p = self.lint(b_h=B_H.replace("#endif", "#define FOO_A 3u\n\n#endif"))
        self.assertEqual(len(p), 1, p)
        self.assertIn("macro FOO_A", p[0])
        self.assertIn("sys/a.h:5", p[0])
        self.assertIn("sys/b.h:16", p[0])
        self.assertIn("different definitions", p[0])

    def test_identical_redefinition_fails(self):
        p = self.lint(b_h=B_H.replace("#endif", "#define FOO_B 2u /* the same */\n\n#endif"))
        self.assertEqual(len(p), 1, p)
        self.assertIn("identical copies", p[0])

    def test_types_tags_constants_globals(self):
        extra = ("typedef struct Bar Bar;\n\nstruct Mode {\n    u32 y;\n};\n\n#define MODE_RUN 1\n\n"
                 "extern u32 g_count;\nextern s32 g_count2;\n\n#endif")
        p = "\n".join(self.lint(a_h=A_H.replace("#endif", extra) + "extern u32 g_count2;\n"))
        self.assertIn("typedef Bar is defined 2 times: sys/a.h:", p)  # a typedef in two headers
        self.assertIn("tag Mode is defined 2 times", p)  # struct Mode and enum Mode: one tag namespace
        self.assertIn("name MODE_RUN is defined 2 times", p)  # an enum constant and a macro
        self.assertIn("declaration g_count2 is defined 2 times", p)  # declared differently
        self.assertNotIn("Later", p)  # a forward declaration defines nothing

    def test_guards(self):
        p = self.lint(b_h=B_H.replace("B_H", "A_H"))
        self.assertTrue(any("macro A_H" in x and "sys/a.h:1" in x and "sys/b.h:1" in x for x in p), p)

    def test_header_command_fails_and_names_both(self):
        ws = make_ws(b_h=B_H.replace("#endif", "#define FOO_A 3u\n\n#endif"))
        r = subprocess.run([sys.executable, str(TOOL / "refine.py"), "--project", str(ws), "header"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("sys/a.h:5", r.stdout)
        self.assertIn("sys/b.h:16", r.stdout)
        self.assertTrue((ws / "readable/include/game.h").exists())  # game.h is still written


class Snapshots(WsCase):
    def acquire(self, **kw):
        logs = []
        snap = header_snapshot.acquire({}, log=logs.append, compile_fn=OK, **kw)
        return snap, logs

    def test_good_snapshot(self):
        snap, logs = self.acquire(wait_s=0)
        self.assertEqual(snap.source, "live")
        self.assertIn("u32 Foo_Get(void);", (snap.include / "game.h").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(header_snapshot.GOOD.read_text(encoding="utf-8"))["hash"], snap.hash)
        self.assertEqual(len(snap.hash), 16)
        snap.release()
        self.assertFalse(snap.dir.exists())
        self.assertTrue((header_snapshot.SNAPS / f"good-{snap.hash}" / "HASH").exists())

    def test_broken_live_waits_then_uses_last_good(self):
        good, _ = self.acquire(wait_s=0)
        good.release()
        put(self.live("sys/b.h"), B_H.replace("#endif", "#define FOO_A 3u\n\n#endif"))  # a designer's half-edit
        t0 = time.monotonic()
        snap, logs = self.acquire(wait_s=0.5, retry_s=0.1)
        self.assertGreaterEqual(time.monotonic() - t0, 0.5)
        self.assertEqual((snap.source, snap.hash), ("last good", good.hash))
        self.assertNotIn("3u", (snap.include / "sys" / "b.h").read_text(encoding="utf-8"))
        text = "\n".join(logs)
        self.assertIn("waiting", text)
        self.assertIn("macro FOO_A", text)  # it says why
        self.assertIn(f"last good snapshot {good.hash}", text)
        snap.release()

    def test_no_last_good_exits_clearly(self):
        put(self.live("sys/b.h"), B_H.replace("#endif", "#define FOO_A 3u\n\n#endif"))
        with self.assertRaises(header_snapshot.HeaderError) as e:
            self.acquire(wait_s=0.2, retry_s=0.1)
        self.assertIn("no last good snapshot", str(e.exception))
        self.assertIn("sys/b.h:16", str(e.exception))
        self.assertEqual([d for d in header_snapshot.SNAPS.iterdir() if d.name.startswith("run-")], [])

    def test_verify_does_not_fall_back(self):
        good, _ = self.acquire(wait_s=0)
        good.release()
        put(self.live("sys/a.h"), A_H + "#define FOO_B 9u\n")
        t0 = time.monotonic()
        with self.assertRaises(header_snapshot.HeaderError):
            self.acquire(wait_s=0, fallback=False)
        self.assertLess(time.monotonic() - t0, 5)

    def test_registry_problem_fails(self):
        put(self.ws / "readable/names/S02.json", {"functions": {"8c010100": {"proto": "u32 Foo_Get(void)"}}})
        with self.assertRaises(header_snapshot.HeaderError) as e:
            self.acquire(wait_s=0)
        self.assertIn("names used twice", str(e.exception))

    def test_stamp_note(self):
        n = header_snapshot.stamp_note
        self.assertIn("older headers (aaaa, now bbbb)", n("aaaa", "bbbb"))
        self.assertIn("not a header change", n("bbbb", "bbbb"))
        self.assertIn("no snapshot recorded", n(None, "bbbb"))


class MidRunEdit(WsCase):
    """A designer edits the live headers and names during a run: the run's builds do not change."""

    def test_edit_does_not_reach_the_run(self):
        snap = header_snapshot.acquire({}, log=lambda m: None, wait_s=0, compile_fn=OK)
        header_snapshot.use(snap)
        self.addCleanup(snap.release)
        put(self.live("sys/a.h"), A_H.replace("FOO_A 1u", "FOO_A 7u") + "#define FOO_A 8u\n")  # broken and changed
        put(self.ws / "readable/names/S01.json", {"functions": {ADDR: {"proto": "u32 Foo_Renamed(void)"}}})

        self.assertEqual(readable.INCLUDE, snap.include)
        self.assertEqual(readable.Registry().functions[ADDR]["proto"], "u32 Foo_Get(void)")
        harness = self.ws / "harness" / ADDR
        put(harness / "meta.json", {"signature": f"u32 fn_{ADDR}(void)", "params": [], "callees": [], "globals": []})
        src = self.ws / "work.c"
        put(src, '#include "game.h"\n\n/* The A value. */\nu32 Foo_Get(void)\n{\n    return FOO_A;\n}\n')
        seen = {}

        def fake_build(cfg, sources, thunk, protos, build_dir, include_dirs=()):
            seen["incs"] = [pathlib.Path(d) for d in include_dirs]
            seen["a_h"] = (seen["incs"][-1] / "sys" / "a.h").read_text(encoding="utf-8")
            return False, ["stopped by the test"]

        with mock.patch.object(rcheck, "build", fake_build):
            res = rcheck.check_readable(paths.config(), ADDR, str(src), self.ws / "build" / "x", harness_dir=harness)
        self.assertEqual(res["stage"], "build", res)
        self.assertEqual(seen["incs"][-1], snap.include)
        self.assertIn("FOO_A 1u", seen["a_h"])
        self.assertNotIn("7u", seen["a_h"])
        launcher = refine.check_launcher(ADDR, self.ws / "work.c")
        self.assertIn(f'"--headers", r"{snap.dir}"', launcher)
        compile(launcher, "check.py", "exec")


VCVARS = None
try:
    VCVARS = pathlib.Path(paths.config()["vcvars"])
except Exception:  # noqa: BLE001  (no config: no compiler test)
    pass


@unittest.skipUnless(VCVARS and VCVARS.exists(), "Visual Studio not installed")
class Compile(WsCase):
    def test_game_h_must_build(self):
        put(self.live("sys/b.h"), B_H.replace("u32 x;", "u32 x"))  # a syntax error the lint does not see
        snap, problems = header_snapshot.take(paths.config())
        self.addCleanup(snap.release)
        self.assertTrue(problems and all("game.h does not build" in p for p in problems), problems)
        self.assertIn("b.h", "\n".join(problems))
        put(self.live("sys/b.h"), B_H)
        snap2, problems = header_snapshot.take(paths.config())
        self.addCleanup(snap2.release)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)

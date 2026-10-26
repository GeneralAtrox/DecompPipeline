"""The judge's build links the runtime compiled once per content (check.runtime_objs), and run_bounded returns
when its command ends instead of at the next 0.2 s sleep.
1. run_bounded never sleeps, still kills a command at its timeout and still honours the stop flag;
2. runtime_objs: compiled once, then reused without a compile; another runtime content gets another set; a runtime
   that does not compile gives None, and the build then compiles the runtime sources itself as before (wiring);
3. with Visual Studio's tools (config.local.json "vcvars"): a real build links the cached objects and its runner
   is the full build's in behaviour (the same listing of work.c).
Synthetic sources. Run: python -W error tests/test_prebuilt_runtime.py (also from test_tooling.py).
"""
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import check, paths  # noqa: E402

PY = [sys.executable, "-I", "-c"]


class RunBounded(unittest.TestCase):
    def test_no_sleep_and_the_limits_hold(self):
        with mock.patch.object(paths.time, "sleep", side_effect=AssertionError("slept")):
            rc, out, why = paths.run_bounded(PY + ["print('hi')"], 30)
            self.assertEqual((rc, out.strip(), why), (0, "hi", None))
            t = time.monotonic()
            rc, _, why = paths.run_bounded(PY + ["import time; time.sleep(30)"], 1)
            self.assertEqual((rc, why), (None, "timeout"))
            self.assertLess(time.monotonic() - t, 10)
            stop = threading.Event()
            threading.Timer(0.5, stop.set).start()
            rc, _, why = paths.run_bounded(PY + ["import time; time.sleep(30)"], 30, stop_flag=stop)
            self.assertEqual((rc, why), (None, "stopped"))


class RuntimeObjs(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.rt = self.d / "runtime"
        shutil.copytree(paths.RUNTIME, self.rt)
        self.cl = self.d / "cl.exe"
        self.cl.write_bytes(b"x")
        self.calls = []

    def fake_cl(self, ok=True):
        def run(cmd, timeout, cwd=None, env=None, **k):
            self.calls.append(cmd)
            if ok:
                for s in check.RUNTIME_SRCS:
                    (pathlib.Path(cwd) / f"{s[:-2]}.obj").write_bytes(b"obj")
            return (0 if ok else 2), "", None
        return run

    def objs(self, ok=True):
        with mock.patch.object(check.paths, "RUNTIME", self.rt), mock.patch.object(check.paths, "BUILD", self.d / "b"), \
                mock.patch.object(check.paths, "run_bounded", self.fake_cl(ok)):
            return check.runtime_objs(str(self.cl), {"INCLUDE": "inc"})

    def test_once_then_reused_and_keyed_by_content(self):
        first = self.objs()
        self.assertEqual([o.name for o in first], ["runner.obj", "tc_rt.obj", "tc_fpu.obj", "tc_p4.obj"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.objs(), first)
        self.assertEqual(len(self.calls), 1)  # reused: no second compile
        (self.rt / "tc_rt.h").write_text((self.rt / "tc_rt.h").read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertNotEqual(self.objs()[0].parent, first[0].parent)  # another header: another set
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(sorted(p.name for p in (self.d / "b" / "runtime_obj").iterdir()),
                         sorted({first[0].parent.name, self.objs()[0].parent.name}))  # no temp folder left

    def test_runtime_that_does_not_compile_builds_as_before(self):
        self.assertIsNone(self.objs(ok=False))
        self.assertFalse(any((self.d / "b" / "runtime_obj").iterdir()))
        meta = {"name": "fn_8c010000", "signature": "void fn_8c010000(void)", "params": [], "ret": "void",
                "callees": [], "globals": []}
        work = self.d / "work.c"
        work.write_text('#include "tc_rt.h"\nvoid fn_8c010000(void) {}\n', encoding="utf-8")
        seen = []

        def run(cmd, timeout, cwd=None, **k):
            seen.append(cmd)
            (pathlib.Path(cwd) / "work.asm").write_text("", encoding="utf-8")
            return 0, "", None
        with mock.patch.object(check, "msvc_env", lambda cfg: {"PATH": ""}), \
                mock.patch.object(shutil, "which", lambda *a, **k: str(self.cl)), \
                mock.patch.object(check, "runtime_objs", lambda cl, env: None), \
                mock.patch.object(check.paths, "run_bounded", run):
            self.assertEqual(check.build({}, meta, work, self.d / "bd"), (True, []))
        self.assertIn("/TC", seen[0])
        self.assertTrue(any(str(c).endswith("tc_rt.c") for c in seen[0]))  # the runtime compiled in the build


VCVARS = paths.config().get("vcvars")


@unittest.skipUnless(os.name == "nt" and VCVARS and pathlib.Path(VCVARS).exists(), "no Visual Studio (config vcvars)")
class RealBuild(unittest.TestCase):
    def test_cached_runtime_build_matches_the_full_build(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        meta = {"name": "fn_8c010000", "signature": "u32 fn_8c010000(u32 param_1)",
                "params": [{"name": "param_1", "kind": "u32", "storage": "r4", "stack_offset": -1}], "ret": "u32",
                "callees": [], "globals": []}
        work = d / "work.c"
        work.write_text('#include "tc_rt.h"\nu32 fn_8c010000(u32 param_1)\n{\n    return param_1 * 3u + 1u;\n}\n',
                        encoding="utf-8")
        cfg = paths.config()
        with mock.patch.object(check.paths, "BUILD", d / "b"):
            env = check.msvc_env(cfg)
            cl = shutil.which("cl.exe", path=next(v for k, v in env.items() if k.upper() == "PATH"))
            with mock.patch.object(check, "runtime_objs", lambda c, e: None):
                ok_full, _ = check.build(cfg, meta, work, d / "full")
            ok, _ = check.build(cfg, meta, work, d / "cached")
            self.assertTrue(ok_full and ok)
            self.assertIsNotNone(check.runtime_objs(cl, env))
            self.assertFalse((d / "cached" / "tc_rt.obj").exists())  # linked from the cache, not compiled here
            listing = lambda p: [ln for ln in p.read_text(errors="replace").splitlines() if str(d) not in ln]  # noqa: E731
            self.assertEqual(listing(d / "full" / "work.asm"), listing(d / "cached" / "work.asm"))
            self.assertTrue((d / "cached" / "runner.exe").is_file())


if __name__ == "__main__":
    unittest.main()

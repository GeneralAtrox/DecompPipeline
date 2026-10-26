"""check.build keeps a clean runner by the key of everything the build reads (_runner_key), so the official judge of
the work.c the agent's last check built (job.py after decomp.py's check) takes that runner instead of compiling it
again. The vectors always run afresh; only the compile and link are skipped.
1. the same inputs in another build folder (agent, then official): built once, the second takes the kept runner;
2. any changed input builds again: the C under test, the thunk (the harness's signature), a runtime file (changed
   or new), the compiler, its INCLUDE or LIB, the runtime objects' source (runtime_objs or the build), the judge's
   build code;
3. a failed build or one with a message is never kept; the folder keeps the latest RUNNERS_KEPT runners;
4. with Visual Studio's tools (config.local.json "vcvars"): two full builds of the same inputs in two folders make
   runners whose code and data sections are equal byte for byte (the folder does not reach the runner), so a kept
   runner is the runner a fresh build would make; and a build of those inputs in a third folder takes it.
Synthetic sources. Run: python -W error tests/test_raw_runner_reuse.py.
"""
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)
from test_prebuilt_runtime_rd import VCVARS, sections  # noqa: E402

from core import check, paths  # noqa: E402

META = {"name": "fn_8c010000", "signature": "u32 fn_8c010000(u32 param_1)",
        "params": [{"name": "param_1", "kind": "u32", "storage": "r4", "stack_offset": -1}], "ret": "u32",
        "callees": [], "globals": []}


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Reuse(unittest.TestCase):
    def setUp(self):
        d = self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.rt, self.core = d / "runtime", d / "core"
        shutil.copytree(paths.RUNTIME, self.rt)
        put(self.core / "check.py", pathlib.Path(check.__file__).read_text(encoding="utf-8"))
        put(d / "bin" / "cl.exe", "cl")
        put(d / "work.c", '#include "tc_rt.h"\nu32 fn_8c010000(u32 param_1) { return param_1; }\n')
        self.env = {"PATH": str(d / "bin"), "INCLUDE": "i", "LIB": "l"}
        self.objs, self.result, self.builds = ["rt.obj"], (True, []), []
        ps = [mock.patch.object(check.paths, "RUNTIME", self.rt),
              mock.patch.object(check, "__file__", str(self.core / "check.py")),
              mock.patch.object(check, "msvc_env", lambda cfg: dict(self.env)),
              mock.patch.object(check, "runtime_objs", lambda cl, env: self.objs),
              mock.patch.object(check, "_build", self.fake_build)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    def fake_build(self, build_dir, env, cl, rt):
        n = len(self.builds) + 1
        self.builds.append(build_dir.name)
        (build_dir / "runner.exe").write_bytes(f"runner {n}".encode())
        return self.result

    def build(self, name, meta=None):
        return check.build({}, meta or META, self.d / "work.c", self.d / "fn" / name)

    def test_agent_then_official(self):
        self.assertEqual(self.build("agent"), (True, []))
        self.assertEqual(self.build("official"), (True, []))
        self.assertEqual(self.builds, ["agent"])  # the official judge took the agent's runner
        self.assertEqual((self.d / "fn" / "official" / "runner.exe").read_bytes(), b"runner 1")
        self.assertTrue((self.d / "fn" / "official" / "work.c").is_file())  # the folder is made as before
        self.assertTrue((self.d / "fn" / "official" / "thunk.c").is_file())

    def test_any_change_builds_again(self):
        changes = [
            lambda: put(self.d / "work.c", '#include "tc_rt.h"\nu32 fn_8c010000(u32 param_1) { return param_1 + 0; }\n'),
            lambda: put(self.rt / "tc_rt.h", (self.rt / "tc_rt.h").read_text(encoding="utf-8") + "\n"),
            lambda: put(self.rt / "sub" / "new.h", "\n"),
            lambda: put(self.d / "bin" / "cl.exe", "another cl"),
            lambda: self.env.update(INCLUDE="i2"),
            lambda: self.env.update(LIB="l2"),
            lambda: setattr(self, "objs", None),
            lambda: put(self.core / "check.py", (self.core / "check.py").read_text(encoding="utf-8") + "\n"),
        ]
        self.build("agent")
        for i, change in enumerate(changes, 2):
            change()
            self.build("official")
            self.assertEqual(len(self.builds), i, i)
        other = {**META, "signature": "u32 fn_8c010000(u32 param_1, u32 param_2)",
                 "params": [*META["params"], {"name": "param_2", "kind": "u32", "storage": "r5", "stack_offset": -1}]}
        self.assertNotEqual(check.gen_thunk(other), check.gen_thunk(META))
        self.build("official", meta=other)
        self.assertEqual(len(self.builds), len(changes) + 2)
        self.build("agent", meta=other)
        self.assertEqual(len(self.builds), len(changes) + 2)  # and the same again is kept

    def test_failed_or_noisy_not_kept(self):
        for result in ((False, ["work.c(2): error C2065"]), (True, ["work.c(2): warning C4100"])):
            self.result = result
            self.assertEqual(self.build("agent"), result)
            self.assertEqual(self.build("official"), result)
        self.assertEqual(len(self.builds), 4)
        self.assertFalse((self.d / "fn" / "raw_runners").exists())

    def test_latest_kept(self):
        for i in range(check.RUNNERS_KEPT + 3):
            put(self.d / "work.c", f"/* {i} */\n")
            self.build("agent")
        kept = [p for p in (self.d / "fn" / "raw_runners").iterdir() if p.is_dir()]
        self.assertEqual(len(kept), check.RUNNERS_KEPT)
        n = len(self.builds)
        self.build("official")  # the latest is still there
        self.assertEqual(len(self.builds), n)


@unittest.skipUnless(os.name == "nt" and VCVARS and pathlib.Path(VCVARS).exists(), "no Visual Studio (config vcvars)")
class RealBuild(unittest.TestCase):
    def test_folder_does_not_reach_the_runner(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        work = d / "work.c"
        work.write_text('#include "tc_rt.h"\nu32 fn_8c010000(u32 param_1)\n{\n    return param_1 * 3u + 1u;\n}\n',
                        encoding="utf-8")
        cfg = paths.config()
        with mock.patch.object(check.paths, "BUILD", d / "b"):
            with mock.patch.object(check, "keep_runner", lambda *a: None):  # two full builds
                a = check.build(cfg, META, work, d / "fn" / "agent")
                b = check.build(cfg, META, work, d / "fn" / "official")
            self.assertEqual((a, b), ((True, []), (True, [])))
            first, second = sections(d / "fn" / "agent" / "runner.exe"), sections(d / "fn" / "official" / "runner.exe")
            self.assertEqual(sorted(first), sorted(second))
            for name in first:
                self.assertEqual(first[name], second[name], name)
            self.assertEqual(check.build(cfg, META, work, d / "fn" / "agent"), (True, []))
            self.assertEqual(check.build(cfg, META, work, d / "fn" / "official"), (True, []))
            self.assertFalse((d / "fn" / "official" / "work.obj").exists())  # the kept runner, not a compile
            self.assertEqual((d / "fn" / "official" / "runner.exe").read_bytes(),
                             (d / "fn" / "agent" / "runner.exe").read_bytes())


if __name__ == "__main__":
    unittest.main()

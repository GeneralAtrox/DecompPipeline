"""readable_check.build keeps a clean runner by the key of everything the build reads (_runner_key), so the official
judge of the work.c the agent's last check built (same header snapshot) takes that runner instead of compiling it
again (finding R1). The vectors always run afresh; only the compile and link are skipped.
1. the same inputs in another build folder (rd_agent, then readable): built once, the second takes the kept runner;
2. any changed input builds again: the C under test, the thunk, the prototype table, a file of an include folder
   (changed or new), the runtime, the link options (readable_image_base), the compiler, the judge's build code;
3. a failed build or one with a message is never kept; the folder keeps the latest RUNNERS_KEPT runners;
4. with Visual Studio's tools (config.local.json "vcvars"): two full builds of the same inputs in two folders make
   runners whose code and data sections are equal byte for byte (the folder does not reach the runner), so a kept
   runner is the runner a fresh build would make.
Synthetic sources. Run: python -W error tests/test_rd_runner_reuse.py.
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

from core import check, paths, readable_check  # noqa: E402

RC = readable_check


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Reuse(unittest.TestCase):
    def setUp(self):
        d = self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.rd, self.rt, self.core = d / "runtime_rd", d / "runtime", d / "core"
        shutil.copytree(RC.RUNTIME_RD, self.rd)
        shutil.copytree(paths.RUNTIME, self.rt)
        for f in ("readable_check.py", "pch_warnings.py", "check.py"):
            put(self.core / f, (pathlib.Path(RC.__file__).parent / f).read_text(encoding="utf-8"))
        put(d / "bin" / "cl.exe", "cl")
        put(d / "work.c", '#include "game.h"\nu32 f(u32 x) { return x; }\n')
        put(d / "fn" / "rd_agent_inc" / "game.h", "/* game */\n")
        put(d / "fn" / "readable_inc" / "game.h", "/* game */\n")
        put(d / "include" / "types.h", "/* types */\n")
        self.cfg, self.result, self.builds = {}, (True, []), []
        ps = [mock.patch.object(RC, "RUNTIME_RD", self.rd), mock.patch.object(RC.paths, "RUNTIME", self.rt),
              mock.patch.object(RC, "__file__", str(self.core / "readable_check.py")),
              mock.patch.object(RC, "msvc_env_x86", lambda cfg: {"PATH": str(d / "bin"), "INCLUDE": "i", "LIB": "l"}),
              mock.patch.object(RC, "_build", self.fake_build)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    def fake_build(self, cfg, names, build_dir, include_dirs, env, cl):
        n = len(self.builds) + 1
        self.builds.append(build_dir.name)
        (build_dir / "rd_runner.exe").write_bytes(f"runner {n}".encode())
        return self.result

    def build(self, name, thunk="thunk", protos=None):
        inc = self.d / "fn" / ("readable_inc" if name == "readable" else f"{name}_inc")
        put(inc / "game.h", (self.d / "fn" / "rd_agent_inc" / "game.h").read_text(encoding="utf-8"))
        return RC.build(self.cfg, [self.d / "work.c"], thunk, protos or {}, self.d / "fn" / name,
                        include_dirs=(inc, self.d / "include"))

    def test_agent_then_official(self):
        self.assertEqual(self.build("rd_agent"), (True, []))
        self.assertEqual(self.build("readable"), (True, []))
        self.assertEqual(self.builds, ["rd_agent"])  # the official judge took the agent's runner
        exe = self.d / "fn" / "readable" / "rd_runner.exe"
        self.assertEqual(exe.read_bytes(), b"runner 1")
        self.assertTrue((self.d / "fn" / "readable" / "work.c").is_file())  # the folder is made as before

    def test_any_change_builds_again(self):
        changes = [
            lambda: put(self.d / "work.c", '#include "game.h"\nu32 f(u32 x) { return x + 0; }\n'),
            lambda: put(self.d / "fn" / "rd_agent_inc" / "game.h", "/* game 2 */\n"),
            lambda: put(self.d / "include" / "types.h", "/* types 2 */\n"),
            lambda: put(self.d / "include" / "sys" / "new.h", "\n"),
            lambda: put(self.rd / "rd_mem.c", (self.rd / "rd_mem.c").read_text(encoding="utf-8") + "\n"),
            lambda: put(self.rt / "tc_rt.h", (self.rt / "tc_rt.h").read_text(encoding="utf-8") + "\n"),
            lambda: self.cfg.update(readable_image_base="0x5b000000"),
            lambda: put(self.d / "bin" / "cl.exe", "another cl"),
            lambda: put(self.core / "pch_warnings.py", (self.core / "pch_warnings.py").read_text(encoding="utf-8")
                        + "\n"),
        ]
        self.build("rd_agent")
        for i, change in enumerate(changes, 2):
            change()
            self.build("readable")
            self.assertEqual(len(self.builds), i, i)
        self.build("readable", thunk="thunk 2")
        self.build("readable", protos={0x8c010000: ("i", "i")})
        self.assertEqual(len(self.builds), len(changes) + 3)
        self.build("rd_agent", protos={0x8c010000: ("i", "i")})
        self.assertEqual(len(self.builds), len(changes) + 3)  # and the same again is kept

    def test_failed_or_noisy_not_kept(self):
        for result in ((False, ["work.c(2): error C2065"]), (True, ["work.c(2): warning C4100"])):
            self.result = result
            self.assertEqual(self.build("rd_agent"), result)
            self.assertEqual(self.build("readable"), result)
        self.assertEqual(len(self.builds), 4)
        self.assertFalse((self.d / "fn" / "rd_runners").exists())

    def test_latest_kept(self):
        for i in range(RC.RUNNERS_KEPT + 3):
            self.build("rd_agent", thunk=f"thunk {i}")
        kept = [p for p in (self.d / "fn" / "rd_runners").iterdir() if p.is_dir()]
        self.assertEqual(len(kept), RC.RUNNERS_KEPT)
        n = len(self.builds)
        self.build("readable", thunk=f"thunk {RC.RUNNERS_KEPT + 2}")  # the latest is still there
        self.assertEqual(len(self.builds), n)


@unittest.skipUnless(os.name == "nt" and VCVARS and pathlib.Path(VCVARS).exists(), "no Visual Studio (config vcvars)")
class RealBuild(unittest.TestCase):
    def test_folder_does_not_reach_the_runner(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        meta = {"name": "fn_8c010000", "signature": "u32 fn_8c010000(u32 param_1)",
                "params": [{"name": "param_1", "kind": "u32", "storage": "r4", "stack_offset": -1}], "ret": "u32",
                "callees": [], "globals": []}
        work = d / "work.c"
        work.write_text('#include "tc_rt.h"\nu32 fn_8c010000(u32 param_1)\n{\n    return param_1 * 3u + 1u;\n}\n',
                        encoding="utf-8")
        cfg = paths.config()
        with mock.patch.object(RC.paths, "BUILD", d / "b"):
            with mock.patch.object(RC, "keep_runner", lambda *a: None):  # two full builds
                a = RC.build(cfg, [work], check.gen_thunk(meta), {}, d / "fn" / "rd_agent")
                b = RC.build(cfg, [work], check.gen_thunk(meta), {}, d / "fn" / "readable")
            self.assertEqual((a, b), ((True, []), (True, [])))
            first, second = sections(d / "fn" / "rd_agent" / "rd_runner.exe"), sections(d / "fn" / "readable" /
                                                                                          "rd_runner.exe")
            self.assertEqual(sorted(first), sorted(second))
            for name in first:
                self.assertEqual(first[name], second[name], name)
            self.assertEqual(RC.build(cfg, [work], check.gen_thunk(meta), {}, d / "fn" / "rd_agent"), (True, []))
            self.assertEqual(RC.build(cfg, [work], check.gen_thunk(meta), {}, d / "fn" / "official"), (True, []))
            self.assertFalse((d / "fn" / "official" / "work.obj").exists())  # the kept runner, not a compile
            self.assertEqual((d / "fn" / "official" / "rd_runner.exe").read_bytes(),
                             (d / "fn" / "rd_agent" / "rd_runner.exe").read_bytes())


if __name__ == "__main__":
    unittest.main()

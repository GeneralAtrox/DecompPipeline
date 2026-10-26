"""readable_outptr.type_sizes keeps the size probe's result by the key of everything the probe reads (_sizes_key), in
the process and in the function's build/ADDR/type_sizes.json, so the official judge of the headers the agent's last
check built, and a later verify run, take the sizes instead of compiling the probe again.
1. the same types and headers from another build folder of the function, or a fresh process: probed once;
2. any changed input probes again: the types, a header (changed or new), the folder order, the compiler, its INCLUDE,
   the probe's code;
3. the file keeps the latest SIZES_KEPT results, and a damaged file is probed again;
4. with Visual Studio's tools (config.local.json "vcvars"): the kept sizes are the probe's own.
Synthetic headers. Run: python -W error tests/test_type_sizes_cache.py.
"""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)
from test_prebuilt_runtime_rd import VCVARS  # noqa: E402

from core import global_extents, readable_outptr as RO  # noqa: E402
from core import readable_check  # noqa: E402


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Kept(unittest.TestCase):
    def setUp(self):
        d = self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.core = d / "core"
        for f in ("readable_outptr.py", "global_extents.py"):
            put(self.core / f, (pathlib.Path(RO.__file__).parent / f).read_text(encoding="utf-8"))
        put(d / "bin" / "cl.exe", "cl")
        put(d / "inc" / "game.h", "typedef struct { int a; } Rec;\n")
        put(d / "types" / "types.h", "typedef unsigned u32;\n")
        self.env = {"PATH": str(d / "bin"), "INCLUDE": "i", "LIB": "l"}
        self.probes = []
        ps = [mock.patch.object(RO, "__file__", str(self.core / "readable_outptr.py")),
              mock.patch.object(readable_check, "msvc_env_x86", lambda cfg: dict(self.env)),
              mock.patch.object(global_extents, "sizes", self.fake_sizes),
              mock.patch.object(RO, "_SIZES", {})]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    def fake_sizes(self, cfg, globs, build_dir, include_dirs):
        build_dir.mkdir(parents=True, exist_ok=True)  # as the probe does
        self.probes.append(build_dir.name)
        return {t: 4 * (i + 1) for i, (t, _) in enumerate(globs)}, {}

    def sizes(self, folder, types=("Rec",), dirs=None):
        RO._SIZES.clear()  # a fresh process: only the function's file is left
        return RO.type_sizes({}, set(types), self.d / "fn" / f"{folder}_outsz",
                             dirs or (self.d / "inc", self.d / "types"))

    def test_agent_then_official_then_verify(self):
        self.assertEqual(self.sizes("rd_agent"), {"Rec": 4})
        self.assertEqual(self.sizes("readable"), {"Rec": 4})
        self.assertEqual(self.sizes("rd_verify"), {"Rec": 4})
        self.assertEqual(self.probes, ["rd_agent_outsz"])
        n = len(self.probes)
        RO.type_sizes({}, {"Rec"}, self.d / "fn" / "readable_outsz", (self.d / "inc", self.d / "types"))
        RO.type_sizes({}, {"Rec"}, self.d / "fn" / "readable_outsz", (self.d / "inc", self.d / "types"))
        self.assertEqual(len(self.probes), n)  # and in the process

    def test_any_change_probes_again(self):
        changes = [
            lambda: put(self.d / "inc" / "game.h", "typedef struct { int a, b; } Rec;\n"),
            lambda: put(self.d / "types" / "sys" / "new.h", "\n"),
            lambda: put(self.d / "bin" / "cl.exe", "another cl"),
            lambda: self.env.update(INCLUDE="i2"),
            lambda: put(self.core / "global_extents.py", (self.core / "global_extents.py").read_text(encoding="utf-8")
                        + "\n"),
            lambda: put(self.core / "readable_outptr.py", (self.core / "readable_outptr.py").read_text(
                encoding="utf-8") + "\n"),
        ]
        self.sizes("rd_agent")
        for i, change in enumerate(changes, 2):
            change()
            self.sizes("readable")
            self.assertEqual(len(self.probes), i, i)
        self.sizes("readable", types=("Rec", "Vec"))
        self.sizes("readable", dirs=(self.d / "types", self.d / "inc"))
        self.assertEqual(len(self.probes), len(changes) + 3)
        self.sizes("rd_agent", types=("Vec", "Rec"))
        self.assertEqual(len(self.probes), len(changes) + 3)  # the same set again is kept

    def test_latest_kept_and_damaged_file(self):
        for i in range(RO.SIZES_KEPT + 3):
            self.sizes("rd_agent", types=(f"T{i}",))
        cache = self.d / "fn" / "type_sizes.json"
        self.assertEqual(len(__import__("json").loads(cache.read_text(encoding="utf-8"))), RO.SIZES_KEPT)
        n = len(self.probes)
        self.sizes("readable", types=(f"T{RO.SIZES_KEPT + 2}",))  # the latest is still there
        self.assertEqual(len(self.probes), n)
        cache.write_text('{"cut sh', encoding="utf-8")
        self.assertEqual(self.sizes("readable", types=("T0",)), {"T0": 4})
        self.assertEqual(len(self.probes), n + 1)


@unittest.skipUnless(os.name == "nt" and VCVARS and pathlib.Path(VCVARS).exists(), "no Visual Studio (config vcvars)")
class RealProbe(unittest.TestCase):
    def test_kept_sizes_are_the_probe_s(self):
        from core import paths
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        put(d / "inc" / "types.h", "typedef struct { int a; double b; } Rec;\ntypedef struct Hidden Hidden;\n")
        dirs = (d / "inc", readable_check.RUNTIME_RD, paths.RUNTIME)
        cfg = paths.config()
        with mock.patch.object(RO, "_SIZES", {}):
            fresh = RO.type_sizes(cfg, {"Rec", "Hidden"}, d / "fn" / "rd_agent_outsz", dirs)
            RO._SIZES.clear()
            with mock.patch.object(global_extents, "sizes", side_effect=AssertionError("probed again")):
                again = RO.type_sizes(cfg, {"Rec", "Hidden"}, d / "fn" / "readable_outsz", dirs)
        self.assertEqual(fresh, {"Rec": 16})  # an incomplete type is left out
        self.assertEqual(again, fresh)


if __name__ == "__main__":
    unittest.main()

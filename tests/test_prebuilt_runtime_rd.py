"""The readable judge links its runtime compiled once per content (readable_check.runtime_rd_objs), as the raw judge
does (check.runtime_objs, tests/test_prebuilt_runtime.py); and neither cache ever removes a complete set.
1. compiled once, then reused without a compile; another runtime content gets another set; a runtime that does not
   compile gives None (the build compiles the runtime sources itself, as before);
2. a game include folder holding a file named like a header the runtime opened gives None (it could shadow it);
3. a set another build completed first is kept (no rmtree under a build that links it: the LNK1181 race), for
   check.runtime_objs too;
4. with Visual Studio's tools (config.local.json "vcvars"): a real readable build with the cached runtime makes a
   runner whose code and data sections are the full build's, byte for byte, and the same listing of work.c.
Synthetic sources. Run: python -W error tests/test_prebuilt_runtime_rd.py.
"""
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import check, paths, readable_check  # noqa: E402

RC = readable_check


class RuntimeRdObjs(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.rd, self.rt = self.d / "runtime_rd", self.d / "runtime"
        shutil.copytree(RC.RUNTIME_RD, self.rd)
        shutil.copytree(paths.RUNTIME, self.rt)
        self.cl = self.d / "cl.exe"
        self.cl.write_bytes(b"x")
        self.calls = []
        self.srcs = [self.rd / s for s in RC.RD_SOURCES] + [self.rt / "tc_p4.c"]

    def fake_cl(self, ok=True, before=None):
        def run(cmd, timeout, cwd=None, env=None, **k):
            self.calls.append(cmd)
            if before:
                before()
            if not ok:
                return 2, "", None
            for s in self.srcs:
                (pathlib.Path(cwd) / f"{s.stem}.obj").write_bytes(b"obj")
                (pathlib.Path(cwd) / f"{s.stem}.asm").write_text("; listing\n", encoding="utf-8")
            notes = [f"Note: including file: {self.rd / 'rd_internal.h'}",
                     f"Note: including file:  {self.d / 'sdk' / 'um' / 'windows.h'}"]  # a system header
            return 0, "\n".join(["rd_mem.c", *notes]), None
        return run

    def objs(self, ok=True, incs=(), before=None):
        with mock.patch.object(RC, "RUNTIME_RD", self.rd), mock.patch.object(RC.paths, "RUNTIME", self.rt), \
                mock.patch.object(RC, "RUNTIME_SOURCES", self.srcs), mock.patch.object(RC.paths, "BUILD", self.d / "b"), \
                mock.patch.object(RC.paths, "run_bounded", self.fake_cl(ok, before)):
            return RC.runtime_rd_objs(str(self.cl), {"INCLUDE": "inc"}, incs)

    def test_once_then_reused_and_keyed_by_content(self):
        first = self.objs()
        self.assertEqual(sorted(pathlib.Path(o).name for o, _ in first.values()),
                         sorted(f"{s.stem}.obj" for s in self.srcs))
        self.assertIn("/showIncludes", self.calls[0])
        folder = next(iter(first.values()))[0].parent
        self.assertEqual((folder / "headers.txt").read_text(encoding="utf-8").split(), ["rd_internal.h", "windows.h"])
        self.assertEqual(self.objs(), first)
        self.assertEqual(len(self.calls), 1)  # reused: no second compile
        (self.rd / "rd_mem.c").write_text((self.rd / "rd_mem.c").read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertNotEqual(next(iter(self.objs().values()))[0].parent, folder)  # another source: another set
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len([p for p in (self.d / "b" / "runtime_rd_obj").iterdir() if p.is_dir()]), 2)  # no temp

    def test_does_not_compile(self):
        self.assertIsNone(self.objs(ok=False))
        self.assertFalse([p for p in (self.d / "b" / "runtime_rd_obj").iterdir() if p.is_dir()])

    def test_a_game_header_that_could_shadow(self):
        game = self.d / "inc"
        (game / "sub").mkdir(parents=True)
        (game / "game.h").write_text("", encoding="utf-8")
        self.assertIsNotNone(self.objs(incs=(game,)))
        (game / "sub" / "Windows.h").write_text("", encoding="utf-8")  # a name the runtime opened (any case)
        self.assertIsNone(self.objs(incs=(game,)))

    def test_a_complete_set_is_kept(self):
        first = self.objs()
        folder = next(iter(first.values()))[0].parent
        (folder / "rd_mem.obj").unlink()  # incomplete: rebuilt, and another build completes it meanwhile

        def other_build_completes():
            (folder / "rd_mem.obj").write_bytes(b"theirs")
        self.assertEqual(self.objs(before=other_build_completes), first)
        self.assertEqual((folder / "rd_mem.obj").read_bytes(), b"theirs")  # kept, not replaced under its linker

    def test_raw_runtime_objs_keeps_a_complete_set(self):
        rt = self.d / "rawrt"
        shutil.copytree(paths.RUNTIME, rt)
        sets = []

        def run(cmd, timeout, cwd=None, env=None, **k):
            for s in check.RUNTIME_SRCS:
                (pathlib.Path(cwd) / f"{s[:-2]}.obj").write_bytes(b"mine")
            d = pathlib.Path(cwd).with_name(pathlib.Path(cwd).name.split(".tmp")[0])
            d.mkdir(parents=True, exist_ok=True)
            for s in check.RUNTIME_SRCS:  # another build finished the same set first
                (d / f"{s[:-2]}.obj").write_bytes(b"theirs")
            sets.append(d)
            return 0, "", None
        with mock.patch.object(check.paths, "RUNTIME", rt), mock.patch.object(check.paths, "BUILD", self.d / "rb"), \
                mock.patch.object(check.paths, "run_bounded", run):
            objs = check.runtime_objs(str(self.cl), {"INCLUDE": "inc"})
        self.assertEqual({o.read_bytes() for o in objs}, {b"theirs"})
        self.assertEqual([p.name for p in (self.d / "rb" / "runtime_obj").iterdir()], [sets[0].name])  # no temp


def sections(exe):
    """{name: raw bytes} of a PE's sections (the code and data the runner runs), the link's time stamp zeroed: the
    linker writes it into the header and the debug directory (.rdata), and two links a second apart differ there."""
    b = exe.read_bytes()
    pe = struct.unpack_from("<I", b, 0x3C)[0]
    n, opt = struct.unpack_from("<H", b, pe + 6)[0], struct.unpack_from("<H", b, pe + 20)[0]
    stamp = b[pe + 8:pe + 12]
    out = {}
    for i in range(n):
        o = pe + 24 + opt + 40 * i
        name = b[o:o + 8].rstrip(b"\0").decode()
        size, ptr = struct.unpack_from("<II", b, o + 16)
        out[name] = b[ptr:ptr + size].replace(stamp, b"\0" * 4)
    return out


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
        with mock.patch.object(RC.paths, "BUILD", d / "b"), mock.patch.object(RC, "keep_runner", lambda *a: None):
            with mock.patch.object(RC, "runtime_rd_objs", lambda *a: None):
                ok_full, msgs_full = RC.build(cfg, [work], check.gen_thunk(meta), {}, d / "full")
            ok, msgs = RC.build(cfg, [work], check.gen_thunk(meta), {}, d / "cached")
        self.assertTrue(ok_full and ok, (msgs_full, msgs))
        self.assertEqual(msgs_full, msgs)
        self.assertFalse((d / "cached" / "rd_mem.obj").exists())  # linked from the cache, not compiled here
        self.assertTrue(list((d / "b" / "runtime_rd_obj").glob("*/rd_mem.obj")))
        listing = lambda p: [ln for ln in p.read_text(errors="replace").splitlines() if str(d) not in ln]  # noqa: E731
        self.assertEqual(listing(d / "full" / "work.asm"), listing(d / "cached" / "work.asm"))
        full, cached = sections(d / "full" / "rd_runner.exe"), sections(d / "cached" / "rd_runner.exe")
        self.assertEqual(sorted(full), sorted(cached))
        for name in full:
            self.assertEqual(full[name], cached[name], name)


if __name__ == "__main__":
    unittest.main()

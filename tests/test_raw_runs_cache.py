"""readable_outrun.RawRuns: the matched raw C's runs behind the out-pointer delivery are cached once per function
(build/ADDR/raw_runs.json), not once per build folder, so the official judge and verify take the runs another folder
of the same function made on the same inputs (finding R3). The runner is never started for a cached run.
1. a run made in one folder (readable_orig) is taken in another (rd_verify_orig) with no build and no run;
2. any input the run reads gives another key: the raw C, the vectors, the sets, the harness meta (the thunk), the
   binary, the code ranges, the FSCA table, the runtime, the link options, the judge's code;
3. a failed run is not cached; the file keeps the latest 16 runs, written whole.
Run: python -W error tests/test_raw_runs_cache.py.
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import paths, readable_check, readable_outrun  # noqa: E402

A = "8c0f1000"
META = {"name": "fn_8c0f1000", "signature": "u32 fn_8c0f1000(u32 param_1)",
        "params": [{"name": "param_1", "kind": "u32", "storage": "r4", "stack_offset": -1}], "ret": "u32",
        "callees": [], "globals": []}


def put(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    (p.write_bytes if isinstance(data, bytes) else lambda t: p.write_text(t, encoding="utf-8"))(data)


class Cache(unittest.TestCase):
    def setUp(self):
        d = self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        put(d / "matched" / f"{A}.c", "u32 fn_8c0f1000(u32 param_1) { return param_1; }\n")
        put(d / "bin" / "game.bin", b"\x09\x00\x0b\x00")
        put(d / "state" / "code_ranges.txt", "8c010000 8c020000\n")
        put(d / "state" / "fsca_table.bin", b"\x00" * 16)
        put(d / "state" / "msvc_env_x86.json", "{}")
        put(d / "vec.txt", "V 1\nE\n")
        self.cfg, self.calls = {"binary_path": d / "bin" / "game.bin"}, []
        ps = [mock.patch.object(paths, "MATCHED", d / "matched"), mock.patch.object(paths, "CODE_RANGES",
                                                                                    d / "state" / "code_ranges.txt"),
              mock.patch.object(paths, "FSCA_TABLE", d / "state" / "fsca_table.bin"),
              mock.patch.object(readable_check, "MSVC_ENV_X86", d / "state" / "msvc_env_x86.json"),
              mock.patch.object(readable_check, "build", self.fake_build),
              mock.patch.object(readable_check, "run", self.fake_run)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.ok = True

    def fake_build(self, cfg, sources, thunk, protos, build_dir, include_dirs=()):
        self.calls.append(("build", build_dir.name))
        build_dir.mkdir(parents=True, exist_ok=True)
        return True, []

    def fake_run(self, cfg, harness, build_dir, sets, vectors):
        self.calls.append(("run", build_dir.name))
        out = build_dir / "actual.txt"
        out.write_text(f"V 1 r0=0\nOB 1 {len(self.calls)}\nX other\n", encoding="utf-8")
        return out, None if self.ok else "runner exited with code 3"

    def runs(self, folder="readable_orig", meta=META, sets=("dev", "hidden")):
        return readable_outrun.RawRuns(self.cfg, A, meta, self.d / "harness", self.d / "build" / folder, sets)

    def test_other_folder_takes_it(self):
        text = self.runs().run(self.d / "vec.txt")
        self.assertEqual(text, "V 1 r0=0\nOB 1 2\n")
        self.assertEqual(self.runs("rd_verify_orig").run(self.d / "vec.txt"), text)
        self.assertEqual(self.calls, [("build", "readable_orig"), ("run", "readable_orig")])
        self.assertTrue((self.d / "build" / "raw_runs.json").is_file())

    def test_inputs_give_another_key(self):
        changes = [
            lambda: put(self.d / "matched" / f"{A}.c", "u32 fn_8c0f1000(u32 param_1) { return param_1 + 0; }\n"),
            lambda: put(self.d / "vec.txt", "V 1\nB 1\nE\n"),
            lambda: put(self.d / "bin" / "game.bin", b"\x09\x00\x0b\x00\x09\x00"),
            lambda: put(self.d / "state" / "code_ranges.txt", "8c010000 8c030000\n"),
            lambda: put(self.d / "state" / "fsca_table.bin", b"\x01" * 16),
            lambda: put(self.d / "state" / "msvc_env_x86.json", '{"PATH": "other"}'),
            lambda: put(readable_check.RUNTIME_RD / "rd_zz_test.h", "\n"),
            lambda: self.cfg.update(readable_image_base="0x5b000000"),
        ]
        self.addCleanup(lambda: (readable_check.RUNTIME_RD / "rd_zz_test.h").unlink(missing_ok=True))
        self.runs().run(self.d / "vec.txt")
        for i, change in enumerate(changes, 1):
            change()
            self.runs("rd_verify_orig").run(self.d / "vec.txt")
            self.assertEqual(sum(c[0] == "run" for c in self.calls), i + 1, i)
        n = len(self.calls)
        self.runs("rd_verify_orig", sets=("dev",)).run(self.d / "vec.txt")
        self.runs("rd_verify_orig", meta=dict(META, ret="void")).run(self.d / "vec.txt")
        self.assertEqual(sum(c[0] == "run" for c in self.calls[n:]), 2)
        core = pathlib.Path(readable_outrun.__file__).parent
        real = pathlib.Path.read_bytes
        with mock.patch.object(pathlib.Path, "read_bytes", lambda p: real(p) + (b"x" if p == core / "check.py"
                                                                                  else b"")):
            self.runs("rd_verify_orig").run(self.d / "vec.txt")
        self.assertEqual(sum(c[0] == "run" for c in self.calls[n:]), 3)

    def test_failed_not_cached_and_latest_kept(self):
        self.ok = False
        self.assertIsNone(self.runs().run(self.d / "vec.txt"))
        self.assertFalse((self.d / "build" / "raw_runs.json").exists())
        self.ok = True
        for i in range(20):
            put(self.d / f"vec{i}.txt", f"V {i}\nE\n")
            self.runs().run(self.d / f"vec{i}.txt")
        self.assertEqual(len(paths.read_json(self.d / "build" / "raw_runs.json")), 16)
        self.assertEqual([p.name for p in (self.d / "build").iterdir() if p.is_file()], ["raw_runs.json"])


if __name__ == "__main__":
    unittest.main()

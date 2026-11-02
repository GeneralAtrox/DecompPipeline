"""readable_outrun.RawRuns: the matched raw C's runs behind the out-pointer delivery are cached once per function
(build/ADDR/raw_runs.json), not once per build folder, so the official judge and verify take the runs another folder
of the same function made on the same inputs (finding R3). The runner is never started for a cached run.
1. a run made in one folder (readable_orig) is taken in another (rd_verify_orig) with no build and no run;
2. any input the run reads gives another key: the raw C, the vectors, the sets, the harness meta (the thunk), the
   binary, the code ranges, the FSCA table, the runtime, the link options, the judge's code;
3. a failed run is not cached; the file keeps the latest 16 runs, written whole;
4. a run that times out keeps the vectors it finished and is run again without the vector it hung in (TC 8c038c2c).
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


NL = chr(10)


def put(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    (p.write_bytes if isinstance(data, bytes) else lambda t: p.write_text(t, encoding="utf-8"))(data)


class Base(unittest.TestCase):
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


class Cache(Base):
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


class Salvage(Base):
    """A runner that hangs in some vectors (the raw C loops on painted bytes) and flushes after each vector's E."""
    HANG = {2, 5}

    def vecs(self, ids):
        put(self.d / "vec.txt", NL.join(["R 1 flag"] + [x for i in ids for x in (f"V {i} dev", "M 0 00", "E")]) + NL)
        return self.d / "vec.txt"

    def fake_run(self, cfg, harness, build_dir, sets, vectors):
        self.calls.append(("run", build_dir.name))
        out, lines, hang = build_dir / "actual.txt", [], None
        for ln in vectors.read_text(encoding="utf-8").splitlines():
            if ln.startswith("V "):
                i = int(ln.split()[1])
                if i in self.HANG:
                    hang = i  # its V line is still in the runner's buffer when it hangs
                    break
                lines += [ln, f"OB 0 8c0f1000 1 {i:02x}", "RET 00"]
            elif ln == "E":
                lines.append("E")
        out.write_text(NL.join(lines) + NL, encoding="utf-8")
        return out, "runner timeout" if hang is not None else None if self.ok else "runner exited with code 3"

    def test_hung_vectors_left_out(self):
        text = self.runs().run(self.vecs(range(8)))
        got = [ln for ln in text.splitlines() if ln.startswith("V ")]
        self.assertEqual(got, [f"V {i} dev" for i in (0, 1, 3, 4, 6, 7)])
        self.assertEqual(sum(ln.startswith("OB ") for ln in text.splitlines()), 6)
        self.assertNotIn("RET", text)
        self.assertEqual([c[0] for c in self.calls], ["build", "run", "run", "run"])  # whole, after 2, after 5
        n = len(self.calls)
        self.assertEqual(self.runs("rd_verify_orig").run(self.d / "vec.txt"), text)  # cached under the whole file
        self.assertEqual(len(self.calls), n)

    def test_old_judge_and_nothing_finished(self):
        with mock.patch.object(readable_outrun, "SALVAGE", False):
            self.assertIsNone(self.runs().run(self.vecs(range(8))))
        self.HANG = {2}
        self.assertIsNone(self.runs("rd_x").run(self.vecs([2])))  # no vector finished
        self.assertFalse((self.d / "build" / "raw_runs.json").exists())

    def test_other_errors_still_none(self):
        self.HANG = set()
        self.ok = False
        self.assertIsNone(self.runs().run(self.vecs(range(3))))


if __name__ == "__main__":
    unittest.main()

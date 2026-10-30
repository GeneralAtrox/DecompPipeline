"""The junk-RAM judge pass (core/junk_ram, config "junk_ram") and its report tool (tools/junk_ram.py).

1. The junk image and the tool's C-against-C comparison.
2. The flag: off (absent, false, 0, a report mode alone) runs nothing and leaves the check result byte-identical;
   on, a check that passes today's pass is judged again on junk RAM against the original's junk run; a vector xemu
   cannot reproduce or whose junk run faults is skipped; report mode only reports; another target is refused.
   check.run_check runs for real with a faked build and faked runs (the runner, xemu): the C's and the original's
   results per vector are set by the test.
The junk round's six caught wrong reads, judged by the real runners, are the proof run (land/jjudge's result file:
task 1). Run: python tests/test_junk_ram.py
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tests")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
import junk_ram as tool  # noqa: E402
from core import check, frame_args, junk_ram, paths, sh4_asm, strict_judge  # noqa: E402
from core.compare import parse_results, parse_vectors  # noqa: E402

META = {"addr": "8c010000", "ret": "u32", "params": [], "callees": [], "globals": []}
VECTORS = "V 0 dev\nR 00000000 00000000 00000000 00000000\nE\nV 1 dev\nR 00000000 00000000 00000000 00000000\nE\n"
N = 32  # vectors 0..15 dev, 16..31 hidden


def results(*runs):
    """One result file's text: per vector (r0, {addr: byte} writes)."""
    out = []
    for i, (r0, writes) in enumerate(runs):
        out.append(f"V {i} dev\nRET {r0:08x} 00000000")
        out += [f"W {a:08x} {b:02x}" for a, b in sorted(writes.items())]
        out.append("E")
    return "\n".join(out) + "\n"


def run_text(ret, extra=None, upto=N):
    """A result file of vectors 0..upto-1: RET ret.get(i, i), extra lines per vector."""
    out = []
    for i in range(upto):
        out += [f"V {i} {'dev' if i < 16 else 'hidden'}", f"RET {ret.get(i, i):08x} 00000000",
                *(extra or {}).get(i, []), "E"]
    return "\n".join(out) + "\n"


class JunkImage(unittest.TestCase):
    def test_pads_to_ram_end_keeping_the_image(self):
        img = bytes(range(256)) * 4
        j = tool.junk_image(img)
        self.assertEqual(len(j), junk_ram.RAM_SIZE - junk_ram.IMAGE_LO)
        self.assertEqual(j[:len(img)], img)
        self.assertNotEqual(j[len(img):len(img) + 64], bytes(64))

    def test_per_address_and_seeded(self):
        a, b = tool.junk_image(b"\0" * 100), tool.junk_image(b"\1" * 300)
        self.assertEqual(a[300:4096], b[300:4096])  # a byte's junk depends on its address, not on the image size
        self.assertEqual(a, junk_ram.junk_image(b"\0" * 100, junk_ram.SEED))
        self.assertNotEqual(a[100:4096], tool.junk_image(b"\0" * 100, seed=2)[100:4096])

    def test_too_big_image_is_cut(self):
        n = junk_ram.RAM_SIZE - junk_ram.IMAGE_LO
        self.assertEqual(len(tool.junk_image(b"\0" * (n + 8))), n)


class Differences(unittest.TestCase):
    def parsed(self, text):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "r.txt").write_text(text, encoding="utf-8")
        return parse_results(d / "r.txt")

    def vecs(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "v.txt").write_text(VECTORS, encoding="utf-8")
        return parse_vectors(d / "v.txt")

    def test_a_junk_dependent_vector_is_listed(self):
        zero = self.parsed(results((1, {0x8c200000: 0}), (2, {})))
        junk = self.parsed(results((1, {0x8c200000: 0x5a}), (2, {})))
        diff, zfail = tool.differences(META, b"", self.vecs(), zero, junk, zero, 0x8cffd000, None)
        self.assertEqual(sorted(diff), [0])
        self.assertEqual(zfail, [])

    def test_return_value_and_zero_run_failures(self):
        zero = self.parsed(results((1, {}), (2, {})))
        junk = self.parsed(results((1, {}), (7, {})))
        exp = self.parsed(results((9, {}), (2, {})))  # vector 0: the zero run is already wrong (the judge fails it)
        diff, zfail = tool.differences(META, b"", self.vecs(), zero, junk, exp, 0x8cffd000, None)
        self.assertEqual(sorted(diff), [1])
        self.assertEqual(zfail, [0])

    def test_unfinished_zero_run_is_left_out(self):
        zero = self.parsed("V 0 dev\nRET 00000001 00000000\n" + results((2, {})).replace("V 0", "V 1"))
        junk = self.parsed(results((5, {}), (2, {})))
        diff, zfail = tool.differences(META, b"", self.vecs(), zero, junk, {}, 0x8cffd000, None)
        self.assertEqual(diff, {})
        self.assertEqual(zfail, [0])


class Mode(unittest.TestCase):
    def test_off_on_and_refused(self):
        dc = {"target": "dreamcast_sh4"}
        for off in ({}, {"junk_ram": False}, {"junk_ram": 0}, {"junk_ram": None}, {"junk_ram": "true"},
                    {"strict_judge": {"junk_ram": "report"}}):
            self.assertIsNone(junk_ram.mode(dict(dc, **off)), off)
        self.assertEqual(junk_ram.mode(dict(dc, junk_ram=True)), "enforce")
        self.assertEqual(junk_ram.mode(dict(dc, junk_ram=1, strict_judge={"junk_ram": "report"})), "report")
        for t in ("saturn_sh2", "gamecube_ppc", None):
            with self.assertRaisesRegex(ValueError, "Dreamcast"):
                junk_ram.mode({"target": t, "junk_ram": True})
        with self.assertRaises(ValueError):
            junk_ram.mode(dict(dc, junk_ram=True, strict_judge={"junk_ram": "off"}))
        # the strict judge's own modes take the key and stay as they were
        self.assertEqual(strict_judge.modes({"strict_judge": {"junk_ram": "report"}}),
                         {"call_snapshots": "enforce", "exit_frame": "enforce"})
        self.assertNotIn("junk_ram", strict_judge.facts_lines({"junk_ram": True}))  # no oracle input: no stamp moves


class Judge(unittest.TestCase):
    """check.run_check end to end with the build and the runs faked: expected RET i for vector i."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        h = self.d / "harness"
        h.mkdir()
        (h / "meta.json").write_text(json.dumps(dict(META, name="fn", signature="u32 fn(void)")), encoding="utf-8")
        (h / "vectors.txt").write_text("".join(f"V {i} {'dev' if i < 16 else 'hidden'}\nR 0 0 0 0\nE\n"
                                               for i in range(N)), encoding="utf-8")
        (h / "expected.txt").write_text(run_text({}), encoding="utf-8")
        (self.d / "game.bin").write_bytes(b"\x09\x00" * 64)
        (self.d / "work.c").write_text("u32 fn(void) { return 0; }\n", encoding="utf-8")
        self.h = h
        # the original (xemu): its junk run faults on 5 and reads junk on 7 (as correct C does)
        self.xemu = {"zero": run_text({}), "junk": run_text({7: 1007}, {5: ["FAULT 8c123456 read"]})}
        self.c = {"zero": run_text({}), "junk": run_text({7: 1007, 5: 1})}  # correct C
        self.runs = []
        junk_ram._ORACLES.clear()  # the same harness bytes in each test: the in-process cache would serve it
        self.build_patch = mock.patch.object(paths, "BUILD", self.d / "build")

    def forget(self):
        """The test changed xemu's results for the same harness: drop the kept junk oracle."""
        junk_ram._ORACLES.clear()
        shutil.rmtree(self.d / "build", ignore_errors=True)

    def fake_runner(self, cmd, timeout, out, cap, env=None):
        side = "xemu" if env and "XEMU_FN" in env else "c"
        tag = "junk" if "junk_ram" in cmd[1] else "zero"
        self.runs.append((side, tag))
        pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(out).write_text(getattr(self, side)[tag], encoding="utf-8")
        return 0, "", None

    def check(self, **cfg):
        cfg = dict({"target": "dreamcast_sh4", "binary_path": self.d / "game.bin", "check_timeout_s": 10,
                    "fsca_table": False}, **cfg)
        with self.build_patch, mock.patch.object(check, "build", return_value=(True, [])), \
                mock.patch.object(check, "run_runner", self.fake_runner), \
                mock.patch.object(check, "lint", return_value=[]), \
                mock.patch.object(frame_args, "lint_frame_args", return_value=[]), \
                mock.patch.object(sh4_asm, "read_packet", return_value=None), \
                mock.patch.object(junk_ram, "xemu_runner", return_value=(self.d / "xemu.exe", "k")):
            return check.run_check(cfg, "8c010000", str(self.d / "work.c"), build_dir=self.d / "b",
                                   harness_dir=self.h, fused=False)

    def test_off_is_byte_identical(self):
        base = check.dumps(self.check())
        self.assertEqual(json.loads(base)["verdict"], "PASS")
        self.c["junk"] = run_text({9: 0xdead})  # wrong C: the junk pass would fail it, but it never runs
        for off in ({"junk_ram": False}, {"junk_ram": 0}, {"strict_judge": {"junk_ram": "report"}}):
            self.assertEqual(check.dumps(self.check(**off)), base, off)
        self.assertNotIn("junk_ram", base)
        self.assertEqual(set(self.runs), {("c", "zero")})  # no junk image, no xemu
        self.assertFalse((self.d / "build" / "junk_ram").exists())

    def test_on_correct_c_passes(self):
        res = self.check(junk_ram=True)
        self.assertEqual(res["verdict"], "PASS")
        jr = res["junk_ram"]
        self.assertEqual((jr["mode"], jr["vectors"], jr["failing"]), ("enforce", N - 1, 0))
        self.assertEqual(jr["skipped"], {"xemu": 0, "fault": 1, "after_stop": 0})  # vector 5
        self.assertEqual(self.runs.count(("xemu", "zero")) + self.runs.count(("xemu", "junk")), 2)
        self.check(junk_ram=True)  # the junk oracle is made once per harness: kept in the process
        junk_ram._ORACLES.clear()
        self.check(junk_ram=True)  # and in build/junk_ram/oracle/
        self.assertEqual(self.runs.count(("xemu", "junk")), 1)
        v = self.h / "vectors.txt"
        v.write_text(v.read_text(encoding="utf-8").replace("R 0 0 0 0", "R 0 0 0 1", 1), encoding="utf-8")  # another
        self.check(junk_ram=True)
        self.assertEqual(self.runs.count(("xemu", "junk")), 2)

    def test_on_wrong_read_fails(self):
        self.c["junk"] = run_text({7: 1007, 9: 0xdead, 20: 0xbeef})  # reads unset RAM where the original does not
        res = self.check(junk_ram=True)
        self.assertEqual((res["verdict"], res["failing"], res["junk_ram"]["failing_ids"]), ("FAIL", 2, [9, 20]))
        self.assertEqual(res["vectors"]["dev"]["failing"], 0)  # today's pass is unchanged
        self.assertIn("unset RAM filled with junk", res["first_diffs"][0]["diffs"][0])
        self.assertIn("2 vectors differ with unset RAM filled with junk", check.format_for_agent(res))

    def test_xemu_gap_and_fault_are_skipped(self):
        self.c["junk"] = run_text({7: 1007, 5: 0xdead})  # differs only where the original faults on junk
        self.assertEqual(self.check(junk_ram=True)["verdict"], "PASS")
        self.xemu["zero"] = run_text({3: 999})  # xemu can't reproduce vector 3: no oracle for the whole function
        self.forget()
        self.c["junk"] = run_text({7: 1007, 9: 0xdead})
        res = self.check(junk_ram=True)
        self.assertEqual((res["verdict"], res["junk_ram"]["vectors"], res["junk_ram"]["skipped"]["xemu"]),
                         ("PASS", 0, N))

    def test_nan_on_both_sides_matches(self):
        nan = {9: ["W 8cc00100 00", "W 8cc00101 00", "W 8cc00102 c0", "W 8cc00103 ff"]}  # 0xffc00000
        self.xemu["junk"] = run_text({7: 1007}, {**nan, 5: ["FAULT 8c123456 read"]})
        self.c["junk"] = run_text({7: 1007}, {9: [x[:-2] + "ff" for x in nan[9]]})  # 0xffffffff
        self.forget()
        self.assertEqual(self.check(junk_ram=True)["verdict"], "PASS")
        self.c["junk"] = run_text({7: 1007}, {9: [x[:-2] + "00" for x in nan[9]]})  # 0: not a NaN
        self.assertEqual(self.check(junk_ram=True)["junk_ram"]["failing_ids"], [9])

    def test_report_mode(self):
        self.c["junk"] = run_text({7: 1007, 9: 0xdead})
        res = self.check(junk_ram=True, strict_judge={"junk_ram": "report"})
        self.assertEqual((res["verdict"], res["failing"], res["junk_ram"]["failing"]), ("PASS", 0, 1))
        self.assertEqual(res["strict_report"]["vectors"], 1)
        self.assertTrue(res["strict_report"]["first"][0]["diffs"][0].startswith("junk_ram (report only): "))

    def test_runs_only_after_todays_pass(self):
        self.c["zero"] = run_text({4: 0xbad})
        res = self.check(junk_ram=True)
        self.assertEqual((res["verdict"], "junk_ram" in res), ("FAIL", False))
        self.assertNotIn(("c", "junk"), self.runs)

    def test_a_hang_stops_the_pass(self):
        self.c["junk"] = run_text({7: 1007}, upto=5)  # the C hung on vector 5, which the original faults on
        res = self.check(junk_ram=True)
        self.assertEqual((res["verdict"], res["junk_ram"]["skipped"]["after_stop"]), ("PASS", N - 6))
        self.c["junk"] = run_text({7: 1007}, upto=9)  # hung on vector 9, which the original finishes
        res = self.check(junk_ram=True)
        self.assertEqual((res["verdict"], res["junk_ram"]["failing_ids"]), ("FAIL", [9]))

    def test_other_target_refused(self):
        with self.assertRaisesRegex(ValueError, "Dreamcast"):
            self.check(target="gamecube_ppc", junk_ram=True)
        self.assertEqual(self.runs, [])


if __name__ == "__main__":
    unittest.main()

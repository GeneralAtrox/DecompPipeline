"""Skies of Arcadia r3 (2026-11-12, DP 76c9a320): 14 functions went PASS to FAIL with no wrong C.

1. The GameCube template's pointer repair (TcInputs.repairVec, d7eeeada) had a sum fallback that made any large
   loaded value a small index. It made the image's f32 constants inputs (8034b0ac, 24.0, set to 0..15) and spent
   the 12 repairs before TcVectors.repair reached the scripted call result that was the pointer (8017cc70 8017e4e4
   801d25dc), and re-ran a loop that ran off RAM's end once per store word, each to the step limit (80239108 and six
   more: out of time). Now a value is an index only when the fault less it, at an element size, is a RAM base, and
   only in a word the vector set (the Java cases).
   Skies r4 (7a47cf53) then showed what that rule dropped: a count (801559bc, 48 valid vectors to none) and a sum with
   both terms unset (8019ae84, 24 dev vectors to 15) explain no address. Such a word, read once in the log, or else
   a register, is made small again, but as a guess: after the values that explain the address and the scripted call
   results, and GEN_BLIND (3) times a vector at most.
   Skies r5 (a8b9ae07) then showed the 12 repairs spent one word at a time on arrays and chains of our own chunks
   (80226dc4 801d25dc 80176a44 80154404 800a40b0 and about 20 more). An instruction that read our patch at a stride
   now gets the whole array one shared chunk in one repair; a word 0..15 of our chunk is a null pointer first and a
   chunk if read again; a word the run wrote itself is never poked. The blind guess is 1 (no draw from rnd, so later
   vectors are unchanged) and is not made for a fault near address 0, a null pointer. The blind_* cases fault at
   81800000, far from 0, so they are unchanged.
2. The template must pass the watcher's lint (core/javalint): d7eeeada's repairNotIndex did not.
3. GameCube dp_judge: a refused harness (meta "unsupported") or one with a set of no valid vector is ERROR
   "harness refused: ...", never FAIL at compare 0/0; the judge's PASS rule (min_valid_vectors per set) is unchanged.
4. Call snapshots (TcStrict, call_snapshots): a pointer argument into the own frame is snapshotted like a heap one, its
   run of stored bytes stopping below the frame's saved registers (TcStrict.saveLo); before, a wrong store into a frame
   buffer passed on (Skies 800a13d8's two-vertex array at r1+8, to 80291bb4) was never compared. A frame slot never
   passed on, the saved registers, the caller's frame and the area below r1 stay unjudged (the Java cases).
Synthetic data. The Java cases need config.local.json's ghidra_home and a JDK.
usage: python -W error tests/test_gc_refused_harness.py
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]

from test_tail import CP, jdk  # noqa: E402

from core import javalint  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"


def load_dp_judge():
    spec = importlib.util.spec_from_file_location("gc_dp_judge", GC / "translator" / "tools" / "dp_judge.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Template(unittest.TestCase):
    def test_lint(self):
        self.assertEqual(javalint.lint_inputs((GC / "template" / "TcInputs.java").read_text(encoding="utf-8")), [])


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Repair(unittest.TestCase):
    def test_cases(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as out:
            src = os.pathsep.join([str(GC / "ghidra_scripts"), str(GC / "template"), str(SHARED)])
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(TOOL / "tests" / "java" / "TcGcRepairStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcGcRepairStub"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        got = {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}
        self.assertEqual(got["image_constant"], "null")  # d7eeeada: poke 8034b0ac
        self.assertEqual(got["runaway_store"], "null")   # d7eeeada: poke 81001010
        self.assertEqual(got["count"], "poke 81001024")        # 34df0f49: null (Skies 801559bc, no valid vector)
        self.assertEqual(got["two_unknowns"], "poke 81001034")  # 34df0f49: null (Skies 8019ae84, 15 dev vectors)
        self.assertEqual(got["call_result"], "null")
        self.assertEqual([got[f"blind_{k}"] for k in range(3)], ["poke 81001040", "poke 81001044", "poke 81001048"])
        self.assertEqual(got["blind_over"], "null")
        self.assertEqual(got["count_reg"], "r6")
        self.assertEqual(got["sum_load"], "poke 81001010")  # the base + index repair is kept
        self.assertEqual(got["sum_reg"], "r3")
        # Skies r5d (2026-11-13): the pointer repair spends TcVectors' 12 re-runs carefully. a8b9ae07 gave each case below
        # the plain one-word repair (or none), shown on the right.
        arr = ["81001400"] + [f"{0x81001024 + 0x18 * j:x}" for j in range(42)]  # stride 0x18 to the chunk's end
        self.assertEqual(got["array_stride"], " ".join("poke " + a for a in arr))  # a8b9: poke 81001400 poke 81001054
        self.assertEqual(got["own_write"], "poke 81001400 poke 81001090")  # a8b9: poke 81001400 poke 81001080
        self.assertEqual(got["null_first_0"], "poke 81001100")  # a8b9: poke 81001400 poke 81001100
        self.assertEqual(got["null_first_1"], "poke 81001400 poke 81001100")  # a8b9: null
        self.assertEqual(got["blind_near_null"], "null")  # a8b9: poke 81001200
        self.assertEqual(got["blind_values"], "1 1 1 1")  # a8b9: rnd.nextInt(16), which moved every later vector


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class FrameSnap(unittest.TestCase):
    def test_cases(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as out:
            src = os.pathsep.join([str(GC / "ghidra_scripts"), str(GC / "template"), str(SHARED)])
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(TOOL / "tests" / "java" / "TcGcFrameSnapStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 8e16304d: no TcStrict.extent or saveLo
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcGcFrameSnapStub"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        got = {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}
        self.assertEqual(got["saveLo_stmw"], "ec")
        self.assertEqual(got["vertex_array"], "16")    # was 0: never compared
        self.assertEqual(got["buffer"], "64")          # RUN_MAX
        self.assertEqual(got["next_to_saves"], "12")   # stops at the stmw's r27
        self.assertEqual(got["in_saves"], "0")
        self.assertEqual(got["caller_frame"], "0")
        self.assertEqual(got["below_sp"], "0")
        self.assertEqual(got["heap"], "64")            # unchanged
        self.assertEqual(got["saveLo_helper"], "3c")   # r11 = r1 + 0x60, r29 at -0x24
        self.assertEqual(got["saveLo_before_stwu"], "1c")
        self.assertEqual(got["saveLo_none"], "true")


class Judge(unittest.TestCase):
    def setUp(self):
        self.dj = load_dp_judge()
        self.tmp = tempfile.TemporaryDirectory()
        self.h = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def harness(self, meta, summary, expected="V 0 dev\nRET 0 0 0\nE\n"):
        (self.h / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (self.h / "oracle_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (self.h / "expected.txt").write_text(expected, encoding="utf-8")
        return meta

    def test_refused(self):
        why = "oracle: vector generation did not finish in time (dev 48 of 215 tried valid, hidden 29 of 130)"
        meta = self.harness({"unsupported": why}, {"dev_valid": 0, "hidden_valid": 0}, expected="")
        self.assertEqual(self.dj.refusal(meta, self.h), why)

    def test_zero_vectors(self):
        meta = self.harness({"unsupported": None}, {"dev_valid": 0, "hidden_valid": 0}, expected="")
        self.assertIn("no expected results", self.dj.refusal(meta, self.h))
        meta = self.harness({"unsupported": None}, {"dev_valid": 20, "hidden_valid": 0, "invalid": {"fault": 3}})
        self.assertIn("hidden set", self.dj.refusal(meta, self.h))

    def test_judged(self):
        """A full or a thin harness is judged: a planted wrong C still FAILs there (the judge decides, not this)."""
        for dev, hidden in ((48, 48), (13, 12)):
            meta = self.harness({"unsupported": None}, {"dev_valid": dev, "hidden_valid": hidden})
            self.assertIsNone(self.dj.refusal(meta, self.h))

    def test_thin_note(self):
        res = {"verdict": "FAIL", "stage": "compare", "failing": 0,
               "vectors": {"dev": {"total": 13}, "hidden": {"total": 12}}}
        self.assertEqual(self.dj.thin_note(res, 16),
                         ["too few valid vectors, none wrong: dev 13, hidden 12 (the judge needs 16 in each)"])
        self.assertEqual(self.dj.thin_note(dict(res, failing=2), 16), [])  # wrong C: no note, the FAIL is the C's
        self.assertEqual(self.dj.thin_note(dict(res, verdict="PASS"), 16), [])


if __name__ == "__main__":
    unittest.main()

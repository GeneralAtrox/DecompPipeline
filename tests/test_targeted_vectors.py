"""Targeted vectors (pre-wave item 1; target.json "targeted_vectors", TcBound and each target's TcBoundPass).

1. TcBound's harvest on hand-built p-code (stub, tests/java/TcBoundStub.java): a compare's constant operand; a
   constant reaching a compare through COPY, a load from the image (a literal pool), a sign extension, integer
   arithmetic (lis/ori), or FLOAT2FLOAT (lfs into a double register); a register overwritten by an unknown value
   is forgotten; float compares set fcmp and keep their f32 or f64 constants; the target's extra constants join.
2. Its draws: every integer value at the asked width is a harvested c or c +- 1, or an edge (0, 1, -1, 0xff, 0xffff,
   the signed minimum and maximum), masked to the width; every edge comes up; floats are harvested ones one ulp either
   way or the specials, widened for f64; the same seed gives the same draws.
3. The wiring: TcBound is one file in every target, DC and Saturn share TcBoundPass, both are locked; TcVectors keeps
   valid vectors for the pass, TcPrepare runs it after the reach pass and reports "bound", TcReach takes its
   constants; the facts file has "targeted_vectors 1" only with the flag, every TcFacts reads it, and the flag is off.
The pass runs the oracle (Ghidra): its proof is a harness byte-identical with the flag off (tools/oracle_probe.py) and
the directions it adds on a sample. Run: python -W error tests/test_targeted_vectors.py (also from test_tooling.py).
"""
import atexit
import json
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_fsca_rm import CP, jdk  # noqa: E402
from test_strict_judge import ON_TARGETS  # noqa: E402  (the targets the proven flags are on for)

from core import ghidra, paths  # noqa: E402

TARGETS = [ROOT / "targets" / t for t in ("dreamcast_sh4", "saturn_sh2", "gamecube_ppc")]
_BUILT = []


def text(p):
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def stub(case):
    """TcBoundStub's output for case: {"I": [...], "F": ..., "C": bool, "V1": [...], ...}."""
    work = ROOT / "tests" / ".work"
    work.mkdir(parents=True, exist_ok=True)
    if not _BUILT:
        tmp = tempfile.TemporaryDirectory(prefix="bound_", dir=work)
        atexit.register(tmp.cleanup)
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", tmp.name, "-cp", CP, "-sourcepath",
                            str(TARGETS[0] / "ghidra_scripts"), str(ROOT / "tests" / "java" / "TcBoundStub.java")],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        _BUILT.append(tmp.name)
    f = pathlib.Path(_BUILT[0], "case.txt")
    f.write_text(case)
    r = subprocess.run([jdk("java"), "-cp", _BUILT[0] + os.pathsep + CP, "TcBoundStub", str(f)], capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    out = {}
    for line in r.stdout.splitlines():
        k, _, rest = line.partition(" ")
        if k == "C":
            out[k] = rest == "true"
        else:  # each S line appends its draws
            out.setdefault(k, []).extend(int(x, 16) for x in rest.split())
    return out


def f32(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def f64(bits32):
    return struct.unpack("<Q", struct.pack("<d", struct.unpack("<f", struct.pack("<I", bits32))[0]))[0]


SPACE = "c:1:8"  # a LOAD's address-space input


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Harvest(unittest.TestCase):
    def test_ints(self):
        case = "\n".join([
            "O INT_EQUAL u:10:1 r:0:4 c:5:4",                      # cmp/eq #5,r0
            "O COPY r:4:4 c:ffffffff:4", "O INT_SLESS u:20:1 r:4:4 r:10:4",  # mov #-1,r1; cmp/gt r1,r4
            "O LOAD r:8:4 %s c:8c001000:4" % SPACE, "O INT_EQUAL u:30:1 r:8:4 r:c:4",  # a literal pool word
            "M 8c001000 4 1234",
            "O COPY r:14:4 c:7:4", "O INT_ADD r:14:4 r:18:4 r:1c:4", "O INT_EQUAL u:40:1 r:14:4 r:20:4",  # forgotten
            "O COPY r:24:4 c:80000000:4", "O INT_OR r:24:4 r:24:4 c:10:4", "O INT_LESS u:50:1 r:28:4 r:24:4",
            "O COPY u:60:1 c:80:1", "O INT_SEXT r:2c:4 u:60:1", "O INT_EQUAL u:70:1 r:2c:4 r:30:4",  # mov.b #-128
            "O INT_ADD r:34:4 r:34:4 c:1:4",  # not a compare: no harvest
            "X 3c",
        ])
        got = stub(case)
        self.assertEqual(got["I"], [5, 0xffffffff, 0x1234, 0x80000010, 0xffffff80, 0x3c])
        self.assertEqual((got["F"], got["D"], got["C"]), ([], [], False))

    def test_floats(self):
        c = f32(0.999)
        case = "\n".join([
            "O LOAD r:100:4 %s c:8c002000:4" % SPACE, "M 8c002000 4 %x" % c,
            "O FLOAT_LESS u:10:1 r:100:4 r:104:4",                    # fcmp/gt against a pool float
            "O LOAD u:20:4 %s c:80400000:4" % SPACE, "M 80400000 4 %x" % f32(2.5),
            "O FLOAT2FLOAT r:200:8 u:20:4", "O FLOAT_LESS u:30:1 r:200:8 r:208:8",  # lfs, fcmpu
            "O FLOAT_NAN u:40:1 r:210:8",
            "Y %x" % f32(1.5),
        ])
        got = stub(case)
        self.assertEqual(got["F"], [c, f32(1.5)])
        self.assertEqual(got["D"], [f64(f32(2.5))])
        self.assertTrue(got["C"])
        self.assertEqual(got["I"], [])


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Draws(unittest.TestCase):
    def test_ints(self):
        got = stub("O INT_EQUAL u:10:1 r:0:4 c:1234:4\nO INT_SLESS u:20:1 r:0:4 c:ffffff80:4\nS 7 600\nS 7 600\n")
        for size in (1, 2, 4):
            mask = (1 << (8 * size)) - 1
            lo = 1 << (8 * size - 1)
            edges = {0, 1, mask, 0xff & mask, 0xffff & mask, lo, lo - 1}
            near = {(c + d) & mask for c in (0x1234, 0xffffff80) for d in (-1, 0, 1)}
            vals = got["V%d" % size]
            self.assertEqual(len(vals), 1200)
            self.assertEqual(vals[:600], vals[600:])  # the same seed, the same draws
            self.assertLessEqual(set(vals), edges | near, size)
            self.assertLessEqual(edges | near, set(vals), size)  # all come up

    def test_floats(self):
        c = f32(0.999)
        got = stub("O LOAD r:100:4 %s c:8c002000:4\nM 8c002000 4 %x\nO FLOAT_LESS u:10:1 r:100:4 r:104:4\nS 3 800\n"
                   % (SPACE, c))
        specials = [0, 0x80000000, 0x7f800000, 0xff800000, 0x7f7fffff, 0xff7fffff, 1, 0x80000001, f32(1), f32(-1)]
        want = set(specials) | {c - 1, c, c + 1}
        self.assertEqual(set(got["G"]), want)
        self.assertEqual(set(got["H"]), {f64(x) for x in want} | {0x7fefffffffffffff, 1, 0x8000000000000001})
        none = stub("O INT_EQUAL u:10:1 r:0:4 r:4:4\nS 3 300\n")  # nothing harvested: the edges only
        self.assertLessEqual(set(none["V1"]), {0, 1, 0xff, 0x80, 0x7f})
        self.assertEqual(set(none["G"]), set(specials))


class Wiring(unittest.TestCase):
    def test_scripts(self):
        one = text(TARGETS[0] / "ghidra_scripts" / "TcBound.java")
        dc_pass = text(TARGETS[0] / "ghidra_scripts" / "TcBoundPass.java")
        for t in TARGETS:
            g = t / "ghidra_scripts"
            self.assertEqual(text(g / "TcBound.java"), one, t.name)
            if t.name != "gamecube_ppc":
                self.assertEqual(text(g / "TcBoundPass.java"), dc_pass, t.name)
            locked = json.loads(text(t / "target.json"))["locked_scripts"]
            self.assertIn("TcBound.java", locked, t.name)
            self.assertIn("TcBoundPass.java", locked, t.name)
            prep = text(g / "TcPrepare.java")
            self.assertIn("if (facts.branchCoverage || facts.targetedVectors) dirs = TcDirs.of(listing, body);", prep)
            self.assertIn("bound = why == null ? TcBoundPass.run(this, proto, seed, new ArrayList<>(globals)) : null;",
                          prep)
            self.assertIn('(bound != null ? bound + "," : "")', prep)
            self.assertIn('case "targeted_vectors": f.targetedVectors = ', text(g / "TcFacts.java"))
            vec = text(g / "TcVectors.java")
            if t.name == "gamecube_ppc":
                self.assertIn("if (facts.targetedVectors) bases.add(new TcBoundPass.Base(v, r));", vec)
                continue
            self.assertLess(prep.index("String reach = why == null ? TcReach.run("), prep.index("String bound = "))
            self.assertIn("if (facts.reachRuns > 0 || facts.targetedVectors) seeds.add(TcReach.entry(this, v, r));",
                          vec)
            reach = text(g / "TcReach.java")
            self.assertIn("consts = TcGuide.constants(g.listing, g.body, g::poolValue);", reach)  # flag-off picks
            self.assertNotIn("TcBoundPass.consts", reach)
            self.assertLessEqual(len(reach.splitlines()), 400, t.name)
            self.assertLessEqual(len(vec.splitlines()), 400, t.name)

    def test_facts(self):
        cfg = dict(paths.config())
        cfg.pop("targeted_vectors", None)
        self.assertNotIn("targeted_vectors", ghidra.facts_body(cfg))
        self.assertIn("targeted_vectors 1\n", ghidra.facts_body(dict(cfg, targeted_vectors=True)))
        for t in TARGETS:  # off until a landing turns it on
            self.assertEqual(json.loads(text(t / "target.json")).get("targeted_vectors", False), t.name in ON_TARGETS,
                             t.name)


if __name__ == "__main__":
    unittest.main()

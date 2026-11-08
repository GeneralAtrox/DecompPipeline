"""Targeted vectors (pre-wave item 1; target.json "targeted_vectors", TcBound and each target's TcBoundPass).

1. TcBound's harvest on hand-built p-code (stub, tests/java/TcBoundStub.java): a compare's constant operand; a
   constant reaching a compare through COPY, a load from the image (a literal pool), a sign extension, integer
   arithmetic (lis/ori), or FLOAT2FLOAT (lfs into a double register); a register overwritten by an unknown value
   is forgotten; float compares set fcmp and keep their f32 or f64 constants; the target's extra constants join.
2. Its draws: every integer value at the asked width is a harvested c or c +- 1, or an edge (0, 1, -1, 0xff, 0xffff,
   the signed minimum and maximum), masked to the width; every edge comes up; floats are harvested ones one ulp either
   way or the specials, widened for f64; the same seed gives the same draws.
   TcBound.Edges (target.json "boundary_vectors"): the sites (integer and float compares of cmp*/fcmp* instructions on
   registers the instruction has not written, or constants) and the edges a run makes there (operands equal or one
   apart; floats equal).
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

TARGETS = [ROOT / "targets" / t for t in ("dreamcast_sh4", "saturn_sh2", "gamecube_ppc", "xbox360_ppc")]
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
        elif k.startswith("T"):
            out[k] = [int(x, 16) for x in rest.split()]
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


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Edges(unittest.TestCase):
    def test_sites_and_hits(self):
        got = stub("\n".join([
            "E 100 cmp/ge", "O INT_SLESSEQUAL r:3c:1 r:4:4 r:0:4",  # cmp/ge r4,r0: site 0
            "E 102 cmp/eq", "O INT_SEXT u:10:4 c:fb:1", "O INT_EQUAL r:3c:1 r:0:4 u:10:4",  # cmp/eq #-5,r0: site 1
            "E 104 dt", "O INT_SUB r:4:4 r:4:4 c:1:4", "O INT_EQUAL r:3c:1 r:4:4 c:0:4",  # not a compare instruction
            "E 106 cmp/str", "O INT_XOR u:20:4 r:0:4 r:4:4", "O INT_EQUAL u:30:1 u:20:4 c:0:4",  # an unknown unique
            "E 108 cmp/hi", "O INT_ADD r:8:4 r:8:4 c:1:4", "O INT_LESS r:3c:1 r:c:4 r:8:4",  # r8 written before
            "E 10a fcmp/eq", "O FLOAT_EQUAL r:3c:1 r:100:4 r:104:4",  # site 2: equal floats only
            "E 10c cmp/pz", "O INT_SLESSEQUAL r:3c:1 c:0:4 r:4:4",  # site 3: a constant first
            "R 0 5", "R 4 5", "T 100", "R 4 6", "T 100", "R 4 4", "T 100", "R 4 9", "T 100",
            "R 0 fffffffb", "T 102", "R 0 fffffffa", "T 102", "T 104", "T 106", "T 108",
            "R 100 3f800000", "R 104 3f800000", "T 10a", "R 100 0", "R 104 80000000", "T 10a", "R 104 1", "T 10a",
            "R 4 ffffffff", "T 10c", "R 4 0", "T 10c",
        ]))
        self.assertEqual(got["N"], [4, 0, 1, 2, 3, 4, 5, 7, 9, 10, 11])  # 4 sites; a float site has one edge
        self.assertEqual([got[f"T{i}"] for i in range(1, 5)], [[1], [2], [0], []])  # r4 - r0: 0, +1, -1, 4
        self.assertEqual((got["T5"], got["T6"]), ([4], [3]))  # r0 - (-5): 0, then -1 (a sign-extended constant)
        self.assertEqual((got["T7"], got["T8"], got["T9"]), ([], [], []))
        self.assertEqual((got["T10"], got["T11"], got["T12"]), ([7], [7], []))  # 1.0 == 1.0, +0 == -0, 0 != tiny
        self.assertEqual((got["T13"], got["T14"]), ([11], [10]))  # 0 - (-1) = +1; 0 - 0


class StampScope(unittest.TestCase):
    """The edge pass moves no stamp while boundary_vectors is off: each target's scopes name today's files (TcBound
    in every target), and a "flags" scope keeps a function's stamp while its flags are off, with no target hook
    (core/oracle_stamp.scoped_hashes)."""

    def test_after_hashes(self):
        import hashlib
        for t in TARGETS:
            scopes = json.loads(text(t / "target.json")).get("stamp_scopes") or []
            for sc in scopes:
                for n, (_, after) in sc["scripts"].items():
                    self.assertEqual(hashlib.sha256((t / "ghidra_scripts" / n).read_bytes()).hexdigest(), after,
                                     (t.name, n))
            self.assertTrue(any("TcBound.java" in sc["scripts"] for sc in scopes), t.name)

    def test_flags(self):
        import hashlib
        from unittest import mock
        from core import oracle_stamp, target_hooks
        with tempfile.TemporaryDirectory(prefix="bvscope_") as d:
            d = pathlib.Path(d)
            (d / "image.bin").write_bytes(bytes(4))
            cfg = {"binary_path": d / "image.bin", "load_address": "0x8c010000", "binary_sha256": "ab"}
            f = d / "TcBound.java"
            f.write_text("old", encoding="utf-8")
            with mock.patch.object(target_hooks, "get", return_value=None):
                before = oracle_stamp.stamp_of(cfg, "8c010000", {f.name: f}, "-", None, 48, 48)
                f.write_text("newer", encoding="utf-8")  # a new size: file_hash caches on mtime and size, and both writes can share one mtime tick
                sha = [hashlib.sha256(x.encode()).hexdigest() for x in ("old", "newer")]
                sc = dict(cfg, stamp_scopes=[{"scope": "boundary_vectors", "flags": ["boundary_vectors"],
                                              "scripts": {f.name: sha}}])
                stamp = lambda c: oracle_stamp.stamp_of(c, "8c010000", {f.name: f}, "-", None, 48, 48)  # noqa: E731
                self.assertEqual(stamp(sc), before)  # flag off: kept
                self.assertNotEqual(stamp(dict(sc, boundary_vectors=True)), before)  # on: re-prepared
                self.assertNotEqual(stamp(cfg), before)  # no scope: every stamp moves
                # 2026-11-07: a "finder" scope (TcOut's tail jumps): with its flag on, only the functions the finder
                # names move; the flag's facts line is left out of every stamp
                fd = dict(sc, stamp_scopes=[dict(sc["stamp_scopes"][0], flags=["tcout_tail_exports"],
                                                 finder="regout_tail")])
                with mock.patch.dict(oracle_stamp.FINDERS, regout_tail=lambda c, a: a == "8c010000"):
                    self.assertEqual(stamp(fd), before)
                    self.assertNotEqual(stamp(dict(fd, tcout_tail_exports=True)), before)  # in its scope
                    other = lambda c: oracle_stamp.stamp_of(c, "8c010100", {f.name: f}, "-", None, 48, 48)  # noqa
                    self.assertEqual(other(dict(fd, tcout_tail_exports=True)), other(fd))  # outside: kept
                self.assertEqual(oracle_stamp.facts_hashed("a 1\ntcout_tail_exports 1\nb 2\n"), "a 1\nb 2\n")


class Wiring(unittest.TestCase):
    def test_scripts(self):
        one = text(TARGETS[0] / "ghidra_scripts" / "TcBound.java")
        dc_pass = text(TARGETS[0] / "ghidra_scripts" / "TcBoundPass.java")
        for t in TARGETS:
            g = t / "ghidra_scripts"
            self.assertEqual(text(g / "TcBound.java"), one, t.name)
            if t.name not in ("gamecube_ppc", "xbox360_ppc"):  # the PowerPC passes are their own
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
            if t.name in ("dreamcast_sh4", "saturn_sh2"):  # the edge pass: the last vectors; edges recorded per run
                self.assertIn('case "boundary_vectors": f.boundaryVectors = ', text(g / "TcFacts.java"))
                self.assertIn("if (facts.boundaryVectors) edges = TcBound.Edges.of(listing, body);", prep)
                self.assertIn("edges.hit(res.edges, pc, ms::getValue);", text(g / "TcOracle.java"))
                tail = prep[prep.index("String edge = why == null ? TcBoundPass.edges("):]
                self.assertNotIn("TcBoundPass.", tail[len("String edge = why == null ? TcBoundPass.edges("):])
                self.assertNotIn("TcFpu.once(", tail)
                self.assertLess(prep.index("String bound = "), prep.index("String edge = "))
            vec = text(g / "TcVectors.java")
            if t.name == "gamecube_ppc":
                self.assertIn("if (facts.targetedVectors) bases.add(new TcBoundPass.Base(v, r));", vec)
                continue
            if t.name == "xbox360_ppc":  # the bases are the valid vectors TcVectors keeps for the width checks
                self.assertIn("baseVecs = new ArrayList<>(g.goodVecs);", text(g / "TcBoundPass.java"))
                self.assertLess(prep.index("bound = why == null ?"), prep.index("probeWidths(proto)"))
                continue
            self.assertLess(prep.index("String reach = why == null ? TcReach.run("), prep.index("String bound = "))
            self.assertIn("if (facts.reachRuns > 0 || facts.targetedVectors) seeds.add(TcReach.entry(this, v, r));",
                          vec)
            reach = text(g / "TcReach.java")
            self.assertIn("consts = TcGuide.constants(g.listing, g.body, g::poolValue);", reach)  # flag-off picks
            self.assertNotIn("TcBoundPass.consts", reach)
            self.assertLessEqual(len(reach.splitlines()), 400, t.name)
            # Saturn's TcVectors is 402 lines (7f2b585a): editing it to fit would move Saturn's oracle stamp, so
            # its cap is its size (the coordinator's decision, 2026-11-06); it may only shrink.
            self.assertLessEqual(len(vec.splitlines()), 402 if t.name == "saturn_sh2" else 400, t.name)

    def test_facts(self):
        cfg = dict(paths.config())
        cfg.pop("targeted_vectors", None)
        self.assertNotIn("targeted_vectors", ghidra.facts_body(cfg))
        self.assertIn("targeted_vectors 1\n", ghidra.facts_body(dict(cfg, targeted_vectors=True)))
        cfg.pop("boundary_vectors", None)
        self.assertNotIn("boundary_vectors", ghidra.facts_body(cfg))
        self.assertIn("boundary_vectors 1\n", ghidra.facts_body(dict(cfg, boundary_vectors=True)))
        cfg.pop("tcout_tail_exports", None)  # 2026-11-07: TcOut.readAfter (Dreamcast: on, scoped by its finder)
        self.assertNotIn("tcout_tail_exports", ghidra.facts_body(cfg))
        self.assertIn("tcout_tail_exports 1\n", ghidra.facts_body(dict(cfg, tcout_tail_exports=True)))
        for t in TARGETS:  # off until a landing turns it on
            self.assertEqual(json.loads(text(t / "target.json")).get("targeted_vectors", False), t.name in ON_TARGETS,
                             t.name)


if __name__ == "__main__":
    unittest.main()

"""Branch-direction coverage in verdicts (pre-wave item 3; target.json "branch_coverage", TcDirs).

1. TcDirs' rule, checked against a model on random runs (stub, tests/java/TcDirsStub.java): a branch with a fixed
   target counts taken at its target and fallthrough at its fallthrough (both when they are one address); one with a
   computed target (a conditional return) counts taken at any pc but its fallthrough; no step after a mocked call
   (prev -1) or after a non-branch counts; the summary fields and their unhit list (at most 64).
2. The wiring: TcDirs is one file in every target and locked; each TcOracle records before the SENTINEL check and
   stays within its line count; TcVectors, TcReach, TcRescue and TcPrepare carry the dev and all sets beside the
   block sets; the facts file has "branch_coverage 1" only with the flag, and every TcFacts reads it.
3. The grade (core/proof.grade): "fully proven" when every block and every branch direction is hit, else "proven
   where tested", in coverage_verdict and check._finish; a summary without direction data gets no new key (the flag
   off leaves every verdict as before).
The oracle runs need Ghidra: the proof is a harness byte-identical with the flag off (tools/oracle_probe.py) and the
direction counts of a sample with it on. Synthetic data. Run: python -W error tests/test_branch_cov.py.
"""
import atexit
import json
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_fsca_rm import CP, jdk  # noqa: E402
from test_strict_judge import ON_TARGETS  # noqa: E402  (the targets the proven flags are on for)

from core import ghidra, paths, proof  # noqa: E402

TARGETS = [ROOT / "targets" / t for t in ("dreamcast_sh4", "saturn_sh2", "gamecube_ppc")]
LINES = {"dreamcast_sh4": 400, "saturn_sh2": 413, "gamecube_ppc": 294}  # each TcOracle's line count (DC's cap: 400)


def text(p):
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def model(branches, steps):
    """The directions hit: branches {addr: (fall, target or -1)} in address order, steps [(prev, pc)]."""
    order = {a: i for i, a in enumerate(sorted(branches))}
    out = set()
    for prev, pc in steps:
        if prev not in order:
            continue
        i, (fall, target) = order[prev], branches[prev]
        if target < 0:
            out.add(2 * i if pc == fall else 2 * i + 1)
            continue
        if pc == fall:
            out.add(2 * i)
        if pc == target:
            out.add(2 * i + 1)
    return out


_BUILT = []


def stub(case):
    """TcDirsStub's output for case (compiled once per run, into tests/.work)."""
    work = ROOT / "tests" / ".work"
    work.mkdir(parents=True, exist_ok=True)
    if not _BUILT:
        tmp = tempfile.TemporaryDirectory(prefix="dirs_", dir=work)
        atexit.register(tmp.cleanup)
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", tmp.name, "-cp", CP, "-sourcepath",
                            str(TARGETS[0] / "ghidra_scripts"), str(ROOT / "tests" / "java" / "TcDirsStub.java")],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        _BUILT.append(tmp.name)
    f = pathlib.Path(_BUILT[0], "case.txt")
    f.write_text(case)
    r = subprocess.run([jdk("java"), "-cp", _BUILT[0] + os.pathsep + CP, "TcDirsStub", str(f)], capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return r.stdout.split(chr(10))


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Rule(unittest.TestCase):
    def test_model(self):
        rnd = random.Random(11)
        cases, want = [], []
        for _ in range(40):
            addrs = sorted(rnd.sample(range(0x8c010000, 0x8c010400, 2), rnd.randrange(1, 12)))
            br = {}
            for a in addrs:
                fall = a + rnd.choice([2, 4])  # a delayed branch's fallthrough is after its slot
                target = rnd.choice([-1, fall, a - 16, a + 40])
                br[a] = (fall, target)
            pcs = addrs + [f for f, _ in br.values()] + [t for _, t in br.values() if t >= 0] + [0x8c020000]
            steps = [(rnd.choice(addrs + [-1, 0x8c00fff0]), rnd.choice(pcs)) for _ in range(rnd.randrange(0, 30))]
            lines = ["B %x %x %x" % (a, f, t) if t >= 0 else "B %x %x -1" % (a, f) for a, (f, t) in br.items()]
            lines += ["H %x %x" % (p, c) if p >= 0 else "H -1 %x" % c for p, c in steps]
            cases.append(("\n".join(lines) + "\n", br, steps))
        for case, br, steps in cases:
            got = stub(case)
            hit = sorted(model(br, steps))
            self.assertEqual(got[0], " ".join(map(str, hit)), case)
            self.assertIn('"branch_dirs_total":%d,' % (2 * len(br)), got[1])
            self.assertIn('"branch_dirs_hit_all":%d,' % len(hit), got[1])
            self.assertEqual('"branch_dirs_unhit":[' in got[1], len(hit) < 2 * len(br))

    def test_cases(self):
        # 100: fixed target 200, fallthrough 104 (a delayed branch); 110: target == fallthrough; 120: computed
        case = "B 100 104 200\nB 110 112 112\nB 120 122 -1\nH 100 104\nD\nH 110 112\nH 120 9000\nH -1 200\nH 104 200\n"
        got = stub(case)
        self.assertEqual(got[0], "0 2 3 5")
        self.assertEqual(got[1], '"branch_dirs_total":6,"branch_dirs_hit_dev":1,"branch_dirs_hit_all":4,'
                                 '"branch_dirs_unhit":["00000100:t","00000120:f"],')
        many = "".join("B %x %x %x\n" % (a, a + 2, a + 50) for a in range(0x100, 0x100 + 2 * 40, 2))
        unhit = json.loads("{" + stub(many)[1].rstrip(",") + "}")["branch_dirs_unhit"]
        self.assertEqual(len(unhit), 64)
        full = stub("B 100 102 200\nH 100 102\nH 100 200\n")[1]
        self.assertNotIn("unhit", full)


class Wiring(unittest.TestCase):
    def test_scripts(self):
        one = text(TARGETS[0] / "ghidra_scripts" / "TcDirs.java")
        for t in TARGETS:
            g = t / "ghidra_scripts"
            self.assertEqual(text(g / "TcDirs.java"), one, t.name)
            self.assertIn("TcDirs.java", json.loads(text(t / "target.json"))["locked_scripts"], t.name)
            oracle = text(g / "TcOracle.java")
            self.assertEqual(len(oracle.splitlines()), LINES[t.name], t.name)
            hook = "if (dirs != null) dirs.hit(res.dirs, prev, pc);"
            self.assertEqual(oracle.count(hook), 1, t.name)
            self.assertLess(oracle.index(hook), oracle.index("if (pc == SENTINEL) break;"), t.name)
            self.assertLess(oracle.index("if (fold(pc) != pc) {"), oracle.index(hook), t.name)
            self.assertIn("Set<Integer> cov = new TreeSet<>(), dirs = new TreeSet<>();", oracle)
            vec = text(g / "TcVectors.java")
            self.assertIn("if (s == 0) { covDev.addAll(r.cov); dirsDev.addAll(r.dirs); }", vec)
            self.assertIn("covAll.addAll(r.cov); dirsAll.addAll(r.dirs);", vec)
            prep = text(g / "TcPrepare.java")
            self.assertIn("if (facts.branchCoverage || facts.targetedVectors) dirs = TcDirs.of(listing, body);", prep)
            self.assertIn("dirs.summary(dirsDev, dirsAll)", prep)
            self.assertIn("            dirsDev.clear();\n            dirsAll.clear();\n", prep)
            self.assertIn('case "branch_coverage": f.branchCoverage = ', text(g / "TcFacts.java"))
            if t.name != "gamecube_ppc":  # GameCube has no reach or rescue pass
                self.assertIn("g.covAll.addAll(e.r.cov); g.dirsAll.addAll(e.r.dirs);", text(g / "TcReach.java"))
                resc = text(g / "TcRescue.java")
                for line in ("s.dirsDev = t.dirsDev;", "t.dirsAll = new TreeSet<>();", "t.dirsAll = dirsAll;"):
                    self.assertIn(line, resc)

    def test_facts(self):
        cfg = dict(paths.config())
        cfg.pop("branch_coverage", None)
        self.assertNotIn("branch_coverage", ghidra.facts_body(cfg))
        self.assertIn("branch_coverage 1\n", ghidra.facts_body(dict(cfg, branch_coverage=True)))
        for t in TARGETS:  # the flag is off until a landing turns it on (the wave's harnesses record it)
            self.assertEqual(json.loads(text(t / "target.json")).get("branch_coverage", False), t.name in ON_TARGETS,
                             t.name)


def summ(blocks, hit, dirs=None, dhit=None, unhit=None):
    s = {"blocks_total": blocks, "blocks_hit_all": hit, "blocks_hit_dev": hit}
    if dirs is not None:
        s.update(branch_dirs_total=dirs, branch_dirs_hit_all=dhit, branch_dirs_hit_dev=dhit)
    if unhit:
        s["branch_dirs_unhit"] = unhit
    return s


class Grade(unittest.TestCase):
    def test_grade(self):
        self.assertIsNone(proof.grade(summ(8, 8)))
        self.assertIsNone(proof.grade(None))
        self.assertEqual(proof.grade(summ(8, 8, 6, 6)), {"grade": proof.FULL, "blocks": [8, 8],
                                                         "branch_dirs": [6, 6], "branch_pct": 100.0})
        self.assertEqual(proof.grade(summ(8, 8, 6, 5))["grade"], proof.TESTED)
        self.assertEqual(proof.grade(summ(8, 7, 6, 6))["grade"], proof.TESTED)
        self.assertEqual(proof.grade(summ(1, 1, 0, 0)), {"grade": proof.FULL, "blocks": [1, 1], "branch_dirs": [0, 0],
                                                         "branch_pct": 100.0})  # no branch: every block decides

    def test_verdict(self):
        gate = proof.coverage_settings({})
        before = proof.coverage_verdict(summ(8, 7), gate)
        self.assertNotIn("grade", before)
        v = proof.coverage_verdict(summ(8, 7, 6, 4, ["8c010000:t", "8c010008:f"]), gate)
        self.assertEqual({k: v[k] for k in before}, before)  # the gate's own fields are unchanged
        self.assertEqual((v["grade"], v["branch_dirs"], v["branch_pct"]), (proof.TESTED, [4, 6], 66.7))
        self.assertEqual(v["dirs_unhit"], ["8c010000:t", "8c010008:f"])
        rows = {"a": {"state": proof.VERIFIED, "coverage": dict(v, grade=proof.FULL), "mutation_flag": None,
                      "mutation": None}, "b": {"state": proof.VERIFIED, "coverage": before, "mutation_flag": None,
                                               "mutation": None}}
        f = proof.figure(rows)
        self.assertEqual((f["graded"], f["fully_proven"]), (1, 1))
        self.assertIn("fully proven 1 of 1 graded", proof.summary_line(f))
        self.assertNotIn("graded", proof.summary_line(proof.figure({"b": rows["b"]})))

    def test_finish(self):
        from core import check
        res = {"stage": "compare", "failing": 0, "vectors": {"dev": {"total": 40}, "hidden": {"total": 40}}}
        old = check._finish(dict(res), ["dev", "hidden"], summ(8, 8))
        self.assertNotIn("grade", old["coverage"])
        new = check._finish(dict(res), ["dev", "hidden"], summ(8, 8, 4, 4))
        self.assertEqual(new["coverage"]["grade"], proof.FULL)
        self.assertEqual(new["coverage"]["branch_dirs_total"], 4)
        self.assertEqual(new["verdict"], old["verdict"])  # report only


if __name__ == "__main__":
    unittest.main()

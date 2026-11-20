"""The Xbox 360 oracle's op-aware extra vectors (branch x360-judge-seedx; TcSeedx, TcVectors.seedExtras).

A diagnosis of 7 planted faults the judge missed found 5 never triggered by any of 96 random vectors: no halfword sum
saturated (82cbc588 vaddshs), no denormal product (82de7510, 82de7878 vmaddfp), every vector took 82a1d008's
alignment early exit. A function whose vector ops pick classes now gets "seed_extra" more vectors per set, appended
after the usual ones from their own generators, each drawn from one class: saturating extremes, tiny floats, or
16-aligned pointers with multiple-of-4 counts. oracle_summary.json "seedx" says what they reached that no usual vector
did, and tools/seedx_reach.py counts it over a workspace.
Checked here (no JDK, no Ghidra):
1. the facts line: "seed_extra N" written only when the config sets it, refused for another target, read by TcFacts;
2. the extras are appended and the usual vectors untouched (TcVectors source): sets 2 and 3 of the seed formula, the
   gen overrides draw nothing more while seedFocus is 0, and an extra vector never refuses the function;
3. TcVmx reports the case (event) and TcOracle notes it per pc; TcSeedx has no Ghidra import; the files stay under
   the 400-line cap;
4. seedx_reach: the headline count, cases by kind, and the --old check that the usual vectors are a byte-identical
   prefix of the new ones (a changed one is listed and exits 1).
Phase 2 (build box B, needs javac): 5. tests/java/TcSeedxStub - the classes per op list, determinism, halfword extremes
saturate vaddshs, floats near 2^-64 give denormal vmaddfp results, the den_in and no-case controls, align and count4,
and GATE on 82a1d008's own words (its gate and fields; GATE vectors always pass the gate and reach its loop).
usage: python tests/test_x360_seedx.py
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import TOOL, X360  # noqa: E402
from test_tail import jdk  # noqa: E402
from core import ghidra, paths  # noqa: E402

try:
    import seedx_reach  # noqa: E402
except ImportError:  # before this branch (the tests then fail, not error)
    seedx_reach = None

SCRIPTS = X360 / "ghidra_scripts"


def src(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


class Facts(unittest.TestCase):
    def cfg(self, **kw):
        cfg = paths.config()
        cfg.pop("seed_extra", None)
        cfg.update(kw)
        return cfg

    def test_line_only_when_set(self):
        self.assertNotIn("seed_extra", ghidra.facts_body(self.cfg()) or "")
        self.assertNotIn("seed_extra", ghidra.facts_body(self.cfg(seed_extra=0)) or "")
        body = ghidra.facts_body(self.cfg(seed_extra=16, target="xbox360_ppc"))
        self.assertIn("seed_extra 16\n", body)

    def test_other_target_refused(self):
        with self.assertRaises(ValueError):
            ghidra.facts_body(self.cfg(seed_extra=16, target="dreamcast_sh4"))

    def test_tcfacts_reads_it(self):
        s = src("TcFacts.java")
        self.assertIn('case "seed_extra": f.seedExtra = Integer.parseInt(v); break;', s)

    def test_off_in_target_json(self):
        # on per project (project.json "seed_extra"): turning it on changes every function's oracle stamp
        self.assertNotIn("seed_extra", json.loads((X360 / "target.json").read_text(encoding="utf-8")))


class Vectors(unittest.TestCase):
    def setUp(self):
        self.s = src("TcVectors.java")

    def test_extras_after_both_sets_own_generators(self):
        mk = self.s[self.s.index("void makeVectors"):self.s.index("int nextId")]
        self.assertIn("for (int s = 0; s < 2; s++)", mk)
        self.assertIn("rnd = new Random(seed * 31 + s * 0x5bd1e995L);", mk)
        self.assertLess(mk.index("break vectors"), mk.index("seedExtras("))
        ex = self.s[self.s.index("void seedExtras"):self.s.index("long word()")]
        self.assertIn("rnd = new Random(seed * 31 + (s + 2) * 0x5bd1e995L);", ex)
        self.assertIn("entryRnd = new Random((seed * 31 + (s + 2) * 0x5bd1e995L) ^ 0x5eedc0de12L);", ex)
        self.assertIn("seedFocus = 0;", ex[ex.index("finally"):])
        self.assertNotIn("outOfTime = true", ex)   # out of time, the extras stop; the function is not refused
        self.assertNotIn("outside =", ex)
        self.assertIn("refused = null;", ex)
        self.assertIn("facts.seedExtra <= 0", ex)

    def test_extras_search_for_novel_inputs(self):
        # 82a1d008 and 82cbc588: the rare inputs come about 1 vector in 35-40, so a valid extra vector that reaches
        # nothing new is kept only for half the quota and the rest of the try budget searches on
        ex = self.s[self.s.index("void seedExtras"):self.s.index("long word()")]
        self.assertIn("TcSeedx.novel(r.cov, r.cases, covAll, oldCases, seedCases)", ex)
        self.assertIn("TcSeedx.keepExtra(novel, seedPlain[s], facts.seedExtra)", ex)
        self.assertLess(ex.index("keepExtra"), ex.index("keep(s, v, r, true)"))
        self.assertIn('\\"skipped\\"', self.s)
        sx = src("TcSeedx.java")
        self.assertIn("return novel || plainKept < quota / 2;", sx)
        self.assertIn("TcSeedx.alignWord(rnd, scrPtr())", self.s)

    def test_overrides_draw_nothing_when_off(self):
        for sig in ("long word()", "int niceFloat()", "long scrPtr()", "long genChunk(Vec v, long offset)"):
            body = self.s[self.s.index(sig):]
            body = body[:body.index("\n    }\n")]
            # every extra draw is behind a seedFocus test; the rest is super's own draw
            for ln in body.splitlines()[1:]:
                if "rnd." in ln or "TcSeedx." in ln:
                    self.assertRegex(ln, r"seedFocus == TcSeedx\.\w+|^\s+for \(|^\s+byte\[\] d", (sig, ln))
            self.assertIn("super.", body, sig)

    def test_old_attempt_order_kept(self):
        at = self.s[self.s.index("Result attempt("):self.s.index("boolean keep(")]
        self.assertLess(at.index("drawEntry(v);"), at.index("applyHints(v, hints);"))
        self.assertLess(at.index("applyHints(v, hints);"), at.index("runOracle(v)"))
        mk = self.s[self.s.index("void makeVectors"):self.s.index("int nextId")]
        self.assertLess(mk.index("makeVec(nextId % 4"), mk.index("genRescue"))
        self.assertLess(mk.index("genRescue"), mk.index("attempt(v, hints"))

    def test_summary_carries_seedx(self):
        self.assertIn("seedxJson(blocks)", src("TcPrepare.java"))
        for k in ('\\"new_blocks\\"', '\\"new_cases\\"', '\\"old_cases\\"', '\\"classes\\"', '\\"stopped\\"'):
            self.assertIn(k, self.s)


class Cases(unittest.TestCase):
    def test_vmx_event(self):
        s = src("TcVmx.java")
        self.assertIn("public String event;", s)
        self.assertIn("TcSeedx.event(m,", s)
        o = src("TcOracle.java")
        self.assertIn('res.cases.add(hex(pc) + ":" + vm.event)', o)

    def test_locked(self):
        self.assertIn("TcSeedx.java", json.loads((X360 / "target.json").read_text(encoding="utf-8"))["locked_scripts"])

    def test_tcseedx_is_pure(self):
        s = src("TcSeedx.java")
        self.assertEqual(re.findall(r"^import .*", s, re.M), ["import java.util.*;"])

    def test_sizes(self):
        for n in ("TcVectors.java", "TcOracle.java", "TcVmx.java", "TcSeedx.java", "TcPrepare.java", "TcFacts.java"):
            self.assertLessEqual(len(src(n).splitlines()), 400, n)

    def test_line_endings_lf(self):
        for n in ("TcVectors.java", "TcOracle.java", "TcVmx.java", "TcSeedx.java", "TcPrepare.java", "TcFacts.java"):
            self.assertNotIn(b"\r\n", (SCRIPTS / n).read_bytes(), n)


class Gate(unittest.TestCase):
    """82a1d008's planted vmrghw swap was caught on seed offsets 0 and 2 but not 1: its vector loop runs only past an
    alignment gate on sums of argument-struct fields, plus a nonzero byte multiplier and a positive count, and the
    ALIGN words (drawn one by one) meet that about 1 try in 200. A function with such a gate gets GATE extras in place
    of ALIGN: the fields the gate reads are set to 16-multiples (TcSeedx.gateFill), so the gate is always passed."""

    def setUp(self):
        self.v = src("TcVectors.java")
        self.x = src("TcSeedx.java")

    def test_class_and_detection(self):
        self.assertIn("GATE = 8", self.x)
        self.assertIn('"gate"', self.x)
        for f in ("static int gateAt(int[] w)", "static int[][] gateFields(int[] w)", "static int withGate(",
                  "static void gateFill("):
            self.assertIn(f, self.x)

    def test_wired_into_extras_only(self):
        g = src("TcGateScan.java")
        seeds = g[g.index("static final class Seeds"):g.index("public void run()")]
        self.assertIn("gateFields = TcSeedx.gateFields(", seeds)   # TcGateScan.Seeds: the one GATE decision
        self.assertIn("TcSeedx.withGate(cw[0], gateFields)", seeds)
        mk = self.v[self.v.index("void makeVectors"):self.v.index("int nextId")]
        self.assertLess(mk.index("break vectors"), mk.index("seedExtras(sd.cw"))   # after the usual vectors
        ex = self.v[self.v.index("void seedExtras"):self.v.index("long word()")]
        fill = ex.index("TcSeedx.gateFill(")
        self.assertLess(ex.index("makeVec(0,"), fill)
        self.assertLess(fill, ex.index("attempt(v, hints"))
        self.assertIn("|| fo == TcSeedx.GATE && TcSeedx.rare(r.cov, blockHits)", ex)   # several vectors past the gate
        self.assertIn("if (seedFocus == TcSeedx.GATE) TcSeedx.gateFill(gateFields, v.r, v.patchAddr, v.patchData, SCR, "
                      "GEN_HEAP_LO,", ex)

    def test_gate_draws_as_align(self):
        self.assertIn("seedFocus == TcSeedx.ALIGN || seedFocus == TcSeedx.GATE) return TcSeedx.alignWord", self.v)
        self.assertIn("seedFocus == TcSeedx.ALIGN || seedFocus == TcSeedx.GATE ? TcSeedx.align(p) : p", self.v)


def summary(seedx=None, **kw):
    d = {"dev_valid": 48, "hidden_valid": 48}
    d.update(kw)
    if seedx is not None:
        d["seedx"] = seedx
    return d


VEC = "V {i} {s}\nR {r}\nE\n"   # r: 0 a usual vector, 9 an extra one


class Reach(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(seedx_reach, "tools/seedx_reach.py is missing")
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="seedx_", dir=paths.TOOL / "tests" / ".work"
                                               if (paths.TOOL / "tests" / ".work").is_dir() else None))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def put(self, root, addr, summ, nvec=2, extra=0):
        h = self.d / root / addr
        h.mkdir(parents=True)
        (h / "oracle_summary.json").write_text(json.dumps(summ), encoding="utf-8")
        v = "".join(VEC.format(i=i, s="dev" if i % 2 == 0 else "hidden", r=0 if i < nvec else 9) for i in range(nvec + extra))
        (h / "vectors.txt").write_text(v, encoding="utf-8")
        (h / "expected.txt").write_text(v.replace("R ", "RET 0 "), encoding="utf-8")

    def test_headline(self):
        sx = {"extra": 16, "classes": ["sat", "align"], "dev_valid": 16, "hidden_valid": 16, "stopped": None,
              "new_blocks": [], "new_cases": ["82cbc5f0:sat"], "old_cases": []}
        self.put("new", "82cbc588", summary(sx))
        self.put("new", "82a1d008", summary(dict(sx, classes=["align"], new_cases=[], new_blocks=["82a1d190"])))
        self.put("new", "82de7510", summary(dict(sx, classes=["tiny"], new_cases=["82de7600:den_in+den_out"],
                                                 hidden_valid=3, stopped="deadline")))
        self.put("new", "82000000", summary(dict(sx, classes=[], new_cases=[], dev_valid=0, hidden_valid=0)))
        self.put("new", "82111111", summary())    # no vector words: no seedx
        r = seedx_reach.reach(self.d / "new")
        self.assertEqual(r["functions"], 5)
        self.assertEqual(r["with_seedx"], 4)
        self.assertEqual(r["with_classes"], 3)
        self.assertEqual([g["addr"] for g in r["gained"]], ["82a1d008", "82cbc588", "82de7510"])
        self.assertEqual(r["cases_by_kind"], {"sat": 1, "den_in": 1, "den_out": 1})
        self.assertEqual(r["new_blocks"], 1)
        self.assertEqual(r["stopped"], ["82de7510"])
        self.assertEqual(r["short"], ["82de7510"])
        self.assertIn("GAINED 3 functions", seedx_reach.report(r))
        r = seedx_reach.reach(self.d / "new", only={"82cbc588", "82ffffff"})
        self.assertEqual((r["functions"], len(r["gained"]), r["missing"]), (1, 1, ["82ffffff"]))

    def test_old_prefix(self):
        self.put("old", "82cbc588", summary(), nvec=4)
        self.put("new", "82cbc588", summary({"classes": ["sat"]}), nvec=4, extra=4)
        self.put("old", "82de7510", summary(), nvec=4)
        self.put("new", "82de7510", summary({"classes": ["tiny"]}), nvec=3, extra=4)   # its 4th usual vector changed
        r = seedx_reach.reach(self.d / "new", old=self.d / "old")
        self.assertEqual(r["changed"], ["82de7510"])
        self.assertIn("CHANGED usual vectors in 1", seedx_reach.report(r))
        self.assertTrue(seedx_reach.prefix_same("V 0 dev\nR 1\nE\n", "V 0 dev\nR 1\nE\nV 1 dev\nR 2\nE\n"))
        self.assertFalse(seedx_reach.prefix_same("V 0 dev\nR 1\nE\n", "V 0 dev\nR 2\nE\n"))


@unittest.skipUnless(jdk("javac") and jdk("java"), "no JDK on this machine (phase 2: build box B)")
class Java(unittest.TestCase):
    def test_stub(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        out = tempfile.mkdtemp(prefix="seedx_", dir=work)
        try:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-sourcepath", str(SCRIPTS),
                                str(TOOL / "tests" / "java" / "TcSeedxStub.java")], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out, "TcSeedxStub"], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([x for x in r.stdout.splitlines() if x.startswith("WRONG")], [], r.stdout)
        self.assertIn("ALL OK", r.stdout)


if __name__ == "__main__":
    unittest.main()

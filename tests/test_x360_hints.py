"""The Xbox 360 judge on a fresh workspace (branch x360-judge-hints), without Ghidra:
1. Too Human's hand-written oracle hints are tracked with the target (targets/xbox360_ppc/games/too_human/hints, the
   image named by game.json) and x360setup's setup copies them into a new workspace (write_oracle,
   tools/game_hints.py). Without them a fresh workspace prepared 7 of the 533 judge-set functions (824c3530, 824d8420,
   82514780, 825ef068, 82bc5158, 82ea7690, 82eaf930) with too few valid vectors.
2. A workspace whose hints set differs from the reference (a file missing, extra, or other content) is warned about,
   one line, at setup and in dp_judge; line endings do not count; a workspace of another image has no reference.
3. A check that compared every vector, none wrong and no runner error, on fewer valid vectors than min_valid_vectors
   is NO_VECTORS on the Xbox 360 (x360_check.finish), not FAIL; a wrong vector, or a runner error, is still FAIL; and
   NO_VECTORS is never a PASS, nor is its exit state in dp_judge's row. core/check._finish is unchanged (still FAIL), so
   the other targets' verdicts are untouched.
4. TcSwitchHits (oracle_summary.json "switch_hits", report only): a bcctr without link is noted with its CTR target,
   a bctrl or blr is not, and kept runs are counted per bctr (tests/java/X360TrackStub, javac against the scripts);
   TcOracle notes it before the step, TcVectors adds a run once its vector is kept, TcPrepare writes it; it is locked.
usage: python tests/test_x360_hints.py
"""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import TOOL, X360, tmpdir  # noqa: E402
from test_tail import CP, jdk  # noqa: E402

import x360_check  # noqa: E402
import x360setup  # noqa: E402
from core import check as core_check  # noqa: E402
try:
    import game_hints  # noqa: E402
except ImportError:  # before this branch: no tracked hints and nothing compares them (the tests then fail, not error)
    game_hints = None
from core import javalint  # noqa: E402

sys.path.insert(0, str(X360 / "translator" / "tools"))
import dp_judge  # noqa: E402

TH = X360 / "games" / "too_human"
TH_SHA = "8f197c63dd0ab6488258dcc05f64975e391e18c467d7be60d905034f1e6dbf79"   # Too Human's image.bin
SCRIPTS = X360 / "ghidra_scripts"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"
SEVEN = ("824c3530", "824d8420", "82514780", "825ef068", "82bc5158", "82ea7690", "82eaf930")


def text(p):
    return pathlib.Path(p).read_bytes().decode("utf-8").replace("\r\n", "\n")


class Hints(unittest.TestCase):
    def setUp(self):
        self.ws = tmpdir("x360hints_")
        self.sha = TH_SHA
        (self.ws / "project.json").write_text(json.dumps({"binary_sha256": self.sha}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_tracked_set(self):
        names = sorted(p.name for p in (TH / "hints").glob("*.txt"))
        self.assertEqual(len(names), 15)  # 8, and the 82d684e0 family (test_x360_ptrseed)
        self.assertTrue((TH / "game.json").is_file())
        self.assertEqual(json.loads(text(TH / "game.json"))["binary_sha256"], TH_SHA)
        for a in SEVEN:
            self.assertIn(f"{a}.txt", names)
        for n in names:
            self.assertEqual(javalint.lint_hints(text(TH / "hints" / n)), [], n)
        self.assertEqual(game_hints.game_of(self.sha.upper()), TH)

    def test_setup_copies_them(self):
        copied = x360setup.write_oracle(self.ws)
        self.assertEqual(len(copied), 15)
        self.assertTrue((self.ws / "oracle" / "TcInputs.java").is_file())
        for n in copied:
            self.assertEqual((self.ws / "oracle" / "hints" / n).read_bytes(), (TH / "hints" / n).read_bytes())
        self.assertEqual(game_hints.differences(self.ws), [])
        body = text(X360 / "tools" / "x360setup.py").split("def setup(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(body.index('(ws / "project.json").write_text('), body.index("write_oracle(ws)"))

    def test_warns_on_a_difference(self):
        x360setup.write_oracle(self.ws)
        h = self.ws / "oracle" / "hints"
        crlf = (h / "82eaf930.txt").read_bytes().replace(b"\n", b"\r\n")
        (h / "82eaf930.txt").write_bytes(crlf)
        self.assertEqual(game_hints.differences(self.ws), [])   # line endings do not count
        (h / "824c3530.txt").unlink()
        err = io.StringIO()
        self.assertEqual(game_hints.warn(self.ws, "test", out=err), ["missing 824c3530.txt"])
        self.assertEqual(len(err.getvalue().splitlines()), 1)
        self.assertIn("WARNING (test)", err.getvalue())
        (h / "82bc5158.txt").write_text("genptr r3\n", encoding="utf-8")
        (h / "80000000.txt").write_text("genptr r4\n", encoding="utf-8")
        self.assertEqual(game_hints.differences(self.ws),
                         ["missing 824c3530.txt", "extra 80000000.txt", "differs 82bc5158.txt"])
        self.assertEqual(game_hints.install(self.ws), ["824c3530.txt"])   # a held file is kept, a missing one copied
        self.assertEqual(game_hints.differences(self.ws), ["extra 80000000.txt", "differs 82bc5158.txt"])

    def test_other_images_have_no_reference(self):
        (self.ws / "project.json").write_text(json.dumps({"binary_sha256": "00" * 32}), encoding="utf-8")
        self.assertEqual(game_hints.install(self.ws), [])
        err = io.StringIO()
        self.assertEqual(game_hints.warn(self.ws, "test", out=err), [])
        self.assertEqual(err.getvalue(), "")

    def test_dp_judge_warns_before_judging(self):
        src = text(X360 / "translator" / "tools" / "dp_judge.py")
        main = src.split("def main():", 1)[1]
        self.assertLess(main.index('game_hints.warn(ws, "dp_judge")'), main.index("mirror(ws, mws, addrs)"))


def result(totals, failing=0, stage="compare", run_error=None):
    r = {"addr": "82000000", "verdict": "FAIL", "stage": stage, "lint": [], "build": [], "first_diffs": [],
         "vectors": {s: {"total": n, "failing": 0} for s, n in totals.items()}, "failing": failing,
         "total": sum(totals.values())}
    if run_error:
        r["run_error"] = run_error
    return r


@mock.patch("core.paths.config", return_value={"min_valid_vectors": 16})
class NoVectors(unittest.TestCase):
    SETS = ("dev", "hidden")

    def finish(self, r):
        # before this branch the Xbox 360 judge's verdict was core/check._finish's own
        return getattr(x360_check, "finish", core_check._finish)(r, self.SETS, {})

    def verdict(self, *a, **kw):
        return self.finish(result(*a, **kw))["verdict"]

    def test_too_few_none_wrong(self, _):
        r = self.finish(result({"dev": 10, "hidden": 20}))
        self.assertEqual(r["verdict"], "NO_VECTORS")
        self.assertIn("dev 10, hidden 20 (the judge needs 16 in each)", r.get("why", ""))
        self.assertEqual(self.verdict({"dev": 0, "hidden": 0}), "NO_VECTORS")

    def test_still_fail(self, _):
        self.assertEqual(self.verdict({"dev": 10, "hidden": 20}, failing=1), "FAIL")      # a wrong vector
        self.assertEqual(self.verdict({"dev": 10, "hidden": 20}, run_error="runner timed out"), "FAIL")
        self.assertEqual(self.verdict({"dev": 10, "hidden": 20}, stage="build"), "FAIL")
        self.assertEqual(self.verdict({"dev": 10, "hidden": 20}, stage="lint"), "FAIL")
        self.assertEqual(self.verdict({"dev": 48, "hidden": 48}, failing=2), "FAIL")
        self.assertEqual(self.verdict({"dev": 48, "hidden": 48}), "PASS")

    def test_core_unchanged(self, _):
        # the shared core (Dreamcast, Saturn, GameCube) still calls it FAIL
        self.assertEqual(core_check._finish(result({"dev": 10, "hidden": 20}), self.SETS, {})["verdict"], "FAIL")

    def test_every_return_goes_through_finish(self, _):
        body = text(X360 / "x360_check.py").split("def run_check(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("core_check._finish", body)
        self.assertEqual(body.count("return finish(result, sets, summary)"), 3)

    def test_dp_judge_rows(self, _):
        x = {"failing": 0, "classes": {}, "first": None}
        self.assertEqual(dp_judge.exit_row({"verdict": "NO_VECTORS", "exit": x})["verdict"], "NO_VECTORS")
        self.assertEqual(dp_judge.exit_row({"verdict": "PASS", "exit": x})["verdict"], "PASS")
        self.assertEqual(dp_judge.exit_row({"verdict": "FAIL", "exit": dict(x, failing=1)})["verdict"], "FAIL")
        self.assertEqual(dp_judge.exit_row({"verdict": "NO_VECTORS", "exit": dict(x, failing=1)})["verdict"], "FAIL")
        src = text(X360 / "translator" / "tools" / "dp_judge.py")
        self.assertIn('if res["verdict"] == "NO_VECTORS":', src)
        self.assertIn('if r["verdict"] != "PASS":', src)   # the summary lists every non-PASS row


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class SwitchHits(unittest.TestCase):
    def test_switch_hits(self):
        out = tmpdir("x360switch_")
        try:
            src = os.pathsep.join(str(p) for p in (SCRIPTS, SHARED, X360 / "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-J-Xmx512m", "-d", str(out), "-cp", CP,
                                "-sourcepath", src, str(TOOL / "tests" / "java" / "X360TrackStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            (out / "cases.txt").write_text("""
S 4e800420               # bctr
S 4d9a0420               # beqctr cr6
S 4e800421               # bctrl: a call
S 4e800020               # blr
S 7c0903a6               # mtctr r0
P
H 8200001c 8200002c
H 8200001c 82000034 82000034
H 82000080 82000090
P
""", encoding="utf-8")
            r = subprocess.run([jdk("java"), "-Xmx256m", "-cp", str(out) + os.pathsep + CP, "X360TrackStub",
                                str(out / "cases.txt")], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            got = [ln.split(": ", 1)[1] for ln in r.stdout.splitlines()]
            self.assertEqual(got, ["true", "true", "false", "false", "false", "{}",
                                   '{"8200001c":{"8200002c":1,"82000034":2},"82000080":{"82000090":1}}'])
        finally:
            shutil.rmtree(out, ignore_errors=True)


class SwitchWiring(unittest.TestCase):
    def test_wiring(self):
        self.assertIn("TcSwitchHits.java", json.loads(text(X360 / "target.json"))["locked_scripts"])
        o = text(SCRIPTS / "TcOracle.java")
        self.assertIn("if (TcSwitchHits.isBcctr(in.getInt(0) & 0xffffffffL) && body.contains(pca)) {\n"
                      "                        res.bctrs.add(new long[] {pc, reg(emu, \"CTR\")});", o)
        self.assertLess(o.index("res.bctrs.add("), o.index("stepped = emu.step(monitor);"))
        v = text(SCRIPTS / "TcVectors.java")
        self.assertLess(v.index("writeVector(id, s == 0 ? \"dev\" : \"hidden\", v, r)"), v.index("switchHits.add(r.bctrs);"))
        p = text(SCRIPTS / "TcPrepare.java")
        self.assertIn('switchHits.hits.isEmpty() ? "" : "\\"switch_hits\\":" + switchHits.json() + ","', p)
        self.assertIn("switchHits.hits.clear();", p)   # a set made unsupported keeps no hits


if __name__ == "__main__":
    unittest.main()

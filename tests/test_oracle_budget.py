"""The oracle's work budgets (r15): every stop decision of DC vector generation counts work (emulated steps and
runs, TcBudget), never the clock, so a harness is a function of its inputs. TC 8c062050 was rescued or not by how long
its first pass took: two fresh prepares of the same inputs made two different harnesses.
1. no clock read in the stamped DC oracle scripts (a clock there could decide a harness again);
2. the facts: a target with "oracle_work_per_s" (DC target.json) gets oracle_work_per_s and oracle_budget_s, never
   oracle_deadline_s (which a server moves on by its age: core/ghidra_serve/TcServe); one without (GameCube) keeps
   oracle_deadline_s;
3. TcFacts reads them and refuses oracle_deadline_s and a budget without a rate (stub, tests/java/TcBudgetStub.java);
4. TcBudget's rule on stub numbers: work, a budget's end, the step-limit fail-fast's projection.
The oracle runs themselves need Ghidra: the proof is TC 8c062050 prepared fresh twice, byte-identical.
Synthetic data. Run: python -W error tests/test_oracle_budget.py (also from test_tooling.py).
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_tail import CP, jdk  # noqa: E402

from core import ghidra, paths  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
WORK = ROOT / "tests" / ".work"
CLOCK = re.compile(r"currentTimeMillis|nanoTime|ManagementFactory|Instant\.now|new Date\(|LocalDateTime|Clock\.")


class Facts(unittest.TestCase):
    def test_no_clock(self):
        for d in ("ghidra_scripts", "template"):
            for p in sorted((SH4 / d).glob("*.java")):
                hits = [n for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                        if CLOCK.search(ln.split("//")[0])]
                self.assertEqual(hits, [], f"{p.name} reads the clock")

    def test_facts(self):
        cfg = dict(paths.config(), ghidra_timeout_s=900)
        rate = int(cfg["oracle_work_per_s"])
        self.assertGreater(rate, 0)  # DC target.json
        body = ghidra.facts_body(cfg)
        self.assertIn(f"oracle_work_per_s {rate}\n", body)
        self.assertIn("oracle_budget_s 750\n", body)
        self.assertIn("oracle_rescue_s 90\n", body)
        self.assertNotIn("oracle_deadline_s", body)
        self.assertEqual(ghidra.facts_body(dict(cfg, ghidra_kill_s=2700)), body)  # the kill limit is no fact: no stamp moves
        other = ghidra.facts_body({k: v for k, v in cfg.items() if k != "oracle_work_per_s"})
        self.assertIn("oracle_deadline_s 750\n", other)  # a target without a rate (GameCube) as before
        self.assertNotIn("oracle_budget_s", other)


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Rule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="budget_", dir=WORK)
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(ROOT / "tests" / "java" / "TcBudgetStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcBudgetStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return {k: v.strip() for k, v in (ln.split(": ", 1) for ln in r.stdout.splitlines() if ": " in ln)}

    def test_facts(self):
        files = {"ok": "oracle_work_per_s 1000\noracle_budget_s 750\noracle_rescue_s 90\nreach_calls 800 150\n",
                 "old": "oracle_deadline_s 750\n", "norate": "oracle_budget_s 750\n"}
        for name, text in files.items():
            (self.out / name).write_text(text, encoding="ascii")
        got = self.run_stub("".join(f"F {self.out / n}\n" for n in files))
        self.assertEqual(got[f"F {self.out / 'ok'}"], "750000 90000 150000")
        self.assertIn("refused unknown target fact: oracle_deadline_s", got[f"F {self.out / 'old'}"])
        self.assertIn("refused oracle_budget_s without oracle_work_per_s", got[f"F {self.out / 'norate'}"])

    def test_rule(self):
        run_work = int(re.search(r"RUN_WORK = (\d+);", (SH4 / "ghidra_scripts" / "TcBudget.java").read_text(
            encoding="utf-8")).group(1))
        got = self.run_stub("W 5 2\nO 11 0\nO 10 10\nO 11 10\n"
                            "R 100 0 10 10 200\nR 100 0 10 10 199\nR 100 0 10 10 0\nR 150 100 4 2 249\n")
        self.assertEqual(got["W 5 2"], str(5 + 2 * run_work))
        self.assertEqual([got["O 11 0"], got["O 10 10"], got["O 11 10"]], ["false", "false", "true"])
        # 100 work for 10 valid since the pass started, 10 more wanted: 200 at the end
        self.assertEqual(got["R 100 0 10 10 200"], "false")
        self.assertEqual(got["R 100 0 10 10 199"], "true")
        self.assertEqual(got["R 100 0 10 10 0"], "false")  # no budget
        self.assertEqual(got["R 150 100 4 2 249"], "true")  # 150 + 50 * 4 / 2 = 250: only the pass's own work counts


if __name__ == "__main__":
    unittest.main()

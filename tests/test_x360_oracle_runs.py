"""The Xbox 360 oracle's run rules that made reached functions unjudgeable (2 false FAILs and 15 functions with no
vectors in Mega Man 10), on Ghidra's real memory bank and the rules' own decisions (tests/java/X360OracleStub.java):
javac and java against the Ghidra jars, no Ghidra application and no project. Skipped without config.local.json's
ghidra_home and a JDK.

usage: python tests/test_x360_oracle_runs.py
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(pathlib.Path(__file__).resolve().parent)]
from test_tail import CP, jdk  # noqa: E402

X360 = TOOL / "targets" / "xbox360_ppc"


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Runs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.out = pathlib.Path(tempfile.mkdtemp(prefix="x360runs_", dir=TOOL / "tests" / ".work"))
        files = [str(p) for p in [*(X360 / "ghidra_scripts").glob("*.java"),
                                  *(TOOL / "core" / "ghidra_shared" / "ppc").glob("*.java")]]
        files += [str(X360 / "template" / "TcInputs.java"), str(TOOL / "tests" / "java" / "X360OracleStub.java")]
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-J-Xmx512m", "-d", str(cls.out), "-cp", CP, *files],
                           capture_output=True, text=True, timeout=600)
        if r.returncode:
            shutil.rmtree(cls.out, ignore_errors=True)
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def run_cases(self, lines):
        f = self.out / "cases.txt"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = subprocess.run([jdk("java"), "-Xmx256m", "-cp", str(self.out) + os.pathsep + CP, "X360OracleStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return [ln.split() for ln in r.stdout.splitlines()]

    def test_read_keeps_what_was_stored(self):
        """A doubleword read starting on unwritten bytes must not wipe the written ones (821bd160: every call record
        reads the stack doublewords)."""
        got = self.run_cases(["b", "s 700fdf24 40001508", "r 700fdf20 8", "r 700fdf24 4"])
        self.assertEqual(got, [["r", "700fdf20", "0000000040001508"], ["r", "700fdf24", "40001508"]])

    def test_read_gaps_and_page_crossing(self):
        got = self.run_cases(["b", "s 70000ffa 1122", "s 70001002 33", "r 70000ff8 c", "r 70000ff8 c", "r 70001002 1"])
        self.assertEqual(got, [["r", "70000ff8", "000011220000000000003300"]] * 2 + [["r", "70001002", "33"]])

    def test_identity_moves(self):
        """Only a move of a register to itself with no record bit reads and writes nothing."""
        same = {"or r8,r8,r8": 0x7d084378, "or r31,r31,r31": 0x7ffffb78, "ori r0,r0,0": 0x60000000,
                "ori r12,r12,0": 0x618c0000, "oris r3,r3,0": 0x64630000, "xori r5,r5,0": 0x68a50000}
        moves = {"mr r3,r4": 0x7c832378, "or r8,r8,r9": 0x7d084b78, "or. r8,r8,r8": 0x7d084379,
                 "ori r3,r4,0": 0x60830000, "ori r3,r3,1": 0x60630001, "addi r3,r3,0": 0x38630000,
                 "and r3,r3,r3": 0x7c631838, "xor r3,r3,r3": 0x7c631a78, "nor r3,r3,r3": 0x7c6318f8}
        got = {int(w, 16): int(v) for _, w, v in self.run_cases([f"i {w:08x}" for w in [*same.values(), *moves.values()]])}
        self.assertEqual([k for k, w in same.items() if not got[w]], [])
        self.assertEqual([k for k, w in moves.items() if got[w]], [])

    def test_tail_jumps(self):
        """A jump out of the body is a tail call only through a pointer (no target in the body) or to code in no
        function; the rest stay refused."""
        cases = {("COMPUTED_JUMP", 0, 0): 1, ("COMPUTED_JUMP", 0, 1): 1, ("CONDITIONAL_COMPUTED_JUMP", 0, 0): 1,
                 ("COMPUTED_JUMP", 1, 0): 0,            # a jump table leaving its table
                 ("UNCONDITIONAL_JUMP", 0, 0): 1, ("CONDITIONAL_JUMP", 0, 0): 1,
                 ("UNCONDITIONAL_JUMP", 0, 1): 0,       # into the middle of another function
                 ("FALL_THROUGH", 0, 0): 0, ("TERMINATOR", 0, 0): 0, ("CONDITIONAL_TERMINATOR", 0, 0): 0}
        got = self.run_cases([f"t {k} {a} {b}" for k, a, b in cases])
        self.assertEqual({(r[1], int(r[2]), int(r[3])): int(r[4]) for r in got}, cases)

    def test_call_returning_past_the_end(self):
        """Only a mocked call whose return address is the word after the body ends the run there (82096680); the
        fall-through of a stepped instruction and an LR the code set elsewhere stay refused."""
        cases = {(-1, 1, 1): 1, (-1, 3, 1): 1, (-1, 1, 0): 0, (-1, 0, 1): 0, (0x82096798, 1, 1): 0}
        got = self.run_cases([f"p {p} {c} {w}" for p, c, w in cases])
        self.assertEqual({(int(r[1]), int(r[2]), int(r[3])): int(r[4]) for r in got}, cases)

    def test_repair_ignores_frame_helpers(self):
        self.assertEqual(self.run_cases(["l 1", "l 0"]), [["l", "1", "0"], ["l", "0", "1"]])

    def test_step_budget(self):
        full = 500000
        rows = self.run_cases([
            f"n {full} " + " ".join(["3000"] * 40),                     # never hits the limit: untouched, however many
            f"n {full} L L " + " ".join(["3000"] * 7),                  # 7 valid are too few
            f"n {full} L L " + " ".join(["3000"] * 8) + " x 9000",      # 16 x the longest, and it follows the longest
            f"n {full} L " + " ".join(["100"] * 8),                     # the floor
            f"n {full} L " + " ".join(["200000"] * 8)])                 # never above the full limit
        self.assertEqual(set(rows[0][1:]), {str(full)})
        self.assertEqual(set(rows[1][1:]), {str(full)})
        self.assertEqual(rows[2][-3:], ["48000", "48000", "144000"])
        self.assertEqual(rows[3][-1], "20000")
        self.assertEqual(rows[4][-1], str(full))


if __name__ == "__main__":
    unittest.main()

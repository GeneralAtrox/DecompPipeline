"""The Xbox 360 oracle's side of the exit state (tests/test_x360_judge_exit.py has the comparer, translator and runtime).
  Oracle (needs Ghidra and a JDK, so not on the main laptop): TcExit's seeds and exit lines and TcFacts' "exit_state",
through tests/java/X360ExitStub.java, parsed by x360_compare.
  Wiring: the flag is off by default and TcExit is locked. The oracle change was scoped to the flag (target.json
stamp_scopes "exit_state": with it off no function's oracle stamp moved) until the entry seeds (TcOracle, vectors.txt
A) changed TcOracle and TcVectors for every function; a scope still listed names today's files.
usage: python tests/test_x360_judge_exit_oracle.py
"""
import hashlib
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_x360_judge_exit import SENT, TOOL, X360, be, bits, cmp, tmpdir  # noqa: E402
from test_tail import CP, jdk  # noqa: E402

SCRIPTS = X360 / "ghidra_scripts"


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    """TcExit's lines and seeds and TcFacts' "exit_state" through tests/java/X360ExitStub.java (javac and java against
    the Ghidra jars). A whole prepare with the flag off and on (vectors.txt and expected.txt byte for byte as before
    with it off) needs a Ghidra project: DESIGN.md "Exit state" gives the build box A commands."""

    def test_lines_and_seeds(self):
        d = tmpdir("x360jxo_")
        try:
            files = [*SCRIPTS.glob("*.java"), *(TOOL / "core" / "ghidra_shared" / "ppc").glob("*.java"),
                     X360 / "template" / "TcInputs.java", TOOL / "tests" / "java" / "X360ExitStub.java"]
            files = [str(f) for f in files]
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-J-Xmx512m", "-d", str(d), "-cp", CP, *files],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            (d / "facts.txt").write_text("exit_state 1\n", encoding="utf-8")
            r = subprocess.run([jdk("java"), "-Xmx256m", "-cp", str(d) + os.pathsep + CP, "X360ExitStub",
                                str(d / "facts.txt")], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = r.stdout.splitlines()
            (d / "vectors.txt").write_text("V 0 dev\n" + "\n".join(out[2:5]) + "\nE\n", encoding="utf-8")
            (d / "expected.txt").write_text("V 0 dev\nRET 0 0\n" + "\n".join(out[6:]) + "\nE\n", encoding="utf-8")
            got = cmp.parse_results(d / "expected.txt")[0]
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(out[:2], ["facts 1", "default 0"])
        n, g, k = (ln.split() for ln in out[2:5])
        self.assertEqual((n[0], len(n), g[0], len(g), k[0]), ("Q", 19, "G", 19, "K"))
        self.assertEqual(int(k[1], 16) & ~0x00FFF000, 0)                          # CR2..CR4 only
        for x in g[1:]:
            self.assertTrue(abs(struct.unpack(">d", bytes.fromhex(x.rjust(16, "0")))[0]) < 2.0 ** 33)   # finite
        self.assertEqual(out[5], "seeds same differ")
        self.assertEqual((got["xs"], got["xf"]), (list(range(32)), [bits(i) for i in range(32)]))
        self.assertEqual(got["xc"], [0x00986001, 1, 0, 1, 8, SENT, 0])
        self.assertEqual((got["xv"], got["xw"]), ({3: (10, 10, 10, 11)}, be(0x700FDFFC, 0x1234, 4)))


class Wiring(unittest.TestCase):
    def test_target(self):
        """Off by default and TcExit locked. The exit_state scope ended with the entry seeds' oracle change (every
        function's stamp moves, as a change outside a scope does); a scope still listed names today's files
        (core/oracle_stamp: the "after" hashes are the files' as they stand), never a stale one."""
        tj = json.loads((X360 / "target.json").read_text(encoding="utf-8"))
        self.assertIs(tj["exit_state"], False)
        self.assertIn("TcExit.java", tj["locked_scripts"])
        for sc in tj["stamp_scopes"]:
            for name, (_, after) in sc["scripts"].items():
                self.assertEqual(hashlib.sha256((SCRIPTS / name).read_bytes()).hexdigest(), after, name)
        import x360_scope
        self.assertFalse(x360_scope.in_scope({}, "82010000", "exit_state"))
        self.assertTrue(x360_scope.in_scope({"exit_state": True}, "82010000", "exit_state"))


if __name__ == "__main__":
    unittest.main()

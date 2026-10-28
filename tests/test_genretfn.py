"""The genretfn hint (decision 7): "genretfn 0xCALLEE LO HI" in oracle/hints/<addr>.txt makes every call to CALLEE
among a vector's first 64 return a value drawn from LO..HI. Spawn 8c5e0f40 indexes a table with 8c5f6154's result
(a remainder by 10), called every 3-4 calls up to call 36, and the random results made too few valid vectors;
"genret N VAL" sets only calls 0-15, to one constant. Pass and fail controls:
1. TcLate (tests/java/TcRetFnStub): the line is taken out of the hints the game's TcInputs sees, and a hints list
   without one is passed on as it is (nothing drawn: the harness stays byte for byte); every call number up to 63
   gets an in-range draw, call 64 and other callees keep theirs; draws depend on the seed, vector and try only;
   a TcLate.copy shares them; a malformed line, a reversed or too wide range and two lines for one callee are
   refused with a clear error;
2. the wiring: TcOracle applies it to Vec.rets and to the later results, TcVectors strips the lines before
   applyHints, the scripts stay at 400 lines, javalint accepts the line, the template documents it;
3. selective_restamp --change retfn takes exactly the functions whose hints have the line.
The oracle itself needs Ghidra: the laptop2 re-prepare of Spawn 8c5e0f40 judges it.
Synthetic data. Run: python -W error tests/test_genretfn.py.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
from core import javalint  # noqa: E402
from test_tail import CP, SH4, jdk  # noqa: E402
import selective_restamp as sr  # noqa: E402

SCRIPTS = SH4 / "ghidra_scripts"
CALLEE = "8c5f6154"


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Draws(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="retfn_", dir=ROOT / "tests" / ".work")
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(ROOT / "tests" / "java" / "TcRetFnStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcRetFnStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return dict(ln.split(": ", 1) for ln in r.stdout.splitlines() if ": " in ln)

    def test_parse(self):
        got = self.run_stub(f"""
H range r4 0 3
H genretfn 0x{CALLEE} 0 9
H # a comment
R one 2355040064 0 0
H range r4 0 3
H genret 3 7
R none 2355040064 0 0
R empty 2355040064 0 0
H genretfn 0x{CALLEE} -1 1
H genretfn 0x8c000100 0x10 0x10
R two 1 2 3
""")
        rest, draw = got["one"].split(" | ")[1:]
        self.assertTrue(got["one"].startswith("new | "))
        self.assertEqual(rest, "rest range r4 0 3;# a comment")  # applyHints gets the rest, in order
        self.assertRegex(draw, rf"^{CALLEE}=0\.\.9/10 n 64 first( [0-9]){{4}}$")  # all ten values among 64
        self.assertEqual(got["none"], "same | rest range r4 0 3;genret 3 7")  # nothing drawn
        self.assertEqual(got["empty"], "same | rest ")
        two = got["two"].split(" | ")
        self.assertEqual(two[1], "rest ")
        self.assertTrue(two[2].startswith("8c000100=10..10/1 n 64"), two[2])
        self.assertRegex(two[3], rf"^{CALLEE}=0\.\.ffffffff/3 n 64")  # -1..1 as 32-bit words: 0, 1, ffffffff

    def test_calls_past_15(self):
        cases = f"H genretfn 0x{CALLEE} 0 9\nR v 5 1 0\n" + "".join(
            f"Q c{c} {CALLEE} {c} 12345678\n" for c in (0, 15, 16, 17, 20, 36, 63, 64)) + (
            f"Q other 8c000100 20 12345678\nC copy {CALLEE} 36\n")
        got = self.run_stub(cases)
        for c in (0, 15, 16, 17, 20, 36, 63):
            self.assertIn(int(got[f"c{c}"], 16), range(10), c)
        self.assertEqual(got["c64"], "12345678")  # past the 64 results the runtimes replay: as drawn (0)
        self.assertEqual(got["other"], "12345678")  # another callee keeps its result
        self.assertEqual(got["copy"], got["c36"])  # TcReach's vector copy keeps the draws
        none = self.run_stub("R v 5 1 0\nQ q 8c5f6154 20 12345678\n")
        self.assertEqual(none["q"], "12345678")

    def test_seeded(self):
        h = f"H genretfn 0x{CALLEE} 0 0xffff\n"
        got = self.run_stub(h + "R a 7 3 1\n" + h + "R b 7 3 1\n" + h + "R t 7 3 2\n" + h + "R i 7 4 1\n"
                            + h + "R s 8 3 1\n")
        self.assertEqual(got["a"], got["b"])
        for k in ("t", "i", "s"):
            self.assertNotEqual(got["a"], got[k], k)

    def test_bad_lines(self):
        bad = {"short": f"genretfn 0x{CALLEE} 0", "long": f"genretfn 0x{CALLEE} 0 9 1", "word": "genretfn zz 0 9",
               "reversed": f"genretfn 0x{CALLEE} 9 0", "wide": f"genretfn 0x{CALLEE} 0 0x100000000",
               "low": f"genretfn 0x{CALLEE} -0x1 9", "neg": "genretfn -5 0 9"}
        got = self.run_stub("".join(f"H {line}\nR {k} 1 0 0\n" for k, line in bad.items())
                            + f"H genretfn 0x{CALLEE} 0 9\nH genretfn 0x{CALLEE} 1 2\nR twice 1 0 0\n")
        for k in ("short", "long", "word", "low"):
            self.assertEqual(got[k], f"ERROR bad genretfn line (genretfn 0xCALLEE LO HI): {bad[k]}", k)
        for k in ("reversed", "wide", "neg"):
            self.assertEqual(got[k], f"ERROR bad genretfn range (a callee address, LO <= HI in 32 bits): {bad[k]}", k)
        self.assertEqual(got["twice"], f"ERROR genretfn twice for one callee: genretfn 0x{CALLEE} 1 2")


class Wiring(unittest.TestCase):
    def test_scripts(self):
        oracle, vectors, late = (text(SCRIPTS / n) for n in ("TcOracle.java", "TcVectors.java", "TcLate.java"))
        self.assertIn("(v.rets[calls] = TcLate.ranged(v, pc, calls, TcFret.at(floatRet, pc, v.rets[calls])))", oracle)
        self.assertIn("(entryLate[k] = TcLate.ranged(v, pc, calls, TcFret.at(floatRet, pc, TcLate.pooled(", oracle)
        self.assertIn("applyHints(v, TcLate.ranges(v, hints, seed, id, idTry));", vectors)
        self.assertNotIn("applyHints(v, hints)", vectors)
        self.assertIn("RANGED.put(to, d);", late)
        for n in ("TcOracle.java", "TcVectors.java", "TcLate.java"):
            self.assertLessEqual(len(text(SCRIPTS / n).splitlines()), 400, n)
        self.assertIn("genretfn 0xCALLEE LO HI", text(SH4 / "template" / "TcInputs.java"))

    def test_lint(self):
        self.assertEqual(javalint.lint_hints(f"genretfn 0x{CALLEE} 0 9\ngenretfn 0x{CALLEE} -1 0x10\n"), [])


class Candidates(unittest.TestCase):
    def test_retfn(self):
        with tempfile.TemporaryDirectory() as t:
            h = pathlib.Path(t)
            (h / "8C5E0F40.txt").write_text(f"range r4 0 3\r\ngenretfn 0x{CALLEE} 0 9\r\n", encoding="utf-8")
            (h / "8c000200.txt").write_text("genret 3 7\n# genretfn 0x8c000100 0 9\n", encoding="utf-8")
            (h / "8c000300.txt").write_text("", encoding="utf-8")
            (h / "8c000400.txt").write_text("genretfnx 0x8c000100 0 9\n", encoding="utf-8")
            self.assertEqual(sr.retfn_candidates(h), {"8c5e0f40"})


if __name__ == "__main__":
    unittest.main()

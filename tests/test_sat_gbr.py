"""The Saturn tester's three changes the user approved on 2026-10-30 (D1, D2, D4 of the Saturn translator round):
D1: the entry SR has no bit 30 (the SH-2 SR has no MD bit; `stc sr,rN` gave 0x400000f0 where the C gave 0xf0);
D2: "range gbr LO HI" in oracle/hints/<addr>.txt sets GBR at entry (Robo Pit reads its globals through the GBR its
    boot code sets once to 0x060ffc00: 06004014 `mov.l @(...),r0`, 06004028 `ldc r0,gbr`), ported from the Dreamcast
    target (51cc9c6) for GBR only;
D4: a `jmp @rN` Ghidra knows no target of leaves the function as a mocked tail call, wherever it lands.
Pass and fail controls:
1. TcLate.regs (tests/java/TcSatGbrStub): the gbr line is taken out of the hints the game's TcInputs sees (every other
   line stays, in order), a hints list without one is passed on as it is (no line b: the harness stays byte for byte),
   draws stay inside LO..HI and depend on seed, vector and try only, TcLate.copy shares the GBR, malformed lines are
   refused;
2. the wiring: TcVectors takes the line before applyHints and writes line b, TcOracle loads GBR, both C runners load line
   b with sh4_ldc_gbr after V's reset, TcSr drops bit 30, entersCallee takes a flowless computed jump anywhere, the
   scripts keep their size limits, javalint accepts the line.
The oracle itself needs Ghidra: the build box B re-prepares judge it. Run: python -W error tests/test_sat_gbr.py.
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
from core import javalint  # noqa: E402
from test_tail import CP, jdk  # noqa: E402

SAT = ROOT / "targets" / "saturn_sh2"
SCRIPTS = SAT / "ghidra_scripts"


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Draws(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="satgbr_", dir=ROOT / "tests" / ".work")
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SAT / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(ROOT / "tests" / "java" / "TcSatGbrStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcSatGbrStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return {k: v.split(" | ") for k, v in (ln.split(": ", 1) for ln in r.stdout.splitlines() if ": " in ln)}

    def test_parse(self):
        got = self.run_stub("""
H range r4 0 3
H range gbr 0x060ffc00 0x060ffc00
H ptr r6
R one 7 1 0
H range r4 0 3
H ptr r6
R none 7 1 0
R empty 7 1 0
H range gbr 0x060ffc00 0x060ffc00
R g 7 1 0
C copy
""")
        self.assertEqual(got["one"], ["new", "rest range r4 0 3;ptr r6", "b b 060ffc00"])
        self.assertEqual(got["none"], ["same", "rest range r4 0 3;ptr r6", "b "])
        self.assertEqual(got["empty"], ["same", "rest ", "b "])
        self.assertEqual(got["g"], ["new", "rest ", "b b 060ffc00"])
        self.assertEqual(got["copy"], ["b 060ffc00"])  # TcReach's vector copy keeps the GBR

    def test_draws(self):
        h = "H range gbr 0x100 0x1ff\n"
        got = self.run_stub("".join(f"{h}R v{i} 9 {i} 0\n" for i in range(40)) + h + "R a 3 2 1\n" + h + "R b 3 2 1\n"
                            + h + "R t 3 2 2\n")
        seen = {int(got[f"v{i}"][2].split()[-1], 16) for i in range(40)}
        self.assertTrue(all(0x100 <= x <= 0x1ff for x in seen))
        self.assertGreater(len(seen), 10)
        self.assertEqual(got["a"], got["b"])
        self.assertNotEqual(got["a"], got["t"])

    def test_bad_lines(self):
        bad = {"short": "range gbr 0", "long": "range gbr 0 9 1", "word": "range gbr zz 9"}
        far = {"reversed": "range gbr 9 0", "wide": "range gbr 0 0x100000000"}
        got = self.run_stub("".join(f"H {line}\nR {k} 1 0 0\n" for k, line in {**bad, **far}.items()))
        for k in bad:
            self.assertEqual(got[k], [f"ERROR bad register range line (range gbr LO HI): {bad[k]}"], k)
        for k in far:
            self.assertEqual(got[k], [f"ERROR bad register range (LO <= HI in 32 bits): {far[k]}"], k)


class Wiring(unittest.TestCase):
    def test_scripts(self):
        oracle, vectors, sr, reach = (text(SCRIPTS / n) for n in ("TcOracle.java", "TcVectors.java", "TcSr.java",
                                                                 "TcReach.java"))
        self.assertIn("applyHints(v, TcLate.regs(v, hints, seed, id, idTry));", vectors)
        self.assertIn("vout.append(TcLate.gbrLine(v)).append(TcSr.line(sr, v))", vectors)
        self.assertIn("TcLate.enter(emu, v);", oracle)
        self.assertIn("TcLate.copy(v, c);", reach)
        self.assertIn("1L << 28 | 1L << 29 | 1L << 30) | (long) imask(v) << 4", sr)  # D1: bit 30 cleared, never set
        ent = oracle.split("boolean entersCallee")[1].split("\n    }\n")[0]
        self.assertTrue(ent.rstrip().endswith("return true;  // a computed jump Ghidra knows no target of leaves the "
                                              "function: a mocked tail call, as jsr @rN"))  # D4
        self.assertNotIn("getLoadedAndInitializedAddressSet", ent)
        for n in ("TcVectors.java", "TcReach.java", "TcLate.java"):
            self.assertLessEqual(len(text(SCRIPTS / n).splitlines()), 400, n)

    def test_runners(self):
        for f in ("runtime/runner.c", "runtime_rd/rd_runner.c"):
            src = text(SAT / f)
            b = src.index("line[0] == 'b'")
            self.assertIn("sh4_ldc_gbr((u32)strtoul(line + 1, NULL, 16));", src[b:b + 200], f)

    def test_lint(self):
        self.assertEqual(javalint.lint_hints("range gbr 0x060ffc00 0x060ffc00\nrange r4 0 3\n"), [])


if __name__ == "__main__":
    unittest.main()

"""Register start values (the user's decision, 2026-10-30): "range rN LO HI" for r0..r3 and r8..r14 and "range gbr LO HI"
in oracle/hints/<addr>.txt. Spawn's draw hooks 8c0703ba, 8c06218a, 8c062d6a store through their caller's r10/r11, and
PS2 Lib_SetPaletteEntries 8c17a960 reads its caller's GBR: no hint could set them, so every run faulted at 0.
Pass and fail controls:
1. TcLate.regs (tests/java/TcRegHintStub): the lines are taken out of the hints the game's TcInputs sees (range r4..r7
   and every other line stay, in order), and a hints list without one is passed on as it is (nothing drawn, no line
   b: the harness stays byte for byte); r0..r3 land in entryLow and r8..r14 in entryGi, made all zero when the
   function drew none and kept as drawn where no line names them; gbr gives line b; draws stay inside LO..HI and
   depend on the seed, vector and try only; a TcLate.copy shares the GBR; malformed lines are refused, and r15 is
   left to TcInputs (which refuses it);
2. the wiring: TcVectors takes the lines before ranges and applyHints and writes line b, TcOracle loads GBR, the C
   runners load line b with sh4_ldc_gbr after V's reset and xemu starts from it, every script stays at 400 lines,
   javalint accepts the lines (r15 and other words not), the template documents them;
3. selective_restamp --change reghint takes exactly the functions whose hints have such a line.
The oracle itself needs Ghidra: the laptop2 re-prepares of the four functions judge it.
Synthetic data. Run: python -W error tests/test_reghint.py.
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


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Draws(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="reghint_", dir=ROOT / "tests" / ".work")
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(ROOT / "tests" / "java" / "TcRegHintStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcRegHintStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return {k: v.split(" | ") for k, v in (ln.split(": ", 1) for ln in r.stdout.splitlines() if ": " in ln)}

    def test_parse(self):
        got = self.run_stub("""
H range r4 0 3
H range r10 0x8cc00100 0x8cc00100
H ptr r6
H range r11 0 0
H range gbr 0x8c8b87c0 0x8c8b87c0
H genretfn 0x8c000100 0 9
R one 7 1 0
H range r4 0 3
H ptr r6
R none 7 1 0 G
R empty 7 1 0
H range r1 5 5
R low 7 1 0
H range r8 0x10 0x10
R keep 7 1 0 G
H range r0 1 1
R lowkeep 7 1 0 L
H range gbr 0x8c000000 0x8c000000
R g 7 1 0
C copy
""")
        self.assertEqual(got["one"][:2], ["new", "rest range r4 0 3;ptr r6;genretfn 0x8c000100 0 9"])
        self.assertEqual(got["one"][2:], ["low null", "gi 0 0 8cc00100 0 0 0 0", "b b 8c8b87c0"])
        self.assertEqual(got["none"], ["same", "rest range r4 0 3;ptr r6", "low null", "gi 11 22 33 44 55 66 77", "b "])
        self.assertEqual(got["empty"], ["same", "rest ", "low null", "gi null", "b "])
        self.assertEqual(got["low"][2:4], ["low 0 5 0 0", "gi null"])
        self.assertEqual(got["keep"][3], "gi 10 22 33 44 55 66 77")  # only the named register changes
        self.assertEqual(got["lowkeep"][2], "low 1 22 33 44")
        self.assertEqual(got["g"][4], "b b 8c000000")
        self.assertEqual(got["copy"], ["b 8c000000"])  # TcReach's vector copy keeps the GBR

    def test_draws(self):
        h = "H range r12 0x100 0x1ff\nH range gbr -1 1\n"
        got = self.run_stub("".join(f"{h}R v{i} 9 {i} 0\n" for i in range(40)) + h + "R a 3 2 1\n" + h + "R b 3 2 1\n"
                            + h + "R t 3 2 2\n")
        seen = set()
        for i in range(40):
            r12 = int(got[f"v{i}"][3].split()[5], 16)
            self.assertIn(r12, range(0x100, 0x200))
            self.assertIn(got[f"v{i}"][4].split()[-1], ("00000000", "00000001", "ffffffff"))  # -1..1 as 32-bit words
            seen.add(r12)
        self.assertGreater(len(seen), 10)
        self.assertEqual(got["a"], got["b"])
        self.assertNotEqual(got["a"], got["t"])

    def test_bad_lines(self):
        bad = {"short": "range r10 0", "long": "range r10 0 9 1", "word": "range r10 zz 9", "neg": "range gbr -0x1 9"}
        far = {"reversed": "range r10 9 0", "wide": "range gbr 0 0x100000000"}
        got = self.run_stub("".join(f"H {line}\nR {k} 1 0 0\n" for k, line in {**bad, **far}.items())
                            + "H range r15 0 9\nR r15 1 0 0\n")
        for k in bad:
            self.assertEqual(got[k], [f"ERROR bad register range line (range rN|gbr LO HI): {bad[k]}"], k)
        for k in far:
            self.assertEqual(got[k], [f"ERROR bad register range (LO <= HI in 32 bits): {far[k]}"], k)
        self.assertEqual(got["r15"][:2], ["same", "rest range r15 0 9"])  # not its line: TcInputs refuses it


class Wiring(unittest.TestCase):
    def test_scripts(self):
        oracle, vectors, late = (text(SCRIPTS / n) for n in ("TcOracle.java", "TcVectors.java", "TcLate.java"))
        self.assertIn("applyHints(v, TcLate.ranges(v, TcLate.regs(this, v, hints, seed, id, idTry), seed, id, idTry));",
                      vectors)
        self.assertIn("vout.append(TcLate.gbrLine(v)).append(TcSr.line(sr, v))", vectors)
        self.assertIn("TcLate.enter(emu, v);", oracle)
        self.assertIn('emu.writeRegister("GBR"', late)
        for n in ("TcOracle.java", "TcVectors.java", "TcLate.java"):
            self.assertLessEqual(len(text(SCRIPTS / n).splitlines()), 400, n)
        self.assertIn("range gbr LO HI", text(SH4 / "template" / "TcInputs.java"))

    def test_runners(self):
        for f in ("runtime/runner.c", "runtime_rd/rd_runner.c"):
            src = text(SH4 / f)
            b = src.index("line[0] == 'b'")
            self.assertIn("sh4_ldc_gbr((u32)strtoul(line + 1, NULL, 16));", src[b:b + 200], f)
            self.assertLess(src.index("tcrt_reset();"), b, f)  # after V's reset, which zeroes GBR
        self.assertIn("x.gbr = sh4_stc_gbr();", text(SH4 / "xemu" / "xe_main.c"))

    def test_lint(self):
        self.assertEqual(javalint.lint_hints("range r0 0 3\nrange r10 0x8cc00000 0x8cc00ff0\nrange r14 -1 1\n"
                                             "range gbr 0x8c8b87c0 0x8c8b87c0\nrange r4 0 3\n"), [])
        for bad in ("range r15 0 3", "range sp 0 3", "range vbr 0 3", "range r10 0"):
            self.assertEqual(len(javalint.lint_hints(bad + "\n")), 1, bad)


class Candidates(unittest.TestCase):
    def test_reghint(self):
        with tempfile.TemporaryDirectory() as t:
            h = pathlib.Path(t)
            (h / "8C06218A.txt").write_text("ptr r4\r\nrange r10 0x8cc00100 0x8cc00100\r\n", encoding="utf-8")
            (h / "8c17a960.txt").write_text("range gbr 0x8c8b87c0 0x8c8b87c0\n", encoding="utf-8")
            (h / "8c000100.txt").write_text("range r1 0 3\n", encoding="utf-8")
            (h / "8c000200.txt").write_text("range r4 0 3\nrange r7 1 2\n# range r10 0 1\n", encoding="utf-8")
            (h / "8c000300.txt").write_text("genretfn 0x8c000100 0 9\n", encoding="utf-8")
            (h / "8c000400.txt").write_text("", encoding="utf-8")
            self.assertEqual(sr.reghint_candidates(h), {"8c06218a", "8c17a960", "8c000100"})


if __name__ == "__main__":
    unittest.main()

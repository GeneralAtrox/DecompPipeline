"""TcSpin (proof-gates item 1): a wait loop nothing in it ends is a halt of its own kind, HALT SPIN, and the C keeps
the loop with tc_spin() as its body. Spawn Game_Main (8c010080) waits each frame on words only its interrupt handlers
write; in the oracle nothing changes them, so every vector ran to the step limit and the function was refused.
1. the wiring: TcSpin is locked and at 400 lines or fewer, TcOracle asks it before each step in the body and drops its
   snapshots at a mocked call, only for a listed function (script arg 10, from core/ghidra.py), TcVectors writes HALT
   SPIN, both runtimes and the port define tc_spin, both headers declare it;
2. the judge: HALT and HALT SPIN are two kinds, and every mix of them fails with its own message (the old two
   messages byte for byte); C that calls tc_spin() early (before what the original does first), in a function that
   never spins on that input, or tc_halt() where the original spins, fails; the right C passes;
3. both runtimes' own tc_spin and report line (compiled from their source) print HALT SPIN, and tc_halt still HALT;
4. opt-in: project.json "spin_waits" ({address: evidence}) lists the functions TcSpin applies to (TC 8c04362c, a
   ring-buffer search, repeats its state on random slots too, and no C can keep the contract there); an entry without
   evidence is refused; an unlisted function's stamp is as without the list, a listed one's differs; selective_restamp
   --change spin takes the listed functions and no other;
5. a loop that calls functions (Spawn 8c0116c0's boot wait): TcSpin proves it once at its head and halts after one pass
   of its calls; its exits may read only the words the evidence names (script arg 10 "spin:w_...", in the stamp), and
   the C calls tc_spin() last in the loop's body.
The oracle itself needs Ghidra: the re-prepare of Game_Main and the step-limit harnesses judge it.
Synthetic data. Run: python -W error tests/test_spin.py.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402
import selective_restamp as sr  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import compare, oracle_stamp  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"
RUNTIMES = (SH4 / "runtime" / "tc_rt.c", SH4 / "runtime_rd" / "rd_mem.c")


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


class Wiring(unittest.TestCase):
    def test_scripts(self):
        self.assertIn("TcSpin.java", json.loads(text(SH4 / "target.json"))["locked_scripts"])
        for name in ("TcSpin.java", "TcOracle.java", "TcVectors.java"):
            self.assertLessEqual(len(text(SCRIPTS / name).splitlines()), 400, name)
        oracle = text(SCRIPTS / "TcOracle.java")
        ask = oracle.index("if (spin != null && spin.spins(emu, prev, pca, steps)) {")
        self.assertLess(oracle.index("if (TcExit.halts(listing, pca)) {  // an endless `bra .`"), ask)
        self.assertLess(ask, oracle.index("if (++steps > stepLimit) {"))
        self.assertIn("res.halted = res.spun = true;", oracle)
        self.assertIn("TcLive.after(live, this, emu, pc, calls++); // the callee's own change of r8..r14 (TcLive)\n"
                      "                    if (spin != null) spin.call();", oracle)
        self.assertIn("TcSpin spin = spinWait ? new TcSpin(this) : null;", oracle)
        prep = text(SCRIPTS / "TcPrepare.java")
        self.assertIn('spinWait = a.length > 10 && a[10].startsWith("spin");', prep)
        self.assertIn('if (spinWait && a[10].startsWith("spin:")) for (String w : a[10].substring(5).split("_")) '
                      'spinWords.add(Long.parseLong(w, 16));', prep)
        self.assertIn('facts, compare.spin_arg(cfg, addr)]', text(ROOT / "core" / "ghidra.py"))
        self.assertIn('eout.append(r.spun ? "HALT SPIN\\n" : "HALT\\n");', text(SCRIPTS / "TcVectors.java"))

    def test_calls(self):  # a loop with calls: proved once at its head after a pass with a call, its exits on named words
        spin = text(SCRIPTS / "TcSpin.java")
        self.assertIn("if (prev >= 0 && pc <= prev && from != null && callAt >= from && !decided.containsKey(pc)) {", spin)
        self.assertLess(spin.index("decided.put(pc, never);"), spin.index("if (steps < FROM) return false;"))
        self.assertIn("void call() {\n        seen.clear();\n        callAt = t;\n    }", spin)
        self.assertIn("!o.spinWords.containsAll(x.loads())", spin)
        self.assertNotIn("emu.readMemory", spin)  # registers and the program's literal pool only, never the run's memory

    def test_runtimes(self):
        for p in RUNTIMES:
            src = text(p)
            self.assertIn("void tc_spin(void)\n{\n    tcrt_halted = 2;\n    longjmp(tcrt_halt_jmp, 1);\n}", src, p.name)
            self.assertIn('fprintf(out, tcrt_halted == 2 ? "HALT SPIN\\n" : "HALT\\n");', src, p.name)
        for p in (SH4 / "runtime" / "tc_rt.h", SH4 / "runtime_rd" / "rd_rt.h"):
            self.assertIn("\nvoid tc_spin(void);\n", text(p), p.name)
        self.assertIn("void tc_spin(void) { port_wait_vblank(); }", text(ROOT / "port" / "runtime" / "sh4_rt.c"))


def result(halt=None, writes=None, calls=()):
    out = {"done": True, "faults": [], "r0": 0, "fr0": 0, "writes": writes or {}, "calls": list(calls),
           "xf": [0] * 16, "cov": set()}
    if halt:
        out["halt"] = halt
    return out


def call(target):
    return [target] + [0] * (compare.CALL_FIELDS - 1)


META = {"ret": "void", "callees": []}
VEC = {"patches": []}
FRAME = {0x8C200000: 1}  # Game_Main's pass before the wait: a store, then a call


def judge(exp, act):
    return compare.compare_vector(META, b"", VEC, exp, act)


class Judge(unittest.TestCase):
    def test_parse(self):
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t) / "expected.txt"
            p.write_text("V 0 dev\nRET 0 0\nHALT\nE\nV 1 dev\nRET 0 0\nHALT SPIN\nE\nV 2 dev\nRET 0 0\nE\n",
                         encoding="utf-8")
            res = compare.parse_results(p)
        self.assertEqual([res[i].get("halt") for i in range(3)], ["loop", "spin", None])

    def test_old_messages(self):  # the two messages before TcSpin, byte for byte
        self.assertEqual(compare.compare_halt("loop", None), [
            "the original never returns here: it ends in an endless loop (`bra .`, or a callee that is one); "
            "call tc_halt() at that point (after the tc_call when the callee is the loop)"])
        self.assertEqual(compare.compare_halt(None, "loop"),
                         ["your code called tc_halt(), but the original does not halt on this input"])

    def test_kinds(self):
        for exp in (None, "loop", "spin"):
            for act in (None, "loop", "spin"):
                d = compare.compare_halt(exp, act)
                self.assertEqual(bool(d), exp != act, (exp, act))
        self.assertIn("tc_spin()", compare.compare_halt("spin", None)[0])
        self.assertIn("call tc_halt()", compare.compare_halt("loop", "spin")[0])
        self.assertIn("call tc_spin()", compare.compare_halt("spin", "loop")[0])
        self.assertIn("does not wait forever", compare.compare_halt(None, "spin")[0])

    def test_right_c_passes(self):  # `while (flag == 0 && cnt == start) tc_spin();` after the frame's work
        exp = result("spin", FRAME, [call(0x8C030000)])
        self.assertEqual(judge(exp, result("spin", FRAME, [call(0x8C030000)])), [])

    def test_early_spin_fails(self):  # tc_spin() before the store and the call the original makes first
        exp = result("spin", FRAME, [call(0x8C030000)])
        d = judge(exp, result("spin"))
        self.assertTrue(any("8c200000" in x for x in d), d)
        self.assertTrue(any("call" in x for x in d), d)

    def test_spin_where_none_fails(self):  # a function that returns on this input
        self.assertTrue(judge(result(None, FRAME), result("spin", FRAME)))

    def test_halt_for_spin_fails(self):
        self.assertTrue(judge(result("spin", FRAME), result("loop", FRAME)))
        self.assertTrue(judge(result("loop", FRAME), result("spin", FRAME)))


MAIN = """#include <setjmp.h>
#include <stdio.h>
static jmp_buf tcrt_halt_jmp;
static int tcrt_halted;
%s
int main(void)
{
    FILE *out = stdout;
    for (int k = 0; k < 3; k++) {
        tcrt_halted = 0;
        if (!setjmp(tcrt_halt_jmp)) {
            if (k == 1) tc_halt();
            if (k == 2) tc_spin();
        }
        printf("V %%d\\n", k);
%s
    }
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class Runtimes(unittest.TestCase):
    def test_report(self):
        work = ROOT / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            vc = str(game_config.vcvars32()).replace("/", "\\")
            for p in RUNTIMES:
                src = text(p)
                fns = "".join(re.search(r"void %s\(void\)\n\{.*?\n\}\n" % f, src, re.S).group(0)
                              for f in ("tc_halt", "tc_spin"))
                report = re.search(r"^ *if \(tcrt_halted\) fprintf\(out, .*?;$", src, re.M).group(0)
                (d / "p.c").write_text(MAIN % (fns, report), encoding="utf-8")
                r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX p.c"', cwd=d,
                                   capture_output=True, text=True, errors="replace", timeout=300)
                self.assertEqual(r.returncode, 0, p.name + r.stdout[-2000:])
                out = subprocess.run([str(d / "p.exe")], capture_output=True, text=True, timeout=60)
                self.assertEqual(out.stdout.split("\n")[:5], ["V 0", "V 1", "HALT", "V 2", "HALT SPIN"], p.name)
        finally:
            shutil.rmtree(d, ignore_errors=True)


LISTED = {"spin_waits": {"8c010080": "waits on vblank word 8cfe000c"}}


BOOT = {"spin_waits": {"8c0116c0": "boot wait 8c0119d4..8c0119ee: loops until the vblank counter word 8cfe000c "
                                    "(pool 0cfe000c) reaches its start + 240; only the vblank interrupt writes it"}}


class Candidates(unittest.TestCase):
    def test_arg(self):  # script arg 10: the evidence's words, folded, for a loop with calls
        self.assertEqual(compare.spin_arg(LISTED, "8c010080"), "spin:0cfe000c")
        self.assertEqual(compare.spin_arg(BOOT, "8C0116C0"), "spin:0c0119d4_0c0119ee_0cfe000c")
        self.assertEqual(compare.spin_arg({"spin_waits": {"8c010080": "a vblank wait"}}, "8c010080"), "spin")
        self.assertEqual(compare.spin_arg(LISTED, "8c04362c"), "-")
        self.assertEqual(compare.spin_arg({}, "8c010080"), "-")
        self.assertNotRegex(compare.spin_arg(BOOT, "8c0116c0"), r"[,;= ]")  # analyzeHeadless.bat splits arguments there
        self.assertEqual(compare.spin_arg({"spin_waits": {"8c010080": "0x8CFE000C, not 18cfe000c0"}}, "8c010080"),
                         "spin:0cfe000c")

    def test_contract(self):  # where tc_spin() goes in a loop with calls
        self.assertIn("last statement when the loop calls functions", compare.compare_halt("spin", None)[0])
        for h in (SH4 / "runtime" / "tc_rt.h", SH4 / "runtime_rd" / "rd_rt.h"):
            self.assertIn("last in its body", text(h).replace("\n * ", " "), h.name)

    def test_listed(self):
        self.assertTrue(compare.spin_wait(LISTED, "8C010080"))
        self.assertFalse(compare.spin_wait(LISTED, "8c04362c"))  # TC's ring-buffer search: not listed
        self.assertFalse(compare.spin_wait({}, "8c010080"))
        for bad in ({"8c010080": ""}, {"8c010080": " "}, {"8c010080": None}, ["8c010080"]):
            with self.assertRaises(ValueError):
                compare.spin_wait({"spin_waits": bad}, "8c010080")
        self.assertEqual(sr.spin_candidates(LISTED), {"8c010080"})
        self.assertEqual(sr.spin_candidates({}), set())
        with self.assertRaises(ValueError):
            sr.spin_candidates({"spin_waits": {"8c010080": ""}})

    def test_stamp(self):
        def s(cfg, addr):
            return oracle_stamp.stamp_of(dict(cfg, binary_sha256="ab"), addr, {}, "-", None, 48, 48)
        self.assertEqual(s(LISTED, "8c04362c"), s({}, "8c04362c"))  # unlisted: as without the list
        self.assertNotEqual(s(LISTED, "8c010080"), s({}, "8c010080"))  # listed: re-prepared
        plain = {"spin_waits": {"8c010080": "a vblank wait"}}
        self.assertNotEqual(s(LISTED, "8c010080"), s(plain, "8c010080"))  # its words are an input


if __name__ == "__main__":
    unittest.main()

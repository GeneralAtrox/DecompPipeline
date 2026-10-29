"""TcTail.extent on a stub program (tests/java/TcTailStub.java): javac and java against the Ghidra jars, no Ghidra
application and no project. Skipped without config.local.json's ghidra_home and a JDK (java_home, else PATH).

Covers the run-on rule (TcTail.runOn, callPr): code that runs on (no jump) into another function's entry with a PR
that is not the function's own is part of the extent (Toy Commander 8c061630 into 8c06163e after the entry split, and
8c061a98's `bsr` returning into 8c061aa0); with the function's own PR back (Spawn 8c020560 pops PR and runs into
8c0205a4) or after a call to a callee that halts, the entry stays a mocked tail call.

With a branch into another branch's delay slot (ds-flow, TcTail.slotEntry) the slot runs on into the next instruction
(PS2 8c197240 and 8c188422's piece 8c189200); a slot reached only from its own branch does not.

usage: python tests/test_tail.py
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL)]

from core import toolcfg  # noqa: E402

SH4 = TOOL / "targets" / "dreamcast_sh4"
CFG = toolcfg.load(TOOL)


def jdk(tool):
    home = CFG.get("java_home")
    exe = pathlib.Path(home, "bin", tool + (".exe" if os.name == "nt" else "")) if home else None
    return str(exe) if exe and exe.is_file() else shutil.which(tool)


def classpath():
    """One wildcard entry per Ghidra lib folder (the whole jar list is past Windows' command-line limit)."""
    home = CFG.get("ghidra_home")
    if not home or not pathlib.Path(home, "Ghidra").is_dir():
        return None
    dirs = sorted({p.parent for p in pathlib.Path(home, "Ghidra").glob("**/lib/*.jar")})
    return os.pathsep.join(str(d / "*") for d in dirs)


CP = classpath()


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class RunOn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="tail_", dir=TOOL / "tests" / ".work")
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(TOOL / "tests" / "java" / "TcTailStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def extent(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcTailStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return dict(ln.split(": ", 1) for ln in r.stdout.split("\n") if ln.startswith("X "))

    RTS = "F 498 498 49b\nI 498 term rts\nI 49a slot _nop\n"  # a callee

    def test_runs_on_after_a_call(self):  # TC 8c061630: PR saved in r2, a call, then on into 8c06163e (jmp @r2)
        x = self.extent(self.RTS + """
F 630 630 63d
I 630 fall sts PR,r2
I 632 fall fmov.s fr13,@-r15
I 634 fall fmov.s fr14,@-r15
I 636 fall fmov.s fr15,@-r15
I 638 call jsr @r0 -> 498
I 63a slot _frchg
I 63c fall frchg
F 63e 63e 641
I 63e jump jmp @r2
I 640 slot _nop
X 630
X 63e
""")
        self.assertEqual(x["X 630"], "630-641")  # its body and the entry it runs on into
        self.assertEqual(x["X 63e"], "63e-641")

    def test_call_returns_into_an_entry(self):  # TC 8c061a98: `bsr` returns into 8c061aa0, which pops its PR
        x = self.extent(self.RTS + """
F a98 a98 a9f
I a98 fall sts.l PR,@-r15
I a9a fall mov r4,r1
I a9c call bsr 498 -> 498
I a9e slot _nop
F aa0 aa0 aa5
I aa0 fall lds.l @r15+,PR
I aa2 term rts
I aa4 slot _nop
X a98
""")
        self.assertEqual(x["X a98"], "a98-aa5")

    def test_own_pr_stays_a_tail_call(self):  # Spawn 8c020560: PR popped, then on into 8c0205a4 (returns for it)
        x = self.extent(self.RTS + """
F 560 560 567
I 560 call bsr 498 -> 498
I 562 slot _nop
I 564 fall lds.l @r15+,pr
I 566 fall mov.l @r15+,r14
F 568 568 56b
I 568 term rts
I 56a slot _nop
X 560
""")
        self.assertEqual(x["X 560"], "560-567")

    def test_not_after_a_callee_that_halts(self):  # TcExit: the run ends in the callee; what follows is not run
        x = self.extent("""
F 800 800 803
I 800 jump bra 800 -> 800
I 802 slot _nop
F 700 700 703
I 700 call bsr 800 -> 800
I 702 slot _nop
F 704 704 707
I 704 term rts
I 706 slot _nop
X 700
""")
        self.assertEqual(x["X 700"], "700-703")

    def test_local_subroutine_joins_the_extent(self):  # D2: PS2 8c188420's bsr to 8c188b60 (catalog not_entry)
        x = self.extent(self.RTS + """
F 900 900 90b
I 900 fall sts.l PR,@-r15
I 902 call bsr 920 -> 920
I 904 slot _nop
I 906 call bsr 498 -> 498
I 908 slot _nop
I 90a fall lds.l @r15+,PR
I 90c term rts
I 90e slot _nop
I 920 fall mov.l @r4+,r0
I 922 term rts
I 924 slot _rotcl r9
X 900
""")
        self.assertEqual(x["X 900"], "900-90f 920-925")  # the subroutine with no function; 498 stays a callee

    # ds-flow (TcTail.slotEntry): a branch into another branch's delay slot runs the slot, then the next instruction
    def test_branch_into_a_slot_runs_on(self):  # PS2 8c197240: bt/s 8c197258 into the slot of `jmp @r0`
        x = self.extent("""
F 240 240 249
I 240 fall cmp/eq #0x0,r0
I 242 cjump bt/s 248 -> 248
I 244 slot _fmov.s fr13,@-r15
I 246 jump jmp @r0
I 248 slot _fmov.s fr12,@-r15
I 24a fall mov r1,r0
I 24c term rts
I 24e slot _nop
X 240
""")
        self.assertEqual(x["X 240"], "240-24f")  # was 240-249: the run to 24a left the body

    def test_piece_branch_into_a_slot_runs_on(self):  # PS2 8c188422's piece 8c189200: bf 8c189208 into a bra's slot
        x = self.extent(self.RTS + """
F 900 900 90b
I 900 fall sts.l PR,@-r15
I 902 call bsr 200 -> 200
I 904 slot _nop
I 906 call bsr 498 -> 498
I 908 slot _nop
I 90a fall lds.l @r15+,PR
I 90c term rts
I 90e slot _nop
I 200 fall tst r8,r8
I 202 cjump bf 206 -> 206
I 204 jump bra 300 -> 300
I 206 slot _tst #0x1,r0
I 208 fall mov r8,r0
I 20a term rts
I 20c slot _nop
I 300 term rts
I 302 slot _nop
X 900
""")
        self.assertEqual(x["X 900"], "200-20d 300-303 900-90f")  # was without 208-20d

    def test_slot_of_its_own_branch_does_not_run_on(self):  # control: no other branch into the slot, as before
        x = self.extent("""
F 400 400 403
I 400 jump bra 500 -> 500
I 402 slot _nop
I 404 fall mov r1,r0
I 500 term rts
I 502 slot _nop
X 400
""")
        self.assertEqual(x["X 400"], "400-403 500-503")


if __name__ == "__main__":
    unittest.main(verbosity=1)

"""TcText.movaWords on opcode tables (tests/java/TcTextStub.java): javac and java against the Ghidra jars, no Ghidra
application and no project. Skipped without config.local.json's ghidra_home and a JDK (java_home, else PATH).

The pool words a body reads through a `mova` base are its text: a vector's patch over them gets the image's bytes
back (TcText.restore), as for PC-relative loads. Spawn 8c0565b0 (`mova 8c0565f0,r0; mov r0,r14`, then mov.l
@(0x14..0x20,r14) on three paths) failed hidden vector 97 only: a repair set 8c056610, its callee 8c053400's address,
to 0x8cc01500, so the original called into scratch and no C naming its callee could match.

usage: python tests/test_mova_pool.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_tail import CP, SH4, TOOL, jdk  # noqa: E402

# Spawn main 8c0565b0, its 32 instructions (mnemonics from its packet's asm.txt, encoded)
SPAWN_8C0565B0 = """
I 8c0565b0 c70f r0  # mova 0x8c0565f0,r0
I 8c0565b2 2fe6     # mov.l r14,@-r15
I 8c0565b4 4f22     # sts.l PR,@-r15
I 8c0565b6 6e03     # mov r0,r14
I 8c0565b8 6042 r0  # mov.l @r4,r0
I 8c0565ba 8800     # cmp/eq #0x0,r0
I 8c0565bc 55e5     # mov.l @(0x14,r14),r5
I 8c0565be 8f08     # bf/s 0x8c0565d2
I 8c0565c0 8801     # _cmp/eq #0x1,r0
I 8c0565c2 53e6     # mov.l @(0x18,r14),r3
I 8c0565c4 430b     # jsr @r3
I 8c0565c6 5ee8     # _mov.l @(0x20,r14),r14
I 8c0565c8 4e0b     # jsr @r14
I 8c0565ca e401     # _mov #0x1,r4
I 8c0565cc 4f26     # lds.l @r15+,PR
I 8c0565ce 000b     # rts
I 8c0565d0 6ef6     # _mov.l @r15+,r14
I 8c0565d2 8b07     # bf 0x8c0565e4
I 8c0565d4 53e7     # mov.l @(0x1c,r14),r3
I 8c0565d6 430b     # jsr @r3
I 8c0565d8 5ee8     # _mov.l @(0x20,r14),r14
I 8c0565da 4e0b     # jsr @r14
I 8c0565dc e401     # _mov #0x1,r4
I 8c0565de 4f26     # lds.l @r15+,PR
I 8c0565e0 000b     # rts
I 8c0565e2 6ef6     # _mov.l @r15+,r14
I 8c0565e4 5ee8     # mov.l @(0x20,r14),r14
I 8c0565e6 4e0b     # jsr @r14
I 8c0565e8 e401     # _mov #0x1,r4
I 8c0565ea 4f26     # lds.l @r15+,PR
I 8c0565ec 000b     # rts
I 8c0565ee 6ef6     # _mov.l @r15+,r14
X
"""


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class MovaPool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="mova_", dir=TOOL / "tests" / ".work")
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(TOOL / "tests" / "java" / "TcTextStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def words(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcTextStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return [ln[3:] for ln in r.stdout.split("\n") if ln.startswith("X: ")]

    def test_spawn_8c0565b0(self):  # all three paths' callee words, the one hidden vector 97 patched among them
        self.assertEqual(self.words(SPAWN_8C0565B0), ["8c056604-8c056613"])

    def test_through_r0(self):  # the straight line after the mova: mov.l @(disp,r0), mov.w @(disp,r0), fmov.s @r0
        self.assertEqual(self.words("""
I 100 c701 r0  # mova 0x108,r0 ((100 & ~3) + 4 + 1*4)
I 102 5102     # mov.l @(8,r0),r1: 110-113
I 104 8503 r0  # mov.w @(6,r0),r0: 10e-10f, then r0 is something else
I 106 5203     # mov.l @(12,r0),r2: not the base any more
X
I 200 c700 r0  # mova 0x204,r0
I 202 f208     # fmov.s @r0,fr2
X
"""), ["10e-113", "204-207"])

    def test_line_ends(self):  # r0 written, a branch's delay slot, a bt: no copy and no load after them counts
        self.assertEqual(self.words("""
I 100 c701 r0  # mova 0x108,r0
I 102 6042 r0  # mov.l @r4,r0
I 104 6e03     # mov r0,r14: r0 is not the base here
I 106 51e1     # mov.l @(4,r14),r1
X
I 200 c701 r0  # mova 0x208,r0
I 202 a000     # bra
I 204 0009     # _nop
I 206 6e03     # mov r0,r14: past the branch
I 208 51e1     # mov.l @(4,r14),r1
X
I 300 c701 r0  # mova 0x308,r0
I 302 8900     # bt
I 304 5101     # mov.l @(4,r0),r1: on one path only
X
"""), ["-", "-", "-"])

    def test_other_registers(self):  # a load through a register no `mov r0,Rn` copied, or a store: not text
        self.assertEqual(self.words("""
I 100 c701 r0  # mova 0x108,r0
I 102 6d03     # mov r0,r13
I 104 51c1     # mov.l @(4,r12),r1
I 106 1d11     # mov.l r1,@(4,r13): a store
X
"""), ["-"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

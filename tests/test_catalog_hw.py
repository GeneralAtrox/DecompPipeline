"""TcCatalog.usedAsAddress, the catalog's hardware test for a hardware-looking constant (hw_refs), on a stub listing
(tests/java/TcCatalogStub.java): javac and java against the Ghidra jars, no Ghidra application and no project.
Skipped without config.local.json's ghidra_home and a JDK (java_home, else PATH).

Covers Spawn main's false hardware functions 8c03c964, 8c03da40 and 8c0481c0 (their only "hardware" is the colour
0xff000000 passed to Fade_Out), and 8c0e7818 (0xffff0000, a return value): the scan follows the flow, ending after
the delay slot of rts, rte, braf and jmp and going on at a bra's target, not at the next address; a call clobbers r0-r7
after its delay slot, not before it. A real use stays one: an address use in a delay slot, on either arm of a
conditional branch, at a bra's target (Spawn 8c059b80, the AICA), through a copy, and in r8-r15 across a call.

MatchedKeepsRow (no JDK): a function matched before the catalog flags it (Spawn 8c059c10) is not excluded: it keeps a
normal tier, its ledger row and its PASS (core/coverage.excluded_functions, core/orch.keep_matched).

usage: python tests/test_catalog_hw.py
"""
import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]

from test_tail import CP, SH4, jdk  # noqa: E402
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from unittest import mock  # noqa: E402

from core import coverage, orch, paths  # noqa: E402

POOL = "I 100 mov.l @(0x40,pc),{r}\n"  # the hardware-looking constant's load (its value does not matter here)


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class HwScan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="cathw_", dir=TOOL / "tests" / ".work")
        cls.out = pathlib.Path(cls.tmp.name)
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath",
                            str(SH4 / "ghidra_scripts"), str(TOOL / "tests" / "java" / "TcCatalogStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def used(self, reg, body):
        f = self.out / "cases.txt"
        f.write_text(POOL.format(r=reg) + body + f"U 100 {reg}\n", encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcCatalogStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        line = [x for x in r.stdout.splitlines() if x.startswith("U 100 ")]
        self.assertEqual(len(line), 1, r.stdout)
        return line[0].endswith(": true")

    def test_call_argument_is_not_an_address(self):  # Spawn 8c03c964: the colour in a call's delay slot
        self.assertFalse(self.used("r12", """
I 102 mov.l @(0x44,pc),r2
I 104 jsr @r2
I 106 _mov r12,r4
I 108 mov.l @(0x8,r14),r4
I 10a mov.w @r4,r3
"""))
        self.assertFalse(self.used("r12", """
I 102 mov.l @(0x44,pc),r2
I 104 jsr @r2
I 106 _mov r12,r4
I 108 mov.w @r4,r3
"""))  # r4 after the call is not the colour, whatever the callee left there

    def test_scan_ends_at_a_jump(self):  # Spawn 8c0481c0: tail call `jmp @r3`, then another case's code
        self.assertFalse(self.used("r4", """
I 102 mov.l @(0x44,pc),r3
I 104 jmp @r3
I 106 _nop
I 108 mov.w @r4,r3
"""))
        for transfer in ("bra 0x200", "braf r1", "rts", "rte"):
            self.assertFalse(self.used("r1", f"""
I 102 {transfer}
I 104 _nop
I 106 mov.l @r1,r0
"""), transfer)

    def test_real_uses(self):
        self.assertTrue(self.used("r1", """
I 102 mov #0x1,r2
I 104 mov.l r2,@r1
"""))  # a store through it
        self.assertTrue(self.used("r1", """
I 102 rts
I 104 _mov.l @r1,r0
"""))  # in the delay slot of rts
        self.assertTrue(self.used("r4", """
I 102 jsr @r2
I 104 _mov.l @r4,r5
"""))  # in the delay slot of a call: before the call's clobber
        self.assertTrue(self.used("r1", """
I 102 tst r0,r0
I 104 bt 0x120
I 106 mov.l @(0x10,r1),r0
"""))  # after a conditional branch (the fall-through path)
        self.assertTrue(self.used("r9", """
I 102 jsr @r2
I 104 _nop
I 106 mov r9,r5
I 108 or r6,r5
I 10a mov.l @r5,r0
"""))  # r8-r15 across a call, through a copy and an or

    def test_follows_branches(self):
        self.assertFalse(self.used("r0", """
I 102 bra 0x10a
I 104 _nop
I 106 mov.l @(r0,r14),r5
I 108 nop
I 10a rts
I 10c _nop
"""))  # Spawn 8c0e7818: a return value; the code after the bra is another case
        self.assertTrue(self.used("r14", """
I 102 bra 0x10c
I 104 _mov #0x0,r7
I 106 mov #0x1,r1
I 108 mov.l r1,@r14
I 10a add #0x1,r7
I 10c cmp/ge r3,r7
I 10e bf 0x106
I 110 rts
I 112 _nop
"""))  # Spawn 8c059b80: a store to the AICA in a loop entered by bra
        self.assertTrue(self.used("r4", """
I 102 cmp/eq #0x1,r0
I 104 bt 0x10e
I 106 bsr 0x200
I 108 _nop
I 10a rts
I 10c _nop
I 10e mov.l @r4,r3
"""))  # Spawn 8c059c10: the taken arm of a bt reads it; the fall-through arm's call does not end that path
        self.assertTrue(self.used("r1", """
I 102 tst r0,r0
I 104 bf/s 0x10c
I 106 _add #0x4,r1
I 108 rts
I 10a _nop
I 10c mov.l @r1,r0
"""))  # bf/s: its delay slot, then the target
        self.assertFalse(self.used("r1", """
I 102 add #0x1,r2
I 104 cmp/ge r3,r2
I 106 bf 0x102
I 108 rts
I 10a _nop
"""))  # a loop with no use ends


class MatchedKeepsRow(unittest.TestCase):
    def test_matched_function_that_gains_the_flag(self):
        f = dict(insns=40, cyclo=3, fpu_special=0, fp_mode=0, jump_tables=0, indirect_calls=0, callees=1, globals=2,
                 sys_ops=0)
        cat = {"functions": [dict(f, addr="8c059c10", hw_refs=1), dict(f, addr="8c059b80", hw_refs=1),
                             dict(f, addr="8c010000", hw_refs=0)]}
        led = {"loop": False, "functions": {"8c059c10": {"addr": "8c059c10", "status": "matched",
                                                         "attempts": [{"n": 1, "verdict": "PASS"}]}}}
        with tempfile.TemporaryDirectory(dir=TOOL / "tests" / ".work") as d:
            d = pathlib.Path(d)
            (d / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
            (d / "ledger.json").write_text(json.dumps(led), encoding="utf-8")
            with mock.patch.object(paths, "CATALOG", d / "catalog.json"), mock.patch.object(paths, "LEDGER",
                                                                                             d / "ledger.json"):
                self.assertEqual(coverage.excluded_functions(), {"8c059b80"})  # flagged, not matched: excluded
        funcs = {e["addr"]: dict(e, tier=orch.tier(e)) for e in cat["functions"]}
        before = copy.deepcopy(led)
        orch.keep_matched(funcs, led)
        self.assertEqual(funcs["8c059c10"]["tier"], "easy")  # a normal tier again (score 74)
        self.assertEqual(funcs["8c059b80"]["tier"], "excluded")
        self.assertEqual(led, before)  # its ledger row and its PASS, untouched


if __name__ == "__main__":
    unittest.main()

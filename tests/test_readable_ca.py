"""The readable judge's CA calls (no callee record) compared on the registers the target's prototype takes
(core/readable_ca.py). Pass controls: a leftover register the prototype has no slot for no longer fails. Fail controls:
a register the prototype takes still fails, a target with no names prototype (or a variadic one) keeps the full
compare, and a raw check (no reads) is unchanged. Pure functions on synthetic data: no Ghidra, no compiler.
Run: python -W error tests/test_readable_ca.py
"""
import copy
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import readable_ca  # noqa: E402
from core.compare import CALL_FIELDS, REG_INDEX, compare_vector  # noqa: E402
from core.readable import Registry  # noqa: E402

SCALE, ALLOC, TABLE, PRINT = 0x8C0622F6, 0x8C04E000, 0x8C0A0000, 0x8C0B0000
REG = Registry.empty({"functions": {
    "8c0622f6": {"proto": "void Mat3_SetScale(Mat3 *m, f32 s)"},  # r4, fr4
    "8c04e000": {"proto": "void *Mem_AllocAligned(u32 size, u32 align)", "storage": ["r4", "none"]},  # r4 only
    "8c0b0000": {"proto": "s32 sprintf(char *buf, const char *fmt, ...)"},  # variadic
}})
REG.typedefs = {"Mat3": "struct"}  # a pointer to it is an int register


def call(target, **regs):
    c = [target] + [0] * (CALL_FIELDS - 1)
    for r, v in regs.items():
        c[REG_INDEX[r]] = v
    return c


def run(calls, ca):
    return {"set": "dev", "writes": {}, "calls": calls, "faults": [], "xf": [0] * 16, "cov": set(), "done": True,
            "r0": 0, "fr0": 0, "call_args": ca}


META = {"ret": "void", "callees": []}
IMAGE, VEC = bytes(0x100), {"patches": []}
CA = ["r4", "r5", "r6", "r7", "fr4"]


def judge(exp, act, reads):
    return compare_vector(META, IMAGE, VEC, readable_ca.narrow({0: copy.deepcopy(exp)}, reads)[0], act)


class TargetRegs(unittest.TestCase):
    def test_prototypes(self):
        self.assertEqual(readable_ca.target_regs(REG, "8c0622f6"), {"r4", "fr4"})
        self.assertEqual(readable_ca.target_regs(REG, "8c04e000"), {"r4"})  # the names entry's "storage"
        self.assertIsNone(readable_ca.target_regs(REG, "8c0a0000"))  # no prototype: keep the full compare
        self.assertIsNone(readable_ca.target_regs(REG, "8c0b0000"))  # variadic: keep the full compare


class Narrow(unittest.TestCase):
    exp = run([call(SCALE, r4=0x8C100000, r5=1, r6=2, r7=3, fr4=0x3F800000)], {0: list(CA)})

    def test_pass_control_leftover_register(self):
        act = run([call(SCALE, r4=0x8C100000, fr4=0x3F800000)], {})  # r5..r7 never set by the readable call
        self.assertTrue(judge(self.exp, act, None))  # old rule: fails on r5..r7
        self.assertEqual(judge(self.exp, act, {SCALE: {"r4", "fr4"}}), [])  # new rule: passes

    def test_fail_control_register_the_prototype_takes(self):
        act = run([call(SCALE, r4=0x8C100004, r5=1, r6=2, r7=3, fr4=0x3F800000)], {})
        d = judge(self.exp, act, {SCALE: {"r4", "fr4"}})
        self.assertEqual(len(d), 1)
        self.assertIn("argument r4", d[0])
        act = run([call(SCALE, r4=0x8C100000, r5=1, r6=2, r7=3, fr4=0x40000000)], {})
        self.assertTrue(judge(self.exp, act, {SCALE: {"r4", "fr4"}}))

    def test_fail_control_unknown_target(self):
        exp = run([call(TABLE, r4=1, r5=2)], {0: ["r4", "r5"]})
        act = run([call(TABLE, r4=1)], {})
        reads = {SCALE: {"r4", "fr4"}, "8c0622f6": {"r4"}}  # a string key is a callee record's (callee_reads)
        self.assertTrue(judge(exp, act, reads))

    def test_stack_words_kept_and_raw_unchanged(self):
        exp = run([call(SCALE, r4=1, r5=2, s0=7)], {0: ["r4", "r5", "s0"]})
        got = readable_ca.narrow({0: copy.deepcopy(exp)}, {SCALE: {"r4", "fr4"}})[0]["call_args"][0]
        self.assertEqual(got, ["r4", "s0"])
        same = {0: copy.deepcopy(exp)}
        self.assertIs(readable_ca.narrow(same, None), same)
        self.assertEqual(same[0]["call_args"][0], ["r4", "r5", "s0"])


class CaReads(unittest.TestCase):
    def test_from_expected(self):
        def line(c):
            return "CALL " + " ".join(f"{x:08x}" for x in c)
        with tempfile.TemporaryDirectory() as d:
            h = pathlib.Path(d)
            (h / "expected.txt").write_text("\n".join([
                "V 0 dev", line(call(SCALE, r4=1)), "CA r4 r5 r6 r7 fr4", line(call(TABLE)), "CA r4 r5",
                line(call(PRINT)), "CA r4 r5 r6", line(call(ALLOC)), "CA r4 r5", "RET 0 0", ""]), encoding="utf-8")
            self.assertEqual(readable_ca.ca_reads(REG, h), {SCALE: {"r4", "fr4"}, ALLOC: {"r4"}})


if __name__ == "__main__":
    unittest.main()

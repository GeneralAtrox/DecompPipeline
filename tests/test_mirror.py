"""The mirror rule (docs/PROOF_GATES.md): 0x0c...... and 0x8c...... are one main-RAM address. One pass and one fail
control each:
1. core/compare.same_word: the two views of a RAM word are equal; another address, another area (0x0d, 0xac) or
   another word is not. compare_vector takes the other view in a call argument (PS2 8c182850: r5 0x0c86d088 written
   as 0x8c86d088), the result, a whole stored word and r8..r14 at exit, and still fails a wrong address (0x8c86d08c),
   a top byte stored alone and the high word of a u64;
2. tools/mirror_ptrs: a pool's 0x0c...... pointer is followed through mov and add into a call's r4..r7 and into
   compares with a register that doesn't hold it; written over, or in r0..r7 after a call, it is gone; two pointers
   compared (`p < end`) are not listed; a callee that compares its argument is (depth 1), one with no packet is
   unscanned; the review list leaves out functions whose C writes every such word as 0x0c.
Synthetic data. Run: python -W error tests/test_mirror.py.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from core import compare  # noqa: E402
import mirror_ptrs  # noqa: E402
from test_float_gaps import insns  # noqa: E402

PTR, VIEW = 0x0C86D088, 0x8C86D088
POOL = "mov.l 0x8c010200,r3  ; = 0x0c86d088  (global 8c86d088)"


def result(r0=0, calls=(), writes=None, gs=None):
    out = {"done": True, "faults": [], "r0": r0, "fr0": 0, "writes": writes or {}, "calls": list(calls),
           "xf": [0] * 16}
    if gs is not None:
        out["gs"] = gs
    return out


def call(target, **regs):
    row = [target] + [0] * 56
    for reg, v in regs.items():
        row[compare.REG_INDEX[reg]] = v
    return row


def word(addr, v):
    return {addr + i: (v >> (8 * i)) & 0xFF for i in range(4)}


META = {"ret": "u32", "callees": [{"addr": "8c182740", "regs": ["r4", "r5"]}]}
VEC = {"patches": []}


def judge(exp, act, meta=META):
    return compare.compare_vector(meta, b"", VEC, exp, act)


class SameWord(unittest.TestCase):
    def test_views(self):
        self.assertTrue(compare.same_word(PTR, VIEW))
        self.assertTrue(compare.same_word(VIEW, PTR))
        self.assertTrue(compare.same_word(0x0C000000, 0x8C000000))
        self.assertTrue(compare.same_word(0x0CFFFFFC, 0x8CFFFFFC))

    def test_not_views(self):
        self.assertFalse(compare.same_word(PTR, 0x8C86D08C))  # another word
        self.assertFalse(compare.same_word(0x0D000000, 0x8D000000))  # outside the 16 MB of RAM
        self.assertFalse(compare.same_word(PTR, 0xAC86D088))  # P2: not folded by the runtime
        self.assertFalse(compare.same_word(0x0B000000, 0x8B000000))


class Judge(unittest.TestCase):
    def test_call_argument(self):  # PS2 8c182850
        exp = result(calls=[call(0x8C182740, r5=PTR)])
        self.assertEqual(judge(exp, result(calls=[call(0x8C182740, r5=VIEW)])), [])
        d = judge(exp, result(calls=[call(0x8C182740, r5=0x8C86D08C)]))
        self.assertEqual(len(d), 1)
        self.assertIn("argument r5 expected 0x0c86d088, got 0x8c86d08c", d[0])

    def test_result_and_state(self):
        self.assertEqual(judge(result(r0=PTR), result(r0=VIEW)), [])
        self.assertTrue(judge(result(r0=PTR), result(r0=PTR + 4)))
        self.assertTrue(compare.compare_return({"ret": "u64"}, result(r0=PTR), result(r0=VIEW)))
        gs = [0] * 7
        self.assertEqual(compare.compare_state({"gs": gs[:6] + [PTR]}, {"gs": gs[:6] + [VIEW]}), [])
        self.assertTrue(compare.compare_state({"gs": gs[:6] + [PTR]}, {"gs": gs[:6] + [VIEW + 4]}))

    def test_stored_word(self):
        at = 0x8C200000
        self.assertEqual(judge(result(writes=word(at, PTR)), result(writes=word(at, VIEW))), [])
        self.assertTrue(judge(result(writes=word(at, PTR)), result(writes=word(at, 0x8C86D08C))))
        self.assertTrue(judge(result(writes={at + 3: 0x0C}), result(writes={at + 3: 0x8C})))  # a byte alone: a value


class FakeProject:
    def __init__(self, asm):
        self.asm = asm

    def insns(self, a):
        return insns(self.asm[a]) if a in self.asm else None


class Follow(unittest.TestCase):
    def test_into_call_and_compare(self):
        ins = insns([POOL, "add #0x18,r3", "mov r3,r5", "mov.l @r4,r1", "cmp/eq r1,r5",
                     "jsr @r2  ; call 8c182740", "_nop", "rts", "_nop"])
        cmps, calls, _ = mirror_ptrs.follow(ins, (), {PTR})
        self.assertEqual(cmps, ["8c010108"])
        self.assertEqual(calls, [("8c182740", ["r5"])])

    def test_gone(self):
        written = insns([POOL, "mov #0,r3", "mov.l @r4,r1", "cmp/eq r1,r3", "rts", "_nop"])
        self.assertEqual(mirror_ptrs.follow(written, (), {PTR})[0], [])
        after_call = insns([POOL, "mov r3,r8", "jsr @r2  ; call 8c182740", "_nop", "mov.l @r4,r1", "cmp/eq r1,r3",
                            "cmp/eq r1,r8", "rts", "_nop"])
        self.assertEqual(mirror_ptrs.follow(after_call, (), {PTR})[0], ["8c01010c"])  # r8 survives, r3 doesn't
        other = insns(["mov.l 0x8c010200,r3  ; = 0x0c86d088  (function 8c86d088)", "mov.l @r4,r1", "cmp/eq r1,r3",
                       "rts", "_nop"])
        self.assertEqual(mirror_ptrs.follow(other, (), {PTR})[0], [])

    def test_two_pointers(self):  # p < end over one table
        ins = insns([POOL, "mov.w 0x8c010202,r6", "add r3,r6", "add #4,r3", "cmp/hs r6,r3", "rts", "_nop"])
        self.assertEqual(mirror_ptrs.follow(ins, (), {PTR})[0], [])

    def test_paths(self):  # r11 is the pointer on the path above in the listing only, not on the one that compares
        ins = insns(["tst r4,r4", "bt 0x8c01010c", "mov.l 0x8c010200,r11  ; = 0x0c86d088  (global 8c86d088)",
                     "mov.l @r4,r1", "rts", "_nop", "mov.l @r4,r1", "cmp/eq r1,r11", "rts", "_nop"])
        self.assertEqual(mirror_ptrs.follow(ins, (), {PTR})[0], [])
        joined = insns(["tst r4,r4", "bt 0x8c010106", "mov.l 0x8c010200,r11  ; = 0x0c86d088  (global 8c86d088)",
                        "mov.l @r4,r1", "cmp/eq r1,r11", "rts", "_nop"])
        self.assertEqual(mirror_ptrs.follow(joined, (), {PTR})[0], ["8c010108"])  # may: one path brings it

    def test_callee(self):
        rp = FakeProject({"8c182740": ["mov r5,r14", "mov.l @r4,r1", "cmp/eq r1,r14", "rts", "_nop"]})
        hits, unscanned = mirror_ptrs.scan_callee(rp, "8c182740", ["r5"], 1, {})
        self.assertEqual(hits, [("8c182740", ["r5"], 1, ["8c010104"])])
        self.assertEqual(mirror_ptrs.scan_callee(rp, "8c182740", ["r4"], 1, {})[0], [])
        self.assertEqual(mirror_ptrs.scan_callee(rp, "8c999999", ["r4"], 1, {}), ([], ["8c999999"]))

    def test_review(self):
        hit = {"callee": "8c182740", "regs": ["r5"], "depth": 1, "compares": ["8c010104"]}
        rows = [{"addr": "8c182850", "c": {"0c86d088": "0x8c"}, "self": [], "callee": [hit]},
                {"addr": "8c010000", "c": {"0c86d088": "0x0c"}, "self": ["8c010104"], "callee": []}]
        self.assertEqual(list(mirror_ptrs.review(rows)), ["8c182850"])


if __name__ == "__main__":
    unittest.main()

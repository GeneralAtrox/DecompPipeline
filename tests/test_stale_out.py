"""Stale words of a callee's out buffer (core/readable_stack.stale_out_words, out_dlines, settle_stores;
core/readable_outptr.parked_bytes): the original copies a whole out buffer that holds words it parked there itself
(the out-pointer delivery hides those bytes from the readable local); a written word equal to such a non-zero whole parked
word reads as the original's, every other word, byte and vector compares as before. Pure functions on synthetic data.
Run: python tests/test_stale_out.py   (or python -m unittest discover -s tests -p "test_*.py")
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import readable_outptr, readable_stack  # noqa: E402
from core.compare import Memory  # noqa: E402

LO = 0x8cffd000  # a frame bottom well above the memory these tests write to
W = 0x8c200000  # the out buffer's words are written here


def writes(words=None):
    return {"writes": {a + i: (v >> (8 * i)) & 0xFF for a, v in (words or {}).items() for i in range(4)}}


def parked(hexes):
    return [(0, 0, h) for h in hexes]


class StaleOut(unittest.TestCase):
    def setUp(self):
        self._on = readable_stack.STALE_OUT
        readable_stack.STALE_OUT = True

    def tearDown(self):
        readable_stack.STALE_OUT = self._on

    def test_masks_a_parked_word(self):
        exp, act = writes({W: 0x8cc02104}), writes({W: 0x11111111})
        readable_stack.stale_out_words({"writes": exp["writes"]}, act, parked(["0421c08c"]), LO)
        self.assertEqual(act["writes"], exp["writes"])

    def test_a_different_word_still_differs(self):
        """discriminates the rule from a blanket mask: the original wrote 5, the C wrote 6, 5 was never parked."""
        exp, act = writes({W: 5, W + 4: 0x8cc02104}), writes({W: 6, W + 4: 0x22222222})
        readable_stack.stale_out_words(exp, act, parked(["0421c08c"]), LO)
        self.assertEqual(act["writes"][W], 6)  # untouched
        self.assertEqual(sum(act["writes"][W + 4 + i] << (8 * i) for i in range(4)), 0x8cc02104)

    def test_zero_partial_and_frame_words_are_not_masked(self):
        exp = writes({W: 0, W + 4: 0x8cc02104, W + 8: 0x8cc02108, LO + 4: 0x8cc02104})
        act = writes({W: 7, W + 4: 1, W + 8: 2, LO + 4: 3})
        # W+8 is parked only in part ("--" bytes), W is a zero parked word, LO+4 is a frame word (not output memory)
        readable_stack.stale_out_words(exp, act, parked(["00000000" "0421c08c" "08--c08c"]), LO)
        self.assertEqual(act["writes"][W], 7)
        self.assertEqual(act["writes"][W + 8], 2)
        self.assertEqual(act["writes"][LO + 4], 3)
        self.assertEqual(sum(act["writes"][W + 4 + i] << (8 * i) for i in range(4)), 0x8cc02104)

    def test_a_word_the_original_did_not_write_is_not_made_up(self):
        exp, act = writes(), writes({W: 0x8cc02104})
        readable_stack.stale_out_words(exp, act, parked(["0421c08c"]), LO)
        self.assertEqual(sum(act["writes"][W + i] << (8 * i) for i in range(4)), 0x8cc02104)
        readable_stack.stale_out_words(exp, None, parked(["0421c08c"]), LO)  # no readable output: nothing to do
        readable_stack.stale_out_words(exp, act, None, LO)  # no parked bytes for the vector: nothing to do

    def test_dlines_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "vectors_out.txt"
            self.assertEqual(readable_stack.out_dlines(out), {})  # no sidecar
            out.with_suffix(".parked").write_text("S 3 0 8 0421c08c\nS 3 1 0 --------\nS 9 0 0 00\nbad line\n", encoding="utf-8")
            self.assertEqual(readable_stack.out_dlines(out), {3: [(0, 8, "0421c08c"), (1, 0, "--------")], 9: [(0, 0, "00")]})
            readable_stack.STALE_OUT = False  # the A/B's OLD side: as if the sidecar were not there
            self.assertEqual(readable_stack.out_dlines(out), {})

    def test_settle_stores_runs_both_readings(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "vectors_out.txt"
            out.with_suffix(".parked").write_text("S 4 0 0 0421c08c\n", encoding="utf-8")
            exp, act = writes({W: 0x8cc02104}), writes({W: 1})
            readable_stack.settle_stores(exp, act, None, {}, {}, lambda a: 0, LO, out, 4)  # no host stack: pair_stores is a no-op
            self.assertEqual(act["writes"], exp["writes"])
            act = writes({W: 1})
            readable_stack.settle_stores(exp, act, None, {}, {}, lambda a: 0, LO, out, 5)  # another vector: not parked there
            self.assertEqual(act["writes"][W], 1)

    def test_parked_bytes(self):
        """a byte the original wrote (deliver's xx) is reported when both paints' runs hold the same value; a byte
        still holding the paint (the original left it) or one that differs between the paints is --."""
        guest, mem = W, Memory(b"", {"patches": []})
        c = [readable_outptr.canary(guest + k) for k in range(4)]
        runs = []
        for flip in readable_outptr.FLIPS:
            h = "".join(f"{b:02x}" for b in (0x04, c[1] ^ flip, 0x99, 0x21 if flip == 0 else 0x22))
            runs.append(((h, "1000"), flip))  # stores mask: byte 0 stored to by the original
        got = readable_outptr.parked_bytes(runs, guest, mem)
        self.assertEqual(got, "04--99--")  # byte 0 agrees; byte 1 holds the paint; byte 2 agrees; byte 3 differs


if __name__ == "__main__":
    unittest.main()

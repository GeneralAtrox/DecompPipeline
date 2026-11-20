"""Decoder tests: encodings worked by hand from the PowerPC and AltiVec manuals' instruction formats, and the
golden words (tools/make_golden.py: one word per op seen in Too Human, decoded when the decoder matched a reference
disassembler on every code word).

    python -m unittest discover tests
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from xenontranslate.decode import decode  # noqa: E402

HERE = os.path.dirname(__file__)

# word -> (op, fields that must match)
MANUAL = {
    0x38600001: ("addi", {"rd": 3, "ra": 0, "simm": 1}),                  # li r3,1
    0x7C0802A6: ("mfspr", {"rd": 0, "spr": 8}),                           # mflr r0
    0x4E800020: ("bclr", {"bo": 20, "bi": 0, "lk": 0}),                   # blr
    0x7C6307B4: ("extsw", {"ra": 3, "rs": 3}),                            # extsw r3,r3
    0x78630020: ("rldicl", {"ra": 3, "rs": 3, "sh": 0, "mb": 32}),        # clrldi r3,r3,32
    0xF8210008: ("std", {"rd": 1, "ra": 1, "d": 8}),                             # std r1,8(r1)
    0xE8610010: ("ld", {"rd": 3, "ra": 1, "d": 16}),                              # ld r3,16(r1)
    0x7C2320CE: ("lvx", {"vd": 1, "ra": 3, "rb": 4}),                     # lvx v1,r3,r4
    0x1022180A: ("vaddfp", {"vd": 1, "va": 2, "vb": 3}),                  # vaddfp v1,v2,v3
    0x1022192B: ("vperm", {"vd": 1, "va": 2, "vb": 3, "vc": 4}),          # vperm v1,v2,v3,v4
    0x7C832000: ("cmp", {"crfd": 1, "l": 0, "ra": 3, "rb": 4}),           # cmpw cr1,r3,r4
    0x7CA32000: ("cmp", {"crfd": 1, "l": 1, "ra": 3, "rb": 4}),           # cmpd cr1,r3,r4
    0x7C6321D2: ("mulld", {"rd": 3, "ra": 3, "rb": 4}),                   # mulld r3,r3,r4
}


class TestDecode(unittest.TestCase):
    def test_manual(self):
        for w, (op, f) in MANUAL.items():
            i = decode(w)
            self.assertEqual(i.op, op, f"{w:08x}")
            for k, v in f.items():
                self.assertEqual(i.f.get(k), v, f"{w:08x} {k}")

    def test_golden(self):
        g = json.load(open(os.path.join(HERE, "golden_words.json")))
        self.assertGreater(len(g), 250)
        for op, e in g.items():
            i = decode(int(e["word"], 16), int(e["addr"], 16))
            self.assertEqual(i.op, op, e["word"])
            self.assertEqual(i.f, e["f"], e["word"])

    def test_vmx128_present(self):
        g = json.load(open(os.path.join(HERE, "golden_words.json")))
        for op in ("lvx128", "stvx128", "vmulfp128", "vmsum4fp128", "vperm128", "vpermwi128", "vrlimi128",
                   "vsldoi128", "vcsxwfp128", "lvlx128", "lvrx128", "vupkd3d128"):
            self.assertIn(op, g)


    def test_vupk_halfword_form(self):
        """vupkhsb128 / vupklsb128 words with vA = 0x60 are the halfword-to-word unpacks (Xenia 95a5c3ee
        ppc_emit_altivec.cc:2024-2051; the game feeds them to vcsxwfp128, vaddsws, vslw128 and vsubsws), and are lifted to the
        halfword helpers. Any other vA keeps the byte name."""
        from types import SimpleNamespace
        from xenontranslate import vmx
        for w, op, helper in ((0x1BC0FFA5, "vupkhsh128", "xv_vupkhsh"), (0x1BC0B7E4, "vupklsh128", "xv_vupklsh"),
                               (0x1BC0FF85, "vupkhsb128", "xv_vupkhsb")):    # last: vA = 0x40, not the halfword form
            i = decode(w, 0x82A131BC)
            self.assertEqual(i.op, op, f"{w:08x}")
            ft = SimpleNamespace(vhelpers=set(), notes=[])
            vmx.lift(ft, i)
            self.assertEqual(ft.vhelpers, {helper}, f"{w:08x}")
        self.assertIn("vupkhsh128", __import__("xenontranslate.decode", fromlist=["x"]).VECTOR_OPS)


if __name__ == "__main__":
    unittest.main()

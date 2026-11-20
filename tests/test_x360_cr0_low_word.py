"""A dotted instruction sets CR0 from the low 32 bits of its result, signed (32-bit mode, MSR[SF]=0). Too Human's _fptostr
82a6cce0 ends its digit loop with addic. r5,r5,-1; bgt, and its r5 is decpt (a zero-extending lwz) plus ndigits, so the
high word is set when decpt is negative. A 64-bit test kept looping and wrote past the stack top (fault at 70100000).
usage: python tests/test_x360_cr0_low_word.py
"""
import pathlib
import sys
import unittest

X360 = pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc"
sys.path.insert(0, str(X360 / "translator"))
from xenontranslate.image import XexImage  # noqa: E402
from xenontranslate.translate import Facts, Signature, translate_function  # noqa: E402

BASE = 0x82010000


def translate(words, port):
    code = bytearray(0x100)
    for i, w in enumerate(words):
        code[i * 4:i * 4 + 4] = w.to_bytes(4, "big")
    sig = Signature("fn_82010000", "u64 fn_82010000(u64 x)", [("x", "u64", "r3", -1)], "u64")   # r3 is read first: the judge hands it over
    src, _ = translate_function(XexImage(bytes(code), BASE), BASE, sig, Facts(port=port), [(BASE, BASE + 4 * len(words))])
    return src


class Cr0LowWord(unittest.TestCase):
    def test_greater_than_tests_the_low_word(self):
        for port in (True, False):
            src = translate([0x3463FFFF,    # addic. r3,r3,-1
                             0x4181FFFC,    # bgt -4
                             0x4E800020], port)
            self.assertNotIn("(s64)", src, src)
            self.assertIn("(s32)", src, src)

    def test_equal_tests_the_low_word(self):
        for port in (True, False):
            src = translate([0x3463FFFF,    # addic. r3,r3,-1
                             0x4082FFFC,    # bne -4
                             0x4E800020], port)
            self.assertIn("(u32)", src, src)


if __name__ == "__main__":
    unittest.main()

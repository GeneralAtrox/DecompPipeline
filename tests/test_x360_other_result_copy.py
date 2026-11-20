"""A copy must not be carried across a call that gives the other result register. The mocked callee sets both r3 and f1,
and passes.other_result gives a float call's r3 as tc_lastr() after it. CallS.writes() said a float call wrote only f1, so
the propagator renamed a saved copy of r3 (mr r30,r3 before the call) to r3, and other_result then overwrote that r3 with
the callee's. Too Human's 82ed4988 then built a new object on the global it had just passed to 82ef2500, and the loader
thread read a float as a pointer (fault at frame 324). The pattern, as there:
    bl alloc ; mr r30,r3 ; li r3,0x40 ; bl float_fn ; mr r3,r30 ; bl use
The third call must get the first call's result, not the float call's r3.
usage: python tests/test_x360_other_result_copy.py
"""
import pathlib
import re
import sys
import unittest

X360 = pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc"
sys.path.insert(0, str(X360 / "translator"))
from xenontranslate.image import XexImage  # noqa: E402
from xenontranslate.translate import Facts, Signature, translate_function  # noqa: E402

BASE = 0x82010000
WORDS = [0x48000101,   # bl 82010100 (returns r3)
         0x7C7E1B78,   # mr r30,r3
         0x38600040,   # li r3,0x40
         0x480001F5,   # bl 82010200 (returns f1)
         0x7FC3F378,   # mr r3,r30
         0x480002ED,   # bl 82010300
         0x4E800020]   # blr


def translate(returns, keeps=None):
    code = bytearray(0x400)
    for i, w in enumerate(WORDS):
        code[i * 4:i * 4 + 4] = w.to_bytes(4, "big")
    for at in (0x100, 0x200, 0x300):
        code[at:at + 4] = (0x4E800020).to_bytes(4, "big")
    sig = Signature("fn_82010000", "u64 fn_82010000(void)", [], "u64", returns=returns, keeps=keeps)
    # the port kit's form, as Too Human is translated: the judge's form kept r30 apart and never showed it
    src, _ = translate_function(XexImage(bytes(code), BASE), BASE, sig, Facts(port=True), [(BASE, BASE + 4 * len(WORDS))])
    return src


def holds_first_result(src):
    """Whether the r3 the third call passes is still the first call's result: follow the names it was copied to, and fail
    on any other assignment to the name passed between the two calls."""
    body = src[src.index("tc_call(0x82010100u"):src.index("tc_call(0x82010300u")]
    first = re.search(r"(\w+) = tc_call\(0x82010100u", src).group(1)
    arg = re.findall(r"a\.r\[0\] = ([^;]+);", body)[-1]
    names = {first}
    for dst, val in re.findall(r"^\s*(\w+) = ([^;]+);", body.split(chr(10), 1)[1], re.M):
        if dst.startswith("a.") or dst in ("f1",):
            continue
        if val in names:
            names.add(dst)
        else:
            names.discard(dst)
    return arg in names


class CopyAcrossOtherResult(unittest.TestCase):
    def test_float_call_does_not_take_the_saved_copy(self):
        src = translate({0x82010100: "r", 0x82010200: "f", 0x82010300: "r"})
        self.assertNotIn("tc_lastr", src, src)   # nothing after the float call reads its r3
        self.assertTrue(holds_first_result(src), src)   # the allocation's result, by whatever name it was kept in

    def test_a_callee_that_keeps_r3_still_lets_the_copy_through(self):
        src = translate({0x82010100: "r", 0x82010200: "f", 0x82010300: "r"}, keeps={0x82010200: {"r3"}})
        self.assertTrue(holds_first_result(src), src)


if __name__ == "__main__":
    unittest.main()

"""sh4-translate: an arithmetic result stored to the own frame and read back as an integer is stored with the bits the
judge's reference holds (f_bits: a NaN is 0x7fc00000), as flds already takes them. Toy Commander 8c09a518 multiplies
by a table word 0xffffffff (a NaN), stores the product to its stack and tests the stored word's sign with mov.l: the
reference's NaN has the sign clear, the PC's keeps the operand's (set), so the C took the other branch on hidden
vector 65. Pass and fail controls on synthetic code:
1. fmul, fmov.s to @r15, mov.l @r15: the store is wr32(r15, f_bits(..));
2. the same with an fmov.s reload instead of the mov.l: wrf32 as before (the memory compare takes a NaN word as one);
3. a parameter (no arithmetic) stored and read back with mov.l: wrf32 as before;
4. fmul, fmov.s to @(r0,r15) with r0 = 4, mov.l @(4,r15): f_bits (the offset is followed through r0);
5. the store at @(r0,r15) with r0 = 8 and the read at @(4,r15): another word, wrf32 as before.
Run: python -W error tests/test_nan_store.py.
"""
import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
from sh4translate import translate as tx  # noqa: E402

ADD_M4, ADD_4, RTS = 0x7FFC, 0x7F04, 0x000B     # add #-4,r15; add #4,r15; rts
FMUL = 0xF122           # fmul fr2,fr1
ST = 0xFF1A             # fmov.s fr1,@r15
ST4 = 0xFF4A            # fmov.s fr4,@r15 (the parameter)
LD_INT = 0x60F2         # mov.l @r15,r0
LD_FLT = 0xF0F8         # fmov.s @r15,fr0
ST_R0 = 0xFF17          # fmov.s fr1,@(r0,r15)
MOV_R0 = 0xE000         # mov #imm,r0
LD_D4 = 0x50F1          # mov.l @(4,r15),r0


def translate(words, params=()):
    data = b"".join(struct.pack("<H", w) for w in words)
    proto = "u32 fn_8c000000(%s)" % (", ".join(f"f32 {n}" for n, _, _ in params) or "void")
    sig = tx.Signature("fn_8c000000", proto, list(params), "u32", None, {})
    src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, sig, tx.TargetFacts())
    return src[0] if isinstance(src, tuple) else src


class NanStore(unittest.TestCase):
    def test_read_back_as_integer(self):
        c = translate([ADD_M4, FMUL, ST, LD_INT, RTS, ADD_4])
        self.assertIn("wr32(r15, f_bits(", c)
        self.assertIn("static u32 f_bits(f32 x)", c)
        self.assertNotIn("wrf32(", c)

    def test_float_reload_unchanged(self):
        c = translate([ADD_M4, FMUL, ST, LD_FLT, RTS, ADD_4])
        self.assertIn("wrf32(r15, ", c)
        self.assertNotIn("f_bits", c)

    def test_parameter_unchanged(self):
        c = translate([ADD_M4, ST4, LD_INT, RTS, ADD_4], [("param_fr4", "f32", "fr4")])
        self.assertNotIn("f_bits", c)
        self.assertIn("wrf32(r15, ", c)

    def test_r0_offset_same_word(self):
        c = translate([ADD_M4, ADD_M4, FMUL, MOV_R0 | 4, ST_R0, LD_D4, ADD_4, RTS, ADD_4])
        self.assertIn("wr32(r15 + 0x4u, f_bits(", c)

    def test_r0_offset_other_word(self):
        c = translate([ADD_M4, ADD_M4, ADD_M4, FMUL, MOV_R0 | 8, ST_R0, LD_D4, ADD_4, ADD_4, RTS, ADD_4])
        self.assertNotIn("f_bits", c)


if __name__ == "__main__":
    unittest.main()

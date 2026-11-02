"""sh2-translate fixes from the four Saturn games' failures (satfix round, 2026-10-30), without Ghidra: each test
translates a few hand-assembled SH-2 instructions and checks the C the judge needs.

usage: python tests/test_sh2_fixes.py
"""
import json
import pathlib
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
SH2 = TOOL / "targets" / "saturn_sh2"
sys.path.insert(0, str(SH2 / "translator"))
from sh2translate.translate import Image, Signature, TargetFacts, translate_function  # noqa: E402

FACTS = TargetFacts.from_target_json(json.loads((SH2 / "target.json").read_text(encoding="utf-8")))


def c_of(code, hi, callees=None):
    """The C of void fn_06000000(void) at 06000000 with body [06000000, 06000000 + hi); callees: meta.json's list."""
    m = {"name": "fn_06000000", "signature": "void fn_06000000(void)", "params": [], "ret": "void"}
    if callees is not None:
        m["callees"] = callees
    return translate_function(Image(bytes.fromhex(code), FACTS.image_base), 0x06000000, Signature.from_meta(m), FACTS,
                              [(0x06000000, 0x06000000 + hi)], entries={0x06000000})


class ComputedCallArgs(unittest.TestCase):
    # mov.l @(tbl,pc),r3; mov.l @r3,r3; mov #5,r4; jsr @r3; nop; rts; nop; nop; tbl: .long 0x0020005c
    CODE = "D303" "6332" "E405" "430B" "0009" "000B" "0009" "0009" "0020005C"

    def test_pointer_call_passes_r4_to_r7(self):
        """A call through a pointer read from memory passes r4..r7 even when every callee DP's meta lists takes only
        r0: the judge compares there the ones the original set (CA lines; Dragon Force 0601132e's BIOS call through
        0x0020005c, r4 = 1 or 0x10e, was passed as 0)."""
        src = c_of(self.CODE, 0x14, [{"addr": "06001000", "regs": ["r0"]}])
        self.assertIn("tc_call(r3, &a);", src)
        self.assertIn("a.r[0] = 0x5u;", src)  # r4 = 5, its constant folded in
        for k in range(1, 4):
            self.assertIn(f"a.r[{k}] = r{4 + k};", src)
        self.assertIn("a.rlo[0] = r0;", src)

    def test_without_callee_list_every_argument(self):
        """No callees in meta: the call gets them all, as before (r4..r7, the stack words, r0..r3)."""
        src = c_of(self.CODE, 0x14)
        self.assertIn("a.s[7] = ", src)
        self.assertIn("a.rlo[3] = r3;", src)


class RegisterMaskTable(unittest.TestCase):
    # mov #0x7c,r1; and r4,r1; mova tbl,r0; mov.l @(r0,r1),r2; jmp @r2; nop;
    # 0600000c: mov #0,r0; rts; nop; 06000012: mov #1,r0; rts; nop; tbl (06000018): 32 words
    CODE = "E17C" "2149" "C704" "021E" "422B" "0009" "E000" "000B" "0009" "E001" "000B" "0009" +         "0600000C06000012" * 16

    def test_jump_through_a_register_masked_table(self):
        """`and r4,r1` with r1 = 0x7c bounds the index to 0, 4, .., 0x7c: a switch over the table's 32 words, not a
        tail call of the loaded word (Robo Pit 0601e1fc and 06081414 made a call the original never makes)."""
        src = c_of(self.CODE, 0x18, [])
        self.assertIn("switch (sw_) {", src)  # (its default arm still calls a word the table never holds)
        for k in ("0x0600000cu", "0x06000012u"):
            self.assertIn(f"case {k}:", src)


if __name__ == "__main__":
    unittest.main()

"""A jump table the function's own body holds is never read by the translated C (rule R8, a read of machine code: Too
Human's 82e0a108, 82c6f098, 82ccf058, 82d89388 and 82dcde50 read their table's first word, right after the bctr, on
every vector with index 0). The switch is on the index, and each case sets what the table load left (r0, CTR) before
its target (xenontranslate/jtab.py). A table outside the body is data and is still read.
usage: python tests/test_x360_table_in_code.py
"""
import json
import pathlib
import re
import struct
import sys
import unittest

X360 = pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc"
sys.path[:0] = [str(X360 / "translator")]
from xenontranslate.image import XexImage  # noqa: E402
from xenontranslate.translate import Facts, Signature, translate_function  # noqa: E402

BASE = 0x82000000


def function(table_at):
    """switch (r3) { 0: return 1; 1: return 2; 2: return 3; } return 0; with its table of targets at table_at."""
    words = [0x2B030002,                    # cmplwi cr6,r3,2
             0x41990040,                    # bgt cr6,82000044
             0x3D808200,                    # lis r12,0x8200
             0x398C0000 | table_at & 0xFFFF,  # addi r12,r12,table_at
             0x5460103A,                    # rlwinm r0,r3,2,0,29
             0x7C0C002E,                    # lwzx r0,r12,r0
             0x7C0903A6,                    # mtctr r0
             0x4E800420,                    # bctr
             0, 0, 0,                       # 82000020: the table when it is in the body
             0x38600001, 0x4E800020,        # 8200002c: li r3,1; blr
             0x38600002, 0x4E800020,        # 82000034: li r3,2; blr
             0x38600003, 0x4E800020,        # 8200003c: li r3,3; blr
             0x38600000, 0x4E800020]        # 82000044: li r3,0; blr
    img = bytearray(0x200)
    struct.pack_into(f">{len(words)}I", img, 0, *words)
    struct.pack_into(">3I", img, table_at - BASE, 0x8200002C, 0x82000034, 0x8200003C)
    dol = XexImage(bytes(img), BASE, [(BASE, BASE + 0x200, True)])
    facts = Facts.from_target(json.loads((X360 / "target.json").read_text()), {}, [])
    src, _ = translate_function(dol, BASE, Signature.generic_for(BASE), facts, [(BASE, BASE + 4 * len(words))])
    return src


class TableInCode(unittest.TestCase):
    def test_table_in_the_body_is_not_read(self):
        src = function(0x82000020)
        self.assertNotIn("rd32(", src)
        self.assertRegex(src, r"switch \((g_|sw_)\)")
        cases = re.findall(r"case (0x[0-9a-f]+u|\d+u?):", src)
        self.assertEqual(len(cases), 3, src)
        for v in ("1ull", "2ull", "3ull"):
            self.assertIn(v, src)

    def test_table_outside_the_body_is_still_read(self):
        self.assertIn("rd32(", function(0x82000100))


if __name__ == "__main__":
    unittest.main()

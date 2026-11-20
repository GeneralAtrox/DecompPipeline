"""GameCube: the f1 an inner frame of a self-recursive function starts with. The translator runs each frame of a
self-call as a C call of px_rec. When the body sets f1 only through calls, frames.recursive_f1 already starts every
frame with tc_lastf(). Skies of Arcadia 80288de4 also loads f1 itself (lfs f1 at 80288eb0); its inner frame started
with f1 = 0.0 and passed that to its callback (bctrl), where the original passes the f1 live at the self-call
(r5d vector 20, call #6: expected 49695.398, got 0). Now such a px_rec takes the caller's f1 as f1_, each self-call
passes its live f1, and the exported wrapper passes tc_lastf() (0 before any call, as before).
The test translates a small recursive function with and without the lfs:
  - with it: px_rec has the f64 f1_ parameter, the self-call passes an f1 argument, the inner frame's callback
    passes f1 (not 0.0), and the wrapper passes tc_lastf();
  - without it: the text is unchanged from before (no f1_, tc_lastf() at entry as frames.recursive_f1 did).
usage: python tests/test_gc_recf1.py
"""
import pathlib
import re
import struct
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
PPC = TOOL / "targets" / "gamecube_ppc"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(PPC), str(PPC / "translator")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
import ppc_check  # noqa: E402
from ppctranslate.image import Dol  # noqa: E402
from ppctranslate.translate import Facts, Signature, translate_function  # noqa: E402

META = {"name": "fn_80003100", "signature": "void fn_80003100(u32 param_1, u32 param_2, u32 param_3)",
        "ret": "void",
        "params": [{"name": f"param_{i + 1}", "kind": "u32", "storage": f"r{3 + i}", "stack_offset": -1}
                   for i in range(3)]}
# fn(r3 n, r4 p, r5 cb): frame; r31 = n, r30 = p, r29 = cb; cb(...) passing f1; if n: [lfs f1,0(p)]; fn(n-1, p, cb)
WORDS = ["9421FFE0", "7C0802A6", "90010024", "93E1001C", "93C10018", "93A10014",
         "7C7F1B78", "7C9E2378", "7CBD2B78",               # mr r31,r3; mr r30,r4; mr r29,r5
         "7FA903A6", "4E800421",                           # mtctr r29; bctrl (cb gets r3.., f1)
         "2C1F0000", "41820018",                           # cmpwi r31,0; beq end (+0x18)
         "C03E0000",                                       # lfs f1,0(r30)   (LFS: the case)
         "387FFFFF", "7FC4F378", "7FA5EB78",               # addi r3,r31,-1; mr r4,r30; mr r5,r29
         "4BFFFFB1",                                       # bl entry (patched below)
         "80010024", "83E1001C", "83C10018", "83A10014", "7C0803A6", "38210020", "4E800020"]
LFS = 13
# loop: mtctr r29; bctrl; lfs f1,0(r30); addic. r31,r31,-1; bne loop. Its head is reached from the entry (f1_)
# and from the back edge (the lfs), so f1 stays a local whose first read is the cb call's argument
LOOP = ["7FA903A6", "4E800421", "C03E0000", "37FFFFFF", "4082FFF0"]


def dol(lfs=True, loop=False):
    w = list(WORDS)
    if not lfs:
        w[LFS] = "60000000"
    if loop:  # LOOP in place of the cmpwi..lfs: the loop head's cb call reads an f1 that merges f1_ and the lfs's
        w[w.index("2C1F0000"):LFS + 1] = LOOP
        w[9:11] = ["60000000", "60000000"]  # no cb call before it: nothing then reads f1_ but the declaration
    bl = 0x80003100 + 4 * w.index("4BFFFFB1")
    w[w.index("4BFFFFB1")] = f"{0x48000001 | ((0x80003100 - bl) & 0x03FFFFFC):08X}"
    code = bytes.fromhex("".join(w))
    head = bytearray(0x100)
    struct.pack_into(">I", head, 0, 0x100)
    struct.pack_into(">I", head, 0x48, 0x80003100)
    struct.pack_into(">I", head, 0x90, len(code))
    return Dol(bytes(head) + code), 0x80003100 + len(code)

def translate(lfs, loop=False):
    d, end = dol(lfs, loop)
    return translate_function(d, 0x80003100, Signature.from_meta(META), Facts(), [(0x80003100, end)])[0]


class RecursiveF1(unittest.TestCase):
    def test_inner_frame_gets_callers_f1(self):
        src = translate(True)
        self.assertRegex(src, r"static void px_rec\(u32 sp_, u32 param_1, u32 param_2, u32 param_3, f64 f1_\)")
        self.assertRegex(src, r"px_rec\(0x[0-9a-f]+u, param_1, param_2, param_3, tc_lastf\(\)\)")
        inner = re.findall(r"px_rec\(([^;]*)\);", src.split("{", 1)[1])
        self.assertTrue(inner and all(not a.rstrip(")").endswith("0.0") for a in inner), inner)
        self.assertEqual(ppc_check.lint(src, META), [])

    def test_merged_f1_local_starts_with_f1_(self):
        # Skies 80288de4 and 24 others: f1 kept as a local (a merge keeps it) was declared 0.0 and f1_ never read;
        # MSVC /W4 /WX rejected the unused parameter (C4100), and the inner frame's callback got 0.0
        src = translate(True, loop=True)
        self.assertRegex(src, r"\bf1 = f1_\b")
        self.assertNotRegex(src, r"\bf1 = 0\.0")
        self.assertEqual(ppc_check.lint(src, META), [])

    def test_without_own_f1_write_unchanged(self):
        src = translate(False)
        self.assertNotIn("f1_", src)
        self.assertIn("tc_lastf()", src)


if __name__ == "__main__":
    unittest.main()

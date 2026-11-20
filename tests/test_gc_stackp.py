"""GameCube: a char or short parameter passed on the stack. Past r10 the EABI passes each argument in a whole word of
the caller's frame (r1+8, r1+12, ...), a char promoted to int; Ghidra gives the parameter its own byte's offset
(Skies of Arcadia 80250d34 param_9 at 11, 8026abd4 param_15 at 35), and the oracle refused any offset not a multiple
of 4 ("stack parameter param_9 at offset 11"), so six Skies functions had no verdict. Now TcBase accepts any offset
in the 8 words r1+8..r1+39 and the judge's thunk reads the whole word (rd32 of r1+8 for 11): the callee reads its
byte from the caller's frame itself, exactly as the original. The test checks the thunk's address for offsets 8, 11,
14, 35 (word-aligned reads only) and that TcBase's refusal no longer requires a multiple of 4 but keeps the range.
usage: python tests/test_gc_stackp.py
"""
import pathlib
import re
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
PPC = TOOL / "targets" / "gamecube_ppc"
sys.path[:0] = [str(TOOL), str(TOOL / "tests"), str(PPC), str(PPC / "translator")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
import ppc_check  # noqa: E402
import ppc_compare  # noqa: E402


def meta(off, kind="u32"):
    return {"name": "fn_80250d34", "signature": "void fn_80250d34(u32 param_9)", "ret": "void",
            "params": [{"name": "param_9", "kind": kind, "storage": "stack", "stack_offset": off}]}


class StackParam(unittest.TestCase):
    def test_thunk_reads_whole_word(self):
        for off, word in ((8, 8), (11, 8), (14, 12), (35, 32)):
            t = ppc_check.gen_thunk(meta(off))
            addrs = [int(x, 16) for x in re.findall(r"rd32\(0x([0-9a-f]{8})u\)", t)]
            self.assertEqual(addrs, [ppc_compare.SP + word], (off, t))

    def test_signed_and_float_kinds(self):
        self.assertIn(f"(s32)rd32(0x{ppc_compare.SP + 12:08x}u)", ppc_check.gen_thunk(meta(15, "s32")))
        self.assertIn(f"rdf32(0x{ppc_compare.SP + 16:08x}u)", ppc_check.gen_thunk(meta(16, "f32")))

    def test_oracle_accepts_unaligned_in_range(self):
        src = (PPC / "ghidra_scripts" / "TcBase.java").read_text()
        cond = re.search(r"if \(q\.stackOffset < 8[^{]*\{", src).group(0)
        self.assertNotIn("% 4", cond)
        self.assertIn("q.stackOffset >= 40", cond)


if __name__ == "__main__":
    unittest.main()

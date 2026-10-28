r"""The translator's dce pass keeps every store-queue store (PS2's Lib_RenderModelA 8c188420: `or #0x10,r0; mov r9,r2;
mov.l r0,@-r2` wrote the template word back through sq_wr32; r2 was dead after it, and SIDE's `\bwr\w*\(` does not
match inside `sq_wr32(`, so dce dropped the store and then the lines feeding it). Pass and fail controls:
1. with sq_window, `mov r4,r2; mov.l r5,@-r2` (r2 dead after it) keeps `sq_wr32(&sq, ...)` under every pass, and the
   simplified C writes the same store as the unsimplified C;
2. control: without sq_window the store is `wr32(...)` and is kept as before;
3. SIDE matches each statement the queue model emits (sq_wr8/16/32, sq_wrf32, sq_pref, sq_moved, sq_mark).
Synthetic data. Run: python -W error tests/test_sq_dce.py.
"""
import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
from sh4translate import translate as tx  # noqa: E402
from sh4translate.simplify import PASSES, SIDE  # noqa: E402

# mov r4,r2; mov.l r5,@-r2; rts; nop
WORDS = [0x6243, 0x2256, 0x000b, 0x0009]


def translate(sq_window, simplify=None):
    data = b"".join(struct.pack("<H", w) for w in WORDS)
    sig = tx.Signature("fn_8c000000", "void fn_8c000000(void)", [], "void", None, {}, sq_window=sq_window)
    src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, sig, tx.TargetFacts(), simplify=simplify)
    src = src[0] if isinstance(src, tuple) else src
    return [x.strip() for x in src[src.index("void fn_8c000000(void)"):].splitlines()]


class SqDce(unittest.TestCase):
    def test_store_kept(self):
        for passes in (None, ("dce",), tuple(PASSES)):
            body = translate("r4", passes)
            self.assertEqual(sum("sq_wr32(&sq, " in x for x in body), 1, (passes, body))
        plain = [x for x in translate("r4", ()) if "sq_wr32(" in x]
        self.assertEqual(plain, ["sq_wr32(&sq, r2, r5);"])

    def test_control_plain_store(self):
        body = translate(None, ("dce",))
        self.assertEqual(sum(x.startswith("wr32(") for x in body), 1, body)
        self.assertFalse(any("sq_" in x for x in body))

    def test_side_matches_queue_statements(self):
        for s in ("sq_wr8(&sq, r2, (u8)r0);", "sq_wr16(&sq, r2, (u16)r0);", "sq_wr32(&sq, r2, r0);",
                  "sq_wrf32(&sq, r2, fr4);", "sq_pref(&sq, r4);", "sq_moved(&sq, sq_p, sh4_r(14));",
                  "sq_mark(&sq, r4, 0x0ull, 0x0ull);"):
            self.assertTrue(SIDE.search(s), s)
        self.assertFalse(SIDE.search("r2 = r9;"))   # control: a plain copy stays droppable


if __name__ == "__main__":
    unittest.main()

"""context.md says when a callee's parameters are not where the convention puts them (core/readable_context)."""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import readable_abi, readable_check, readable_context  # noqa: E402


class Placement(unittest.TestCase):
    def note(self, cproto, where):
        with mock.patch.object(readable_abi, "callee_where", return_value=(where, [])), \
                mock.patch.object(readable_check, "callee_storage", return_value=None):
            return readable_context.placement_note(None, "8c000000", {"addr": "8c000100"}, cproto)

    def test_out_of_order_registers(self):
        note = self.note("void Matrix_BuildRowD(u32 pos, u32 out, u32 matrix)", ["r5", "r6", "r4"])
        self.assertEqual(len(note), 1)
        self.assertIn("pos: r5, out: r6, matrix: r4", note[0])

    def test_float_order_and_stack_word(self):
        self.assertIn("bits: fr5, x: fr4", self.note("u32 Lookup(u32 bits, f32 x)", ["fr5", "fr4"])[0])
        self.assertEqual(self.note("void F(s32 a, u32 b, u32 c, u32 d, u32 e)", ["r4", "r5", "r6", "r7", "stack+0"]), [])
        self.assertIn("flags: s0", self.note("void F(s32 t, u32 c, u32 flags)", ["r4", "r5", "stack+0"])[0])

    def test_default_order_says_nothing(self):
        self.assertEqual(self.note("void F(s32 a, f32 b, u32 c)", ["r4", "fr4", "r5"]), [])


if __name__ == "__main__":
    unittest.main()

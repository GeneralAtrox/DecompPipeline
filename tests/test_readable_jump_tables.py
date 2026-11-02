"""context.md shows a jump table's index -> arm mapping, read from the game's bytes (core/readable_context)."""
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import check, readable_context  # noqa: E402

TABLE = check.IMAGE_BASE + 0x100
RAW = f"""r1 = (u32)(s32)(s16)rd16(r5 + 0x{TABLE:08x}u);
sw_ = r1 + 0x{TABLE:08x}u;
switch (sw_) {{
case 0x{TABLE + 0x20:08x}u:
case 0x{TABLE + 0x40:08x}u:
}}"""


class JumpTables(unittest.TestCase):
    def image(self, words, fmt="<h"):
        img = bytearray(0x200)
        for i, w in enumerate(words):
            struct.pack_into(fmt, img, 0x100 + i * struct.calcsize(fmt), w)
        return bytes(img)

    def test_maps_index_to_arm(self):
        text = "\n".join(readable_context.jump_table_lines(self.image([0x20, 0x40, 0x20]), RAW))
        self.assertIn(f"0 -> case 0x{TABLE + 0x20:08x}, 1 -> case 0x{TABLE + 0x40:08x}, 2 -> case 0x{TABLE + 0x20:08x}", text)

    def test_stops_at_a_word_that_is_no_arm(self):
        text = "\n".join(readable_context.jump_table_lines(self.image([0x40, 0x9]), RAW))
        self.assertIn(f"0 -> case 0x{TABLE + 0x40:08x}", text)
        self.assertNotIn("1 ->", text)

    def test_byte_table_scale(self):
        raw = RAW.replace("(s16)rd16", "(s8)rd8")
        text = "\n".join(readable_context.jump_table_lines(self.image([0x10, 0x20], "<b"), raw))
        self.assertIn(f"0 -> case 0x{TABLE + 0x20:08x}, 1 -> case 0x{TABLE + 0x40:08x}", text)

    def test_no_switch_says_nothing(self):
        self.assertEqual(readable_context.jump_table_lines(bytes(0x200), "r0 = 1;"), [])


if __name__ == "__main__":
    unittest.main()

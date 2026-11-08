"""game.json part_draw (port/tools/part_draw.py): the key checked, and the PartDraw_ADDR wrapper gen_port adds, which
brackets the game's per-part 3D draw with port_part_begin and _end (pvr/pvr_parts.c). Synthetic; no game workspace.
Run: python -W error -m unittest tests.test_part_draw
"""
import pathlib
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "port" / "tools"))
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL))

import game_config  # noqa: E402
import gen_port  # noqa: E402
import part_draw  # noqa: E402
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import readable  # noqa: E402

E = game_config.ConfigError
PROTOS = {"8c02e07c": "void Fx_DrawPart(GameObj *obj, ModelPart *part);", "8c02e100": "u32 Fx_Count(GameObj *obj, s32 n);",
          "8c02e200": "s32 Fx_Pick(GameObj *obj, ModelPart *part, s32 n);"}


class PartDraw(unittest.TestCase):
    def test_config(self):
        self.assertIsNone(part_draw.config(None, {}, {}, None, E))
        self.assertEqual(part_draw.config({"function": "8C02E07C", "stamp": "0x24", "world": "0x28"}, {}, {}, None, E),
                         {"function": "8c02e07c", "stamp": 0x24, "world": 0x28})
        good = {"function": "8c02e07c", "stamp": "0x24", "world": "0x28"}
        for raw, wrap, wide in (({"function": "8c02e07c"}, {}, None), ({**good, "function": "8c02e0"}, {}, None),
                                ({**good, "world": "40"}, {}, None), ({**good, "stamp": 36}, {}, None),
                                (good, {"8c02e07c": "Fx_Wrap"}, None), (good, {}, {"functions": ["8c02e07c"]})):
            with self.subTest(raw=raw, wrap=wrap, wide=wide), self.assertRaises(E):
                part_draw.config(raw, wrap, {}, wide, E)

    def test_wrapper(self):
        self.assertIsNone(part_draw.c_text(readable, None, PROTOS, gen_port.wrapper_proto))
        text, wraps = part_draw.c_text(readable, {"function": "8c02e07c", "stamp": 0x24, "world": 0x28}, PROTOS,
                                       gen_port.wrapper_proto)
        self.assertEqual(wraps, {"8c02e07c": "PartDraw_8c02e07c"})
        self.assertIn("void PartDraw_8c02e07c(GameObj *obj, ModelPart *part)\n{\n"
                      "    port_part_begin(obj, part, 0x24u, 0x28u);\n    Fx_DrawPart(obj, part);\n"
                      "    port_part_end();\n}\n", text)
        text, _ = part_draw.c_text(readable, {"function": "8c02e200", "stamp": 0x24, "world": 0x28}, PROTOS,
                                   gen_port.wrapper_proto)
        self.assertIn("    s32 part_result = Fx_Pick(obj, part, n);\n    port_part_end();\n    return part_result;\n", text)
        for addr in ("8c02e100", "8c02e300"):  # the second argument is no pointer; no function there
            with self.subTest(addr=addr), self.assertRaises(SystemExit):
                part_draw.c_text(readable, {"function": addr, "stamp": 0, "world": 0}, PROTOS, gen_port.wrapper_proto)


if __name__ == "__main__":
    unittest.main()

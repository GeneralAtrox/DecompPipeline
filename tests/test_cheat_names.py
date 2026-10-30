"""tools/cheat_names.py: a libretro cheat list becomes naming hints: each code's guest address, the game functions
whose literal pools hold it (or a base just below it), the name the designers already gave it, and a region warning.
Run: python tests/test_cheat_names.py
"""
import json
import pathlib
import struct
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
import cheat_names  # noqa: E402

CHT = """cheat0_address = "243852"
cheat0_desc = "Extra Missiles"
cheat0_memory_search_size = "3"
cheat0_value = "1"
cheat1_address = "243860"
cheat1_desc = "Infinite Health"
cheat1_memory_search_size = "5"
cheat1_repeat_count = "4"
cheat1_repeat_add_to_address = "4"
cheat2_address = "999999"
cheat2_desc = "Unused"
cheat2_memory_search_size = "4"
cheat3_code = "02123456 00000063"
cheat3_desc = "Encoded"
cheats = "4"
"""


def workspace(d):
    """One 8-byte function at 0x8c010000: mov.l @(1,PC),r1; rts; nop; nop, its pool word 0x0c03b88c (a mirror of
    0x8c03b88c, cheat 0's address) right after it."""
    ws = d / "decomp"
    (ws / "state").mkdir(parents=True)
    (ws / "readable" / "names").mkdir(parents=True)
    code = struct.pack("<4H", 0xD101, 0x000B, 0x0009, 0x0009) + struct.pack("<I", 0x0C03B88C)
    (ws / "game.bin").write_bytes(code)
    (ws / "project.json").write_text(json.dumps({"binary": "game.bin", "game_end": "8c020000",
                                                 "disc": "x/Game (Europe) (En,Fr).cue"}), encoding="utf-8")
    (ws / "state" / "catalog.json").write_text(json.dumps({"functions": [{"addr": "8c010000", "bytes": 8}]}),
                                               encoding="utf-8")
    (ws / "state" / "code_ranges.txt").write_text("8c010000 8c010008\n", encoding="utf-8")
    (ws / "readable" / "names" / "S01.json").write_text(json.dumps(
        {"functions": {}, "globals": {"8c03b88c": {"decl": "Ammo g_ammo"}}}), encoding="utf-8")
    return ws


class CheatNames(unittest.TestCase):
    def test_parse(self):
        c = cheat_names.parse_cht(CHT)
        self.assertEqual([x["desc"] for x in c], ["Extra Missiles", "Infinite Health", "Unused", "Encoded"])
        self.assertEqual((c[0]["addr"], c[0]["bits"]), (0x8C03B88C, 8))
        self.assertEqual((c[1]["count"], c[1]["step"], c[1]["bits"]), (4, 4, 32))
        self.assertNotIn("addr", c[3])
        self.assertEqual(c[3]["code"], "02123456 00000063")

    def test_region(self):
        self.assertEqual(cheat_names.region_of("Toy Commander (USA) (En,Fr,De,Es).cht"), "USA")
        self.assertEqual(cheat_names.region_of("x/Game (Europe) (En,Fr).cue"), "Europe")
        self.assertIsNone(cheat_names.region_of("game.cht"))

    def test_hints(self):
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t)
            ws = workspace(d)
            (d / "Game (USA).cht").write_text(CHT, encoding="utf-8")
            self.assertEqual(cheat_names.main(["--project", str(ws), "--cht", str(d / "Game (USA).cht"),
                                               "--json", str(d / "h.json")]), 0)
            rows = json.loads((d / "h.json").read_text(encoding="utf-8"))
            self.assertEqual((rows[0]["used_by"], rows[0]["named"]), (["8c010000"], "Ammo g_ammo"))
            self.assertEqual((rows[1]["via_base"], rows[1]["named"]),
                             ([["8c010000", 0x8C03B88C, 8]], "Ammo g_ammo + 0x8"))
            self.assertEqual((rows[2]["used_by"], rows[2]["via_base"]), ([], []))
            md = (ws / "readable" / "cheat_hints.md").read_text(encoding="utf-8")
            self.assertIn("Region mismatch", md)
            self.assertIn("| Extra Missiles | 0x8c03b88c | 8 | Ammo g_ammo | 8c010000 | - |", md)
            self.assertIn("- Unused: 0x8c0f423f", md)
            self.assertIn("1 codes given only as encoded codes (not decoded): Encoded", md)
            # an address inside the program code is a code patch, named by the function holding it
            (d / "code.cht").write_text('cheat0_address = "65540"\ncheat0_desc = "Patch"\n', encoding="utf-8")
            cheat_names.main(["--project", str(ws), "--cht", str(d / "code.cht"), "--json", str(d / "c.json")])
            row = json.loads((d / "c.json").read_text(encoding="utf-8"))[0]
            self.assertEqual((row["in_code"], row["used_by"]), ("8c010000", []))
            # address 0: a flag bit's meaning for the code before it, never RAM 0x8c000000
            (d / "bits.cht").write_text('cheat0_address = "243852"\ncheat0_desc = "Flags"\ncheat1_address = "0"\n'
                                        'cheat1_desc = "0002 - Low Gravity"\ncheat1_value = "2"\n', encoding="utf-8")
            cheat_names.main(["--project", str(ws), "--cht", str(d / "bits.cht"), "--json", str(d / "b.json")])
            row = json.loads((d / "b.json").read_text(encoding="utf-8"))[1]
            self.assertEqual((row.get("addr"), row["of"]), (None, 0x8C03B88C))
            md = (ws / "readable" / "cheat_hints.md").read_text(encoding="utf-8")
            self.assertIn("- 0x8c03b88c: 0002 - Low Gravity (value 2)", md)


if __name__ == "__main__":
    unittest.main()

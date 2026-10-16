"""Batch 26: machine names for read-only strings, the effort and TASK note of large functions, and constants a
projection hid (core/rodata_strings, core/names_insert, core/agent_cli, core/lean_hidden, core/lean_index).

One pass and one fail control each:
1. a NUL-terminated printable run after a NUL is a string; a non-printable or unterminated run is not, nor a
   one-character run outside a string area;
2. an unnamed string address raw C passes is named; one inside a named global's extent is left alone;
3. names come from the content (g_fmt_ for a format), and a name in use gets a suffix;
4. a names file gets the new lines in its own layout; an existing entry is never overridden;
5. the owner system is the one with most users (a tie: the lowest user's);
6. a large raw.c gets readable_effort_large and the TASK note; a small one neither;
7. a dropped member's comment pulls in a matching group of at most 2 constants; a larger group is not pulled in;
8. constants named after the declarations' words come back (by_family); a group with too many such names does not;
9. index.txt lists the macros a group shown in part leaves out.
Synthetic data. Run: python -W error tests/test_batch26.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import (agent_cli, lean_hidden, lean_index, lean_select, names_insert, readable,  # noqa: E402
                  readable_headers, readable_sandbox, rodata_strings)

BASE = 0x8C010000


def image():
    img = bytearray(0x800)
    img[0x101:0x10b] = b"Kamui V%d\0"      # 0x8c010101: a format after a NUL
    img[0x200:0x206] = b"Named\0"          # 0x8c010200: a named global's string
    img[0x301:0x304] = b"\x01\x02\0"       # 0x8c010301: not printable
    img[0x601:0x601 + 300] = b"A" * 300    # 0x8c010601: no NUL within MAX_LEN
    img[0x501:0x505] = b"*.*\0"            # 0x8c010501: a string, then a one-character one at 0x8c010505
    img[0x505:0x507] = b"*\0"
    img[0x581:0x583] = b"x\0"              # 0x8c010581: one character after zeros only: not a string area
    return bytes(img)


RAW = """u32 fn_8c000000(void)
{
    args.r[0] = 0x8c010101u;
    args.r[1] = 0x8c010200u;
    args.r[2] = 0x8c010301u;
    args.r[3] = 0x8c010601u;
    args.s[0] = 0x8c010505u;
    args.s[1] = 0x8c010581u;
    return tc_call(0x8c010000u, &args);
}
"""


class Strings(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.bin = self.dir / "image.bin"
        self.bin.write_bytes(image())
        (self.dir / "8c000000.c").write_text(RAW, encoding="utf-8")
        self.cfg = {"binary_path": self.bin}
        self.reg = readable.Registry.empty({"globals": {"8c010200": {"decl": "const char g_named[6]"}}})

    def test_string_at(self):
        img = image()
        self.assertEqual(rodata_strings.string_at(img, 0x8C010101), "Kamui V%d")
        self.assertIsNone(rodata_strings.string_at(img, 0x8C010102))  # control: inside a string, not after a NUL
        self.assertIsNone(rodata_strings.string_at(img, 0x8C010301))  # control: not printable
        self.assertIsNone(rodata_strings.string_at(img, 0x8C010601))  # control: unterminated
        self.assertEqual(rodata_strings.string_at(img, 0x8C010505), "*")  # one character among strings
        self.assertIsNone(rodata_strings.string_at(img, 0x8C010581))  # control: one character alone

    def test_scan_leaves_named_alone(self):
        prop, rep = rodata_strings.scan(self.cfg, self.reg, [(0x8C010200, 6)], ["8c000000"], self.dir, [], None,
                                        {"g_fmt_KamuiV"})
        self.assertEqual({v: n for v, (n, _) in prop.items()},
                         {0x8C010101: "g_fmt_KamuiV_2", 0x8C010505: "g_str_8c010505"})
        self.assertNotIn(0x8C010200, prop)  # control: already named (inside g_named's extent)
        self.assertEqual(rep["skipped"], {"already named": 1})
        self.assertEqual(rodata_strings.entry("g_fmt_KamuiV_2", "Kamui V%d")["decl"],
                         "const char g_fmt_KamuiV_2[10]")

    def test_names(self):
        self.assertEqual(rodata_strings.base_name("Error: NOT_ENOUGH_MEM (%s)", 1), "g_fmt_ErrorNotEnoughMem")
        self.assertEqual(rodata_strings.base_name("Init Movie Sound Failed", 1), "g_str_InitMovieSoundFailed")
        self.assertEqual(rodata_strings.base_name("100%", 0x8C0A0000), "g_str_100")  # control: `%` alone: no format
        self.assertEqual(rodata_strings.base_name("%s (%d)", 1), "g_fmt_SD")

    def test_names_file_layout(self):
        text = '{\n "functions": {\n  "8c000000": {"proto": "void F(void)"}\n },\n "globals": {}\n}'
        new = names_insert.insert(text, "globals", {"8c0a2710": {"decl": "const char g_x[3]"}})
        self.assertIn('\n  "8c0a2710": {"decl": "const char g_x[3]"}\n }', new)
        self.assertTrue(new.startswith(text[:text.index('"globals"')]))  # the designer's lines unchanged
        crlf = text.replace("\n", "\r\n").replace(' "globals": {}\r\n', "")[:-1].rstrip().rstrip(",") + "\r\n}\r\n"
        crlf = crlf.replace("},\r\n\r\n}", "}\r\n}")
        added = names_insert.insert(crlf, "globals", {"8c0a2710": {"decl": "const char g_x[3]"}})
        self.assertEqual(json.loads(added)["globals"], {"8c0a2710": {"decl": "const char g_x[3]"}})
        self.assertNotIn("\n\n", added.replace("\r\n", "\n"))
        with self.assertRaises(ValueError):  # control: an existing entry is never overridden
            names_insert.insert(new, "globals", {"8c0a2710": {"decl": "const char g_y[3]"}})

    def test_write_never_overrides(self):
        names = self.dir / "names"
        names.mkdir()
        (names / "S01.json").write_text('{"functions": {}, "globals": {"8c0a2710": {"decl": "const char g_a[3]"}}}',
                                        encoding="utf-8")
        with mock.patch.object(readable, "NAMES", names), mock.patch.object(readable, "INCLUDE", self.dir / "none"):
            added = rodata_strings.write_entries(names / "S02.json", {
                "8c0a2710": rodata_strings.entry("g_fmt_X", "X%d"), "8c0a2720": rodata_strings.entry("g_str_Y", "Y")})
            self.assertEqual(added, ["8c0a2720"])  # control: 8c0a2710 is S01's: left out
            self.assertEqual(readable.Registry().globals["8c0a2710"]["decl"], "const char g_a[3]")
            self.assertEqual(readable.Registry().where["8c0a2720"], "S02.json")

    def test_owner(self):
        sys_of = {"8c000010": "S02", "8c000020": "S01", "8c000030": "S01", "8c000040": "S03"}
        self.assertEqual(rodata_strings.owner(["8c000010", "8c000020", "8c000030"], sys_of), "S01")  # most users
        self.assertEqual(rodata_strings.owner(["8c000040", "8c000010"], sys_of), "S02")  # a tie: the lowest user
        self.assertIsNone(rodata_strings.owner(["8c000050"], sys_of))  # control: no system


class Large(unittest.TestCase):
    def test_effort(self):
        cfg = {"readable_effort": "high", "readable_effort_large": "medium", "readable_large_bytes": 4096}
        self.assertEqual(agent_cli.readable_effort(cfg, None, 5000), "medium")
        self.assertEqual(agent_cli.readable_effort(cfg, None, 4096), "high")  # control: not over the threshold
        self.assertEqual(agent_cli.readable_effort(cfg, "max", 5000), "max")  # --effort wins
        self.assertEqual(agent_cli.readable_effort(dict(cfg, readable_large_bytes=0), None, 9999), "high")

    def test_task_note(self):
        self.assertIn("## A long function", readable_sandbox.LARGE_NOTE)
        self.assertTrue(agent_cli.is_large({}, 5000))
        self.assertFalse(agent_cli.is_large({}, 300))  # control: a small function gets no note


HEADER = """#ifndef T_H
#define T_H

#include "rd_rt.h"

#define SHOP_ITEM_COUNT 9
#define SHOP_ITEM_PRICE 3
#define SHOP_OPEN 1

#define ARMOR_LEVEL_MIN 1
#define ARMOR_LEVEL_MAX 5

#define TRIM_A 0x31
#define TRIM_B 0x32
#define TRIM_C 0x33

typedef struct Big2 {
    u32 flags;        /* 0x00 */
    u32 armor;        /* 0x04 */
    u32 level;        /* 0x08: ARMOR_LEVEL_MIN..ARMOR_LEVEL_MAX */
    u32 trim;         /* 0x0c: TRIM_A, TRIM_B or TRIM_C */
    u32 trim_kind;    /* 0x10 */
    u32 w5;           /* 0x14 */
    u32 w6;           /* 0x18 */
    u32 w7;           /* 0x1c */
    u32 w8;           /* 0x20 */
} Big2;

#endif
"""
DECL = "void Big2_Tick(Big2 *b);"


class Hidden(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(HEADER, encoding="utf-8")
        self.hs = readable_headers.Headers(d).finish()

    def chosen(self, evidence, decl=DECL, group_max=2):
        v, items = lean_select.select(self.hs, decl, (), evidence, (), (), (), group_max)
        return v, {n for it in items for n in it.names}

    def test_dropped_comment(self):
        v, names = self.chosen({0x10})  # level and trim are dropped; armor (0x04) is not reached either
        self.assertNotIn("ARMOR_LEVEL_MAX", names)  # no shown member shares a word, no value in raw.c
        v, names = self.chosen({0x04, 0x10})  # armor shown: ARMOR_LEVEL_* (2 names) share its word
        self.assertIn("ARMOR_LEVEL_MAX", names)
        self.assertIn("u8 _pad_0x8", v.shown(self.hs.by_name["Big2"]))  # level itself stays dropped
        self.assertNotIn("TRIM_A", names)  # control: TRIM_* shares trim_kind's word, but has 3 names (cap 2)
        self.assertIn("TRIM_A", self.chosen({0x04, 0x10}, group_max=3)[1])  # with a cap of 3 it comes in
        self.assertNotIn("ARMOR_LEVEL_MAX", self.chosen({0x04, 0x10}, group_max=0)[1])  # off

    def test_family(self):
        decl = "void Shop_SetItemPrice(Big2 *b, s32 item);"
        v, names = self.chosen({0x10}, decl)
        shown = v.shown(self.hs.by_name["SHOP_OPEN"])
        self.assertIn("SHOP_ITEM_PRICE", shown)  # shop, item, price: all the declaration's words
        self.assertIn("SHOP_ITEM_COUNT", shown)  # shop, item (count is generic)
        self.assertNotIn("SHOP_OPEN", shown)  # control: open is not a word of the declaration
        many = self.chosen({0x10}, "void Shop_Item(Big2 *b, s32 price, s32 open);")[1]
        self.assertNotIn("SHOP_ITEM_PRICE", many)  # control: 3 names of the group match, the cap is 2

    def test_index_lists_left_out_macros(self):
        decl = "void Shop_SetItemPrice(Big2 *b, s32 item);"
        v, items = lean_select.select(self.hs, decl, (), {0x10}, (), (), (), 2)
        group = self.hs.by_name["SHOP_ITEM_PRICE"]
        self.assertEqual(v.lines.get(group), {"SHOP_ITEM_PRICE", "SHOP_ITEM_COUNT"})
        self.assertIn("1 more macro(s)", v.shown(group))
        index = lean_index.index_txt(self.hs, items, None, v.lines)
        self.assertIn("SHOP_OPEN = 1", index)  # left out of the part: listed
        self.assertNotIn("SHOP_ITEM_PRICE =", index)  # shown: not listed
        self.assertNotIn("SHOP_OPEN", lean_index.index_txt(self.hs, items, None))  # control: batch 25's index
        self.assertEqual(lean_hidden.words("SHOP_ITEM_COUNT"), {"shop", "item"})


if __name__ == "__main__":
    unittest.main()

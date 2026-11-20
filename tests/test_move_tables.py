"""port/tools/move_tables.py (game.json move_tables): literals inside a moved table follow it in every P0/P1/P2 form,
the end value moves only in the listed functions or at a listed instruction (and before the next moved table's start),
comments keep their text, a counted table's listed sites and ends follow the run-time count,
a listed end that is missing stops the build, and overlapping tables are refused.

usage: python -W error tests/test_move_tables.py
"""
import pathlib
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "port" / "tools"))

import move_tables  # noqa: E402


class Err(Exception):
    pass


MOVES = move_tables.config([
    {"name": "cams", "from": "8c5412fc", "bytes": "be0", "to": "88000000", "ends": ["8c052c38"]},
    {"name": "slots", "from": "8c541edc", "bytes": "260", "to": "88001000", "ends": ["8c045240@8c045270"]},
    {"name": "fighters", "from": "8c532698", "bytes": "e4e0", "to": "88010000", "ends": ["8c067808"]},
], Err)


class MoveTables(unittest.TestCase):
    def test_forms_and_offsets(self):
        text = "r3=macl+0x0c532698u; r4=0x8c532ad0u; r5=0xac540b77u; r6=0x0c540b78u; /* 0x0c532698u */"
        new, ends = move_tables.rewrite_text(text, MOVES, "8c000000")
        self.assertEqual(new, "r3=macl+0x08010000u; r4=0x88010438u; r5=0xa801e4dfu; r6=0x0c540b78u; /* 0x0c532698u */")
        self.assertEqual(ends, {})

    def test_listed_end(self):
        new, ends = move_tables.rewrite_text("r7=0x0c540b78u;", MOVES, "8c067808")
        self.assertEqual((new, ends), ("r7=0x0801e4e0u;", {("fighters", "8c067808"): 1}))

    def test_listed_instruction(self):
        """FUNC@INSN: only the literal after that instruction's comment is the end; the same literal as the next
        object's base (g_flow, right after the player slots) stays, before and after it."""
        text = ("/* 8c045242  mov.l @(0x8c0452d8),r14 */ r14 = 0x0c54213cu; /* 8c04526c  mov.w */ /* 8c045270  add r5,r6 */"
                " r6 = 0x0c54213cu; r4 = 0x0c541edcu; /* 8c045272  mov r5,r4 */ wr8(0x0c542143u, 1); r0 = 0x0c54213cu;")
        new, ends = move_tables.rewrite_text(text, MOVES, "8c045240")
        self.assertEqual(new, text.replace("r6 = 0x0c54213cu", "r6 = 0x08001260u").replace("0x0c541edcu", "0x08001000u"))
        self.assertEqual(ends, {("slots", "8c045240@8c045270"): 1})
        self.assertEqual(move_tables.rewrite_text(text, MOVES, "8c000000")[1], {})
        with self.assertRaises(Err):
            move_tables.config([{"from": "8c000000", "bytes": 16, "to": "88000000", "ends": ["8c045240@45270"]}], Err)

    def test_end_before_next_table(self):
        """The cameras' end is the player slots' start: in a listed camera function it is the cameras' end."""
        self.assertEqual(move_tables.rewrite_text("r1=0x0c541edcu;", MOVES, "8c052c38")[0], "r1=0x08000be0u;")
        self.assertEqual(move_tables.rewrite_text("r1=0x0c541edcu;", MOVES, "8c000000")[0], "r1=0x08001000u;")

    def test_globals(self):
        self.assertEqual(move_tables.global_address("8c541edc", MOVES), "88001000")
        self.assertEqual(move_tables.global_address("8c540b78", MOVES), "8c540b78")

    def test_apply_and_missing_end(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            judged = d / "8c067808.c"
            judged.write_text("do{r5+=0x3938u;}while(r5<0x0c540b78u); r2=0x0c532698u;", encoding="utf-8")
            other = d / "8c052c38.c"
            other.write_text("r1=0x0c541edcu;", encoding="utf-8")
            texts = {a: f'#include "tx_rt.h"' + chr(10) + f'#include "{p.as_posix()}"' for a, p in (("8c067808", judged), ("8c052c38", other))}
            moves = [dict(m, ends=[e for e in m["ends"] if "@" not in e]) for m in MOVES]
            n, ends = move_tables.apply(texts, d / "out", lambda p, t: p.write_text(t, encoding="utf-8"), moves)
            self.assertEqual((n, ends), (2, 2))
            self.assertIn("tx_move/8c067808.c", texts["8c067808"])
            self.assertIn("0x0801e4e0u", (d / "out" / "tx_move" / "8c067808.c").read_text(encoding="utf-8"))
            self.assertIn("0x0c540b78u", judged.read_text(encoding="utf-8"))  # the judged file is untouched
            texts = {"8c067808": f'#include "{other.as_posix()}"'}
            with self.assertRaises(SystemExit):
                move_tables.apply(texts, d / "out2", lambda p, t: None, moves)

    def test_counts(self):
        """A counted table: at a listed instruction the original count (0x4u, in the mov and in a compare it was folded
        into) becomes the run-time count, elsewhere it stays; a listed end becomes to + stride * count."""
        moves = move_tables.config([{"name": "f", "from": "8c532698", "bytes": "e4e0", "to": "88000000", "stride": "3938",
                                     "max": 8, "ends": ["8c067808"], "counts": ["8c02e926@8c02e926", "8c02e926@8c02e958"]}], Err)
        cnt = "(g_move_count[0] ? g_move_count[0] : 4u)"
        text = ("/* 8c02e926  mov #4,r11 */ r11 = 0x4u; /* 8c02e928  mov #4,r10 */ r10 = 0x4u; r9 = 0x48u;"
                " /* 8c02e958  cmp/ge r11,r3 */ t = (s32)r3 >= (s32)0x4u; x = 0x0c532698u;")
        new, ends = move_tables.rewrite_text(text, moves, "8c02e926")
        self.assertEqual(new, f"/* 8c02e926  mov #4,r11 */ r11 = {cnt}; /* 8c02e928  mov #4,r10 */ r10 = 0x4u; r9 = 0x48u;"
                              f" /* 8c02e958  cmp/ge r11,r3 */ t = (s32)r3 >= (s32){cnt}; x = 0x08000000u;")
        self.assertEqual(ends, {("f", "8c02e926@8c02e926"): 1, ("f", "8c02e926@8c02e958"): 1})
        self.assertEqual(move_tables.rewrite_text("r7=0x8c540b78u;", moves, "8c067808")[0],
                         f"r7=(0x88000000u + 0x3938u * {cnt});")
        self.assertEqual(move_tables.rewrite_text(text, MOVES, "8c02e926")[0], text.replace("0x0c532698u", "0x08010000u"))
        slot = ("/* 8c0cf9ec  bt/s 0x8c0cfa10; 8c0cf9ee  mov #4,r10 */ r10 = 0x4u; /* 8c0cf9f0  mov.w @(0x8c0cfa40),r1 */"
                " r1 = 0xe4e0u; /* 8c0cf9f2  mov #4,r2 */ r2 = 0x4u;")
        m4 = move_tables.config([{"name": "f", "from": "8c532698", "bytes": "e4e0", "to": "88000000", "stride": "3938",
                                  "max": 8, "counts": ["8c0cf9c8@8c0cf9ee", "8c0cf9c8@8c0cf9f0"]}], Err)
        new, ends = move_tables.rewrite_text(slot, m4, "8c0cf9c8")  # a delay slot's address; a size folded into an end
        self.assertEqual(new, slot.replace("r10 = 0x4u", f"r10 = {cnt}").replace("0xe4e0u", f"(0x3938u * {cnt})"))
        self.assertEqual(ends, {("f", "8c0cf9c8@8c0cf9ee"): 1, ("f", "8c0cf9c8@8c0cf9f0"): 1})
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            j = d / "8c02e926.c"
            j.write_text(text, encoding="utf-8")
            texts = {"8c02e926": f'#include "{j.as_posix()}"', "8c067808": f'#include "{j.as_posix()}"'}
            with self.assertRaises(SystemExit):  # 8c067808's listed end is not in its text
                move_tables.apply(dict(texts), d / "o", lambda p, t: p.write_text(t, encoding="utf-8"), moves)
            m2 = [dict(moves[0], ends=[])]
            n, found = move_tables.apply(texts, d / "o", lambda p, t: p.write_text(t, encoding="utf-8"), m2)
            self.assertEqual(found, 2)
            self.assertTrue((d / "o" / "tx_move" / "8c02e926.c").read_text(encoding="utf-8")
                            .startswith("extern unsigned int g_move_count[32];"))
            m3 = [dict(moves[0], ends=[], counts=moves[0]["counts"] + ["8c02e926@8c02e930"])]
            with self.assertRaises(SystemExit):  # a listed count site with no count in its code
                move_tables.apply({"8c02e926": f'#include "{j.as_posix()}"'}, d / "o3", lambda p, t: None, m3)
        bad = [{"from": "8c000000", "bytes": "10", "to": "88000000", "stride": "3"},
               {"from": "8c000000", "bytes": "10", "to": "88000000", "counts": ["8c000000@8c000002"]},
               {"from": "8c000000", "bytes": "10", "to": "88000000", "stride": "4", "counts": ["8c000000"]},
               {"from": "8c000000", "bytes": "10", "to": "88000000", "stride": "4", "max": 2}]
        for b in bad:
            with self.assertRaises(Err):
                move_tables.config([b], Err)
        with self.assertRaises(Err):  # room for max entries runs into the next table's new place
            move_tables.config([{"from": "8c000000", "bytes": "10", "to": "88000000", "stride": "4", "max": 8},
                                {"from": "8c001000", "bytes": "10", "to": "88000018"}], Err)

    def test_overlap_refused(self):
        with self.assertRaises(Err):
            move_tables.config([{"from": "8c000000", "bytes": 16, "to": "88000000"},
                                {"from": "8c000008", "bytes": 16, "to": "88001000"}], Err)
        with self.assertRaises(Err):
            move_tables.config([{"from": "8c000000", "bytes": 16, "to": "88000000"},
                                {"from": "8c001000", "bytes": 16, "to": "88000008"}], Err)


if __name__ == "__main__":
    unittest.main()

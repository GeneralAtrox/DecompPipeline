"""port/tools/tx_edits.py (game.json tx_edits): an edit changes only the code after its instruction comment, a find
that is missing or there twice stops the build, the judged file is untouched, and move_tables then moves the edit's
literals.

usage: python -W error tests/test_tx_edits.py
"""
import json
import pathlib
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "port" / "tools"))

import move_tables  # noqa: E402
import tx_edits  # noqa: E402

TEXT = ("/* 8c000010  mov #4,r1 */ r1 = 0x4u; /* 8c000012  mov #4,r2 */ r2 = 0x4u; "
        "/* 8c000014  bt/s 0x8c000020; 8c000016  and #3,r0 */ r0 &= 0x3u; r3 = 0x0c532698u;")


class Err(Exception):
    pass


def spec(d, edits, prelude=()):
    (d / "e.json").write_text(json.dumps({"prelude": list(prelude), "edits": edits}), encoding="utf-8")
    return tx_edits.config("e.json", d, Err)


def write(p, t):
    p.write_text(t, encoding="utf-8")


class TxEdits(unittest.TestCase):
    def test_edit_at_instruction_and_chain(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            judged = d / "8c000010.c"
            judged.write_text(TEXT, encoding="utf-8")
            texts = {"8c000010": '#include "tx_rt.h"' + chr(10) + f'#include "{judged.as_posix()}"'}
            s = spec(d, [{"func": "8c000010", "insn": "8c000012", "find": "0x4u", "replace": "N"},
                         {"func": "8c000010", "insn": "8c000016", "find": "r0 &= 0x3u;",
                          "replace": "r0 &= N - 1u; r4 = 0x0c532698u;"}], ["#define N 8u"])
            self.assertEqual(tx_edits.apply(texts, d / "out", write, s), (1, 2))
            new = (d / "out" / "tx_edit" / "8c000010.c").read_text(encoding="utf-8")
            self.assertTrue(new.startswith("#define N 8u" + chr(10)))
            self.assertIn("r1 = 0x4u; /* 8c000012  mov #4,r2 */ r2 = N;", new)
            self.assertIn("and #3,r0 */ r0 &= N - 1u; r4 = 0x0c532698u; r3 = 0x0c532698u;", new)
            self.assertEqual(judged.read_text(encoding="utf-8"), TEXT)
            moves = move_tables.config([{"from": "8c532698", "bytes": "e4e0", "to": "88000000"}], Err)
            move_tables.apply(texts, d / "out", write, moves)
            self.assertIn("tx_move/8c000010.c", texts["8c000010"])
            self.assertIn("r4 = 0x08000000u; r3 = 0x08000000u;",
                          (d / "out" / "tx_move" / "8c000010.c").read_text(encoding="utf-8"))

    def test_missing_or_twice_stops(self):
        for insn, find in (("8c000010", "0x5u"), ("8c000014", "0x4u"), ("8c000099", "0x4u")):
            with self.assertRaises(SystemExit):
                tx_edits.rewrite_text(TEXT, [{"insn": insn, "find": find, "replace": "x"}], "8c000010")
        with self.assertRaises(SystemExit):
            tx_edits.rewrite_text(TEXT + " /* 8c000012  again */ r5 = 0x4u;",
                                  [{"insn": "8c000012", "find": "0x4u", "replace": "x"}], "f")

    def test_no_instruction(self):
        """An empty insn: anywhere in the code, still exactly once (0x4u is there twice, 0x3u once)."""
        self.assertIn("r0 &= 7u;", tx_edits.rewrite_text(TEXT, [{"insn": "", "find": "r0 &= 0x3u;", "replace": "r0 &= 7u;"}], "f"))
        with self.assertRaises(SystemExit):
            tx_edits.rewrite_text(TEXT, [{"insn": "", "find": "0x4u", "replace": "x"}], "f")

    def test_config_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            self.assertIsNone(tx_edits.config(None, d, Err))
            for bad in ([{"func": "8c00001", "insn": "8c000010", "find": "a", "replace": "b"}],
                        [{"func": "8c000010", "insn": "8c000010", "find": "", "replace": "b"}],
                        [{"func": "8c000010", "insn": "8c000010", "find": "a", "replace": "b /* c */"}]):
                with self.assertRaises(Err):
                    spec(d, bad)
            with self.assertRaises(Err):
                tx_edits.config("none.json", d, Err)
            with self.assertRaises(SystemExit):
                tx_edits.apply({}, d, None, spec(d, [{"func": "8c000010", "insn": "8c000010", "find": "a", "replace": "b"}]))


if __name__ == "__main__":
    unittest.main()

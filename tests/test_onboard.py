"""Onboarding a new game (docs/NEW_GAME.md): the disc scan's code score and link form, and the whole-program Ghidra
time-out. No Ghidra, no disc: synthetic bytes."""
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
_TMP = tempfile.TemporaryDirectory(prefix="onboard_", dir=TOOL / "tests" / ".work")
(pathlib.Path(_TMP.name) / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "ghidra_name": "fixture",
                                                                  "binary": "game.bin"}), encoding="utf-8")
os.environ.setdefault("DECOMP_PROJECT", _TMP.name)  # core.paths resolves a project at import
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "targets" / "dreamcast_sh4" / "tools"))
import gdrom  # noqa: E402
from core import ghidra  # noqa: E402

BASE = 0x8C010000


def tearDownModule():
    _TMP.cleanup()


def words(*ws):
    return struct.pack(f"<{len(ws)}H", *ws)


class Scan(unittest.TestCase):
    def test_code_scores_above_data(self):
        fn = words(0x4F22, 0x2FE6, 0xE000, 0x0009, 0x4F26, 0x000B, 0x6EF6, 0x0009)  # sts.l pr / lds.l pr / rts
        code = fn * 64
        data = bytes(range(256)) * 4 + words(0x000B) * 32  # stray rts words do not make data code
        self.assertGreater(gdrom.code_score(code)[0], gdrom.CODE_PER_KB)
        self.assertLess(gdrom.code_score(data)[0], gdrom.CODE_PER_KB)

    def test_link_form(self):
        p0 = struct.pack("<8I", *[BASE - 0x80000000 + 4 * i for i in range(8)])  # 0x0c... pointers into the file
        p1 = struct.pack("<8I", *[BASE + 4 * i for i in range(8)])
        self.assertEqual(gdrom.code_score(p0)[1:3], (0, 8))
        self.assertEqual(gdrom.code_score(p1)[1:3], (8, 0))

    def test_version_strings(self):
        blob = b"\x00\x00gdFs Ver 1.08 Build:Apr 26 1999\x00\xffNAOMI LIBRARY Ver 0.9 AM R&D\x00junk\x00"
        self.assertEqual(gdrom.code_score(blob)[3], ["NAOMI LIBRARY Ver 0.9 AM R&D", "gdFs Ver 1.08 Build:Apr 26 1999"])


class Relocate(unittest.TestCase):
    def test_both_mirror_forms(self):
        """xproject stage: a literal keeps its form (0x0c... P0 or 0x8c...); a pair recorded in either form maps it."""
        import xproject
        text, missing = xproject.relocate(
            "wr32(0x0c011ac0u, rd32(0x8c011ac0)); fn_8c000010(); s = 0x8cff0000; y = 0x0c123456; z = 0x8c555555;",
            {"0c011ac0": "0c022bd0", "8c123456": "8c999999"}, "8c000010", "8c000020")
        self.assertEqual(text, "wr32(0x0c022bd0u, rd32(0x8c022bd0)); fn_8c000020(); s = 0x8cff0000; y = 0x0c999999; "
                               "z = 0x8c555555;")
        self.assertEqual(missing, ["8c555555"])

    def test_own_form_pair_gives_the_new_images_word(self):
        """TC's pool word 0x8c0dd7dc is Spawn's 0x0c0dd7dc: the literal takes the value the new code loads."""
        import xproject
        text, missing = xproject.relocate("wr32(p, 0x8c0dd7dcu); q = 0x0c0dd7dc;", {"8c0dd7dc": "0c0dd7dc"},
                                          "8c000010", "8c000020")
        self.assertEqual(text, "wr32(p, 0x0c0dd7dcu); q = 0x0c0dd7dc;")
        self.assertEqual(missing, [])

    def test_match_pairs_win_over_a_dropped_global_pair(self):
        """Two thunks `bra X; nop` both match one earlier thunk, so the global map drops its target; each match's
        own pair still relocates it."""
        import xproject
        m = {"from": "8c011e20", "kind": "exact", "ratio": 1.0, "pairs": {"8c011e24": "8c0167cc"}}
        text, missing = xproject.relocate_match("tc_call(0x8c011e24u, &args); fn_8c011e20();", {}, m, "8c01678a")
        self.assertEqual(text, "tc_call(0x8c0167ccu, &args); fn_8c01678a();")
        self.assertEqual(missing, [])


class WholeTimeout(unittest.TestCase):
    def test_setup_is_not_bounded_by_the_prepare_timeout(self):
        self.assertEqual(ghidra.whole_timeout({"ghidra_timeout_s": 900}), 3600)
        self.assertEqual(ghidra.whole_timeout({"ghidra_timeout_s": 5000}), 5000)
        self.assertEqual(ghidra.whole_timeout({"ghidra_timeout_s": 900, "ghidra_setup_timeout_s": 7200}), 7200)


if __name__ == "__main__":
    unittest.main(warnings="error")

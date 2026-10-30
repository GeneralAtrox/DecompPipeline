"""core/mid_entries.py: second entry points folded into another function's body (r15; TC 8c05f40c).

A synthetic game: F1 (8c010000) branches to its second body range 8c010100, which starts with its own prologue;
F2 (8c010040) calls into that range and stores a pointer to it; a table of functions holds it. Guards for the false
flags found on TC, Spawn and Power Stone 2: a function's own long jump through its literal pool, a literal pool that
looks like a table, a switch table of one function's labels, and a branch to a block the branching function's packet
lists although the body file gives it to another function.
Run: python -W error tests/test_mid_entries.py
"""
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core import mid_entries as me, sh4_asm  # noqa: E402

BASE = 0x8C010000
F1, F2, F3 = 0x8C010000, 0x8C010040, 0x8C010080
BODIES = "8c010000 8c010000 8c010010\n8c010000 8c010100 8c010140\n8c010040 8c010040 8c010080\n" \
         "8c010080 8c010080 8c0100c0\n"
LISTINGS = {
    F1: """
  8c010000  mov.l r4,@-r15
  8c010002  bra 0x8c010100
  8c010004  _nop
  8c010100  mov.l r14,@-r15
  8c010102  mov.l 0x8c010288,r0                  ; = 0x8c01010c  (code)
  8c010104  jmp @r0
  8c010106  _nop
  8c01010c  rts
  8c01010e  _nop
""",
    F2: """
  8c010040  bsr 0x8c010104                       ; call 8c010104
  8c010042  _nop
  8c010044  mov.l 0x8c010070,r2                  ; = 0x8c010108  (global x)
  8c010046  mov.l r2,@r4
""",
    F3: """
  8c010080  bra 0x8c010130
  8c010082  _nop
  8c010130  rts
  8c010132  _nop
""",
}


def image(table=True):
    img = bytearray(0x400)

    def put(at, *words):
        struct.pack_into(f"<{len(words)}I", img, at - BASE, *words)
    struct.pack_into("<H", img, 0x100, 0x2FE6)  # 8c010100: mov.l r14,@-r15
    if table:  # a table of functions, one entry in P2 form
        put(0x8C010200, F1, F2, 0xAC010100, F3)
    put(0x8C010280, F1, F2, 0x8C01010C, F3)  # 8c010288 is F1's literal-pool word: not a table
    put(0x8C010300, 0x8C010044, 0x8C010048, 0x8C01004C, 0x8C010050)  # a switch table inside F2
    return bytes(img)


def setup(table=True):
    listings = {k: sh4_asm.parse(v) for k, v in LISTINGS.items()}
    return listings, me.Bodies(BODIES, [F1, F2, F3]), image(table)


class MidEntries(unittest.TestCase):
    def test_rules(self):
        listings, bodies, img = setup()
        hits = me.find(listings, bodies, img, BASE)
        self.assertEqual(sorted(hits), [0x8C010100, 0x8C010104, 0x8C010108])
        self.assertEqual(hits[0x8C010104], ["call from 8c010040 at 8c010040"])
        self.assertEqual(hits[0x8C010108], ["pool word loaded by 8c010040 at 8c010044"])
        self.assertEqual(hits[0x8C010100], ["body range of 8c010000 with its own prologue, branched to at 8c010002",
                                            "table word 8c010208 (a table of 4 code addresses at 8c010200)"])
        self.assertTrue(all(me.needs_override(ev) for ev in hits.values()))

    def test_merged_body_alone_needs_nothing(self):
        listings, bodies, img = setup(table=False)
        hits = me.find(listings, bodies, img, BASE)
        self.assertFalse(me.needs_override(hits[0x8C010100]))
        self.assertTrue(me.needs_override(hits[0x8C010104]))

    def test_decided_left_out(self):
        listings, bodies, img = setup()
        self.assertNotIn(0x8C010104, me.find(listings, bodies, img, BASE, decided={"8c010104"}))

    def test_fragments(self):
        """A switch arm the catalog split off (PS2 8c0cab42): it calls, then pops the frame of the function before
        it. A function that saves PR is no fragment, nor is one that never touches it; decided ones are left out."""
        f4, f5 = 0x8C0100C0, 0x8C0100D0
        listings = {k: sh4_asm.parse(v) for k, v in {F3: LISTINGS[F3], F2: LISTINGS[F2], f4: """
  8c0100c0  mov.l 0x8c0100f0,r3                  ; = 0x0c010040  (function 8c010040)
  8c0100c2  jsr @r3                              ; call 8c010040
  8c0100c4  _mov r14,r4
  8c0100c6  lds.l @r15+,PR
  8c0100c8  rts
  8c0100ca  _mov.l @r15+,r14
""", f5: """
  8c0100d0  sts.l PR,@-r15
  8c0100d2  lds.l @r15+,PR
  8c0100d4  rts
  8c0100d6  _nop
"""}.items()}
        bodies = me.Bodies(BODIES + "8c0100c0 8c0100c0 8c0100cc\n8c0100d0 8c0100d0 8c0100d8\n", [F1, F2, F3, f4, f5])
        self.assertEqual(me.fragments(listings, bodies), {f4: ["restores PR it never saved; 8c010080's body runs "
                                                               "up to it"]})
        self.assertEqual(me.fragments(listings, bodies, decided={"8c0100c0"}), {})

    def test_pool_is_no_table(self):
        listings, bodies, img = setup()
        self.assertEqual(me.pool_words(listings), {0x8C010288, 0x8C010070})
        self.assertIn(0x8C01010C, me.from_tables(img, BASE, bodies))  # without the pools it would be one

    def test_folding_and_owner(self):
        _, bodies, img = setup()
        self.assertEqual((me.p1(0x0C010100), me.p1(0xAC010100), me.p1(0x7C010100)), (0x8C010100, 0x8C010100, None))
        self.assertEqual((bodies.owner(0x8C010120), bodies.owner(0x8C010140), bodies.owner(0x8C010010)),
                         (F1, None, None))


class ApplyKeepsTheFile(unittest.TestCase):
    """tools/common.write_project_json, shared by the --apply of mid_entries, asm_modules and data_funcs --add."""

    def roundtrip(self, raw, add):
        import json
        import tempfile
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
        from common import write_project_json
        with tempfile.TemporaryDirectory() as td:
            pj = pathlib.Path(td) / "project.json"
            pj.write_bytes(raw.encode("utf-8"))
            data = json.loads(raw)
            data["catalog_overrides"]["entry"] += add
            write_project_json(pj, raw, data)
            return pj.read_bytes().decode("utf-8")

    def test_three_space_crlf_stays(self):
        raw = '{\r\n   "name": "x",\r\n   "catalog_overrides": {\r\n      "entry": [\r\n         "8c010000"\r\n' \
              '      ]\r\n   }\r\n}\r\n'
        got = self.roundtrip(raw, ["8c010104"])
        self.assertEqual(got, raw.replace('"8c010000"\r\n', '"8c010000",\r\n         "8c010104"\r\n'))

    def test_one_space_lf_and_unchanged_file(self):   # Spawn fe's own style; with nothing added the bytes are equal
        raw = '{\n "name": "Spawn’s fe",\n "catalog_overrides": {\n  "entry": []\n }\n}\n'
        self.assertEqual(self.roundtrip(raw, []), raw)

    def test_the_tools_use_it(self):
        tools = pathlib.Path(__file__).resolve().parents[1] / "tools"
        for name in ("mid_entries.py", "asm_modules.py", "data_funcs.py"):
            src = (tools / name).read_text(encoding="utf-8")
            self.assertIn("write_project_json(pj, raw, data)", src, name)
            self.assertNotIn("indent=2) +", src, name)


if __name__ == "__main__":
    unittest.main()

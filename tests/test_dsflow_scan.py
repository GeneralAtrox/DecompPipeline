"""core/dsflow_scan (selective_restamp --change dsflow): the functions whose listing has a branch into another branch's
delay slot with the fall-on missing. Known hits from PS2's packets (laptop2 recheck copy, 2026-10-29): 8c188422's piece
8c189200 (`bf 0x8c189208` into the slot of `bra 0x8c189316`) and 8c197240 (`bt/s 0x8c197258` into the slot of
`jmp @r0`); a caller of a hit is a candidate too. Controls: a fall-on already listed, a branch to a plain
instruction, a bt/s's own slot, a tail call. Synthetic folders from those listings. Run: python -W error tests/test_dsflow_scan.py.
"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from core import dsflow_scan  # noqa: E402
from core.sh4_asm import parse  # noqa: E402

P8C188422 = """; fn_8c188422  entry 8c188422
  8c188770  bsr 0x8c189200                       ; call 8c189200
  8c188772  _nop
LAB_8c189200:
  8c189200  tst r8,r8
  8c189202  mov r8,r0
  8c189204  bf 0x8c189208
LAB_8c189206:
  8c189206  bra 0x8c189316
  8c189208  _tst #0x1,r0
  8c189316  rts
  8c189318  _nop
"""
P8C197240 = """; fn_8c197240  entry 8c197240  26 bytes
  8c19724e  cmp/eq #0x0,r0
  8c197250  fmov.s fr14,@-r15
  8c197252  bt/s 0x8c197258
  8c197254  _fmov.s fr13,@-r15
LAB_8c197256:
  8c197256  jmp @r0
  8c197258  _fmov.s fr12,@-r15
"""
LISTED = P8C197240 + "  8c19725a  rts\n  8c19725c  _nop\n"  # after the change: the fall-on is in the listing
PLAIN = """  8c010000  bt 0x8c010006
  8c010002  bra 0x8c010008
  8c010004  _nop
  8c010006  mov r1,r0
  8c010008  bt/s 0x8c01000c
  8c01000a  _nop
  8c01000c  bra 0x8c020000                       ; call 8c020000
  8c01000e  _nop
"""
CALLER = """  8c180000  jsr @r1                             ; call 8c197240
  8c180002  _nop
"""  # a caller of a hit


class Finder(unittest.TestCase):
    def test_known_hits(self):
        for text, want in ((P8C188422, (0x8c189204, 0x8c189208, 0x8c18920a, False)),
                           (P8C197240, (0x8c197252, 0x8c197258, 0x8c19725a, False))):
            self.assertEqual(dsflow_scan.slot_branches(parse(text)), [want])

    def test_listed_fall_on_and_controls(self):
        self.assertEqual(dsflow_scan.slot_branches(parse(LISTED)), [(0x8c197252, 0x8c197258, 0x8c19725a, True)])
        self.assertEqual(dsflow_scan.slot_branches(parse(PLAIN)), [])

    def test_packets_folder(self):
        with tempfile.TemporaryDirectory() as t:
            for name, parts in (("8c188422", [P8C188422[:200], P8C188422[200:]]), ("8c197240", [P8C197240]),
                                ("8c197300", [LISTED]), ("8c010000", [PLAIN]), ("8c180000", [CALLER])):
                d = pathlib.Path(t, name)
                d.mkdir()
                if len(parts) == 1:
                    (d / "asm.txt").write_text(parts[0], encoding="utf-8")
                else:  # a packet in parts (asm_1.txt, asm_2.txt): a part ends on a line
                    cut = parts[0].rfind("\n") + 1
                    (d / "asm_1.txt").write_text(P8C188422[:cut], encoding="utf-8")
                    (d / "asm_2.txt").write_text(P8C188422[cut:], encoding="utf-8")
            self.assertEqual(dsflow_scan.dsflow_candidates(t), {"8c188422", "8c197240", "8c180000"})  # + its caller
            self.assertEqual(set(dsflow_scan.dsflow_scan(t, every=True)), {"8c188422", "8c197240", "8c197300"})
            self.assertEqual(dsflow_scan.dsflow_candidates(pathlib.Path(t, "none")), set())

    def test_restamp_wiring(self):
        import selective_restamp as sr
        self.assertIn('"dsflow"', pathlib.Path(sr.__file__).read_text(encoding="utf-8"))
        self.assertIs(sr.dsflow_scan, dsflow_scan)


if __name__ == "__main__":
    unittest.main(verbosity=1)

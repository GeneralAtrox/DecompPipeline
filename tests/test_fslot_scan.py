"""core/fslot_scan (selective_restamp --change fslot): a function with a float instruction in a delay slot, and its
callers. The hit's listing is V8 8c070dc0's shape (`jsr @r9` with `_fmov fr12,fr5`). Controls: a float instruction
outside a slot, an integer slot, a caller of neither. Run: python -W error tests/test_fslot_scan.py.
"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from core import fslot_scan  # noqa: E402
from core.sh4_asm import parse  # noqa: E402

HIT = """; fn_8c070dc0  entry 8c070dc0
  8c070e4e  jsr @r9                              ; call 8c070da0
  8c070e50  _fmov fr12,fr5
  8c070e52  rts
  8c070e54  _nop
"""
CALLER = """; fn_8c071080  entry 8c071080
  8c071090  jsr @r9                              ; call 8c070dc0
  8c071092  _nop
  8c071094  rts
  8c071096  _nop
"""
PLAIN_FLOAT = """; fn_8c072000  entry 8c072000
  8c072000  fmov fr12,fr5
  8c072002  jsr @r9                              ; call 8c072100
  8c072004  _mov #0,r4
  8c072006  rts
  8c072008  _nop
"""
OTHER_CALLER = """; fn_8c073000  entry 8c073000
  8c073000  jsr @r9                              ; call 8c072000
  8c073002  _nop
  8c073004  rts
  8c073006  _nop
"""


class Scan(unittest.TestCase):
    def test_slots(self):
        self.assertEqual(fslot_scan.float_slots(parse(HIT)), [0x8c070e50])
        self.assertEqual(fslot_scan.float_slots(parse(PLAIN_FLOAT)), [])

    def test_hits_and_callers(self):
        with tempfile.TemporaryDirectory() as td:
            for name, text in (("8c070dc0", HIT), ("8c071080", CALLER), ("8c072000", PLAIN_FLOAT), ("8c073000", OTHER_CALLER)):
                d = pathlib.Path(td) / name
                d.mkdir()
                (d / "asm.txt").write_text(text, encoding="utf-8")
            self.assertEqual(fslot_scan.fslot_candidates(td), {"8c070dc0", "8c071080"})

    def test_no_folder(self):
        with self.assertRaises(SystemExit):
            fslot_scan.fslot_candidates(pathlib.Path(__file__).parent / "no_such_folder")

    def test_restamp_wiring(self):
        import selective_restamp as sr
        text = pathlib.Path(sr.__file__).read_text(encoding="utf-8")
        self.assertIn('"fslot"', text)
        self.assertIs(sr.fslot_scan, fslot_scan)

    def test_combined_choice(self):
        """A single change's refresh stamps the other changes' candidates current unprepared, so they run as one."""
        import re
        from unittest import mock
        import selective_restamp as sr

        class Parsed(Exception):
            pass
        argv = ["selective_restamp", "--project", "p", "--old-scripts", "o", "--phase", "must", "--change"]
        with mock.patch.object(sr, "use_project", side_effect=Parsed), mock.patch.object(sys, "argv", argv + ["fret+lrep+fslot"]):
            self.assertRaises(Parsed, sr.main)
        text = pathlib.Path(sr.__file__).read_text(encoding="utf-8")
        scans = set(re.findall(r'if "(\w+)" in a\.change', text)) | set(re.findall(r'or "(\w+)" in a\.change', text))
        self.assertLess({"fret", "lrep", "fslot", "dsflow"}, scans)  # the pattern still sees the scans
        self.assertEqual({s for s in scans if s in "fret+lrep+fslot"}, {"fret", "lrep", "fslot"})  # substring match: no other


if __name__ == "__main__":
    unittest.main(verbosity=1)

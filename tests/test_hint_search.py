"""tools/hint_search.py offline: candidate hints from a small asm fixture, the keep/drop rule, and the greedy loop
with a fake probe (no Ghidra). Run: python tests/test_hint_search.py"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import hint_search as hs  # noqa: E402

ASM = """; fn_8c000000  entry 8c000000
  8c000000  mov.l 0x8c000100,r1                  ; = 0x8c0fc460  (data)
  8c000002  mov.l @r1,r2
  8c000004  cmp/eq #0x5,r0
  8c000006  mov.l @(0x10,r4),r0
  8c000008  cmp/eq #0x7,r0
  8c00000a  bf 0x8c000020
  8c00000c  tst r5,r5
  8c00000e  bt 0x8c000020
  8c000010  mov.l @(0x4,r6),r3
  8c000012  mov.l @(0x8,r3),r0
  8c000014  dt r7
  8c000016  mov #0x20,r2
  8c000018  cmp/hi r2,r4
  8c00001a  jsr @r3                              ; call 8c001234
"""


class T(unittest.TestCase):
    def test_candidates(self):
        lines = {c[2] for c in hs.candidates(ASM)}
        self.assertIn("genrange r4+0x10 5 9", lines)       # word at r4+0x10 compared with 7
        self.assertIn("range r5 0 2", lines)                # zero test of a parameter
        self.assertIn("range r7 1 8", lines)                # loop counter
        self.assertIn("range r4 30 34", lines)              # r4 compared with the constant 0x20
        self.assertIn("genretfn 0x8c001234 0 3", lines)     # a callee's return value
        self.assertIn("ptr r6+0x4", lines)                  # a pointer field dereferenced
        self.assertTrue(all("0x8c0fc460" not in l or l.startswith("genrange") or l.startswith("ptr") for l in lines))

    def test_gain(self):
        old = {"dev_valid": 0, "hidden_valid": 0, "blocks": "0/5"}
        self.assertTrue(hs.gain(old, {"dev_valid": 20, "hidden_valid": 20, "blocks": "0/5"}))
        self.assertTrue(hs.gain({"dev_valid": 48, "hidden_valid": 48, "blocks": "2/5"}, {"dev_valid": 40, "hidden_valid": 30, "blocks": "3/5"}))
        self.assertFalse(hs.gain({"dev_valid": 48, "hidden_valid": 48, "blocks": "2/5"}, {"dev_valid": 4, "hidden_valid": 4, "blocks": "5/5"}))
        self.assertFalse(hs.gain({"dev_valid": 48, "hidden_valid": 48, "blocks": "2/5"}, {"dev_valid": 48, "hidden_valid": 48, "blocks": "2/5"}))

    def test_search(self):
        base = {"8c000000": {"addr": "8c000000", "dev_valid": 48, "hidden_valid": 48, "blocks": "1/4"}}

        def fake(project, tag, addrs, out):
            txt = (pathlib.Path(project) / "oracle" / "hints" / "8c000000.txt").read_text()
            n = len(txt.split("\n")) - 1
            return {a: {"addr": a, "dev_valid": 48, "hidden_valid": 48, "blocks": f"{3 if 'range r5' in txt else 1}/4",
                        "dev": "48/48", "hidden": "48/48"} for a in addrs}
        with tempfile.TemporaryDirectory() as d:
            st = hs.search(d, "t", ["8c000000"], lambda a: ASM, base, 8, log=lambda m: None, probe=fake)
            s = st["8c000000"]
            self.assertTrue(any(l.startswith("range r5") for l in s["kept"]))
            self.assertEqual(len(s["kept"]), 1)
            self.assertTrue((pathlib.Path(d) / "oracle/hints/8c000000.txt").read_text().endswith("\n"))


if __name__ == "__main__":
    unittest.main()

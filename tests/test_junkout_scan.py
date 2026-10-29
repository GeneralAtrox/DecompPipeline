"""core/junkout_scan.py (the finder behind selective_restamp --change junkout) on a hand-assembled image:
- f_tail calls g, then tail-jumps to g (`bra`) with r4..r7 unwritten: the walk from its call site reads nothing
  under the old exits (only a return) and r4..r7 under the new ones (a jump out is an exit): a candidate when it
  exports;
- f_ret calls g, then returns: both walks read r4..r7 at the rts, so it is never one;
- a function exporting nothing is never one (exitLive is 0: TcStrict.walk's answer is as before);
- exporters() takes meta.json "r_exports"; selective_restamp has the option.
Synthetic data. Run: python -W error tests/test_junkout_scan.py
"""
import json
import pathlib
import struct
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import junkout_scan, regout  # noqa: E402

BASE = 0x8c010000
NOP, RTS = 0x0009, 0x000b
G, F_TAIL, F_RET = BASE, BASE + 0x08, BASE + 0x10
WORDS = {
    0x00: 0xe405, 0x02: RTS, 0x04: NOP,                  # g: mov #5,r4; rts; nop
    0x08: 0xbffa, 0x0a: NOP, 0x0c: 0xaff8, 0x0e: NOP,    # f_tail: bsr g; nop; bra g; nop
    0x10: 0xbff6, 0x12: NOP, 0x14: RTS, 0x16: NOP,       # f_ret: bsr g; nop; rts; nop
}
BODIES = {G: [(G, G + 6)], F_TAIL: [(F_TAIL, F_TAIL + 8)], F_RET: [(F_RET, F_RET + 8)]}


def image():
    b = bytearray(0x20)
    for off, w in WORDS.items():
        struct.pack_into("<H", b, off, w)
    return bytes(b)


class JunkoutScan(unittest.TestCase):
    def test_walks(self):
        old, new = (k(image(), BASE, BODIES) for k in (junkout_scan.Old, junkout_scan.New))
        self.assertEqual(old.walk(F_TAIL, F_TAIL + 4, regout.R4_7) & regout.R4_7, 0)
        self.assertEqual(new.walk(F_TAIL, F_TAIL + 4, regout.R4_7) & regout.R4_7, regout.R4_7)
        for c in (old, new):
            self.assertEqual(c.walk(F_RET, F_RET + 4, regout.R4_7) & regout.R4_7, regout.R4_7)

    def test_scan(self):
        found = junkout_scan.scan(image(), BASE, BODIES, exported={F_TAIL, F_RET})
        # the bsr, and the bra: TcOut.outs walks a tail site from jump + 4 (here f_ret's code), as regout does
        self.assertEqual(found, {F_TAIL: [F_TAIL, F_TAIL + 4]})
        self.assertEqual(junkout_scan.scan(image(), BASE, BODIES), {})  # nothing exported

    def test_exporters(self):
        with tempfile.TemporaryDirectory() as d:
            for a, meta in (("8c010008", {"r_exports": ["r4"]}), ("8c010010", {"callees": []})):
                (pathlib.Path(d) / "harness" / a).mkdir(parents=True)
                (pathlib.Path(d) / "harness" / a / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            self.assertEqual(junkout_scan.exporters(d), {F_TAIL})

    def test_selective_restamp_option(self):
        src = (ROOT / "tools" / "selective_restamp.py").read_text(encoding="utf-8")
        self.assertIn('"junkout"', src)
        self.assertIn('regout_candidates(ws, junk=True) if "junkout" in a.change', src)


if __name__ == "__main__":
    unittest.main()

"""core/regout.py (the TcOut candidate finder behind selective_restamp --change regout) on a hand-assembled image:
- g_clob hands back r4 (mov #5,r4); g_keep leaves every register;
- f_reads calls g_clob and reads r4 after: a candidate (a), and g_clob is one too (b: its r4 is read by a caller);
- f_writes writes r4 before reading it, f_keeps calls g_keep: neither;
- f_tail ends with `mov.l @(pc),r1; jmp @r1` to g_clob through the 0x0c mirror of its address, the literal pool right
  after: the walk from jump + 4 finds no instruction, so it reads r1..r7: a candidate;
- the address mirrors, and selective_restamp's --change regout / sz+regout.
Synthetic data. Run: python -W error tests/test_regout.py
"""
import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import regout  # noqa: E402

BASE = 0x8c010000


def image(words):
    """words: {offset: 16-bit word or 32-bit literal as ('l', value)}; the rest nop-free zeros."""
    b = bytearray(0x60)
    for off, w in words.items():
        if isinstance(w, tuple):
            struct.pack_into("<I", b, off, w[1])
        else:
            struct.pack_into("<H", b, off, w)
    return bytes(b)


NOP, RTS = 0x0009, 0x000b
G_CLOB, G_KEEP, F_READS, F_WRITES, F_KEEPS, F_TAIL = (BASE + x for x in (0x00, 0x08, 0x10, 0x1c, 0x2c, 0x38))
WORDS = {
    0x00: 0xe405, 0x02: RTS, 0x04: NOP,                                             # g_clob: mov #5,r4; rts; nop
    0x08: RTS, 0x0a: NOP,                                                           # g_keep
    0x10: 0xbff6, 0x12: NOP, 0x14: 0x6043, 0x16: RTS, 0x18: NOP,                    # f_reads: bsr g_clob; mov r4,r0
    0x1c: 0xbff0, 0x1e: NOP, 0x20: 0xe401, 0x22: 0x6043, 0x24: RTS, 0x26: NOP,      # f_writes: ...; mov #1,r4; mov r4,r0
    0x2c: 0xbfec, 0x2e: NOP, 0x30: 0x6043, 0x32: RTS, 0x34: NOP,                    # f_keeps: bsr g_keep; mov r4,r0
    0x38: 0xd101, 0x3a: 0x412b, 0x3c: NOP, 0x40: ("l", 0x0c010000),                 # f_tail: jmp @r1 -> 0x0c010000
}
BODIES = {G_CLOB: [(G_CLOB, G_CLOB + 6)], G_KEEP: [(G_KEEP, G_KEEP + 4)], F_READS: [(F_READS, F_READS + 10)],
          F_WRITES: [(F_WRITES, F_WRITES + 12)], F_KEEPS: [(F_KEEPS, F_KEEPS + 10)], F_TAIL: [(F_TAIL, F_TAIL + 6)]}


class Regout(unittest.TestCase):
    def setUp(self):
        self.a, self.b, self.why = regout.scan(image(WORDS), BASE, BODIES)

    def test_callers(self):
        self.assertEqual(self.a, {F_READS, F_TAIL})

    def test_exporters(self):
        self.assertEqual(self.b, {G_CLOB})

    def test_kept(self):
        c = regout.Code(image(WORDS), BASE, BODIES)
        self.assertEqual(c.kept(G_KEEP), regout.R4_7)
        self.assertEqual(c.kept(G_CLOB), regout.R4_7 & ~0x10)          # r4 only is changed
        self.assertEqual(c.kept(F_READS), regout.R4_7 & ~0x10)         # r4 comes back from g_clob

    def test_pool_after_tail_jump_reads_everything(self):
        c = regout.Code(image(WORDS), BASE, BODIES)
        self.assertFalse(c.covered(F_TAIL + 6))
        self.assertEqual(c.walk(F_TAIL, F_TAIL + 6), regout.R1_7)
        self.assertEqual(c.target(F_TAIL + 2), G_CLOB)                  # the 0x0c mirror literal

    def test_mirrors(self):
        self.assertEqual(regout.p1(0x0c01e9e4), 0x8c01e9e4)
        self.assertEqual(regout.p1(0xac01e9e4), 0x8c01e9e4)
        self.assertEqual(regout.p1(0x8c01e9e4), 0x8c01e9e4)

    def test_selective_restamp_option(self):
        src = (ROOT / "tools" / "selective_restamp.py").read_text(encoding="utf-8")
        self.assertIn('"regout", "sz+regout"', src)
        self.assertIn('regout_candidates(ws) if "regout" in a.change', src)


if __name__ == "__main__":
    unittest.main()

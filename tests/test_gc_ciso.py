"""GameCube: a CISO file reads as the whole disc (targets/gamecube_ppc/tools/ciso.py), and gcdisc.Disc opens it.
Synthetic files only: a 4 KiB-block CISO with absent blocks, a cut-short last block, a read across blocks, bad headers.
usage: python tests/test_gc_ciso.py
"""
import pathlib
import struct
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "targets" / "gamecube_ppc" / "tools"))
import ciso  # noqa: E402
import gcdisc  # noqa: E402

BS = 0x1000


def make(path, blocks, mapbytes=None, block=BS):
    """blocks: {index: bytes}; the file holds them in index order."""
    m = bytearray(HEADER - 8)
    for i in blocks:
        m[i] = 1
    head = b"CISO" + struct.pack("<I", block) + (mapbytes or bytes(m))
    path.write_bytes(head + b"".join(blocks[i] for i in sorted(blocks)))


HEADER = ciso.HEADER


class Ciso(unittest.TestCase):
    def test_reads_present_absent_and_across_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "a.ciso"
            make(p, {0: bytes([1]) * BS, 2: bytes([3]) * BS, 4: bytes([5]) * 100})  # block 4 is cut short
            im = ciso.Image(p)
            self.assertEqual(im.size, 5 * BS)
            self.assertEqual(im.read(0, 4), bytes([1]) * 4)
            self.assertEqual(im.read(BS, 8), bytes(8))                      # absent block: zeros
            self.assertEqual(im.read(BS - 2, 4), bytes([1, 1, 0, 0]))       # across present and absent
            self.assertEqual(im.read(2 * BS + 5, 2), bytes([3, 3]))
            self.assertEqual(im.read(4 * BS, 200), bytes([5]) * 100 + bytes(100))
            with self.assertRaises(ValueError):
                im.read(5 * BS - 1, 2)
            im.close()

    def test_bad_headers(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "b.ciso"
            p.write_bytes(b"NOPE" + bytes(HEADER))
            with self.assertRaises(ValueError):
                ciso.Image(p)
            make(p, {0: bytes(BS)}, block=3000)
            with self.assertRaises(ValueError):
                ciso.Image(p)
            make(p, {0: bytes(BS)}, mapbytes=bytes([2]) + bytes(HEADER - 9))
            with self.assertRaises(ValueError):
                ciso.Image(p)

    def test_disc_opens_a_ciso(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "c.ciso"
            head = bytearray(0x440)
            head[0:6] = b"GALE01"
            head[0x1C:0x20] = struct.pack(">I", gcdisc.GC_MAGIC)
            head[0x20:0x2A] = b"Test Title"
            make(p, {0: bytes(head) + bytes(BS - 0x440)})
            disc = gcdisc.Disc(p)
            self.assertEqual(disc.read(0, 6), b"GALE01")
            self.assertEqual(disc.size, BS)
            disc.close()


if __name__ == "__main__":
    unittest.main()

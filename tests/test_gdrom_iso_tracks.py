"""targets/dreamcast_sh4/tools/gdrom.py reads a .gdi whose data tracks are 2048-byte .iso files as well as raw 2352-byte
tracks (outside user's feedback, 2026-11-13: "LBA 45000: not a Mode 1 data sector in track 3"). Synthetic tracks only:
a tiny ISO 9660 image with IP.BIN, one directory and one file, written both ways.
Run: python -W error tests/test_gdrom_iso_tracks.py
"""
import pathlib
import struct
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "targets" / "dreamcast_sh4" / "tools")]
import gdrom  # noqa: E402

BODY = b"HELLO FROM THE DISC"


def sectors():
    """The data sectors of LBA 45000..45019 (relative 0..19): IP.BIN, PVD at 16, root dir at 18, file at 19."""
    s = [bytes(2048) for _ in range(20)]
    ip = bytearray(16 * 2048)
    ip[0:16] = b"SEGA SEGAKATANA "
    ip[0x60:0x70] = b"1ST_READ.BIN    "
    ip[0x80:0x88] = b"TESTGAME"
    for i in range(16):
        s[i] = bytes(ip[i * 2048:(i + 1) * 2048])
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    root = bytearray(34)
    root[0] = 34
    root[2:6] = struct.pack("<I", 45018)
    root[10:14] = struct.pack("<I", 2048)
    root[25] = 2
    root[32] = 1
    pvd[156:190] = root
    s[16] = bytes(pvd)

    def rec(name, lba, size):
        r = bytearray(33 + len(name) + (len(name) + 1) % 2)
        r[0] = len(r)
        r[2:6] = struct.pack("<I", lba)
        r[10:14] = struct.pack("<I", size)
        r[32] = len(name)
        r[33:33 + len(name)] = name
        return bytes(r)

    d = rec(b"\x00", 45018, 2048) + rec(b"\x01", 45018, 2048) + rec(b"1ST_READ.BIN;1", 45019, len(BODY))
    s[18] = d + bytes(2048 - len(d))
    s[19] = BODY + bytes(2048 - len(BODY))
    return s


def raw(data):
    return b"\x00" + b"\xff" * 10 + b"\x00" + b"\x00\x00\x00\x01" + data + bytes(2352 - 16 - 2048)


class IsoTracks(unittest.TestCase):
    def make(self, size):
        d = pathlib.Path(tempfile.mkdtemp(prefix="gd_"))
        blob = b"".join((raw(x) if size == 2352 else x) for x in sectors())
        (d / "track03.bin").write_bytes(blob)
        (d / "g.gdi").write_text(f'3\n1 0 4 2352 "track01.bin" 0\n2 450 0 2352 "track02.raw" 0\n'
                                 f'3 45000 4 {size} "track03.bin" 0\n', encoding="utf-8")
        return d / "g.gdi"

    def check(self, size):
        disc = gdrom.Disc(self.make(size))
        self.assertEqual(disc.ip()["boot_file"], "1ST_READ.BIN")
        (path, lba, n), = disc.walk()
        self.assertEqual((path, lba, n), ("1ST_READ.BIN", 45019, len(BODY)))
        self.assertEqual(disc.read(lba, n), BODY)

    def test_raw_2352(self):
        self.check(2352)

    def test_iso_2048(self):
        self.check(2048)

    def test_odd_sector_size_refused(self):
        with self.assertRaises(SystemExit):
            gdrom.Disc(self.make(2336))


if __name__ == "__main__":
    unittest.main()

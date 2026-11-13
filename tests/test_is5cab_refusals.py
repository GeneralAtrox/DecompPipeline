"""tools/is5cab.py names its limits up front (outside user's feedback, 2026-11-13: a multi-volume set failed with
"zlib.error: invalid block type"): a data2.cab beside data1.cab, or files stored past the end of the cab, and an
InstallShield 6 header, each raise a ValueError that names unshield. Synthetic files only.
Run: python -W error tests/test_is5cab_refusals.py
"""
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools")]
import is5cab  # noqa: E402


def entry(offset, csize):
    return {"name": "a.bin", "dir": "", "group": "", "flags": is5cab.FLAG_COMPRESSED, "size": 10, "csize": csize,
            "offset": offset, "md5": "0" * 32}


class Refusals(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="is5_"))
        (self.d / "data1.hdr").write_bytes(b"x")
        (self.d / "data1.cab").write_bytes(bytes(100))

    def run_extract(self, files):
        with mock.patch.object(is5cab, "read_header", lambda b: {"major": 5, "dirs": [], "files": files}):
            return is5cab.extract(self.d / "data1.hdr", self.d / "out")

    def test_next_volume_beside(self):
        (self.d / "data2.cab").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "multi-volume.*unshield"):
            self.run_extract([entry(0, 10)])

    def test_file_past_end_of_cab(self):
        with self.assertRaisesRegex(ValueError, "multi-volume.*unshield"):
            self.run_extract([entry(90, 50)])

    def test_single_volume_still_goes_on(self):
        self.assertEqual(self.run_extract([entry(0, 10)])["bad"], 1)  # reaches the extractor (garbage data is BAD)

    def test_version_6_names_unshield(self):
        hdr = struct.pack("<5I", is5cab.SIGNATURE, 0x01006000, 0, 0, 0)  # major 6 by major_version()
        self.assertEqual(is5cab.major_version(0x01006000), 6)
        with self.assertRaisesRegex(ValueError, "not supported.*unshield"):
            is5cab.read_header(hdr)


if __name__ == "__main__":
    unittest.main()

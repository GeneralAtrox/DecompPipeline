"""The port kit's FSCA table C (core/fsca.table_c) from a project's table file instead of a rebuild (port/tools/gen_port
passes paths.fsca_table(); a build's file pass took 3-5 s longer for the rebuild each time):
1. table_c(table_file=F) is the same text as table_c() when F is the pinned file() (its SHA-256 is FILE_SHA256);
2. any other file (a changed byte, the old one-table size, a missing path) is ignored and the table is rebuilt: the
   text is still the pinned one;
3. gen_port writes gen_report.txt only when it differs, as every other generated file (a no-change run rewrites nothing).
Run: python -W error tests/test_fsca_cache.py
"""
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import fsca  # noqa: E402


class TableFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=TMP)
        cls.good = pathlib.Path(cls.tmp.name) / "good.bin"
        cls.good.write_bytes(fsca.file())
        cls.rebuilt = fsca.table_c()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_pinned_file_gives_the_same_text_and_is_not_a_rebuild(self):
        t = time.perf_counter()
        text = fsca.table_c(table_file=self.good)
        self.assertEqual(text, self.rebuilt)
        self.assertLess(time.perf_counter() - t, 2.0)  # a rebuild of both tables takes several seconds

    def test_another_file_is_ignored(self):
        bad = bytearray(self.good.read_bytes())
        bad[100] ^= 1
        p = pathlib.Path(self.tmp.name) / "bad.bin"
        p.write_bytes(bytes(bad))
        old = pathlib.Path(self.tmp.name) / "old.bin"
        old.write_bytes(self.good.read_bytes()[:fsca.SIZE // 2])
        for f in (p, old, pathlib.Path(self.tmp.name) / "missing.bin"):
            self.assertIsNone(fsca.sines_of_file(f))
            self.assertEqual(fsca.table_c(table_file=f), self.rebuilt)

    def test_gen_port_writes_the_report_and_the_table_only_when_changed(self):
        text = (ROOT / "port" / "tools" / "gen_port.py").read_text(encoding="utf-8")
        self.assertIn('write_if_changed(out / "gen_report.txt"', text)
        self.assertIn("fsca.table_c(table_file=paths.fsca_table())", text)


if __name__ == "__main__":
    unittest.main()

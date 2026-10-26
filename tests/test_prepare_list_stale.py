"""tools/prepare_list.py --harness-stale: the functions the readable ledger marks harness_stale are the list (the one an
operator copied out by hand), added to any given addresses; Ghidra is never run.
Run: python -W error tests/test_prepare_list_stale.py.
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

import prepare_list  # noqa: E402
from core import refine  # noqa: E402


class HarnessStale(unittest.TestCase):
    def test_from_the_ledger(self):
        rows = {"8c010200": {"status": "harness_stale", "note": "FAIL"}, "8c010100": {"status": "harness_stale"},
                "8c010300": {"status": "readable"}, "8c010400": {"status": "failed"}}
        with mock.patch.object(refine, "load_ledger", return_value=rows):
            self.assertEqual(prepare_list.harness_stale(), ["8c010100", "8c010200"])

    def test_options(self):
        src = pathlib.Path(prepare_list.__file__).read_text(encoding="utf-8")
        self.assertIn("addrs = list(dict.fromkeys(addrs + harness_stale()))", src)
        self.assertIn('ap.error("no addresses (ADDR, @FILE or --harness-stale)")', src)


if __name__ == "__main__":
    unittest.main()

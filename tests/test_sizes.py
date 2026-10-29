"""The 400-line cap ratchet (tools/sizes.py, AGENTS.md "Layout"): no tracked file goes over the cap unless it is listed in
tests/sizes_over_400.txt, and no listed file grows. Needs git (skipped without it). Run: python tests/test_sizes.py
"""
import pathlib
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
import sizes  # noqa: E402


class Ratchet(unittest.TestCase):
    def test_no_file_over_the_cap_grows(self):
        over = sizes.current()
        if over is None:
            self.skipTest("git not available")
        self.assertEqual(sizes.breaches(over, sizes.read_baseline()), [])

    def test_breaches(self):
        base = {"a.py": 500, "b.py": 450}
        self.assertEqual(sizes.breaches({"a.py": 500, "b.py": 420}, base), [])         # same and shrunk: fine
        self.assertEqual(sizes.breaches({"a.py": 500}, base), [])                      # b split below the cap
        bad = sizes.breaches({"a.py": 501, "c.md": 401}, base)                         # grew; new over the cap
        self.assertEqual([b.split(":")[0] for b in bad], ["a.py", "c.md"])

    def test_over_cap_counts_only_the_kinds_read_whole(self):
        with tempfile.TemporaryDirectory() as d:
            for name, n in (("big.py", 401), ("cap.py", 400), ("big.json", 900), ("big.md", 402)):
                (pathlib.Path(d) / name).write_text("x\n" * n, encoding="utf-8")
            got = [(n, p.name) for n, p in sizes.over_cap(sorted(pathlib.Path(d).iterdir()))]
            self.assertEqual(got, [(402, "big.md"), (401, "big.py")])

    def test_baseline_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "list.txt"
            sizes.write_baseline({"x/a.py": 410, "b.c": 1200}, f)
            self.assertEqual(sizes.read_baseline(f), {"x/a.py": 410, "b.c": 1200})


if __name__ == "__main__":
    unittest.main()

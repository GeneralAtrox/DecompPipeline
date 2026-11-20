"""motion_check.py passes a scroll and a still picture, and fails a planted pop."""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "port" / "tools" / "motion_check.py"
HAVE = all(importlib.util.find_spec(m) for m in ("numpy", "PIL"))


@unittest.skipUnless(HAVE, "needs numpy and Pillow")
class MotionCheck(unittest.TestCase):
    def run_on(self, make):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            for n in range(12):
                im = Image.new("RGB", (320, 180))
                make(im, n)
                im.save(pathlib.Path(d) / f"frame_{n:06d}.png")
            return subprocess.run([sys.executable, "-B", str(TOOL), d], capture_output=True, text=True).returncode

    @staticmethod
    def box(im, x, y):
        im.paste((200, 60, 60), (x, y, x + 40, y + 20))

    def test_scroll_passes(self):
        self.assertEqual(self.run_on(lambda im, n: self.box(im, 60 + n, 40 + n)), 0)

    def test_a_logo_leaving_the_frame_passes(self):
        self.assertEqual(self.run_on(lambda im, n: self.box(im, 300 + n, 40 + n)), 0)

    def test_a_logo_that_vanishes_fails(self):
        def make(im, n):
            self.box(im, 60 + n, 40 + n)
            if n < 6:
                self.box(im, 200 + n, 100 + n)
        self.assertEqual(self.run_on(make), 1)

    def test_a_logo_that_appears_fails(self):
        def make(im, n):
            self.box(im, 60 + n, 40 + n)
            if n >= 6:
                self.box(im, 200 + n, 100 + n)
        self.assertEqual(self.run_on(make), 1)


if __name__ == "__main__":
    unittest.main()

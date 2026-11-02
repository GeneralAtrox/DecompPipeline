"""The port regress run's `filled` check (port/tools/frames.py filled(), regress.py script key `filled`).

A full-screen picture that drew only part of itself (Toy Commander's second movie drew only its top 32-pixel strip when a
store-queue word overwrote the clip's lower edge) has empty edge bands; the check needs no golden. A frame passes when
none of its four edge bands is empty, and a script's `filled` frames are dumped as well as the `frames` ones.
Run: python -W error tests/test_port_regress_filled.py
"""
import importlib.util
import pathlib
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "port" / "tools"))
import frames  # noqa: E402


def png(d, name, rows):
    """A 640x480 picture of mid-grey over its first `rows` rows, black below."""
    a = np.zeros((480, 640, 3), dtype=np.uint8)
    a[:rows] = 120 + np.arange(640, dtype=np.uint8)[None, :, None] % 32
    p = pathlib.Path(d) / name
    Image.fromarray(a).save(p)
    return p


class Filled(unittest.TestCase):
    def test_full_frame_passes_and_a_top_strip_fails(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(frames.filled(png(d, "full.png", 480)), [])
            self.assertEqual(frames.filled(png(d, "strip.png", 32)), ["bottom"])
            self.assertEqual(sorted(frames.filled(png(d, "black.png", 0))), ["bottom", "left", "right", "top"])

    def test_one_flat_colour_is_not_a_picture(self):  # the movie's regression in ToyCommander attract_movie: "or white"
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "white.png"
            Image.fromarray(np.full((480, 640, 3), 255, dtype=np.uint8)).save(p)
            self.assertEqual(sorted(frames.filled(p)), ["bottom", "left", "right", "top"])

    def test_wrong_frames_fail_and_rounding_passes(self):  # numbers on real goldens: docs/proofs/check-audit.md
        rng = np.random.default_rng(1)
        gold = 20 + (np.indices((480, 640)).sum(axis=0)[..., None] % 200) * np.ones(3, dtype=np.int64)

        def verdict(frame):
            with tempfile.TemporaryDirectory() as d:
                a, b = pathlib.Path(d) / "a.png", pathlib.Path(d) / "b.png"
                Image.fromarray(np.clip(gold, 0, 255).astype(np.uint8)).save(a)
                Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8)).save(b)
                return frames.compare(a, b)["pass"]
        self.assertTrue(verdict(gold))
        self.assertTrue(verdict(gold + rng.integers(-1, 2, gold.shape)))  # rounding everywhere
        dots = gold.copy()
        dots[rng.integers(0, 480, 20), rng.integers(0, 640, 20)] += 40    # 20 scattered pixels
        self.assertTrue(verdict(dots))
        obj = gold.copy()
        obj[100:106, 100:106] = 0                                         # a 6x6 object gone: 0.01% of the frame
        self.assertFalse(verdict(obj))
        self.assertFalse(verdict(gold + 6))                               # brighter, under CHANGED_LEVEL
        self.assertFalse(verdict(gold + np.array([6, 0, 0])))             # a tint, under CHANGED_LEVEL too
        self.assertFalse(verdict(np.roll(gold, 5, axis=1)))               # moved

    def test_script_key(self):
        spec = importlib.util.spec_from_file_location("port_regress", TOOL / "port" / "tools" / "regress.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("build", __import__("build"))
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "x.script"
            p.write_text("seconds 5\nframes 3\nfilled 10000, 10500  # a movie\n", encoding="utf-8")
            s = mod.parse_script(p)
            self.assertEqual((s["frames"], s["filled"]), ([3], [10000, 10500]))


if __name__ == "__main__":
    unittest.main()

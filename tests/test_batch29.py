"""Batch 29: the words a `mova` points at, in the Haiku sandbox's asm.txt (core/asm_pool), and the rescue pass's
store-fault repair (TcOracle.storeFaults, TcProvenance; judged by the gate's fixture tc-store-fault-8c079c1e).

One pass and one fail control each:
1. a `mova` into a literal pool gets its first words (hex, and the f32 reading of a plain number); a `mova` into a code
   range, one outside the image and a line that already has a comment are left as they are;
2. f32 readings: the shortest decimal that gives the same f32 (0.0001), signed zero, FLT_MAX; none for NaN, infinity
   or a pointer-looking word (0x8c0635b8 reads as -1e-31);
3. the sandbox brief says what a `mova` line shows; the rescue-only store check is wired (TcVectors sets
   storeFaults = rescue, TcOracle stops a store outside RAM only under it); the locked scripts (compiled alone for a
   staged prepare) include every Tc class they use.
Synthetic data. Run: python -W error tests/test_batch29.py (also from test_tooling.py).
"""
import json
import pathlib
import re
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import agent, asm_pool  # noqa: E402

BASE = 0x8C010000
SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "targets/dreamcast_sh4/ghidra_scripts"


def image():
    """0x40 bytes: code at +0..+0x20, a literal pool at +0x20 (0.0001f, a function pointer)."""
    img = bytearray(0x40)
    struct.pack_into("<II", img, 0x20, 0x38D1B717, 0x8C0635B8)
    return bytes(img)


def listing(*rows):
    return "; fn\n" + "".join(f"  {a:08x}  {i:<36}" + (f"; {n}" if n else "") + "\n" for a, i, n in rows)


class Mova(unittest.TestCase):
    RANGES = [(BASE, BASE + 0x20)]

    def test_pool_target_annotated(self):
        text = listing((BASE + 2, "mov.l 0x8c010024,r3", "= 0x8c0635b8  (function 8c0635b8)"),
                       (BASE + 4, "mova 0x8c010020,r0", None), (BASE + 6, "fmov.s @r0,fr3", None))
        out = asm_pool.annotate(text, image(), BASE, self.RANGES).splitlines()
        self.assertIn("; r0 = 0x8c010020 (literal pool): words 0x38d1b717 (f32 0.0001), 0x8c0635b8", out[2])
        self.assertEqual(out[2].index(";"), out[1].index(";"))  # aligned with the listing's own notes
        self.assertEqual(out[1], text.splitlines()[1])
        self.assertEqual(out[3], text.splitlines()[3])

    def test_controls_left_alone(self):
        text = listing((BASE + 4, "mova 0x8c010010,r0", None),  # into code (a jump table base)
                       (BASE + 6, "mova 0x8c020000,r0", None),  # outside the image
                       (BASE + 8, "mova 0x8c010020,r0", "already noted"))
        self.assertEqual(asm_pool.annotate(text, image(), BASE, self.RANGES), text)

    def test_delay_slot_mova(self):
        text = listing((BASE + 4, "_mova 0x8c010020,r0", None))
        self.assertIn("(literal pool)", asm_pool.annotate(text, image(), BASE, self.RANGES))

    def test_code_ranges_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "code_ranges.txt"
            p.write_text("8c010000 8c010020\n8c010040 8c010080\n", encoding="utf-8")
            self.assertEqual(asm_pool.code_ranges(p), [(BASE, BASE + 0x20), (BASE + 0x40, BASE + 0x80)])
            self.assertEqual(asm_pool.code_ranges(pathlib.Path(d) / "missing.txt"), [])


class Readings(unittest.TestCase):
    def test_plain_numbers(self):
        self.assertEqual(asm_pool._f32(0x38D1B717), "0.0001")
        self.assertEqual(asm_pool._f32(0x3F800000), "1")
        self.assertEqual(asm_pool._f32(0xBF000000), "-0.5")
        self.assertEqual(asm_pool._f32(0x00000000), "0.0")
        self.assertEqual(asm_pool._f32(0x80000000), "-0.0")
        self.assertEqual(struct.unpack("<f", struct.pack("<f", float(asm_pool._f32(0x7F7FFFFF))))[0],
                         struct.unpack("<f", struct.pack("<I", 0x7F7FFFFF))[0])

    def test_not_numbers(self):
        for word in (0x7FC00000, 0x7F800000, 0xFF800000, 0x8C0635B8, 0x00000001):
            self.assertIsNone(asm_pool._f32(word), hex(word))


class Wiring(unittest.TestCase):
    def test_brief(self):
        self.assertIn("a `mova` line shows the first words", agent.TASK)

    def test_store_check_rescue_only(self):
        vectors = (SCRIPTS / "TcVectors.java").read_text(encoding="utf-8")
        oracle = (SCRIPTS / "TcOracle.java").read_text(encoding="utf-8")
        self.assertIn("storeFaults = rescue;", vectors)
        self.assertIn("TcProvenance.repair(this, v, r)", vectors)
        self.assertIn("boolean storeFaults = false;", oracle)
        self.assertIn("if (storeFaults) {", oracle)
        self.assertTrue((SCRIPTS / "TcProvenance.java").exists())

    def test_locked_scripts_closed(self):
        """gate.staging_scripts compiles the locked scripts alone: each Tc class one of them uses is locked too
        (batch 29 found TcProvenance, TcSaves and TcText missing)."""
        locked = set(json.loads((SCRIPTS.parent / "target.json").read_text(encoding="utf-8"))["locked_scripts"])
        have = {p.stem for p in SCRIPTS.glob("*.java")}
        for name in sorted(locked):
            text = re.sub(r"//.*", "", (SCRIPTS / name).read_text(encoding="utf-8"))
            used = {m for m in re.findall(r"\b(Tc[A-Z]\w*)\b", text) if m in have}
            self.assertEqual(sorted(u for u in used if u + ".java" not in locked), [], name)


if __name__ == "__main__":
    unittest.main()

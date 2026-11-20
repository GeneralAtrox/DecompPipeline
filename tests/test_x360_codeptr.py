"""ppcscan.code_built_starts, the shared rule for function starts the code builds with lis + addi (catalogue source
"codeptr", and the port kit's gen_xbox translate --code-pointers), on a made-up image:
- a start built by lis/addi from another function, inside an entry the pointer pass sized over it, is found;
- a C++ catch continuation built the same way is left out;
- a start whose first word has primary opcode 0 (data in the code section) is left out;
- the entry that had run over the start (the "swallowing parent") ends at it;
- a start in a gap between entries is found but not added by scan (catfeed gives it the kit's body).
Too Human's 82644cf0 (a vcall thunk inside 82644ce8) stopped the port on missing_function, 2026-11-14.
"""
import pathlib
import struct
import sys
import unittest
from unittest import mock

X360 = pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc"
sys.path += [str(X360 / "tools")]
import ppcscan  # noqa: E402

BASE = 0x82000000
F = 0x82010000      # a .pdata function that builds the three addresses
P = 0x82010100      # a pointer entry (a data word names it), sized to the next entry: it runs over T1, T2 and T3
T1 = 0x82010108     # a vcall thunk: lwz r12,0(r3); lwz r11,64(r12); mtctr r11; bctr
T2 = 0x82010120     # a catch continuation (eh_tables): li r3,0; blr
T3 = 0x82010130     # data after a blr: first word 0x00001234 (primary opcode 0)
BLR, BCTR, NOP = 0x4E800020, 0x4E800420, 0x60000000


def lis(rd, hi):
    return (15 << 26) | (rd << 21) | hi


def addi(rd, ra, lo):
    return (14 << 26) | (rd << 21) | (ra << 16) | lo


def image(pointer=True):
    img = bytearray(0x21000)

    def put(a, words):
        for k, v in enumerate(words):
            struct.pack_into(">I", img, a - BASE + 4 * k, v)
    put(F, [lis(11, 0x8201), addi(4, 11, T1 & 0xFFFF), lis(10, 0x8201), addi(5, 10, T2 & 0xFFFF),
            lis(9, 0x8201), addi(6, 9, T3 & 0xFFFF), BLR, NOP])
    put(P, [0x81830000, BCTR,                                   # P: lwz r12,0(r3); bctr
            0x81830000, 0x816C0040, 0x7D6903A6, BCTR, NOP, BLR,   # T1
            0x38600000, BLR, NOP, BLR,                           # T2
            0x00001234, 0x00005678, NOP, BLR])                   # T3; the zero word at P + 0x40 ends P
    put(0x82020000, [P if pointer else 0])                       # .data: the pointer that names P
    desc = {"original_pe_name": "t.exe", "load_address": BASE, "image_size": len(img), "entry_point": F,
            "image_sha1": "0" * 40, "imports": [], "pdata": [(F, 0x20, 0)],
            "sections": [{"name": ".text", "start": 0x82010000, "end": 0x82011000, "raw_end": 0x82011000, "execute": True},
                         {"name": ".data", "start": 0x82020000, "end": 0x82020100, "raw_end": 0x82020100, "execute": False}]}
    return bytes(img), desc


class CodeBuiltStarts(unittest.TestCase):
    def setUp(self):
        self.img, self.desc = image()
        self.im = ppcscan.Image(self.img, self.desc)
        self.funcs = {F: F + 0x20, P: P + 0x40}

    def test_lis_addi_start_found(self):
        self.assertIn(T1, ppcscan.code_built_starts(self.im, self.funcs, ()))

    def test_eh_continuation_left_out(self):
        self.assertIn(T2, ppcscan.code_built_starts(self.im, self.funcs, ()))    # found as a start without the tables
        self.assertEqual(ppcscan.code_built_starts(self.im, self.funcs, {T2}), [T1])

    def test_opcode_0_first_word_left_out(self):
        self.assertNotIn(T3, ppcscan.code_built_starts(self.im, self.funcs, ()))
        self.assertTrue(ppcscan.after_terminator(self.im.word(T3 - 4)))         # only its first word rules it out

    def test_own_body_case_label_left_out(self):
        self.assertEqual(ppcscan.code_built_starts(self.im, {F: P + 0x40}, ()), [])   # one body holds the builder and T1

    def test_scan_adds_codeptr_and_resizes_parent(self):
        with mock.patch.object(ppcscan, "eh_continuations", return_value={T2}):
            cat = ppcscan.scan(self.img, self.desc)
        by = {int(f["addr"], 16): f for f in cat["functions"]}
        self.assertEqual(sorted(by), [F, P, T1])
        self.assertEqual((by[P]["source"], by[P]["size"]), ("pointer", 8))      # was 0x40, over T1..T3
        self.assertEqual((by[T1]["source"], by[T1]["size"]), ("codeptr", 0x38))
        self.assertEqual(cat["stats"]["by_source"]["codeptr"], 1)

    def test_scan_without_eh_tables(self):
        cat = ppcscan.scan(self.img, self.desc)                                  # no .pdata section: no continuations
        self.assertIn(f"{T2:08x}", {f["addr"] for f in cat["functions"] if f["source"] == "codeptr"})

    def test_gap_start_found_not_added(self):
        img, desc = image(pointer=False)                                         # no P: T1 sits in a gap
        self.assertIn(T1, ppcscan.code_built_starts(ppcscan.Image(img, desc), {F: F + 0x20}, {T2}))
        with mock.patch.object(ppcscan, "eh_continuations", return_value={T2}):
            cat = ppcscan.scan(img, desc)
        self.assertEqual([f["addr"] for f in cat["functions"]], [f"{F:08x}"])

    def test_port_kit_imports_the_rule(self):
        src = (X360 / "port" / "tools" / "gen_xbox.py").read_text(encoding="utf-8")
        body = src[src.index("def code_pointers(k):"):src.index("def bringup(k, a):")]
        self.assertIn("ppcscan.code_built_starts(", body)
        self.assertNotIn("lis rD", body)                                         # no copy of the scan of its own


if __name__ == "__main__":
    unittest.main()

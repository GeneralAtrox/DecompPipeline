"""FPSCR.SZ from the callers' code (core/sz_entry.py, TcFpscr.Sz, vectors line z, meta "fpscr_sz"):
1. the analysis: a function called inside a caller's fschg pair runs with SZ=1, one called both ways runs in both, a
   call after a callee that returns with SZ flipped (`rts; fschg`) runs in the flipped mode, and a call whose SZ
   depends on a branch is reported and left out; arg() is TcPrepare's script arg 13 for exactly those functions;
2. the translator: meta "fpscr_sz" "sz:1" gives the SZ=1 body alone (fmov moves 8 bytes: a copy loop's pair), "sz:01"
   a body per mode chosen by sh4_sz(), and a callee that returns with SZ changed makes the code after its call run
   in that mode, with the C setting the mode after the (mocked) call; without the fact the C is as before (4 bytes);
3. the runners, xemu and the oracle take the entry mode (line z; TcFpscr.Sz's hooks).
Synthetic machine code. Run: python -W error tests/test_dc_sz_entry.py.
"""
import json
import pathlib
import re
import struct
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
from core import sz_entry  # noqa: E402
from sh4translate.translate import Image, Signature, fpscr_sz, translate_function  # noqa: E402
from core.lint import lint  # noqa: E402

BASE = 0x8C010000
FSCHG, RTS, NOP = 0xF3FD, 0x000B, 0x0009
COPY = [0xF248, 0xF52A, RTS, NOP]   # fmov.s @r4,fr2; fmov.s fr2,@r5 (SZ=1: dr2, 8 bytes each)
WORDS = ([FSCHG, 0xB011, NOP, FSCHG, RTS, NOP]                 # 00 A: bsr COPY inside an fschg pair
         + [0xB00C, NOP, RTS, NOP]                              # 0c B: bsr COPY with SZ=0
         + [0xB006, NOP, 0xB00A, NOP, 0xB002, NOP, RTS, NOP]    # 14 C: bsr FLIP; bsr COPY2; bsr FLIP
         + [RTS, FSCHG]                                         # 24 FLIP: rts; fschg
         + COPY + COPY                                          # 28 COPY, 30 COPY2
         + [0x2448, 0x8900, FSCHG, 0xB003, NOP, RTS, NOP, NOP]  # 38 D: tst; bt over fschg; bsr COPY3
         + COPY)                                                # 48 COPY3
IMG = struct.pack(f"<{len(WORDS)}H", *WORDS)
A, B, C, FLIP, COPY1, COPY2, D, COPY3 = (BASE + o for o in (0x00, 0x0C, 0x14, 0x24, 0x28, 0x30, 0x38, 0x48))
BODIES = {A: [(A, B)], B: [(B, C)], C: [(C, FLIP)], FLIP: [(FLIP, COPY1)], COPY1: [(COPY1, COPY2)],
          COPY2: [(COPY2, D)], D: [(D, COPY3)], COPY3: [(COPY3, COPY3 + 8)]}


class Analysis(unittest.TestCase):
    def setUp(self):
        self.r = sz_entry.analyse(IMG, BASE, BODIES)

    def test_modes(self):
        m = self.r["modes"]
        self.assertEqual(m[COPY1], [0, 1])      # A calls it with SZ=1, B with SZ=0
        self.assertEqual(m[COPY2], [1])         # after FLIP's `rts; fschg`
        self.assertEqual(m[FLIP], [0, 1])       # C calls it again to flip back
        self.assertEqual(m[A], [0])
        self.assertNotIn(COPY3, m)              # its one call's SZ depends on a branch

    def test_reported(self):
        self.assertEqual(self.r["data"], [(D, D + 6, COPY3)])
        self.assertEqual(self.r["changers"][FLIP], (1, 0))
        self.assertEqual(self.r["flips"], {C: [FLIP]})

    def test_arg(self):
        with tempfile.TemporaryDirectory() as t:
            t = pathlib.Path(t)
            (t / "image.bin").write_bytes(IMG)
            (t / "function_bodies.txt").write_text("".join(f"{e:08x} {lo:08x} {hi:08x}\n" for e, r in BODIES.items()
                                                           for lo, hi in r))
            (t / "target.json").write_text(json.dumps({"load_address": "0x8c010000"}))
            paths = types.SimpleNamespace(FUNCTION_BODIES=t / "function_bodies.txt", STATE=t, TARGET=t, PROJECT={})
            cfg = {"binary_path": str(t / "image.bin"), "binary_sha256": "test"}
            sz_entry._memo.clear()
            self.assertEqual(sz_entry.arg(cfg, paths, f"{COPY1:08x}"), "sz:01")
            self.assertEqual(sz_entry.arg(cfg, paths, f"{COPY2:08x}"), "sz:1")
            self.assertEqual(sz_entry.arg(cfg, paths, f"{C:08x}"), f"sz:0:{FLIP:08x}.10")
            self.assertEqual(sz_entry.arg(cfg, paths, f"{A:08x}"), "-")    # its own fschg pair: no fact needed
            self.assertEqual(sz_entry.arg(cfg, paths, f"{COPY3:08x}"), "-")
            self.assertTrue((t / "fpscr_sz_entry.json").exists())
            sz_entry._memo.clear()


def sig(arg, name="fn_8c010000"):
    entry, exits = fpscr_sz(arg)
    return Signature(name, f"void {name}(u32 a0, u32 a1)", [("a0", "u32", "r4"), ("a1", "u32", "r5")], "void",
                     sz_entry=entry, sz_exits=exits)


class Translator(unittest.TestCase):
    def test_fact(self):
        self.assertEqual(fpscr_sz(None), ((0,), None))
        self.assertEqual(fpscr_sz("sz:01"), ((0, 1), None))
        self.assertEqual(fpscr_sz("sz:0:8c010024.10_8c010030.u1"), ((0,), {FLIP: "10", COPY2: "u1"}))
        meta = {"name": "fn_8c010000", "signature": "void fn_8c010000(void)", "params": [], "ret": "void",
                "fpscr_sz": "sz:1"}
        self.assertEqual(Signature.from_meta(meta).sz_entry, (1,))

    def test_copy_moves_8_bytes_at_sz1(self):
        img = Image(IMG, BASE)
        for sp in (False, True):
            plain = translate_function(img, COPY1, sig(None), sp_input=sp)
            one = translate_function(img, COPY1, sig("sz:1"), sp_input=sp)
            both = translate_function(img, COPY1, sig("sz:01"), sp_input=sp)
            self.assertNotRegex(plain, r"\+ 4u")                       # SZ=0: one word
            self.assertRegex(one, r"fr3 = rdf32\((r4|a0) \+ 4u\);")    # SZ=1: the pair, 8 bytes
            self.assertRegex(one, r"wrf32\((r5|a1) \+ 4u, fr3\);")
            self.assertNotIn("sh4_sz", one)
            self.assertIn("if (sh4_sz())", both)
            self.assertEqual(plain, translate_function(img, COPY1, sig("sz:0"), sp_input=sp))
            # judged in both modes: the judge form passes R5 (its mode bodies are body_sz0/1, not fn_ names)
            self.assertEqual([e for e in lint(both, {"name": "fn_8c010000", "signature": "void fn_8c010000(void)"}) if e.startswith("R5")], [])

    def test_callee_returns_with_sz_changed(self):
        # 00 sts.l pr,@-r15; bsr 10; nop; the copy's fmov pair; lds.l @r15+,pr; rts; nop | 10 rts; fschg
        img = Image(struct.pack("<10H", 0x4F22, 0xB005, NOP, 0xF248, 0xF52A, 0x4F26, RTS, NOP, RTS, FSCHG), BASE)
        flip = BASE + 0x10
        kept = translate_function(img, BASE, sig(None), entries={BASE, flip}, sp_input=True)
        flipped = translate_function(img, BASE, sig(f"sz:0:{flip:08x}.10"), entries={BASE, flip}, sp_input=True)
        self.assertNotIn("fr3", kept)
        self.assertRegex(flipped, r"fr3 = rdf32\((r4|a0) \+ 4u\);")
        self.assertIn("if (!sh4_sz()) sh4_fschg();", flipped)


class EntryMode(unittest.TestCase):
    def test_runners_take_line_z(self):
        for p in ("targets/dreamcast_sh4/runtime/runner.c", "targets/dreamcast_sh4/runtime_rd/rd_runner.c"):
            src = (ROOT / p).read_text(encoding="utf-8")
            self.assertIn("} else if (line[0] == 'z') {", src, p)
            self.assertIn("if (strtoul(line + 1, NULL, 16) & 0x00100000u) sh4_fschg();", src, p)
            self.assertLess(src.index("tcrt_set_fpscr_state("), src.index("line[0] == 'z') {"), p)   # after line N

    def test_xemu_and_oracle_hooks(self):
        xe = (ROOT / "targets/dreamcast_sh4/xemu/xe_main.c").read_text(encoding="utf-8")
        self.assertIn("(sh4_sz() ? XE_FPSCR_SZ : 0u)", xe)
        self.assertIn("strncmp(ln, \"szx \", 4)", xe)
        gs = ROOT / "targets/dreamcast_sh4/ghidra_scripts"
        self.assertIn("TcFpscr.Sz.enter(emu);", (gs / "TcOracle.java").read_text(encoding="utf-8"))
        self.assertIn("TcFpscr.Sz.call(emu, pc);", (gs / "TcOracle.java").read_text(encoding="utf-8"))
        self.assertIn("TcFpscr.Sz.draw(id);", (gs / "TcVectors.java").read_text(encoding="utf-8"))
        self.assertIn(".append(TcFpscr.Sz.line())", (gs / "TcVectors.java").read_text(encoding="utf-8"))
        self.assertIn("|| TcFpscr.Sz.wide()", (gs / "TcSig.java").read_text(encoding="utf-8"))
        self.assertIn('"\\nz "', (gs / "TcFpscr.java").read_text(encoding="utf-8"))   # line z: Z is TcHw's GBR line
        self.assertIn("TcFpscr.Sz.parse(a.length > 13 ? a[13] : \"-\")", (gs / "TcPrepare.java").read_text(encoding="utf-8"))
        sys.path.insert(0, str(ROOT / "tools"))
        import xemu_check
        meta = {"addr": "8c010014", "blocks": [], "fpscr_sz": f"sz:0:{FLIP:08x}.1u"}
        self.assertIn(f"szx {FLIP:08x} 19\n", xemu_check.config_text(meta, 0x00040001))
        self.assertNotIn("szx", xemu_check.config_text({"addr": "8c010014", "blocks": []}, 0x00040001))


if __name__ == "__main__":
    unittest.main()

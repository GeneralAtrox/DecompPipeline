"""The original's own frame in what an agent sees (core/readable_stack.frame_lo, core/frame_offsets.py).

One pass and one fail control per change:
1. the stack window is bounded: compare's [STACK_LO, SP) is 0xe000 bytes and Spawn keeps globals in it (save data at
   0x8cff9000), which raw.c's notes and context.md called "a local of yours" and lean projection left out. A frame
   address is one from frame_lo() (entry r15 less "readable_frame_max") up that no global names;
2. a struct local the original passes by address (`call_gen(.., rr + 0x24u, ..)`) shows the members raw.c fills
   through frame literals (`wrf32(rr + 0x34u, ..)`: member 0x10), which no literal holds (Spawn S07 8c0160a0);
3. a global raw.c names in an address mirror's form (`0x0cfe0320u`, Spawn's P0 work RAM) is a used global, and one
   whose declared extent holds an oracle global however deep (PS2 g_fighters + 0x380c).
Synthetic data. Run: python -W error tests/test_stack_window.py (also from test_tooling.py).
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import (design_requests, frame_offsets, lean_select, readable, readable_headers,  # noqa: E402
                  readable_lean, readable_stack)
from core.compare import SP  # noqa: E402

RAW = "    x = rd32(0x8cff9044u);\n    wr32(0x8cffdf00u, x);\n"
META = {"addr": "8c000100", "signature": "void fn_8c000100(void)", "params": [], "callees": [], "globals": []}


def reg_with(functions=None, globals_=None):
    reg = readable.Registry.empty({"functions": functions or {}, "globals": globals_ or {}})
    reg.typedefs = {}
    return reg


class Window(unittest.TestCase):
    def test_bounded(self):
        """A global below the own frame (0x8cff9044) is no stack address; the frame's own (0x8cffdf00) still is."""
        self.assertEqual(readable_stack.frame_lo(), SP - readable_stack.FRAME_MAX)
        self.assertEqual(readable_stack.constants(RAW), [0x8CFFDF00])
        got = readable_stack.annotate_raw(RAW, reg_with(), "8c000100", META).splitlines()
        self.assertEqual(got[0], RAW.splitlines()[0])  # no "own frame ... (a local)" note on the global
        self.assertIn("own frame", got[1])  # control: the local keeps its note
        lines = "\n".join(readable_stack.context_lines(reg_with(), "8c000100", META, RAW))
        self.assertNotIn("0x8cff9044", lines)
        self.assertIn("0x8cffdf00", lines)

    def test_named_global(self):
        """An address the oracle's globals or the registry name is a global even inside the frame window."""
        meta = dict(META, globals=["8cffdf00"])
        self.assertEqual(readable_stack.context_lines(reg_with(), "8c000100", meta, RAW), [])
        self.assertEqual(readable_stack.annotate_raw(RAW, reg_with(), "8c000100", meta), RAW)
        reg = reg_with(globals_={"8cffdf00": {"decl": "u32 g_frameWord;"}})
        self.assertEqual(readable_stack.constants(RAW, readable_stack.known(reg, META)), [])
        self.assertEqual(readable_stack.constants(RAW, readable_stack.known(reg_with(), META)), [0x8CFFDF00])

    def test_frame_max(self):
        """target.json or project.json "readable_frame_max" sets the bound (hex text or a number)."""
        with mock.patch("core.paths.config", return_value={"readable_frame_max": "0xf0"}):
            self.assertEqual(readable_stack.frame_lo(), SP - 0xf0)
            self.assertEqual(readable_stack.constants(RAW), [])  # 0x8cffdf00 is SP - 0x100: below it
        with mock.patch("core.paths.config", return_value={"readable_frame_max": 0x200}):
            self.assertEqual(readable_stack.constants(RAW), [0x8CFFDF00])

    def test_lean_global(self):
        """A declared global's extent in the window (0x8cff9000, 0x100 bytes) is used; a frame literal is not."""
        reg = reg_with(globals_={"8cff9000": {"decl": "SaveData g_saveData;"}})
        ext = [(0x8CFF9000, 0x100), (0x8CFFDE00, 0x200)]
        got = readable_lean.used_globals(reg, META, raw_text=RAW, ext=ext)
        self.assertIn("8cff9000", got)
        self.assertNotIn("8cffde00", got)  # control: 0x8cffdf00 is the own frame

    def test_mirror_literal(self):
        """A global raw.c writes through its mirror form (`wr32(0x0cfe0320u + 4u * i, ..)`, Spawn 8c011520's
        g_overlayServiceTables, which the oracle's list misses) is used: Haiku wrote `&g_mainModeState + 0x20`."""
        reg = reg_with(globals_={"8cfe0320": {"decl": "u32 g_tables[8];"}})
        raw = "    wr32(0x0cfe0320u + 4u * i, 0u);\n"
        with mock.patch("core.retire.mirrors", return_value={"0c": "8c"}):
            self.assertIn("8cfe0320", readable_lean.used_globals(reg, META, raw_text=raw))
        with mock.patch("core.retire.mirrors", return_value={}):  # control: no mirror, no global
            self.assertNotIn("8cfe0320", readable_lean.used_globals(reg, META, raw_text=raw))

    def test_deep_global(self):
        """An oracle global deep inside a declared global's extent (g_fighters + 0x380c) declares that global."""
        reg = reg_with(globals_={"8c010000": {"decl": "u8 g_big[0x8000];"}})
        meta = dict(META, globals=["8c01380c"])
        self.assertIn("8c010000", readable_lean.used_globals(reg, meta, ext=[(0x8C010000, 0x8000)]))
        self.assertNotIn("8c010000", readable_lean.used_globals(reg, meta))  # control: no extents, 0x1000 rule

    def test_requests(self):
        """A design request's missing name in the window below the frame is kept; a frame address is not."""
        got = design_requests.parse("MISSING GLOBALS: 0x8cff9000\nMISSING GLOBALS: 0x8cffdf00\n")
        self.assertEqual([g[1] for g in got if g[0] == "name"], ["8cff9000"])


FRAME_RAW = ("    e = 0x8cffe000u;\n    b = e - 0x88u;\n    rr = b - 4u;\n    wr32(rr, 0u);\n"
             "    wr32(rr + 0x24u, t);\n    wrf32(rr + 0x34u, 1.25f);\n    wr32(b + 4u * i, 0u);\n"
             "    (void)call_gen(0x8c057010u, rr + 0x24u, 0u, 0u, 0u, fr4, fr5, rr);\n")


class FrameLocals(unittest.TestCase):
    def test_offsets(self):
        """rr + 0x24 escapes (a struct local passed by address): rr + 0x34 is its member 0x10."""
        self.assertEqual(frame_offsets.offsets(FRAME_RAW), {0, 1, 2, 3, 0x10, 0x11, 0x12, 0x13})  # 32-bit stores
        no_escape = FRAME_RAW.replace("rr + 0x24u, 0u", "0u, 0u")
        self.assertEqual(frame_offsets.offsets(no_escape), set())  # control: nothing passed, no base
        forgot = FRAME_RAW.replace("    wr32(rr + 0x24u", "    rr = rd32(0x8c100000u);\n    wr32(rr + 0x24u")
        self.assertEqual(frame_offsets.offsets(forgot), set())  # control: rr no longer holds a frame address

    def test_constant_bases(self):
        """A frame variable set to the entry r15 less K in one line, and a frame constant plus a literal, place the
        struct local too: BUS_BACKUPFILEHEADER's saveData at +0x58 (PS2 8c03b968, 8c03ce26 wrote `&header + 0x58`)."""
        one_line = ("    u32 frame = 0x8cffe000u - 0x68u;\n    a.r[0] = frame + 4u;\n"
                    "    src = rd32(frame + 0x5cu);\n")
        summed = "    a.r[0] = 0x8cffdf98u + 0x4u;\n    r6 = rd32(0x8cffdf98u + 0x5cu);\n"
        for raw in (one_line, summed):
            self.assertEqual(frame_offsets.offsets(raw), {0, 0x58, 0x59, 0x5a, 0x5b})
        self.assertEqual(frame_offsets.offsets(summed.replace("+ 0x4u", "* 0x4u")), set())  # control: no escape
        self.assertEqual(frame_offsets.offsets(one_line.replace("0x8cffe000u", "0x8c100000u")), set())  # control

    def test_member_shown(self):
        hdr = "".join(f"    f32 m{i:02x};\n" for i in range(0, 0x80, 4))
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(f'#include "rd_rt.h"\ntypedef struct Desc {{\n{hdr}}} Desc;\n', encoding="utf-8")
        hs = readable_headers.Headers(d).finish()
        decl, lits = "void Fill(Desc *out);", readable_lean.int_literals(FRAME_RAW)

        def shown(extra):
            return lean_select.select(hs, decl, (), lits, (), (), readable_lean.added_offsets(FRAME_RAW) | extra)[
                0].shown(hs.by_name["Desc"])
        self.assertIn("f32 m10;", shown(frame_offsets.offsets(FRAME_RAW)))
        self.assertNotIn("f32 m10;", shown(set()))  # control: literals and added offsets alone miss 0x10

    def test_lean_set_wiring(self):
        """lean_set hands the frame offsets to the projection."""
        seen = {}

        def select(hs, text, names, lits, uses, fields, offsets, group_max):
            seen["offsets"] = offsets
            return {}, set()
        with mock.patch.object(readable_lean, "project_headers", return_value=mock.Mock()), \
                mock.patch.object(lean_select, "select", side_effect=select), \
                mock.patch.object(readable_lean, "notes_for", return_value=[]):
            readable_lean.lean_set(reg_with(), ["void Fill(Desc *out);"], FRAME_RAW)
        self.assertIn(0x10, seen["offsets"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

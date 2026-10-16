"""Batch 25: a smaller lean sandbox (core/lean_layout, core/lean_select, core/lean_groups, core/lean_proof).

One pass and one fail control per change:
1. struct layouts as the judge's compiler computes them (a bitfield: no layout, the struct is shown whole);
2. a big struct is projected onto the members raw.c's offsets reach (and the readable file names), padding keeps
   every offset; without raw.c's literals nothing is projected;
3. the static-assert proof passes for a projection and fails for one with wrong padding;
4. a struct further than DEPTH pointer hops is only declared, unless the readable file names a member;
5. the comment rounds read only the shown text: a dropped member's comment pulls no constant group in;
6. an accessor group is shown in part (plus the macros built on a kept one);
7. an address inside a declared global is an offset into its struct;
8. the types are shown once (context.md): the sandbox types.h is a stub, TASK.md no longer lists it.
Synthetic headers. Run: python -W error tests/test_batch25_lean.py (also from test_tooling.py).
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import lean_groups, lean_layout, lean_proof, lean_select, paths, readable_headers  # noqa: E402

HEADER = """#ifndef T_H
#define T_H

#include "rd_rt.h"

typedef struct Vec { f32 x, y, z; } Vec;

/* Three hops from Big. */
typedef struct Far { u32 a; u32 b; } Far;

/* Two hops. */
typedef struct Mid { Far *far; u32 n; } Mid;

/* One hop. */
typedef struct Near { Mid *mid; u32 k; } Near;

/* Modes. */
#define BIG_MODE_A 1
#define BIG_MODE_B 2

/* Kinds. */
#define BIG_KIND_X 7

typedef struct Big {
    u32 flags;        /* 0x00 */
    u8 tag;           /* 0x04 */
    u16 count;        /* 0x06 */
    Vec pos;          /* 0x08 */
    f32 speed;        /* 0x14 */
    u32 mode;         /* 0x18: BIG_MODE_* */
    u32 kind;         /* 0x1c: BIG_KIND_X */
    Near *near;       /* 0x20 */
    u8 name[6];       /* 0x24 */
    u32 timer;        /* 0x2c */
    s16 hp;           /* 0x30 */
} Big;

typedef struct Bits { u32 a : 3; u32 b : 29; } Bits;

/* Words of a Big's extension, as raw words. */
#define BIG_WORD_A(b) (*(u32 *)((u8 *)(b) + 0x40))
#define BIG_WORD_B(b) (*(u32 *)((u8 *)(b) + 0x44))
#define BIG_WORD_B_PTR(b) (&BIG_WORD_B(b))

#endif
"""
OFFSETS = {"flags": 0, "tag": 4, "count": 6, "pos": 8, "speed": 0x14, "mode": 0x18, "kind": 0x1c, "near": 0x20,
           "name": 0x24, "timer": 0x2c, "hp": 0x30}
DECL = "void Tick(Big *b);"


def include_dir():
    d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
    (d / "types.h").write_text(HEADER, encoding="utf-8")
    return d


class LeanViews(unittest.TestCase):
    def setUp(self):
        self.dir = include_dir()
        self.hs = readable_headers.Headers(self.dir).finish()

    def views(self, evidence, fields=()):
        return lean_select.select(self.hs, DECL, (), evidence, (), fields)[0]

    def item(self, name):
        return self.hs.by_name[name]

    def test_layout(self):
        lay = lean_layout.layout(self.hs, self.item("Big"))
        self.assertEqual({m.names[0]: m.offset for m in lay.members}, OFFSETS)
        self.assertEqual((lay.size, lay.align), (0x34, 4))
        self.assertIsNone(lean_layout.layout(self.hs, self.item("Bits")))  # control: a bitfield is not sized

    def test_projection(self):
        shown = self.views({0x1c, 0x20}).shown(self.item("Big"))
        for kept in ("flags", "kind", "near", "u8 _pad_0x4[0x18];", "u8 _pad_0x24[0x10];"):
            self.assertIn(kept, shown)
        self.assertNotIn("speed", shown)
        self.assertIn("speed", self.views({0x1c, 0x20}, fields={"speed"}).shown(self.item("Big")))  # a redo's names
        self.assertEqual(self.views(None).shown(self.item("Big")), self.item("Big").text)  # control: no raw.c

    def test_embedded_member(self):
        v = self.views({0x10})  # inside pos (0x08..0x14): pos is kept, and Vec's offset 0x8 is reached
        self.assertIn("Vec pos;", v.shown(self.item("Big")))
        self.assertIn(8, v.offs[self.item("Vec")])
        self.assertNotIn("Vec pos;", self.views({0x30}).shown(self.item("Big")))  # control

    def test_proof(self):
        v, cfg = self.views({0x1c, 0x20}), paths.config()
        self.assertEqual(len(lean_proof.projections(v)), 1)
        self.assertEqual(lean_proof.prove(cfg, v, self.dir / "ok", self.dir), [])
        real = lean_layout.pad_line
        with mock.patch.object(lean_layout, "pad_line", lambda off, n: real(off, n + 4)):  # wrong padding
            msgs = lean_proof.prove(cfg, v, self.dir / "bad", self.dir)
        self.assertTrue(msgs and any("Big" in m for m in msgs), msgs)

    def test_declared_only(self):
        v = self.views({0x20})  # Big (0) -> Near (1) -> Mid (2) -> Far (3): only declared
        self.assertTrue(v.shown(self.item("Far")).startswith("typedef struct Far Far;"))
        self.assertIn("members not shown", v.shown(self.item("Far")))
        self.assertIn("u32 n;", v.shown(self.item("Mid")))  # control: two hops are shown
        self.assertIn("u32 b;", self.views({0x20}, fields={"b"}).shown(self.item("Far")))  # the readable file's name

    def test_rounds_read_shown_text(self):
        items = lean_select.select(self.hs, DECL, (), {0x1c}, ())[1]
        self.assertIn(self.item("BIG_KIND_X"), items)  # kind is shown: its comment's constant
        self.assertNotIn(self.item("BIG_MODE_A"), items)  # mode is dropped: its comment pulls nothing in
        self.assertIn(self.item("BIG_MODE_A"), lean_select.select(self.hs, DECL, (), {0x18}, ())[1])  # control

    def test_accessor_part(self):
        group = self.item("BIG_WORD_A")
        self.assertEqual(lean_groups.accessor_names(self.hs, group, {0x44}), {"BIG_WORD_B", "BIG_WORD_B_PTR"})
        self.assertIn("1 more macro(s)", lean_groups.part(group, {"BIG_WORD_B", "BIG_WORD_B_PTR"}))
        self.assertEqual(lean_groups.accessor_names(self.hs, group, {0x48}), set())  # control: no offset reached
        self.assertEqual(lean_groups.accessor_names(self.hs, group, {0x48}, {"BIG_WORD_A"}), {"BIG_WORD_A"})

    def test_types_shown_once(self):
        from core import readable_sandbox
        self.assertNotIn("typedef", readable_sandbox.TYPES_STUB)  # the sandbox's types.h holds no types
        self.assertNotIn("`types.h`", readable_sandbox.TASK)  # and TASK.md no longer points at it
        self.assertIn("`game.h`", readable_sandbox.TASK)  # control: game.h is still listed
        self.assertIn("_pad_", readable_sandbox.TASK)  # the padding is explained

    def test_global_offsets(self):
        self.assertEqual(lean_select.global_offsets(["8c0a0000"], {0x8c0a01b0, 5}), {0x1b0})
        self.assertEqual(lean_select.global_offsets(["8c0a0000"], {0x8c1a0000, 0x8c09fff0}), set())  # control


if __name__ == "__main__":
    unittest.main(verbosity=1)

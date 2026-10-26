"""Batch 25: a smaller lean sandbox (core/lean_layout, core/lean_select, core/lean_groups, core/lean_proof).

One pass and one fail control per change:
1. struct layouts as the judge's compiler computes them (a bitfield: no layout, the struct is shown whole);
2. a big struct is projected onto the members raw.c's offsets reach (and the readable file names), padding keeps
   every offset; without raw.c's literals nothing is projected;
3. the static-assert proof passes for a projection and fails for one with wrong padding;
4. a struct further than DEPTH pointer hops is only declared, unless the readable file names a member;
5. the comment rounds read only the shown text: a dropped member's comment pulls no constant group in;
6. an accessor group is shown in part (plus the macros built on a kept one);
7. an address inside a declared global (or in a mirror of it, target.json address_mirrors) is an offset into its
   struct;
8. the types are shown once (context.md): the sandbox types.h is a stub, TASK.md no longer lists it.
9. an offset raw.c adds (`+ 0x1`) reaches a projected struct's member, 1 included (int_literals drops it).
10. an offset raw.c loads from the pool into a register and steps (`r0 = r0 + 4u`) reaches its member (reg_offsets).
11. a derived base (`r4 = r5 + 0x0eu; wr16(r4 + 4u, ..)`: member 0x12) reaches its member, from a pointer or from a
    global's constant address, and a pool-loaded offset in a named local (`off_ac`) too (reg_offsets.reached).
12. an rd/wr covers its width (two word stores clear eight u8 members), and literals added in a row reach their
    sum (`base + 0x12c + 0x1c`).
13. a derived base plus a literal taken as a value (`wr32(sp + 4u, r8 + 0x1cu)`) reaches its member.
14. a struct a header macro lays over other bytes at +K (`((Track *)((u8 *)(e) + 0x68))`, core/lean_overlay) shows
    the members at raw.c's offsets less K; also through a macro naming a generic macro's type, or a member's address.
15. a sum of a pool word or a derived base, an index and a literal (`addr = rd16(pool) + r14 + r13`) reaches the
    indexed array member (GameObj.parts[i]); a literal first (`r14 = 0x38f8u + A`) is a derived base too.
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
        mirror = {"0c": "8c"}  # target.json address_mirrors: the P0 form of a global's address is the global
        self.assertEqual(lean_select.global_offsets(["8c3623c8"], {0x0c3623ca}, mirror), {2})
        self.assertEqual(lean_select.global_offsets(["8c3623c8"], {0x0c3623ca}), set())  # control: no mirrors given
        self.assertEqual(lean_select.global_offsets(["8c3623c8"], {0x1c3623ca}, mirror), set())  # control: no mirror

    def test_member_at_one(self):
        """raw.c's `+ 0x1` reaches a big struct's member at offset 1 (int_literals drops 1: it was hidden)."""
        from core import readable_lean as rl
        hdr = "".join(f"    u8 b{i};\n" for i in range(12))
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(f'#include "rd_rt.h"\ntypedef struct Line {{\n{hdr}}} Line;\n', encoding="utf-8")
        hs = readable_headers.Headers(d).finish()
        raw = "wr8(param_1 + 0x0, 1u); wr8(param_1 + 0x1, 2u); wr8(param_1 + 0xb, 3u);"
        self.assertEqual(rl.added_offsets(raw), {0, 1, 0xb})
        decl, lits = "void Get(Line *out);", rl.int_literals(raw)
        shown = lean_select.select(hs, decl, (), lits, (), (), rl.added_offsets(raw))[0].shown(hs.by_name["Line"])
        self.assertIn("u8 b1;", shown)
        self.assertNotIn("u8 b5;", shown)  # still a projection: an offset raw.c never adds stays hidden
        shown = lean_select.select(hs, decl, (), lits, (), ())[0].shown(hs.by_name["Line"])
        self.assertNotIn("u8 b1;", shown)  # control: the literals alone miss it

    def test_register_offsets(self):
        """A pool-loaded offset stepped through r0 (`r0 = 5u; wr8(p + r0, ..); r0 = r0 + 2u;`) or read from the pool
        word (`rd16(0x8c010040u)`) reaches its member (PS2 S04 8c02ae40 wrote `(u8 *)cache + 0x1024`)."""
        from core import readable_lean as rl, reg_offsets
        hdr = "".join(f"    u8 b{i};\n" for i in range(12))
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(f'#include "rd_rt.h"\ntypedef struct Line {{\n{hdr}}} Line;\n', encoding="utf-8")
        hs = readable_headers.Headers(d).finish()
        raw = ("    r0 = 5u;\n    wr8(param_1 + r0, 3u);\n    r0 = r0 + 2u;\n    wr8(r0 + param_1, 4u);\n"
               "    r0 = r0 - 3u;\n    r3 = (u32)(s32)(s16)rd16(0x8c010040u);\n    wr8(param_1 + r3, 5u);\n"
               "    r0 = param_2;\n    wr8(param_1 + r0, 6u);\n")
        image = bytearray(0x100)
        image[0x40:0x42] = (9).to_bytes(2, "little")  # the mov.w pool word: offset 9
        self.assertEqual(reg_offsets.offsets(raw, bytes(image)), {5, 7, 9})  # r0 = 4 is never added; param_2 forgets
        self.assertEqual(reg_offsets.offsets(raw), {5, 7})  # control: no image, no pool word
        decl, lits = "void Set(Line *out);", rl.int_literals(raw)

        def shown(extra):
            return lean_select.select(hs, decl, (), lits, (), (), rl.added_offsets(raw) | extra)[0].shown(
                hs.by_name["Line"])
        with_regs = shown(reg_offsets.offsets(raw, bytes(image)))
        self.assertIn("u8 b7;", with_regs)
        self.assertIn("u8 b9;", with_regs)
        self.assertNotIn("u8 b8;", with_regs)  # still a projection
        self.assertNotIn("u8 b7;", shown(set()))  # control: literals and added offsets alone miss 7 and 9
        self.assertNotIn("u8 b9;", shown(set()))


    def test_derived_base(self):
        """`r4 = base + K` then rd/wr at r4 + d is member K + d: of what a pointer points at, or of the global at a
        constant address (PS2 S03 8c05ef00 wrote `(u8 *)&g_flow + 0x12`)."""
        from core import readable_lean as rl, reg_offsets
        hdr = "".join(f"    u16 h{i:02x};\n" for i in range(0, 0x80, 2))
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(f'#include "rd_rt.h"\ntypedef struct Flow {{\n{hdr}}} Flow;\n', encoding="utf-8")
        hs = readable_headers.Headers(d).finish()
        raw = ("    u32 r5 = 0x0c010100u;\n    u32 r4;\n    r4 = r5 + 0x0eu;\n    wr16(r4 + 4u, 0u);\n"
               "    p = param_1 + 0x20u;\n    wr16(p + 6u, 0u);\n    q = p + 8u;\n    wr16(q, 0u);\n"
               "    off_ac = 0x30u;\n    wr16(param_1 + off_ac, 1u);\n")
        offs, addrs = reg_offsets.reached(raw)
        self.assertEqual(offs, {4, 5, 6, 7, 0x26, 0x27, 0x28, 0x29, 0x30, 0x31})  # derived (chained), named local
        self.assertEqual(addrs, {0x0C010112, 0x0C010113})  # from the constant base: g_flow + 0x12 (a u16: 2 bytes)
        forgot = raw.replace("    wr16(r4 + 4u", "    r4 = rd32(0x8c100000u);\n    wr16(r4 + 4u")
        self.assertEqual({a for a in reg_offsets.reached(forgot)[1] if a >> 24 == 0x0C}, set())  # control: r4 forgot
        steps = "    u32 r2 = 0x0c010100u;\n    wr32(r2, 0u);\n    r2 += 8u;\n    wr32(r2, 0u);\n    r2 -= 4u;\n"
        self.assertEqual(reg_offsets.reached(steps + "    wr32(r2, 0u);\n")[1], set(range(0x0C010100, 0x0C01010C)))
        forgot = reg_offsets.reached(steps.replace("+= 8u", "= rd32(0x8c100000u)"))[1]
        self.assertEqual({a for a in forgot if a >> 24 == 0x0C}, set(range(0x0C010100, 0x0C010104)))  # control
        stepped = "    p = param_1 + 8u;\n    p += 4u;\n    wr8(p + 1u, 0u);\n"
        self.assertEqual(reg_offsets.reached(stepped)[0], {1, 0xd})
        mirror = {"0c": "8c"}
        self.assertEqual(lean_select.global_offsets(["8c010100"], addrs, mirror), {0x12, 0x13})
        decl, lits = "void Set(Flow *out);", rl.int_literals(raw)

        def shown(extra):
            return lean_select.select(hs, decl, (), lits, (), (), rl.added_offsets(raw) | extra)[0].shown(
                hs.by_name["Flow"])
        got = shown(offs | lean_select.global_offsets(["8c010100"], addrs, mirror))
        for member in ("u16 h12;", "u16 h26;", "u16 h28;"):
            self.assertIn(member, got)
        self.assertNotIn("u16 h12;", shown(set()))  # control: literals and added offsets alone miss them
        self.assertNotIn("u16 h26;", shown(set()))
        self.assertNotIn("u16 h28;", shown(set()))

    def test_derived_wiring(self):
        """lean_set hands a derived base's address to global_offsets (the global's member offset reaches select)."""
        from core import readable, readable_lean as rl
        seen = {}

        def select(hs, text, names, lits, uses, fields, offsets, group_max):
            seen["offsets"] = offsets
            return {}, set()
        raw = "    u32 r5 = 0x0c010100u;\n    r4 = r5 + 0x0eu;\n    wr16(r4 + 4u, 0u);\n"
        with mock.patch.object(rl, "project_headers", return_value=mock.Mock()), \
                mock.patch.object(lean_select, "select", side_effect=select), \
                mock.patch.object(rl, "notes_for", return_value=[]), \
                mock.patch("core.retire.mirrors", return_value={"0c": "8c"}):
            rl.lean_set(readable.Registry.empty({}), ["void Set(Flow *out);"], raw, global_addrs=["8c010100"])
        self.assertIn(0x12, seen["offsets"])

    def test_wide_store(self):
        """Two word stores clear bytes 0..7: the u8 members at 1-3 and 5-7 are shown, not padding (Spawn S06's
        GameFlow, where Haiku wrote `*((u8 *)flow + 0x6)`)."""
        from core import readable_lean as rl, reg_offsets
        hdr = "".join(f"    u8 b{i:02x};\n" for i in range(0x40))
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(f'#include "rd_rt.h"\ntypedef struct Flow {{\n{hdr}}} Flow;\n', encoding="utf-8")
        hs = readable_headers.Headers(d).finish()
        raw = "    wr32(param_1, 0u);\n    wr32(param_1 + 4u, 0u);\n    x = rd16(param_1 + 0x10u);\n"
        offs = reg_offsets.reached(raw)[0]
        self.assertEqual(offs, {0, 1, 2, 3, 4, 5, 6, 7, 0x10, 0x11})
        self.assertEqual(reg_offsets.reached(raw.replace("wr32", "wr8").replace("rd16", "rd8"))[0], {0, 4, 0x10})
        decl, lits = "void Clear(Flow *out);", rl.int_literals(raw)

        def shown(extra):
            return lean_select.select(hs, decl, (), lits, (), (), rl.added_offsets(raw) | extra)[0].shown(
                hs.by_name["Flow"])
        got = shown(offs)
        for member in ("u8 b05;", "u8 b06;", "u8 b07;", "u8 b11;"):
            self.assertIn(member, got)
        self.assertNotIn("u8 b08;", got)  # still a projection
        self.assertNotIn("u8 b06;", shown(set()))  # control: the literals alone show only 0, 4 and 0x10

    def test_chained_sum(self):
        """`wrf32(base + 0x12c + 0x1c, ..)` reaches +0x148 (TC 8c03c954), which no single literal holds."""
        from core import readable_lean as rl, reg_offsets
        raw = "    base = (u32)param_r4;\n    wrf32(base + 0x12c + 0x1c, fr4);\n"
        self.assertIn(0x148, reg_offsets.reached(raw)[0])
        self.assertNotIn(0x1c, reg_offsets.reached(raw)[0])  # the chain's last literal alone is no member here
        self.assertNotIn(0x148, rl.added_offsets(raw) | rl.int_literals(raw))  # control: literals one at a time
        self.assertNotIn(0x148, reg_offsets.reached(raw.replace("+ 0x1c", "* 0x1c"))[0])  # control: no sum

    def test_derived_value(self):
        """`r8 = r14 + 0xa8u` then `r8 + 0x1cu` stored as a value is GameObj.parts at 0xc4 (PS2 8c02e14e wrote
        `(u8 *)obj + 0xc4`): an address taken, which no rd/wr reads through."""
        from core import readable_lean as rl, reg_offsets
        hdr = "".join(f"    u32 m{i:03x};\n" for i in range(0, 0x100, 4))
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(f'#include "rd_rt.h"\ntypedef struct Obj {{\n{hdr}}} Obj;\n', encoding="utf-8")
        hs = readable_headers.Headers(d).finish()
        raw = "    r14 = rd32(0x8c100000u);\n    r8 = r14 + 0xa8u;\n    wr32(sp + 4u, r8 + 0x1cu);\n"
        offs = reg_offsets.reached(raw)[0]
        self.assertIn(0xc4, offs)
        self.assertNotIn(0xc4, reg_offsets.reached(raw.replace("r14 + 0xa8u", "rd32(r14)"))[0])  # control: no base
        defined = raw.replace("wr32(sp + 4u, r8 + 0x1cu)", "r9 = r8 + 0x1cu")
        self.assertNotIn(0xc4, reg_offsets.reached(defined)[0])  # control: a definition is no address taken
        decl, lits = "void Draw(Obj *obj);", rl.int_literals(raw)

        def shown(extra):
            return lean_select.select(hs, decl, (), lits, (), (), rl.added_offsets(raw) | extra)[0].shown(
                hs.by_name["Obj"])
        self.assertIn("u32 m0c4;", shown(offs))
        self.assertNotIn("u32 m0c4;", shown(set()))  # control: the literals alone (0xa8, 0x1c, 4) miss it

    def test_overlay(self):
        """raw.c's 0xa8 is the overlay's member 0x40 (PS2 S13/S19: Haiku wrote magic offsets for the hidden ones)."""
        from core import lean_overlay
        hdr = "".join(f"    u32 m{i:03x};\n" for i in range(0, 0x80, 4))

        def shown(macro):
            d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
            (d / "types.h").write_text(f'#include "rd_rt.h"\ntypedef struct Track {{\n{hdr}}} Track;\n{macro}\n',
                                       encoding="utf-8")
            hs = readable_headers.Headers(d).finish()
            return lean_select.select(hs, "void Update(Track *t);", (), {0xa8}, (), (), set())[0].shown(
                hs.by_name["Track"])
        self.assertIn("u32 m040;", shown("#define TRACK(e) ((Track *)((u8 *)(e) + 0x68))"))
        self.assertNotIn("u32 m040;", shown("#define TRACK(e) ((Track *)(e))"))  # control: no offset, no shift
        self.assertNotIn("u32 m040;", shown("#define TRACK(e) ((Track *)((Track *)(e) + 0x68))"))  # control: no u8
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(
            '#include "rd_rt.h"\ntypedef struct Obj { u32 a[4]; u8 work[8]; } Obj;\n'
            "typedef struct Music { u8 m; } Music;\ntypedef struct Mob { u8 m; } Mob;\n"
            "typedef struct Lone { u8 m; } Lone;\n#define WORK_OFFSET 0x41c\n"
            "#define OBJ_WORK(obj, T) ((T *)((u8 *)(obj) + WORK_OFFSET))\n"
            "#define MUSIC(obj) OBJ_WORK(obj, Music)\n#define MOB(obj) ((Mob *)&(obj)->work)\n", encoding="utf-8")
        hs = readable_headers.Headers(d).finish()
        got = {n: lean_overlay.bases(hs).get(hs.by_name[n].order) for n in ("Music", "Mob", "Lone")}
        self.assertEqual(got, {"Music": {0x41c}, "Mob": {0x10}, "Lone": None})  # control: Lone is no overlay

    def test_indexed_member(self):
        """A pool word plus a pointer plus an index (`addr = (u32)(s32)(s16)rd16(..) + r14 + r13; rd32(addr)`) is
        GameObj.parts[i] at 0xc4 (PS2 8c14b3b4 wrote `(u8 *)spark + 0xc4 + i * 4`); so is a derived base plus an index
        plus a literal (`r4 = r11 + r14 + 0x8u`, Spawn 8c03bae0), and a sum with the literal first (`0x38f8u + A`)."""
        from core import reg_offsets
        raw = ("    r14 = rd32(0x8c100000u);\n    r13 = 0u;\n"
               "    u32 addr = (u32)(s32)(s16)rd16(0x8c010040u) + r14 + r13;\n    v = rd32(addr);\n")
        image = bytearray(0x100)
        image[0x40:0x42] = (0xc4).to_bytes(2, "little")
        self.assertIn(0xc4, reg_offsets.offsets(raw, bytes(image)))
        self.assertNotIn(0xc4, reg_offsets.offsets(raw))  # control: no image, no pool word
        idx = "    r14 = param_1 + 0x14u;\n    r4 = r11 + r14 + 0x8u;\n    wr8(r4, 0u);\n"
        self.assertIn(0x1c, reg_offsets.offsets(idx))
        self.assertNotIn(0x1c, reg_offsets.offsets(idx.replace("r11 + r14", "r11 + r12")))  # control: no derived base
        two = idx.replace("    r4 =", "    r11 = param_2 + 4u;\n    r4 =")
        self.assertNotIn(0x1c, reg_offsets.offsets(two))  # control: two derived bases, no one base
        rev = "    r14 = 0x38f8u + param_1;\n    x = rdf32(r14 + 0x1cu);\n"
        self.assertIn(0x3914, reg_offsets.offsets(rev))
        self.assertNotIn(0x3914, reg_offsets.offsets(rev.replace("0x38f8u +", "0x38f8u *")))  # control: no sum


if __name__ == "__main__":
    unittest.main(verbosity=1)

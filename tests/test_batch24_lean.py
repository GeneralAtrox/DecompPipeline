"""Batch 24: complete lean sandbox headers (core/readable_lean, core/lean_uses, core/readable_headers).

One pass and one fail control per class of the 64 TC files that did not build against their lean headers:
1. a constant, type or macro the readable file uses is selected (and a constant whose value only it has, when raw.c
   writes that value);
2. a global or function the readable file uses is declared;
3. what a selected item needs is selected too, transitively (a global's type, a prototype's types);
4. the header parser keeps a declaration's /* comment that closes on a later line with it.
Synthetic headers and a fake registry. Run: python -W error tests/test_batch24_lean.py (also from test_tooling.py).
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import lean_uses, paths, readable_headers, readable_lean  # noqa: E402

HEADER = """#ifndef T_H
#define T_H

typedef struct Slot { int n; } Slot;

typedef struct Foo { Slot *slot; } Foo;

/* Kamui's structures. */
typedef struct KMSURF KMSURF;      /* a surface (a
                                    * SpriteTexture holds 0x20 bytes of it) */
typedef struct KMVB KMVB;          /* a vertex buffer */
struct KMVB { KMSURF *surf; };

typedef struct Widget { KMVB *vb; } Widget;

/* An angle step. */
#define ANGLE_STEP 0x123

/* The font's space. */
#define FONT_SPACE_CODE 0x20

/* Bytes per vertex. */
#define PVR_VERTEX_SIZE 0x20u

/* A node's data word. */
#define MESH_DATA_38(n) (*(u8 **)((u8 *)(n) + 0x38))

/* A light. */
typedef struct Light { int on; } Light;

/* Modes. */
enum { MODE_A = 0x30, MODE_B, MODE_C };

/* A flag. */
#define FLAG_X (1u << 6)

#endif
"""
FILE = """#include "game.h"

/* Uses g_never in a comment only: "g_never" in a string too. */
void Tick(Foo *f)
{
    Light *light = (Light *)MESH_DATA_38(f);
    const char *s = g_strNull;
    RigidStepFn step = Physics_Step;
    (void)s; (void)step;
    light->on = FONT_SPACE_CODE + g_widget.vb != 0;
}
"""

USES_KM = "void Tick(Foo *f) { KMSURF *s = 0; (void)f; (void)s; (void)g_widget; }"


class Reg:
    isolated = False
    functions = {"8c010000": {"proto": "void Tick(Foo *f)"}, "8c010100": {"proto": "s32 Physics_Step(Slot *s)"},
                 "8c010200": {"proto": "void Unused(Light *l)"}}
    globals = {"8c0a0000": {"decl": "Widget g_widget"}, "8c0a0100": {"decl": "const char g_strNull[4]"},
               "8c0a0200": {"decl": "s32 g_never"}}

    def proto(self, a, meta=None, raw_sig=None):
        return self.functions[a]["proto"]


def parse(text):
    d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
    (d / "types.h").write_text(text, encoding="utf-8")
    return readable_headers.Headers(d).finish()


def old_decl_end(lines, i):
    """The batch 23 declaration end: the ';' line, even when its comment goes on."""
    j, depth = i, 0
    while j < len(lines):
        code = readable_headers.split_comments(lines[j])[0]
        depth += code.count("{") - code.count("}")
        if depth <= 0 and ";" in code:
            break
        j += 1
    return j


class LeanUses(unittest.TestCase):
    def setUp(self):
        self.hs = parse(HEADER)
        self.reg = Reg()

    def lean(self, raw_text="", readable_text=None, hs=None):
        with mock.patch.object(readable_lean, "project_headers", lambda reg: hs or self.hs), \
                mock.patch.object(readable_lean, "own_pool", lambda meta: set()):
            return readable_lean.headers_for(self.reg, {"globals": []}, ["8c010000"], {}, raw_text,
                                             readable_text=readable_text)

    def code_names(self, text):
        return set(lean_uses.IDENT.findall(readable_headers.split_comments(text)[0]))

    # class 1: constants, types and macros the readable file uses
    def test_file_names_selected(self):
        got = self.code_names(self.lean(readable_text=FILE)["types.h"])
        self.assertTrue({"Light", "MESH_DATA_38", "FONT_SPACE_CODE"} <= got, got)
        self.assertNotIn("PVR_VERTEX_SIZE", got)  # same value, but the file does not use it

    def test_control_without_the_file(self):
        got = self.code_names(self.lean()["types.h"])
        self.assertFalse({"Light", "MESH_DATA_38", "FONT_SPACE_CODE"} & got, got)

    def test_unique_value_selected(self):
        got = self.lean(raw_text="r5 = 0x123; r6 = 0x20;")
        self.assertIn("ANGLE_STEP", self.code_names(got["types.h"]))  # only ANGLE_STEP is 0x123
        self.assertNotIn("FONT_SPACE_CODE", self.code_names(got["types.h"]))  # two constants are 0x20: listed
        self.assertIn("FONT_SPACE_CODE = 0x20", got["index.txt"])
        self.assertIn("PVR_VERTEX_SIZE = 0x20u", got["index.txt"])
        self.assertNotIn("ANGLE_STEP", self.code_names(self.lean(raw_text="r5 = 0x124;")["types.h"]))  # control

    def test_values_counted_and_evaluated(self):
        vals = lean_uses.values(self.hs)
        self.assertEqual((vals["MODE_B"][0], vals["MODE_C"][0], vals["FLAG_X"][0]), (0x31, 0x32, 0x40))
        self.assertNotIn("MESH_DATA_38", vals)  # a function-like macro has no value
        index = readable_lean.index_txt(self.hs, set(), {0x40, 0x31})
        self.assertIn("FLAG_X = (1u << 6) (= 0x40)", index)
        self.assertIn("MODE_B = 0x31", index)
        with mock.patch.object(lean_uses, "named", lambda *a: {}):  # control: literals in the value text only
            index = readable_lean.index_txt(self.hs, set(), {0x40, 0x31})
        self.assertNotIn("FLAG_X", index)
        self.assertNotIn("MODE_B", index)

    # class 2: globals and functions the readable file uses
    def test_file_globals_and_functions_declared(self):
        got = self.lean(readable_text=FILE)
        self.assertEqual(got["globals"], {"8c0a0000", "8c0a0100"})  # not g_never: comment and string only
        self.assertEqual(got["functions"], ["8c010100"])  # Physics_Step, used as data
        self.assertIn("s32 Physics_Step(Slot *s);", got["decl"])
        self.assertIn("Slot", self.code_names(got["types.h"]))  # its parameter type

    def test_control_globals_without_the_file(self):
        got = self.lean()
        self.assertEqual((got["globals"], got["functions"]), (set(), []))

    # class 3: dependency closure
    def test_closure_of_a_global_type(self):
        got = self.lean(readable_text="void Tick(Foo *f) { (void)f; (void)g_widget; }")
        self.assertIn("extern Widget g_widget;", got["decl"])
        self.assertTrue({"Widget", "KMVB", "KMSURF", "Foo", "Slot"} <= self.code_names(got["types.h"]))

    # class 4: the parser
    def test_declaration_comment_on_two_lines(self):
        self.assertIn("KMVB", self.hs.by_name)
        self.assertIn("SpriteTexture holds", self.hs.by_name["KMSURF"].text)
        types_h = self.lean(readable_text=USES_KM)["types.h"]
        self.assertEqual(types_h.count("/*"), types_h.count("*/"))  # no comment left open (C1071)

    def test_control_old_parser(self):
        with mock.patch.object(readable_headers, "_decl_end", old_decl_end):
            hs = parse(HEADER)
        self.assertNotIn("KMVB", hs.by_name)
        types_h = self.lean(readable_text=USES_KM, hs=hs)["types.h"]
        self.assertGreater(types_h.count("/*"), types_h.count("*/"))

    def test_unique_counts_published_whole(self):
        """verify's worker threads share the parsed headers: the value counts appear only when complete (a half-built
        table gave TC 8c03e8be and 8c03f9de `KeyError: 128` in a parallel run)."""
        seen = []
        real = lean_uses.values

        def values(hs):
            seen.append(hasattr(hs, "_lean_count"))
            return real(hs)
        with mock.patch.object(lean_uses, "values", values):
            lean_uses.unique(self.hs, {0x123})
        self.assertFalse(seen[0])  # not published while it is counted
        self.assertTrue(hasattr(self.hs, "_lean_count"))

    def test_current_text(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "a.c").write_text("int a;", encoding="utf-8")
        self.assertEqual(readable_lean.current_text(d / "a.c", None, str(d / "a.c"), d / "missing.c"), "int a;")


class Reaped(unittest.TestCase):
    def test_killed_command_is_reaped(self):
        made = []
        real = subprocess.Popen

        def popen(*a, **k):
            made.append(real(*a, **k))
            return made[-1]
        with mock.patch.object(subprocess, "Popen", popen):
            rc, _, reason = paths.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], 1)
        self.assertEqual((rc, reason), (None, "timeout"))
        self.assertIsNotNone(made[0].returncode)  # waited for after the kill: no ResourceWarning later


if __name__ == "__main__":
    unittest.main()

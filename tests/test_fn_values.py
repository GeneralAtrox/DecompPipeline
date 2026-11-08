"""port/tools/fn_values.py: a function name readable C uses as a value is its guest address in the port, as in the
judge (whose game.h defines a callee as its guest address). Found 2026-11-06: Power Stone 2's VmuMsg_Create stored
OBJ_UPDATE_MIRROR(VmuMsg_Update) as 0x1b227153, the host body masked, where the original stores 0x0c03dc90.

Text checks: which uses are values (rewritten) and which are calls, members, comments or strings (kept); header macros
(redefined, aliases, notes); the files gen_port compiles. The C check builds a port-shaped program (game.h with the
host prototypes, fn_values.h last) in 32-bit MSVC at /W4 /WX and runs it: the stored words must be the original's
(0x0c03dc90 masked, 0x8c...... raw, a table entry, a header macro's) while every call stays a direct host call.
Controls: the unrewritten file (the port before this change) and a rewrite that also takes a call must each fail it.
Skips the C build when no 32-bit MSVC is here (VCVARS32, or vcvars32.bat next to config.local.json's vcvars).
Run: python -m unittest tests.test_fn_values
Nearest existing: tests/test_autoload.py - the same way of building a kit file with a C test.
"""
import pathlib
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "port" / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
import fn_values  # noqa: E402
import game_config  # noqa: E402
from core.readable import fn_pointer_type  # noqa: E402

NAMES = {"8c03dc90": "VmuMsg_Update", "8c03dd60": "VmuMsg_Create", "8c010000": "Obj_Tick"}
PROTOS = {"8c03dc90": "void VmuMsg_Update(GameObj *o);", "8c03dd60": "void VmuMsg_Create(GameObj *obj);",
          "8c010000": "s32 Obj_Tick(s32 n);"}
TYPES_H = """#ifndef TYPES_H
#define TYPES_H
#include <stdint.h>
typedef unsigned int u32;
typedef int s32;
typedef struct GameObj { u32 update, raw, amp, hook, table, alias; } GameObj;
typedef void (*ObjUpdateFn)(GameObj *);
#include "sys/common.h"
#endif
"""
COMMON_H = """/* MIRROR_PTR(VmuMsg_Update) in a comment is not a use */
#define MIRROR_PTR(p) ((void *)((uintptr_t)(p) & 0x1fffffffu))
#define OBJ_UPDATE_MIRROR(fn) ((ObjUpdateFn)MIRROR_PTR(fn))
#define TICK_HOOK ((u32)(uintptr_t)Obj_Tick) /* a header macro taking one's address */
#define TICK_ALIAS Obj_Tick
#define TICK_CALL(n) Obj_Tick(n)
"""
SRC = """#include "game.h"

/* VmuMsg_Update(obj) here is a comment, "VmuMsg_Update" a string. */
static const ObjUpdateFn k_table[1] = {VmuMsg_Update};

void VmuMsg_Create(GameObj *obj)
{
    s32 n = Obj_Tick(1) + (Obj_Tick)(2) + ((s32 (*)(s32))Obj_Tick)(3) + TICK_CALL(4) + TICK_ALIAS(5);
    obj->update = (u32)(uintptr_t)OBJ_UPDATE_MIRROR(VmuMsg_Update);
    obj->raw = (u32)(uintptr_t)VmuMsg_Update;
    obj->amp = n & 1 ? (u32)(uintptr_t)&VmuMsg_Update : 0u;
    obj->hook = TICK_HOOK;
    obj->table = (u32)(uintptr_t)k_table[0];
    obj->alias = (u32)(uintptr_t)TICK_ALIAS;
}
"""
MAIN = r"""#include <stdio.h>
#include "game.h"
static s32 g_ticks;
void VmuMsg_Update(GameObj *o) { (void)o; }
s32 Obj_Tick(s32 n) { g_ticks += n; return n; }
int main(void)
{
    GameObj o = {0};
    VmuMsg_Create(&o);
    printf("update %08x raw %08x amp %08x hook %08x table %08x alias %08x ticks %d\n", o.update, o.raw, o.amp, o.hook,
           o.table, o.alias, g_ticks);
    if (o.update != 0x0c03dc90u || o.raw != 0x8c03dc90u || o.amp != 0x8c03dc90u || o.hook != 0x8c010000u ||
        o.table != 0x8c03dc90u || o.alias != 0x8c010000u || g_ticks != 15) {
        puts("FAIL: a stored function address is not the original's, or a call did not run on the host");
        return 1;
    }
    puts("all ok");
    return 0;
}
"""


def workspace(tmp):
    """A readable workspace (src/8c03dd60.c, include/types.h and sys/common.h) and gen_port's view of it."""
    (tmp / "src").mkdir()
    (tmp / "include" / "sys").mkdir(parents=True)
    (tmp / "src" / "8c03dd60.c").write_text(SRC, encoding="utf-8")
    (tmp / "include" / "types.h").write_text(TYPES_H, encoding="utf-8")
    (tmp / "include" / "sys" / "common.h").write_text(COMMON_H, encoding="utf-8")
    (tmp / "out" / "include").mkdir(parents=True)
    return types.SimpleNamespace(SRC=tmp / "src", INCLUDE=tmp / "include", fn_pointer_type=fn_pointer_type)


def write(path, text):
    path.write_text(text, encoding="utf-8")


def generate(tmp):
    """gen_port's part: fn_values.write, then a port-shaped game.h (prototypes, fn_values.h last)."""
    rd, notes = workspace(tmp), []
    src = fn_values.write(tmp / "out", rd, ["8c03dd60"], PROTOS, NAMES, write, notes)
    game_h = "\n".join(["#ifndef GAME_H", "#define GAME_H", '#include "types.h"', *PROTOS.values(), "#endif", ""])
    write(tmp / "out" / "include" / "game.h", fn_values.tail(game_h))
    return src, notes


def build_run(vcvars, tmp, unit):
    write(tmp / "main.c", MAIN)
    inc = f'/I"{tmp / "out" / "include"}" /I"{tmp / "include"}"'
    bat = tmp / "b.bat"
    write(bat, "\n".join(["@echo off", f'call "{vcvars}" >nul', f'cd /d "{tmp}"',
                          f'cl /nologo /W4 /WX {inc} main.c "{unit}" /Fe:t.exe', "if errorlevel 1 exit /b 99",
                          f'"{tmp}\\t.exe"']) + "\n")
    r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


class Text(unittest.TestCase):
    def spans(self, text, names=("F", "G")):
        return [text[s:e] for s, e, _ in fn_values.value_spans(text, set(names))]

    def test_values(self):
        for text, want in [("x = F;", ["F"]), ("MIRROR_PTR(F)", ["F"]), ("{F, G}", ["F", "G"]), ("p = &F;", ["&F"]),
                           ("(u32)(uintptr_t)&F", ["&F"]), ("f(a, F)", ["F"]), ("p == F ? G : 0", ["F", "G"]),
                           ("OBJ(F)(o)", []), ("x = a && F;", ["F"]), ("x = a&&F;", ["F"]), ("(T)(x) & F", ["& F"])]:
            self.assertEqual(self.spans(text), want, text)

    def test_not_values(self):
        for text in ["F(1);", "void F(int a)\n{", "void F (void);", "(F)(1);", "((T)F)(1);", "o.F = 1;", "o->F = 1;",
                     '"F"', "/* F */", "// F\n", "'F'", "#define F 1", "#undef F", "#ifdef F", "FF = 1;", "F_x = 1;",
                     "F /* c */ (1);"]:
            self.assertEqual(self.spans(text), [], text)

    def test_rewrite(self):
        text, got = fn_values.rewrite("a = F; F(a); b = &G;", {"F": "F", "G": "G"})
        self.assertEqual((text, got), ("a = RDV_F; F(a); b = RDV_G;", {"F", "G"}))
        text, got = fn_values.rewrite("a = ALIAS;", {"ALIAS": "F"})
        self.assertEqual((text, got), ("a = RDV_F;", {"F"}))

    def test_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = workspace(pathlib.Path(tmp))
            write(rd.INCLUDE / "sys" / "common.h", COMMON_H + "#define ENDS_ON (u32)Obj_Tick\nstatic void *k = Obj_Tick;\n")
            write(rd.INCLUDE / "unreached.h", "#define NOT_REACHED Obj_Tick + 0\n")
            lines, used, alias, notes = fn_values.header_macros(rd.INCLUDE, {n: n for n in NAMES.values()})
        self.assertEqual(lines, ["#undef TICK_HOOK", "#define TICK_HOOK ((u32)(uintptr_t)RDV_Obj_Tick)"])
        self.assertEqual((used, alias), ({"Obj_Tick"}, {"TICK_ALIAS": "Obj_Tick"}))
        self.assertEqual(notes, ["sys/common.h: macro ENDS_ON ends on Obj_Tick: its user may call it (not rewritten)",
                                 "sys/common.h: Obj_Tick used as a value outside a macro (not rewritten)"])

    def test_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            write(tmp / "stale.c", "")
            (tmp / "out" / "fnv").mkdir(parents=True)
            (tmp / "stale.c").replace(tmp / "out" / "fnv" / "8c000000.c")
            src, notes = generate(tmp)
            copy = (tmp / "out" / "fnv" / "8c03dd60.c").read_text(encoding="utf-8")
            header = (tmp / "out" / "include" / "fn_values.h").read_text(encoding="utf-8")
            self.assertFalse((tmp / "out" / "fnv" / "8c000000.c").exists(), "a stale copy is kept")
            self.assertEqual(src / "8c03dd60.c", tmp / "out" / "fnv" / "8c03dd60.c")
            self.assertEqual(src / "8c0aaaaa.c", tmp / "src" / "8c0aaaaa.c")
            self.assertEqual(src.as_posix(), (tmp / "src").as_posix())
        self.assertTrue(copy.startswith(f'#line 1 "{(tmp / "src" / "8c03dd60.c").as_posix()}"\n'))
        self.assertEqual(copy.split("\n", 1)[1], SRC.replace("{VmuMsg_Update}", "{RDV_VmuMsg_Update}").replace(
            "OBJ_UPDATE_MIRROR(VmuMsg_Update)", "OBJ_UPDATE_MIRROR(RDV_VmuMsg_Update)").replace(
            "(uintptr_t)VmuMsg_Update;", "(uintptr_t)RDV_VmuMsg_Update;").replace(
            "&VmuMsg_Update", "RDV_VmuMsg_Update").replace("(uintptr_t)TICK_ALIAS;", "(uintptr_t)RDV_Obj_Tick;"))
        self.assertIn("#define RDV_VmuMsg_Update ((void (*)(GameObj *))0x8c03dc90u)", header)
        self.assertIn("#define RDV_Obj_Tick ((s32 (*)(s32))0x8c010000u)", header)
        self.assertNotIn("RDV_VmuMsg_Create", header)
        self.assertEqual(notes[0], "function names used as values (guest addresses, fn_values.py): 5 uses in 1 "
                                   "readable files, 1 header macros, 1 alias macros, 2 functions")

    def test_gen_port_wired(self):
        text = (ROOT / "port" / "tools" / "gen_port.py").read_text(encoding="utf-8")
        self.assertIn("return fn_values.tail(", text)
        self.assertIn("src = fn_values.write(out, readable, files, protos, names, write_if_changed, notes)", text)
        self.assertIn("defines = wrap_defines(src, files", text)
        self.assertIn('{**cfg, "sources": sources}, src, files,', text)
        self.assertIn("no_pch(src, files, defines)", text)
        self.assertEqual(fn_values.tail("#ifndef G\n#endif\n"),
                         '#ifndef G\n#include "fn_values.h"  /* function names used as values: guest addresses '
                         '(fn_values.py) */\n#endif\n')


class Built(unittest.TestCase):
    def setUp(self):
        self.vcvars = game_config.vcvars32()
        if self.vcvars is None:
            self.skipTest("no 32-bit MSVC here")

    def test_port_stores_the_originals_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            generate(tmp)
            rc, out = build_run(self.vcvars, tmp, tmp / "out" / "fnv" / "8c03dd60.c")
        self.assertNotEqual(rc, 99, f"does not build:\n{out}")
        self.assertTrue(rc == 0 and "all ok" in out, out)
        print("\nrewritten:", out.strip().splitlines()[-2])

    def test_controls_fail(self):
        def every_use(text, names):  # a rewrite that also takes a call: Obj_Tick(1) becomes a call to 0x8c010000
            return sorted(spans(text, names) + [
                (m.start(), m.end(), "Obj_Tick") for m in re.finditer(r"\bObj_Tick(?=\(1\))", text)])
        spans = fn_values.value_spans
        for name, unit in [("the unrewritten file (the port before)", None), ("a call rewritten", every_use)]:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = pathlib.Path(tmp)
                with mock.patch.object(fn_values, "value_spans", unit or spans):
                    generate(tmp)
                rc, out = build_run(self.vcvars, tmp, tmp / ("src" if unit is None else "out/fnv") / "8c03dd60.c")
            self.assertNotEqual(rc, 99, f"{name}: does not build:\n{out}")
            self.assertTrue(rc != 0 and "all ok" not in out, f"planted {name} was not caught:\n{out}")
            print(f"\ncaught: {name} (exit {rc & 0xffffffff:#x}) {out.strip().splitlines()[-1] if out.strip() else ''}")


if __name__ == "__main__":
    unittest.main()

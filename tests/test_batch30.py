"""Batch 30 (engine design leftovers): four small tooling fixes, one pass and one fail control each.
1. core/sig_rewrite: a void -> value tail call whose line ends in a comment is rewritten (the comment stays); a call
   that is not the last statement still is not;
2. core/stack_uninit: a frame address passed in a callee's own argument registers (meta "regs", e.g. r0 / r1) escapes
   like one in r4..r7; without the callee record it does not;
3. designer renames: a names entry's "was" old name is found in code and comments (not in a string, not as a member),
   an old name that is some entry's current name is skipped, and sync renames it;
4. sync drops `(void)` in front of a statement call whose callee now returns void, keeps it for a value callee, and
   still refuses a used result of a void callee.
5. lint RR7 (core/readable_lint.void_result_lint): a void-declared function cast to a value-returning type and its
   result used (assigned, returned, the cast pointer kept) is refused; a statement call, a `(void)` call and a
   value-returning callee pass.
6. core/arg_passthrough: an argument register forwarded unwritten to a callee that takes it (context.md note).
Synthetic data. Run: python -W error tests/test_batch30.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import NAMED, OLD, TMP, A, B, SyncCase, put  # noqa: E402  (sets DECOMP_PROJECT when run alone)
from test_batch27 import body, listing  # noqa: E402

from core import paths, raw_names, readable, sh4_asm, sig_rewrite, stack_uninit, sync_run  # noqa: E402
from core import readable_check as rcheck  # noqa: E402
from core.readable_lint import void_result_lint  # noqa: E402
from core.sync_names import Sync, SyncRefused  # noqa: E402


class TailComment(unittest.TestCase):
    def test_trailing_comment_kept(self):
        src = "void fn_8c000200(u32 a)\n{\n    tc_call(0x8c000300u, a);   /* jmp @r2 (tail call) */\n}\n"
        out = sig_rewrite.rewrite(src, "8c000200", "void", "s32")
        self.assertIn("    return (s32)tc_call(0x8c000300u, a);   /* jmp @r2 (tail call) */\n", out)
        self.assertIn("s32 fn_8c000200(u32 a)", out)

    def test_not_last_statement(self):
        src = "void fn_8c000200(u32 a)\n{\n    tc_call(0x8c000300u, a);   /* bsr */\n    a += 1u;\n}\n"
        self.assertIsNone(sig_rewrite.rewrite(src, "8c000200", "void", "s32"))


def r0_frame():
    """add #-8,r15; r0 = the frame address; a call that takes its pointer in r0; then a read of @r15."""
    rows = [("add #-8,r15", ""), ("mov r15,r0", ""), ("bsr 0x8c000300", "call 8c000300"), ("_nop", ""),
            ("mov.l @r15,r1", ""), ("rts", ""), ("_add #8,r15", "")]
    return sh4_asm.parse(listing(body(0x8c001000, rows)))


class CalleeStorage(unittest.TestCase):
    def test_r0_argument_escapes(self):
        res = stack_uninit.scan(r0_frame(), callees=[{"addr": "8c000300", "regs": ["r0", "r1"]}])
        self.assertEqual((res["status"], res["reads"]), ("ok", []))

    def test_without_the_record_it_is_read(self):
        res = stack_uninit.scan(r0_frame())
        self.assertEqual([(r["off"], r["size"]) for r in res["reads"]], [(-8, 4)])
        res = stack_uninit.scan(r0_frame(), callees=[{"addr": "8c000300", "regs": ["r4"]}])
        self.assertEqual(len(res["reads"]), 1)  # r0 is not this callee's argument

    def test_packet_meta(self):
        d = TMP / "b30_packet"
        put(d / "asm.txt", listing(body(0x8c001000, [("add #-8,r15", ""), ("mov r15,r0", ""),
                                                     ("bsr 0x8c000300", "call 8c000300"), ("_nop", ""),
                                                     ("mov.l @r15,r1", ""), ("rts", ""), ("_add #8,r15", "")])))
        put(d / "meta.json", {"callees": [{"addr": "8c000300", "regs": ["r0", "r1"]}]})
        self.assertEqual(stack_uninit.scan_packet(d)["reads"], [])
        self.assertEqual(len(stack_uninit.scan_packet(d, callees=[])["reads"]), 1)


WAS_TEXT = """/* g_oldName counts the runs. */
void f(Obj *o)
{
    g_oldName += 1;
    o->g_oldName = 0;
    puts("g_oldName");
}
"""


class Was(unittest.TestCase):
    def test_find(self):
        named, _ = raw_names.find(WAS_TEXT, {}, {}, {"g_oldName": "g_newName"})
        self.assertEqual(named, {"g_oldName": "g_newName"})
        only = WAS_TEXT.replace("/* g_oldName counts the runs. */\n", "").replace("    g_oldName += 1;\n", "")
        self.assertEqual(raw_names.find(only, {}, {}, {"g_oldName": "g_newName"})[0], {})  # string and member only

    def test_map(self):
        entries = [({"was": ["g_old", "g_taken", "bad name"]}, "g_new"), ({}, "g_taken")]
        self.assertEqual(raw_names.was_map(entries, {"g_new", "g_taken"}), {"g_old": "g_new"})


class SyncWasAndVoid(SyncCase):
    def reg_with(self, b_proto, globs=None):
        named = json.loads(json.dumps(NAMED))
        named["functions"][B]["proto"] = b_proto
        named["globals"] = globs or {}
        put(self.ws / "readable/names/S01.json", named)
        return readable.Registry()

    def sync(self, reg, text, names):
        m = paths.read_json(paths.HARNESS / A / "meta.json")
        return Sync(reg, A, m, lambda c: rcheck.callee_storage(paths.HARNESS / A, c), names, "clean").run(text)

    def test_void_cast_dropped(self):
        reg = self.reg_with("void Vec3_Scale(Vec3 *v, s32 k)")
        text = OLD.replace("return fn_8c010100((u32)(uintptr_t)v, param_r5) + 1u;",
                           "(void)fn_8c010100((u32)(uintptr_t)v, param_r5);\n    return 1u;")
        new = self.sync(reg, text, {f"fn_{B}": "Vec3_Scale", f"fn_{A}": "Thing_Run"})
        self.assertIn("\n    Vec3_Scale(v, count);\n", new)
        self.assertNotIn("(void)Vec3_Scale", new)

    def test_void_cast_kept_or_refused(self):
        reg = self.reg_with("u32 Vec3_Scale(Vec3 *v, s32 k)")
        text = OLD.replace("return fn_8c010100((u32)(uintptr_t)v, param_r5) + 1u;",
                           "(void)fn_8c010100((u32)(uintptr_t)v, param_r5);\n    return 1u;")
        self.assertIn("(void)Vec3_Scale(v, count);", self.sync(reg, text, {f"fn_{B}": "Vec3_Scale"}))
        reg = self.reg_with("void Vec3_Scale(Vec3 *v, s32 k)")
        with self.assertRaises(SyncRefused):  # the result is still used
            self.sync(reg, OLD, {f"fn_{B}": "Vec3_Scale"})

    def test_was_rename(self):
        reg = self.reg_with(NAMED["functions"][B]["proto"],
                            {"8c0a0000": {"decl": "s32 g_newName", "was": ["g_oldName"]}})
        self.assertEqual(sync_run.was_names(reg), {"g_oldName": "g_newName"})
        text = OLD.replace("char name[8];", "char name[8];\n\n    g_oldName = 0; /* g_oldName reset */")
        named, _ = sync_run.needs_sync(reg, *sync_run.registry_names(reg), A, text)
        self.assertEqual(named["g_oldName"], "g_newName")
        new = self.sync(reg, text, {"g_oldName": "g_newName"})
        self.assertIn("    g_newName = 0; /* g_newName reset */", new)
        self.assertNotIn("g_oldName", new)


class VoidResult(unittest.TestCase):
    REG = readable.Registry.empty({"functions": {"8c000400": {"proto": "void Xf_Load(const Xf *xf)"},
                                                 "8c000500": {"proto": "s32 Host_Close(s32 fd)"}}})

    def lint(self, body):
        return void_result_lint('#include "game.h"\nvoid F(Xf *a, Xf *b)\n{\n' + body + "\n}\n", self.REG)

    def test_used_result_refused(self):
        for body in ("    u32 p = ((u32 (*)(const Xf *))Xf_Load)(b);\n    ((void (*)(Xf *))(uintptr_t)p)(a);",
                     "    return ((s32 (*)(Xf *))Xf_Load)(a);",
                     "    s32 (*fn)(void) = (s32 (*)(void))Xf_Load;\n    (void)fn();"):
            errs = self.lint(body)
            self.assertEqual(len(errs), 1, body)
            self.assertTrue(errs[0].startswith("RR7: Xf_Load is declared void"), errs)

    def test_discarded_or_value_callee_passes(self):
        for body in ("    ((f32 (*)(Xf *))Xf_Load)(a);",
                     "    if (a) {\n        (void)((u32 (*)(Xf *))Xf_Load)(a);\n    }",
                     "    Xf_Load(a);\n    Xf_Load(b);",
                     "    return ((s32 (*)(s32))Host_Close)(3);",
                     "    ((void (*)(Xf *))Xf_Load)(a);"):
            self.assertEqual(self.lint(body), [], body)


class PassThrough(unittest.TestCase):
    """core/arg_passthrough: r4 forwarded unwritten to a callee that takes it (a context.md note), not r5 (written
    in the delay slot), not a placeholder parameter, and nothing once the register is written first."""
    ASM = ("; fn_8c000600  entry 8c000600  16 bytes\n"
           "  8c000600  sts.l PR,@-r15\n"
           "  8c000602  mov.l 0x8c000610,r2                  ; = 0x8c000700  (function 8c000700)\n"
           "  8c000604  jsr @r2                              ; call 8c000700\n"
           "  8c000606  _mov.l @r3,r5\n"
           "  8c000608  lds.l @r15+,PR\n"
           "  8c00060a  rts\n"
           "  8c00060c  _nop\n")

    def reg(self, callee):
        return readable.Registry.empty({"functions": {"8c000600": {"proto": "s32 Dir_Load(const char *path)"},
                                                      "8c000700": {"proto": callee}}})

    def test_forwarded(self):
        from core import arg_passthrough
        reg = self.reg("s32 Lib_LoadDir(const char *name, void *rec)")
        self.assertEqual(arg_passthrough.scan(sh4_asm.parse(self.ASM), reg, 0x8c000600), {"r4": ("to", "8c000700")})
        reg = self.reg("s32 Lib_LoadDir(u32 unused_r4, void *rec)")
        self.assertEqual(arg_passthrough.scan(sh4_asm.parse(self.ASM), reg, 0x8c000600), {})
        written = self.ASM.replace("  8c000600  sts.l PR,@-r15\n", "  8c000600  mov #0x0,r4\n")
        reg = self.reg("s32 Lib_LoadDir(const char *name, void *rec)")
        self.assertEqual(arg_passthrough.scan(sh4_asm.parse(written), reg, 0x8c000600), {})


if __name__ == "__main__":
    unittest.main()

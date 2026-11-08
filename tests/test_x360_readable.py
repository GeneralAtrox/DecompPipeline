"""The Xbox 360 readable stage without Ghidra or a workspace: the Xenon placement of a readable prototype (gaps in a raw
signature, param_rN / param_fN, the harness's recorded storage, the stack doublewords), the call trap's letters and
regs codes, the entry thunk (64-bit registers, pointers from the low word, results extended as their type says),
the prototypes of calls through function pointers (expected.txt CA), rd_rt.h's ppc_ helpers as tc_rt.h's, the 64-bit
game.h (core/readable.game_h wide), the shared PowerPC lint with this target's raw API and addresses, the sandbox's
context.md (with the addresses raw.c passes as values), and the readable TASK formatting.
Skipped parts: none (the runtime build and the readable proofs run on a Too Human workspace: docs/proofs/2026-11-06).
usage: python tests/test_x360_readable.py
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360  # noqa: E402

from core import paths, raw_consts, readable  # noqa: E402

import x360_readable  # noqa: E402
import x360_readable_abi as abi  # noqa: E402
import x360_readable_sandbox  # noqa: E402


def reg_of(functions=None, globals_=None):
    reg = readable.Registry.empty({"functions": functions or {}, "globals": globals_ or {}})
    reg.typedefs = {"Actor": "struct", "Count": "u32", "Kind": "enum"}
    return reg


META = {"addr": "82010000", "name": "fn_82010000", "ret": "s32", "signature": "s32 fn_82010000(u64 param_1, f64 param_2)",
        "params": [{"name": "param_1", "kind": "u64", "storage": "r3", "stack_offset": -1},
                   {"name": "param_2", "kind": "f64", "storage": "f1", "stack_offset": -1}],
        "callees": [{"addr": "82020000", "regs": ["f1", "r3", "r6"],
                     "signature": "void fn_82020000(f64 param_f1, u64 param_r3, u64 param_r6)"}],
        "globals": ["82300000"], "ppc_special": []}


class Placement(unittest.TestCase):
    def test_gaps_by_name(self):
        reg = reg_of()
        p = "void fn_82020000(f64 param_f1, u64 param_r3, u64 param_r6)"
        self.assertEqual(abi.place(reg, p), ["f1", "r3", "r6"])
        self.assertEqual(abi.kinds(reg, p), ("dLL", "v", "a03"))

    def test_convention_counts_apart(self):
        reg = reg_of()
        self.assertEqual(abi.place(reg, "u64 F(f64 x, u64 a, f32 y, Actor *p)"), ["f1", "r3", "f2", "r4"])
        self.assertEqual(abi.kinds(reg, "u64 F(f64 x, u64 a, f32 y, Actor *p)"), ("dLfP", "L", "a0b1"))

    def test_stack_doublewords(self):
        reg = reg_of()
        p = "void F(" + ", ".join(f"u64 a{i}" for i in range(10)) + ")"
        self.assertEqual(abi.place(reg, p)[7:], ["r10", "s0", "s1"])
        self.assertEqual(abi.kinds(reg, p)[2], "01234567ST")

    def test_meta_storage_kept_by_name(self):
        reg = reg_of()
        meta = {"params": [{"name": "param_1", "storage": "r5", "stack_offset": -1},
                           {"name": "param_2", "storage": "stack", "stack_offset": 0x58}]}
        self.assertEqual(abi.place(reg, "void F(u32 param_1, u64 param_2, u32 count)", meta), ["r5", "s1", "r6"])

    def test_kinds(self):
        reg = reg_of()
        self.assertEqual(abi.kinds(reg, "void F(u32 a, s32 b, int c, Count d, Kind e, uintptr_t f, u8 g, s16 h)")[0],
                         "IiiIiLCh")
        with self.assertRaises(readable.RegistryError):
            abi.kinds(reg, "void F(Actor a)")

    def test_same_register_twice_refused(self):
        with self.assertRaises(readable.RegistryError):
            abi.place(reg_of(), "void F(u64 a, u64 param_r3)")


class RegisterSig(unittest.TestCase):
    def test_narrow_ints_widen(self):
        self.assertEqual(abi.register_sig("u32 fn_825494c0(s32 param_1, u64 param_2)"),
                         "u64 fn_825494c0(u64 param_1, u64 param_2)")
        self.assertEqual(abi.register_sig("void fn_82020000(f64 param_f1, u8 *param_r3, const s16 param_r6)"),
                         "void fn_82020000(f64 param_f1, u8 * param_r3, u64 param_r6)")
        self.assertEqual(abi.register_sig("f32 fn_82020000(void)"), "f32 fn_82020000(void)")


class Thunk(unittest.TestCase):
    def test_registers_and_result(self):
        reg = reg_of()
        t = abi.thunk_c(reg, "82010000", "s32 Run(Actor *self, f64 speed, u32 n)", None)
        self.assertIn("(Actor *)(uintptr_t)(u32)r[0]", t)
        self.assertIn("(f64)f[0]", t)
        self.assertIn("(u32)(u32)r[1]", t)
        self.assertIn("*out_r3 = (u64)(s64)(s32)Run(", t)
        self.assertIn("rd_set_self((u64)(uintptr_t)&Run, 0x82010000u);", t)
        self.assertIn('_Static_assert(sizeof(Actor *) == 8, "size of Actor *");', t)
        self.assertIn("void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1)", t)

    def test_stack_float_in_gpr_and_float_result(self):
        reg = reg_of()
        meta = {"params": [{"name": "param_9", "storage": "stack", "stack_offset": 0x50},
                           {"name": "x", "storage": "r4", "stack_offset": -1}]}
        t = abi.thunk_c(reg, "82010000", "f32 G(u64 param_9, f64 x)", meta)
        self.assertIn(f"(u64)rd64(0x{0x700FE000 + 0x50:08x}u)", t)
        self.assertIn("(f64)rd_bits_f64(r[1])", t)
        self.assertIn("static f64 rd_bits_f64(u64 x)", t)
        self.assertIn("*out_f1 = (f64)G(", t)

    def test_void_and_u64(self):
        reg = reg_of()
        self.assertIn("    V();", abi.thunk_c(reg, "82010000", "void V(void)"))
        self.assertIn("*out_r3 = (u64)W(", abi.thunk_c(reg, "82010000", "u64 W(void)"))


class PointerProtos(unittest.TestCase):
    def test_ca_lines(self):
        call = [0x82050000] + [0] * 29
        exp = {1: {"calls": [call], "call_args": {0: ["r4", "f1", "r3"]}},
               2: {"calls": [call], "call_args": {0: ["r3"]}}}
        self.assertEqual(abi.pointer_protos(exp), {0x82050000: ("LLd", "?", "01a")})


class Headers(unittest.TestCase):
    def test_rd_rt_has_tc_rt_ppc_helpers(self):
        def helpers(p):  # the "PowerPC instruction helpers" section's ppc_ lines
            text = p.read_text(encoding="utf-8")
            return [ln for ln in text[text.index("/* PowerPC instruction helpers"):].splitlines() if "ppc_" in ln]
        rd = helpers(X360 / "runtime_rd" / "rd_rt.h")
        self.assertGreater(len(rd), 40)
        self.assertEqual(rd, helpers(X360 / "runtime" / "tc_rt.h"))

    def test_game_h_wide(self):
        reg = reg_of({"82020000": {"proto": "void Draw(Actor *a)"}}, {"82300000": {"decl": "be_u32 g_count"}})
        h = readable.game_h(reg, verify_self="82010000", addrs=["82010000"], raw_sigs={"82010000": META["signature"]},
                            wide=True)
        self.assertIn("(uintptr_t)0x82020000u)", h)
        self.assertIn("(*(rdg_g_count_t *)(uintptr_t)0x82300000u)", h)
        self.assertNotIn("(uintptr_t)", readable.game_h(reg, verify_self="82010000", addrs=["82010000"],
                                                        raw_sigs={"82010000": META["signature"]}))


class Lint(unittest.TestCase):
    def test_raw_api_addresses_and_names(self):
        reg = reg_of({"82020000": {"proto": "void Draw(u64 a)"}})
        src = ('#include "game.h"\n/* Runs. */\ns32 Run(u64 a, f64 b)\n{\n    (void)b;\n'
               '    fn_82020000(rd64(0x82001000u) + a);\n    return 0;\n}\n')
        errs = x360_readable.gc.lint_ppc(src, reg, "Run", "s32 Run(u64 a, f64 b)",
                                         x360_readable.image_ranges(b"\0" * 0x10000), {},
                                         raw_api=x360_readable.RAW_API_X360, fn_pat=x360_readable.FN_PAT)
        self.assertTrue(any(e.startswith("RR2: 'rd64'") for e in errs), errs)
        self.assertTrue(any(e.startswith("RR3: guest address 0x82001000") for e in errs), errs)
        self.assertTrue(any(e.startswith("RR4: fn_82020000") for e in errs), errs)
        ok = '#include "game.h"\n/* Runs. */\ns32 Run(u64 a, f64 b)\n{\n    (void)b;\n    Draw(a + 0x7fff0000u);\n    return 0;\n}\n'
        self.assertEqual([e for e in x360_readable.gc.lint_ppc(ok, reg, "Run", "s32 Run(u64 a, f64 b)",
                                                               x360_readable.image_ranges(b"\0" * 0x10000), {},
                                                               raw_api=x360_readable.RAW_API_X360,
                                                               fn_pat=x360_readable.FN_PAT) if e[:3] in ("RR2", "RR3", "RR4")], [])


class LintWide(unittest.TestCase):
    def test_sign_extended_image_address(self):
        ranges = x360_readable.image_ranges(b"\0" * 0x200000)
        errs = x360_readable.lint_wide("u64 F(void) { return 0xffffffff82150af4ull + 0xffffffff7fff0000ull; }", ranges)
        self.assertEqual(len(errs), 1)
        self.assertIn("0x82150af4", errs[0])
        self.assertEqual(x360_readable.lint_wide("/* 0xffffffff82000010 */ u64 F(void) { return 0x82u; }", ranges), [])


class Sandbox(unittest.TestCase):
    def test_context_md(self):
        reg = reg_of()
        md = x360_readable_sandbox.context_md(reg, "82010000", META, types_body="")
        self.assertIn("- param_1 (r3) -> `u64 param_1`", md)
        self.assertIn("- param_2 (f1) -> `f64 param_2`", md)
        self.assertIn("tc_call(0x82020000) -> `void fn_82020000(f64 param_f1, u64 param_r3, u64 param_r6)`", md)
        self.assertIn("r13 is 0x7fff0000", md)

    def test_task_formats(self):
        text = (X360 / "readable_task.md").read_text(encoding="utf-8")
        out = text.format(addr="82010000", name="Run", proto="s32 Run(u64 a)", box="BOX", previous="")
        self.assertIn("Xbox 360", out)
        self.assertNotIn("GameCube", out)


class DataValues(unittest.TestCase):
    """Addresses raw.c passes as values, in the sign-extended form lis leaves: a catalog function with no harness is
    declared with a placeholder signature (x360_readable.data_decls), and context.md names each value."""
    def test_unprepared_callback_and_rdata_value(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "matched").mkdir()
        (d / "harness").mkdir()
        raw = ("void fn_82010000(void) { a.r[0] = 0xffffffff83118888ull; a.r[1] = 0xffffffff8215fbd8ull; "
               "tc_call(0x82020000u, &a); }")
        (d / "matched" / "82010000.c").write_text(raw, encoding="utf-8")
        (d / "catalog.json").write_text(json.dumps({"functions": [{"addr": "83118888"}, {"addr": "82020000"}]}),
                                        encoding="utf-8")
        x360_readable._STARTS.clear()
        self.addCleanup(x360_readable._STARTS.clear)
        with mock.patch.object(paths, "MATCHED", d / "matched"), mock.patch.object(paths, "HARNESS", d / "harness"),                 mock.patch.object(paths, "CATALOG", d / "catalog.json"),                 mock.patch.object(raw_consts, "IMAGE_BASE", 0x82000000):
            data, placeholders = x360_readable.data_decls("82010000", META)
            self.assertEqual((data, placeholders), ([], {"83118888": "void fn_83118888(void)"}))
            sigs, _ = x360_readable_sandbox.sandbox_decls(reg_of(), "82010000", META)
            self.assertEqual(sigs["83118888"], "void fn_83118888(void)")
            reg = reg_of(globals_={"8215fbd8": {"decl": "be_u32 g_table_15fbd8"}})
            image = mock.Mock(read_bytes=lambda: b"\x00" * 0x1200000)
            md = x360_readable_sandbox.context_md(reg, "82010000", META, cfg={"binary_path": image}, raw_text=raw,
                                                  types_body="", decls=sigs, ext=[(0x8215fbd8, 4)])
        self.assertIn("## Addresses raw.c passes or stores as values", md)
        self.assertIn("- 0x83118888: the function `fn_83118888` (use its name as the pointer", md)
        self.assertIn("- 0x8215fbd8: the global `be_u32 g_table_15fbd8`", md)
        self.assertNotIn("0x82020000: the function", md)  # a callee is listed under the calls


class CallSites(unittest.TestCase):
    """x360_readable.callee_sigs: a void callee whose r3 raw C takes is declared with a u64 result (Too Human 82f7d2d0
    and fn_8277b880); a call target the harness does not list is declared from the call site (82981000 calls
    0x829bc1c8, 82a4be58 returns 0x82a48470's r3); game.h and context.md carry both."""
    RAW = ("u32 fn_82010000(u64 param_1, f64 param_2) { u64 r3 = 0; f64 f1 = 0.0; TcArgs a = {0};\n"
           "a.r[0] = param_1; a.r[1] = 0xffffffff8215fb8cull; r3 = tc_call(0x82020000u, &a);\n"
           "a.r[0] = r3; a.r[2] = 0ull; a.f[0] = 0.0; (void)tc_call(0x829bc1c8u, &a);\n"
           "a.f[1] = param_2; f1 = tc_callf(0x82a48470u, &a); r3 = tc_call(rd32((u32)r3), &a);\n"
           "return (u32)tc_call(0x82a50000u, &a); }\n")

    def test_call_site_signatures(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "matched").mkdir()
        (d / "matched" / "82010000.c").write_text(self.RAW, encoding="utf-8")
        with mock.patch.object(paths, "MATCHED", d / "matched"), mock.patch.object(paths, "HARNESS", d / "harness"):
            sigs = x360_readable.callee_sigs("82010000", META)
            decls, _ = x360_readable_sandbox.sandbox_decls(reg_of(), "82010000", META)
            md = x360_readable_sandbox.context_md(reg_of(), "82010000", META, decls=decls)
        self.assertEqual(sigs, {
            "82020000": "u64 fn_82020000(f64 param_f1, u64 param_r3, u64 param_r6)",  # void in the raw signature
            "829bc1c8": "void fn_829bc1c8(u64 param_r3, u64 param_r4, u64 param_r5, f64 param_f1)",
            "82a48470": "f64 fn_82a48470(f64 param_f1, f64 param_f2)",  # only what was set since the call before
            "82a50000": "u64 fn_82a50000(void)"})  # a call's result returned
        self.assertEqual({a: decls[a] for a in sigs}, sigs)
        game_h = readable.game_h(reg_of(), verify_self="82010000", addrs=list(decls), raw_sigs=decls, wide=True)
        self.assertIn("fn_829bc1c8", game_h)
        self.assertIn("u64 fn_82020000(", md)
        self.assertIn("- tc_call(0x829bc1c8) -> `void fn_829bc1c8(u64 param_r3, u64 param_r4, u64 param_r5, f64 "
                      "param_f1)` (outside the prepared functions", md)


if __name__ == "__main__":
    unittest.main()

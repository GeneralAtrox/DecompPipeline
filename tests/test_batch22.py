"""Batch 22: the original's guest stack in what an agent sees and in what the judge compares (core/readable_stack.py:
raw.c's annotated stack addresses, context.md's section, RR6, stored stack addresses paired one to one) and callee
buffers sized past their pointee (core/readable_outptr.py: the names entry's "out", size arguments, trace runs).
Pure functions on synthetic data: no Ghidra, no compiler (the runtime side, rd_trace.c and rd_p4.c's `and 0` store,
is in the tc-rds fixtures of tests/patterns.json). Run: python -W error tests/test_batch22.py (also from
tests/test_tooling.py).
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import readable, readable_outptr, readable_stack  # noqa: E402
from core.readable_outptr import Spec  # noqa: E402

ADDR = "8c058402"
PROTO = "void Pvr_EmitListClip(u32 pcw, s32 x0, s32 y0, s32 x1, s32 y1)"
META = {"addr": ADDR, "signature": "void fn_8c058402(u32 param_1, u32 param_2, u32 param_3, u32 param_4, u32 param_5)",
        "params": [{"name": f"param_{i + 1}", "storage": s, "stack_offset": -1 if s != "stack" else 0}
                   for i, s in enumerate(("r4", "r5", "r6", "r7", "stack"))], "callees": []}
RAW = ("void fn_8c058402(void)\n{\n    u32 r1;\n    /* rd32(0x8cffe004u) in a comment is not a use */\n"
       "    r1 = rd32(0x8cffe000u);\n    wr32(0x8c100000u, r1);\n    r2 = 0x8cffdfacu; r3 = \"0x8cffdf00\";\n}\n")


def reg_with(functions, typedefs=None):
    reg = readable.Registry.empty({"functions": functions})
    reg.typedefs = dict(typedefs or {})
    return reg


class StackNotes(unittest.TestCase):
    def setUp(self):
        self.reg = reg_with({ADDR: {"proto": PROTO}})

    def test_constants_and_params(self):
        self.assertEqual(readable_stack.constants(RAW), [0x8CFFDF00, 0x8CFFDFAC, 0x8CFFE000])  # not the comment's
        self.assertEqual(readable_stack.stack_params(self.reg, ADDR, META), {0: "y1"})

    def test_annotate_raw(self):
        got = readable_stack.annotate_raw(RAW, self.reg, ADDR, META).splitlines()
        self.assertEqual(len(got), len(RAW.splitlines()))  # line numbers unchanged
        self.assertEqual(got[4],
                         "    r1 = rd32(0x8cffe000u);  /* 0x8cffe000 = stack argument 0 = `y1`, not a global */")
        self.assertIn("0x8cffdfac = own frame, entry r15 - 0x54 (a local)", got[6])
        self.assertNotIn("8cffdf00 =", got[6])  # inside a string literal
        self.assertEqual(got[3], RAW.splitlines()[3])  # a comment line is left alone
        self.assertEqual(readable_stack.annotate_raw("x = rd32(0x8c100000u);\n", self.reg, ADDR, META),
                         "x = rd32(0x8c100000u);\n")

    def test_context_lines(self):
        lines = readable_stack.context_lines(self.reg, ADDR, META, RAW)
        self.assertIn("## Stack addresses in raw.c (never globals)", lines)
        text = "\n".join(lines)
        self.assertIn("- 0x8cffe000: stack argument 0 (entry r15 + 0x0): your parameter `y1`", text)
        self.assertIn("- 0x8cffdfac: this function's own stack frame (entry r15 - 0x54)", text)
        self.assertEqual(readable_stack.context_lines(self.reg, ADDR, META, "x = 1;\n"), [])

    def test_rr6(self):
        lint = readable_stack.stack_arg_lint
        good = f"{PROTO}\n{{\n    g_frame->arg_extra = (u32)y1;\n}}\n"
        bad = f"{PROTO}\n{{\n    (void)y1;\n    g_frame->arg_extra = *(u32 *)0x8cffe000u;\n}}\n"
        self.assertEqual(lint(good, RAW, self.reg, ADDR, META, "Pvr_EmitListClip"), [])
        got = lint(bad, RAW, self.reg, ADDR, META, "Pvr_EmitListClip")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("RR6: raw.c reads 0x8cffe000, stack argument 0: that is your parameter `y1`"))
        # raw.c that never reads the slot by its address: nothing to say
        self.assertEqual(lint(bad, "void f(void) { wr32(0x8c100000u, 1u); }", self.reg, ADDR, META,
                              "Pvr_EmitListClip"), [])

    def test_preflight_stub_skips_rr6(self):
        """refine's do-nothing preflight stub writes `(void)y1;`, which RR6 refuses: preflight must check it with
        stub=True, or every function reading a stack argument is `blocked` before an agent starts (8c058402)."""
        import ast
        from core import refine
        stub = refine.stub_c(self.reg, PROTO)
        self.assertTrue(readable_stack.stack_arg_lint(stub, RAW, self.reg, ADDR, META, "Pvr_EmitListClip"))
        tree = ast.parse(pathlib.Path(refine.__file__).read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "preflight")
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "check_readable"]
        self.assertEqual(len(calls), 1)
        self.assertIn(("stub", True), [(k.arg, getattr(k.value, "value", None)) for k in calls[0].keywords])


class PairStores(unittest.TestCase):
    """8c027610 stores &tex (guest 0x8cffdfac) into g_pvrHeaders[2].texture (0x8c0fc508)."""
    W, G, LO, HI = 0x8C0FC508, 0x8CFFDFAC, 0x0019F000, 0x001A0000

    def run_pair(self, h, g2h=None, h2g=None):
        exp = {"writes": {self.W + i: (self.G >> 8 * i) & 0xFF for i in range(4)}}
        act = {"writes": {self.W + i: (h >> 8 * i) & 0xFF for i in range(4)}}
        g2h, h2g = dict(g2h or {}), dict(h2g or {})
        readable_stack.pair_stores(exp, act, (self.LO, self.HI), g2h, h2g, lambda a: 0)
        return act["writes"] == exp["writes"], g2h

    def test_pass_pairs_new_and_known(self):
        self.assertEqual(self.run_pair(0x0019FF10), (True, {self.G: 0x0019FF10}))
        self.assertTrue(self.run_pair(0x0019FF10, {self.G: 0x0019FF10}, {0x0019FF10: self.G})[0])  # &tex passed too

    def test_fail_wrong_local(self):
        # the calls paired &tex with 0x0019ff10; the store holds another local's address
        self.assertFalse(self.run_pair(0x0019FF40, {self.G: 0x0019FF10}, {0x0019FF10: self.G})[0])
        # the host address already stands for another guest slot
        self.assertFalse(self.run_pair(0x0019FF10, {}, {0x0019FF10: 0x8CFFDF00})[0])

    def test_fail_not_a_pointer(self):
        self.assertFalse(self.run_pair(0x00000005)[0])
        self.assertFalse(self.run_pair(0x8C0FC000)[0])  # a guest global address, not the host stack

    def test_initial_bytes(self):
        # the readable code's word equals the initial memory in one byte (that byte is not among its writes)
        h = 0x0019FF10
        exp = {"writes": {self.W + i: (self.G >> 8 * i) & 0xFF for i in range(4)}}
        act = {"writes": {self.W + i: (h >> 8 * i) & 0xFF for i in range(3)}}
        readable_stack.pair_stores(exp, act, (self.LO, self.HI), {}, {}, lambda a: 0x00)
        self.assertEqual(act["writes"], exp["writes"])


def callee(addr, sig, regs):
    return {"addr": addr, "signature": sig, "regs": regs}


class OutSizes(unittest.TestCase):
    TOC, READ, CELL, RAY, CUSTOM = "8c0b3188", "8c04e000", "8c0126ec", "8c012700", "8c012800"
    FUNCS = {TOC: {"proto": "s32 Gd_GetToc(s32 area, u32 *toc)"},
             READ: {"proto": "s32 File_Read(u32 file, void *buf, u32 size)"},
             CELL: {"proto": "void Grid_CellOfPoint(s32 *out_cell, const Vec3 *p)"},
             RAY: {"proto": "s32 Ray_Cast(Vec3 *hit, f32 len)"},
             CUSTOM: {"proto": "void Rec_Fill(u8 *rec, u32 count)", "out": {"rec": "count"}}}
    META = {"addr": "8c049bfc", "callees": [
        callee(TOC, "u32 fn_8c0b3188(void)", []),  # raw signature void: the record compares nothing
        callee(READ, "u32 fn_8c04e000(u32 param_1, u32 param_2, u32 param_3)", ["r4", "r5", "r6"]),
        callee(CELL, "void fn_8c0126ec(u32 param_1, u32 param_2)", ["r4", "r5"]),
        callee(RAY, "u32 fn_8c012700(u32 param_1, u32 param_fr4)", ["r4", "fr4"]),
        callee(CUSTOM, "void fn_8c012800(u32 param_1, u32 param_2)", ["r4", "r5"])]}

    def specs(self, functions=None):
        reg = reg_with(functions or self.FUNCS, {"Vec3": "struct"})
        return {s.addr: s for s in readable_outptr.out_params(reg, self.META, lambda c: None)}

    def test_rules(self):
        s = self.specs()
        self.assertEqual(s[self.TOC], Spec(self.TOC, 2, "u32", "trace", "r5", False))  # scalar: traced, uncompared
        self.assertEqual(s[self.READ], Spec(self.READ, 2, "void", ("arg", 3), "r5", True))  # size argument r6
        self.assertEqual(s[self.CELL], Spec(self.CELL, 1, "s32", "trace", "r4", True))
        self.assertEqual(s[self.RAY], Spec(self.RAY, 1, "Vec3", None, "r4", True))  # a struct: its sizeof
        self.assertEqual(s[self.CUSTOM], Spec(self.CUSTOM, 1, "u8", ("arg", 2), "r4", True))  # names entry "out"
        fixed = dict(self.FUNCS, **{self.CUSTOM: {"proto": "void Rec_Fill(u8 *rec, u32 count)", "out": {"rec": 24}}})
        self.assertEqual(self.specs(fixed)[self.CUSTOM].size, 24)
        none = dict(self.FUNCS, **{self.CUSTOM: {"proto": "void Rec_Fill(u8 *rec, u32 count)", "out": {"rec": 0}}})
        self.assertNotIn(self.CUSTOM, self.specs(none))  # "out": 0 -> nothing delivered

    def test_paired_adds_register(self):
        s = self.specs()[self.TOC]
        stack = {0: {"calls": [[0x8C0B3188, 0, 0x8CFFDE58] + [0] * 30]}}
        keep, judged = readable_outptr.paired(self.META, stack, [s])
        self.assertEqual(keep, [s])
        self.assertEqual(judged["callees"][0]["regs"], ["r5"])
        self.assertEqual(self.META["callees"][0]["regs"], [])  # the meta itself is unchanged
        other = {0: {"calls": [[0x8C0B3188, 0, 0x8CFFDE58] + [0] * 30]},
                 1: {"calls": [[0x8C0B3188, 0, 0x8C100000] + [0] * 30]}}  # a call passes no stack address: dropped
        self.assertEqual(readable_outptr.paired(self.META, other, [s]), ([], self.META))

    def test_trace_and_sizes(self):
        g = 0x8CFFDE58
        exp = {0: {"calls": [[0x8C0B3188, 0, g] + [0] * 30, [0x8C04E000, 0, 0x8CFFDFD8, 10] + [0] * 30]}}
        specs = [Spec(self.TOC, 2, "u32", "trace", "r5", True), Spec(self.READ, 2, "void", ("arg", 3), "r5", True)]
        self.assertEqual(readable_outptr.trace_lines(exp, specs),
                         {0: [f"T 0 8c0b3188 2 {g:08x} {0x8CFFDFD8 - g}"]})  # up to the next object passed
        ext = readable_outptr.parse_ot("V 0\nOT 0 8c0b3188 2 408\nOT bad\nE\n")
        self.assertEqual(ext, {(0, 0, "8c0b3188", 2): 408})
        got = readable_outptr.lines_for(exp, specs, {"u32": 4}.get, ext)
        self.assertEqual(got, {0: [f"O 0 8c0b3188 2 {g:08x} 408", "O 1 8c04e000 2 8cffdfd8 10"]})
        # without a trace: the pointee's sizeof only (the batch 20 judgement)
        self.assertEqual(readable_outptr.lines_for(exp, specs[:1], {"u32": 4}.get), {0: [f"O 0 8c0b3188 2 {g:08x} 4"]})
        # an extent past MAX_OUT delivers nothing
        big = {(0, 0, "8c0b3188", 2): readable_outptr.MAX_OUT + 4}
        self.assertEqual(readable_outptr.lines_for(exp, specs[:1], {"u32": 4}.get, big), {})


if __name__ == "__main__":
    unittest.main()

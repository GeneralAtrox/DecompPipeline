"""Batch 27: raw return-type overrides and their candidates, functions referenced only as data, and stack words read
before any write (core/sig_override, core/sig_rewrite, core/ret_use, core/data_funcs, core/stack_uninit).

One pass and one fail control each:
1. an override of a catalog function is valid; one with no function there, or a bad kind, is refused; void is refused
   while a call site reads the result;
2. apply_meta rewrites ret and signature, keeps the first raw ret, and is undone by applying it back;
3. the matched C follows (f32 -> void, void -> s32 tail call, f32 -> s32); a body it cannot rewrite gives None;
4. candidates: an f32 no site reads (void) and a void tail call to a value-returning function; an f32 a site reads,
   and an f32 whose address is taken, are not;
5. a frame word written on one path but not the other is read uninitialized; written on both paths it is not;
   pin_text adds the pin once;
6. a pool word used as a value outside every body is a data-function candidate; one used as an address, inside a
   body or odd is not; an address TcEntryCheck calls invalid (or data, or unchecked) is refused by --add.
Synthetic data. Run: python -W error tests/test_batch27.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import data_funcs, ret_use, sh4_asm, sig_override, sig_rewrite, stack_uninit  # noqa: E402

UNUSED = {"sites": 3, "unknown": 0, "r0": 0, "fr0": 0, "address_taken": False}


def listing(rows):
    """asm.txt text from (addr, insn, note) rows."""
    return "; fn\n" + "".join(f"  {a:08x}  {i:<36}" + (f" ; {n}" if n else "") + "\n" for a, i, n in rows)


def body(start, insns):
    return [(start + 2 * k, i, n) for k, (i, n) in enumerate(insns)]


class Override(unittest.TestCase):
    def test_validate(self):
        cat = {"8c000100"}
        meta = {"ret": "f32", "signature": "f32 fn_8c000100(void)"}
        self.assertIsNone(sig_override.validate("8c000100", "s32", cat, meta, lambda: UNUSED))
        self.assertIsNone(sig_override.validate("8c000100", "void", cat, meta, lambda: UNUSED))
        self.assertIn("no function", sig_override.validate("8c000102", "s32", cat, None, lambda: UNUSED))
        self.assertIn("not one of", sig_override.validate("8c000100", "f64", cat, meta, lambda: UNUSED))
        read = dict(UNUSED, r0=1)
        self.assertIn("do not show", sig_override.validate("8c000100", "void", cat, meta, lambda: read))

    def test_apply_meta(self):
        meta = {"ret": "f32", "ret_reg": "fr0", "signature": "f32 fn_8c07b738(s32 param_3)"}
        self.assertTrue(sig_override.apply_meta(meta, "s32"))
        self.assertEqual((meta["ret"], meta["signature"]), ("s32", "s32 fn_8c07b738(s32 param_3)"))
        self.assertEqual(meta["ret_override"], {"from": "f32", "to": "s32"})
        self.assertNotIn("ret_reg", meta)
        self.assertFalse(sig_override.apply_meta(meta, "s32"))
        self.assertTrue(sig_override.apply_meta(meta, "f32"))
        self.assertNotIn("ret_override", meta)
        with self.assertRaises(ValueError):
            sig_override.apply_meta(meta, "double")

    def test_names(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "S1.json"
            p.write_text(json.dumps({"functions": {"8c000100": {"proto": "void A(void)", "raw_ret": "void"},
                                                   "8c000200": {"proto": "s32 B(void)"}}}), encoding="utf-8")
            self.assertEqual(sig_override.overrides(td), {"8c000100": ("void", "S1.json")})
        self.assertIsNone(sig_override.readable_mismatch("const char *", "u32"))
        self.assertIsNotNone(sig_override.readable_mismatch("f32", "s32"))


class Rewrite(unittest.TestCase):
    def test_rewrites(self):
        src = ("static f32 u2f(u32 x) { return 0.0f; }\n"
               "f32 fn_8c000100(u32 a)\n{\n    f32 fr0 = 2.0f;\n    return fr0;\n}\n")
        out = sig_rewrite.rewrite(src, "8c000100", "f32", "void")
        self.assertIn("void fn_8c000100(u32 a)", out)
        self.assertIn("    return;\n", out)
        self.assertIn("return 0.0f;", out)  # helpers keep their returns
        tail = "void fn_8c000200(u32 a)\n{\n    (void)tc_call(0x8c000300u, a);\n}\n"
        self.assertIn("return (s32)tc_call(0x8c000300u, a);", sig_rewrite.rewrite(tail, "8c000200", "void", "s32"))
        regs = "f32 fn_8c000400(void)\n{\n    u32 r0 = 1u;\n    f32 fr0 = 0.0f;\n    return fr0;\n}\n"
        self.assertIn("return (s32)r0;", sig_rewrite.rewrite(regs, "8c000400", "f32", "s32"))

    def test_cannot(self):
        mid = "void fn_8c000200(u32 a)\n{\n    tc_call(0x8c000300u, a);\n    a += 1u;\n}\n"
        self.assertIsNone(sig_rewrite.rewrite(mid, "8c000200", "void", "s32"))  # the call is not the last statement
        self.assertIsNone(sig_rewrite.rewrite("f32 fn_8c000400(void)\n{\n    return 1.0f;\n}\n", "8c000400", "f32",
                                              "s32"))
        self.assertIsNone(sig_rewrite.rewrite("void other(void) {}\n", "8c000100", "f32", "void"))


def workspace(root, funcs):
    """funcs: {addr: (ret, rows)}."""
    for a, (ret, rows) in funcs.items():
        h, p = root / "harness" / a, root / "packets" / a
        h.mkdir(parents=True)
        p.mkdir(parents=True)
        (h / "meta.json").write_text(json.dumps({"ret": ret, "signature": f"{ret} fn_{a}(void)"}), encoding="utf-8")
        (p / "asm.txt").write_text(listing(rows), encoding="utf-8")


RTS = [("rts", ""), ("_nop", "")]


class Candidates(unittest.TestCase):
    def test_cases(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            workspace(root, {
                "8c000100": ("f32", body(0x8c000100, [("fldi1 fr0", "")] + RTS)),  # unread f32
                "8c000200": ("f32", body(0x8c000200, [("fldi1 fr0", "")] + RTS)),  # read f32
                "8c000300": ("f32", body(0x8c000300, [("fldi1 fr0", "")] + RTS)),  # address taken
                "8c000400": ("s32", body(0x8c000400, [("mov #0x1,r0", "")] + RTS)),
                "8c000500": ("void", body(0x8c000500, [("bsr 0x8c000100", "call 8c000100"), ("_nop", ""),
                                                       ("bsr 0x8c000200", "call 8c000200"), ("_nop", ""),
                                                       ("fmov fr0,fr4", ""), ("bra 0x8c000400", "call 8c000400"),
                                                       ("_nop", "")])),
                "8c000600": ("void", body(0x8c000600, [("mov.l 0x8c000700,r1", "= 0x8c000300  (function 8c000300)"),
                                                       ("mov.l 0x8c000704,r2", "= 0x8c000300  (function 8c000300)"),
                                                       ("bsr 0x8c000300", "call 8c000300"), ("_nop", "")] + RTS)),
            })
            got = {c["addr"]: c for c in ret_use.Project(root).candidates()}
        self.assertEqual(sorted(got), ["8c000100", "8c000500"])
        self.assertEqual((got["8c000100"]["case"], got["8c000100"]["suggest"]), ("f32_unread", "void"))
        self.assertEqual((got["8c000500"]["case"], got["8c000500"]["suggest"]), ("tail", "s32"))


def frame(write_both):
    """add #-8,r15; a write of @r15 on one path (or before the branch); then a read of @r15."""
    pre = [("add #-8,r15", ""), ("mov #0x1,r1", "")] + ([("mov.l r1,@r15", "")] if write_both else [])
    rows = pre + [("tst r4,r4", ""), ("bt 0x8c00100c" if write_both else "bt 0x8c00100a", ""),
                  ("mov.l r1,@r15", ""), ("mov.l @r15,r0", ""), ("rts", ""), ("_add #8,r15", "")]
    return sh4_asm.parse(listing(body(0x8c001000, rows)))


class StackUninit(unittest.TestCase):
    def test_one_path(self):
        res = stack_uninit.scan(frame(False))
        self.assertEqual(res["status"], "ok")
        self.assertEqual([(r["off"], r["size"]) for r in res["reads"]], [(-8, 4)])
        self.assertEqual(stack_uninit.words(res), [stack_uninit.ENTRY_SP - 8])

    def test_both_paths(self):
        res = stack_uninit.scan(frame(True))
        self.assertEqual((res["status"], res["reads"]), ("ok", []))

    def test_pin(self):
        w = 0x8cffdfd0
        text = stack_uninit.pin_text("seed 1\n", [w])
        self.assertTrue(text.startswith("seed 1\n" + stack_uninit.PIN_COMMENT + "\n"))
        self.assertEqual(stack_uninit.pinned(text), {w})
        self.assertIsNone(stack_uninit.pin_text(text, [w]))
        meta = {"uninit_stack": {"status": "ok", "reads": [{"at": "8c080f70", "addr": "8cffdfd0", "off": -48,
                                                            "size": 4}]}}
        self.assertIn("pins it to 0", "\n".join(stack_uninit.context_lines(meta, text)))
        self.assertIn("not pinned", "\n".join(stack_uninit.context_lines(meta, "")))


class DataFuncs(unittest.TestCase):
    def test_candidates(self):
        g = "(global {})"
        rows = [("mov.l 0x8c000080,r3", "= 0x8c0811fe  " + g.format("8c0811fe")), ("mov.l r3,@r2", ""),
                ("mov.l 0x8c000084,r4", "= 0x8c081300  " + g.format("8c081300")), ("mov.l @r4,r0", ""),
                ("mov.l 0x8c000088,r5", "= 0x8c000010  " + g.format("8c000010")), ("mov.l r5,@r2", ""),
                ("mov.l 0x8c00008c,r6", "= 0x8c081401  " + g.format("8c081401")), ("mov.l r6,@r2", ""),
                ("mov.l 0x8c000090,r7", "= 0xac081500  " + g.format("ac081500")), ("mov.l r7,@r2", "")] + RTS
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "8c000000"
            p.mkdir()
            (p / "asm.txt").write_text(listing(body(0x8c000000, rows)), encoding="utf-8")
            got = data_funcs.candidates(pathlib.Path(td), 0x8c000000, 0x8c100000, [(0x8c000000, 0x8c00007f)],
                                        {0x8c000000})
        # value-used, even, outside every body (P2 form normalized); not the address use, the body, the odd one
        self.assertEqual(sorted(got), ["8c0811fe", "8c081500"])
        self.assertEqual(got["8c0811fe"], [("8c000000", "8c000000")])

    def test_refused(self):
        v = {"8c0811fe": ("code", "mov.l r13,@-r15 (prologue)"), "8c09f660": ("invalid", "add #0x25,r3"),
             "8c09f740": ("data", "string"), "8c060fe0": ("valid", "fldi1 fr4")}
        self.assertEqual(data_funcs.refused(v, ["8c0811fe", "8c060fe0"]), [])
        self.assertEqual([a for a, _ in data_funcs.refused(v, ["8c0811fe", "8c09f660", "8c09f740", "8c0a0000"])],
                         ["8c09f660", "8c09f740", "8c0a0000"])


if __name__ == "__main__":
    unittest.main()

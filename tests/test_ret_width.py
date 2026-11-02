"""A function's callers use a result register its signature does not name (docs/PROOF_GATES.md section 3): TC 8c093e94
Lib_MathPow was void while powf read its fr0, the judge compared nothing, and a mission ran at half speed. Each listing is
a small synthetic copy of the shape it names; the numbers on the real games are in docs/proofs/ret-width.md.
Run: python -W error tests/test_ret_width.py."""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "targets" / "gamecube_ppc"))
from core import compare, ret_reg, ret_use  # noqa: E402
import float_gaps  # noqa: E402
import ppc_compare  # noqa: E402

READ_FR0 = ["fcmp/gt fr3,fr0"]
READ_R0 = ["tst r0,r0"]
END = ["rts", "_nop"]


def call(target):
    return [f"jsr @r3  ; call {target}", "_nop"]


def project(root, funcs):
    """funcs: {addr: (ret, listing lines)}. Each function's listing starts at its own address."""
    for a, (ret, lines) in funcs.items():
        at, out = int(a, 16), []
        for ln in lines:
            out.append(f"  {at:08x}  {ln}\n")
            at += 2
        (root / "packets" / a).mkdir(parents=True)
        (root / "packets" / a / "asm.txt").write_text("".join(out), encoding="utf-8")
        (root / "harness" / a).mkdir(parents=True)
        (root / "harness" / a / "meta.json").write_text(
            json.dumps({"addr": a, "ret": ret, "signature": f"{ret} fn_{a}(void)", "callees": []}), encoding="utf-8")
    return ret_use.Project(root)


class Narrowed(unittest.TestCase):
    F, G, H = "8c010000", "8c020000", "8c030000"

    def found(self, funcs):
        with tempfile.TemporaryDirectory() as t:
            return {a: n["regs"] for a, n in project(pathlib.Path(t), funcs).narrowed().items()}

    def test_a_void_function_whose_caller_reads_fr0(self):  # the shape of Lib_MathPow
        got = self.found({self.F: ("void", ["fldi1 fr0"] + END), self.G: ("void", call(self.F) + READ_FR0 + END)})
        self.assertEqual(got, {self.F: ["fr0"]})

    def test_a_void_wrapper_whose_caller_reads_r0(self):  # TC 8c02870a: jsr, then return what it left
        got = self.found({self.H: ("u32", ["mov #1,r0"] + END), self.F: ("void", call(self.H) + END),
                          self.G: ("void", call(self.F) + READ_R0 + END)})
        self.assertEqual(got, {self.F: ["r0"]})

    def test_a_result_passed_on_through_a_real_reader(self):  # powf passes Lib_MathPow's fr0 on to its own reader
        got = self.found({self.F: ("void", ["fldi1 fr0"] + END), self.H: ("void", call(self.F) + END),
                          self.G: ("void", call(self.H) + READ_FR0 + END)})
        self.assertEqual(got, {self.F: ["fr0"], self.H: ["fr0"]})

    def test_a_function_called_through_a_table_may_leave_it(self):  # TC 8c01c0e8: a jump table's handler sets fr0
        table = ["mov.l @(r0,r1),r2", "jmp @r2", "_nop"]
        got = self.found({self.F: ("void", table), self.G: ("void", call(self.F) + READ_FR0 + END)})
        self.assertEqual(got, {self.F: ["fr0"]})

    def test_nothing_reads_it(self):
        self.assertEqual(self.found({self.F: ("void", ["fldi1 fr0"] + END), self.G: ("void", call(self.F) + END)}), {})

    def test_a_leftover_f32_signature_is_no_reader(self):  # PS2: 8c17c2c0's 454 "readers" were raw f32 leftovers
        got = self.found({self.F: ("void", ["fldi1 fr0"] + END), self.G: ("f32", call(self.F) + END)})
        self.assertEqual(got, {})

    def test_the_signature_names_it(self):
        got = self.found({self.F: ("f32", ["fldi1 fr0"] + END), self.G: ("void", call(self.F) + READ_FR0 + END)})
        self.assertEqual(got, {})

    def test_a_routine_that_keeps_the_register(self):  # TC 8c061520 loads fr1..fr15 and leaves fr0 for its caller
        got = self.found({self.F: ("void", ["fldi1 fr1"] + END), self.G: ("void", call(self.F) + READ_FR0 + END)})
        self.assertEqual(got, {})

    def test_fr0_under_frchg_is_xf0(self):  # TC 8c0613cc
        got = self.found({self.F: ("void", ["frchg", "fldi1 fr0", "rts", "_frchg"]),
                          self.G: ("void", call(self.F) + READ_FR0 + END)})
        self.assertEqual(got, {})

    def test_the_caller_writes_before_it_reads(self):
        got = self.found({self.F: ("void", ["fldi1 fr0"] + END),
                          self.G: ("void", call(self.F) + ["fldi0 fr0"] + READ_FR0 + END)})
        self.assertEqual(got, {})


class Judge(unittest.TestCase):
    META = {"addr": "8c010000", "ret": "void", "signature": "void fn_8c010000(void)"}

    def compared(self, regs, exp, act, **meta):
        judged = ret_reg.judged(dict(self.META, **meta), {"8c010000": set(regs)} if regs else {})
        return compare.compare_return(judged, exp, act)

    def test_a_listed_register_is_compared(self):
        exp, wrong, right = {"r0": 5, "fr0": 0x3f800000}, {"r0": 0, "fr0": 0}, {"r0": 5, "fr0": 0x3f800000}
        self.assertEqual(self.compared((), exp, wrong), [])  # the gap: nothing compared
        self.assertIn("raw_ret f32", self.compared(["fr0"], exp, wrong)[0])
        self.assertIn("raw_ret u32", self.compared(["r0"], exp, wrong)[0])
        self.assertEqual(len(self.compared(["r0", "fr0"], exp, wrong)), 2)
        self.assertEqual(self.compared(["r0", "fr0"], exp, right), [])

    def test_a_named_register_is_not_listed_twice(self):
        out = ret_reg.judged(dict(self.META, ret="u32"), {"8c010000": {"r0"}})
        self.assertNotIn("r0_result", out)
        self.assertNotIn("fr0_result", ret_reg.judged(dict(self.META, ret="f32"), {"8c010000": {"fr0"}}))

    def test_the_list_file(self):
        with tempfile.TemporaryDirectory() as t:
            st = pathlib.Path(t)
            (st / ret_reg.LIST).write_text("# c\n8c010000\n8c010100 r0\n8c010200 fr0 r0\n", encoding="utf-8")
            self.assertEqual(ret_reg.listed(st), {"8c010000": {"fr0"}, "8c010100": {"r0"}, "8c010200": {"fr0", "r0"}})

    def test_a_stale_list_shows(self):
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            p = project(root, {"8c010000": ("void", ["fldi1 fr0"] + END),
                               "8c020000": ("void", call("8c010000") + READ_FR0 + END)})
            (root / "state").mkdir()
            self.assertEqual(p.unlisted(root / "state"), [("8c010000", ["fr0"])])
            (root / "state" / ret_reg.LIST).write_text("8c010000 fr0\n", encoding="utf-8")
            self.assertEqual(p.unlisted(root / "state"), [])


class FloatMocks(unittest.TestCase):
    F, G, H = "8c010000", "8c020000", "8c030000"

    def test_a_reader_of_a_listed_callee_gets_float_results(self):  # PS2 8c0b2db4 hardcodes what 8c0b1de8 leaves in fr0
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            p = project(root, {self.F: ("void", ["fldi1 fr0"] + END), self.G: ("void", call(self.F) + READ_FR0 + END),
                               self.H: ("void", call(self.F) + END)})
            (root / "state").mkdir()
            self.assertEqual(p.fret_callers(root / "state"), {self.G: [self.F]})

    def test_a_raw_ret_f32_stays_once_no_longer_narrowed(self):  # else writing the override would undo the callers' draws
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            p = project(root, {self.F: ("f32", ["fldi1 fr0"] + END), self.G: ("void", call(self.F) + READ_FR0 + END)})
            (root / "state").mkdir()
            self.assertEqual(p.fret_callers(root / "state"), {})  # Ghidra said f32 too: TcPrepare has it already
            p.meta[self.G]["callees"] = [{"addr": self.F, "signature": f"f32 fn_{self.F}(void)",
                                          "ghidra_signature": f"void fn_{self.F}(void)"}]
            self.assertEqual(p.fret_callers(root / "state"), {self.G: [self.F]})

    def test_the_list_file_and_the_script_arg(self):
        with tempfile.TemporaryDirectory() as t:
            st = pathlib.Path(t)
            self.assertEqual(ret_reg.fret_arg(st, self.G), "-")
            (st / ret_reg.FRET).write_text(f"{self.G} {self.F} {self.H}\n", encoding="utf-8")
            self.assertEqual(ret_reg.fret_arg(st, self.G), f"{self.F}_{self.H}")
            self.assertEqual(ret_reg.fret_arg(st, self.H), "-")

    def test_the_oracle_takes_the_arg(self):
        src = ROOT / "targets" / "dreamcast_sh4" / "ghidra_scripts"
        self.assertIn("TcFret.parse(a.length > 15", (src / "TcPrepare.java").read_text(encoding="utf-8"))
        self.assertIn("if (TcFret.listed.contains", (src / "TcPrepare.java").read_text(encoding="utf-8"))


class PowerPc(unittest.TestCase):
    EXP = {"r3": 5, "f1": 0x3ff0000000000000, "r4": 0}

    def test_a_listed_register_is_compared(self):
        void = {"ret": "void", "addr": "80010000"}
        wrong = {"r3": 6, "f1": 0}
        self.assertEqual(ppc_compare.compare_return(void, self.EXP, wrong), [])  # not listed: as before
        self.assertEqual(ret_reg.judged(void, {}), void)
        for regs, n in (({"r3"}, 1), ({"f1"}, 1), ({"r3", "f1"}, 2)):
            meta = ret_reg.judged(void, {"80010000": regs})
            self.assertEqual(len(ppc_compare.compare_return(meta, self.EXP, wrong)), n, regs)
            self.assertEqual(ppc_compare.compare_return(meta, self.EXP, dict(self.EXP)), [], regs)

    def test_the_signature_is_not_compared_twice(self):
        for ret, regs in (("u32", {"r3"}), ("f64", {"f1"})):
            meta = ret_reg.judged({"ret": ret, "addr": "80010000"}, {"80010000": regs})
            self.assertEqual(len(ppc_compare.compare_return(meta, self.EXP, {"r3": 6, "f1": 0})), 1, ret)

    def test_a_bare_address_changes_nothing_here(self):  # it means fr0, which PowerPC has not
        meta = ret_reg.judged({"ret": "void", "addr": "80010000"}, {"80010000": {"fr0"}})
        self.assertEqual(ppc_compare.compare_return(meta, self.EXP, {"r3": 6, "f1": 0}), [])


class Dropped(unittest.TestCase):
    def test_a_result_discarded(self):
        self.assertEqual(float_gaps.bare_calls("  Lib_MathPow(x, y);\n", "Lib_MathPow"), 1)
        self.assertEqual(float_gaps.bare_calls("{ (void)Lib_MathPow(x, y); }", "Lib_MathPow"), 1)
        self.assertEqual(float_gaps.bare_calls("z = Lib_MathPow(x, y);", "Lib_MathPow"), 0)
        self.assertEqual(float_gaps.bare_calls("z =\n  Lib_MathPow(x, y);", "Lib_MathPow"), 0)
        self.assertEqual(float_gaps.bare_calls("if (Lib_MathPow(x, y)) {", "Lib_MathPow"), 0)
        self.assertEqual(float_gaps.bare_calls("/* Lib_MathPow(x, y); */", "Lib_MathPow"), 0)

    def test_the_reader_is_named(self):
        with tempfile.TemporaryDirectory() as t:
            rd = pathlib.Path(t) / "readable"
            (rd / "src").mkdir(parents=True)
            (rd / "src" / "8c010000.c").write_text("void Lib_MathPow(f32 x)\n{\n}\n", encoding="utf-8")
            (rd / "src" / "8c020000.c").write_text("f32 powf(f32 x)\n{\n    Lib_MathPow(x);\n    return 0;\n}\n", encoding="utf-8")
            (rd / "src" / "8c030000.c").write_text("f32 g(f32 x)\n{\n    return Lib_MathPow(x);\n}\n", encoding="utf-8")
            row = {"8c010000": {"regs": ["fr0"], "readers": {"8c020000": (1, 1), "8c030000": (1, 1)}}}
            got = float_gaps.dropped(rd, row)
            self.assertEqual([(r["addr"], r["name"]) for r in got], [("8c020000", "Lib_MathPow")])
            self.assertFalse(got[0]["unreached"])  # no harness: not known
            h = rd.parent / "harness" / "8c020000"
            h.mkdir(parents=True, exist_ok=True)
            try:
                (h / "expected.txt").write_text("V 0\nCALL 8c0f0000 0\n", encoding="utf-8")
                self.assertTrue(float_gaps.dropped(rd, row)[0]["unreached"])  # PS2 8c0b2db4: the judge cannot see this one
                (h / "expected.txt").write_text("V 0\nCALL 8c010000 0\n", encoding="utf-8")
                self.assertFalse(float_gaps.dropped(rd, row)[0]["unreached"])
            finally:
                shutil.rmtree(h.parent)


if __name__ == "__main__":
    unittest.main()

"""readable-tx tests: the readable pass's machine files in the pipeline (core/draft_run: the machine file first, adopted
at any length when it passes the judge, the readability gate and the literal check, `readable_by: machine` in the
ledger, else the agent's start) and the lift's dataflow rules that keep the raw C's meaning (core/rtx: a call is
never moved where it may not run, constant conditions, one variable per value, scaled indexes).
Synthetic data; the judge is faked (the real-judge results are in docs/proofs/READABLE_TX.md).
Run: python -W error tests/test_readable_tx.py (also from tests/test_tooling.py).
"""
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import draft_gate, draft_run, paths, readable, refine  # noqa: E402
from core.rtx import cast, fields, flow, framebuf, jumptable, lift, selfcall, webs  # noqa: E402
from core.rtx import source  # noqa: E402

OK = {"verdict": "PASS", "stage": "compare"}
BAD = {"verdict": "FAIL", "stage": "compare", "failing": 3, "total": 96}
LONG = '#include "game.h"\n\nvoid Long(void)\n{\n' + "".join(f"    g_a = g_a + g_b;\n" for _ in range(40)) + "}\n"


class JumpTables(unittest.TestCase):
    """core/rtx/jumptable: a switch over code addresses becomes one over the index the table is read by."""
    TEXT = ("{ r1 = (u32)(s32)(s16)rd16(r2 + 0x8c000100u); sw_ = r1 + 0x8c000000u; switch (sw_) { case 0x8c000010u: r3 = 1u; "
            "break; case 0x8c000020u: r3 = 2u; break; default: r0 = tc_call(sw_, &a); return; } }")

    def run_on(self, table):
        b = body(self.TEXT)
        jumptable.rewrite(b, lambda addr, width: table.get(addr))
        return " ".join(lines(b))

    def test_over_the_index(self):
        out = self.run_on({0x8c000100: 0x10, 0x8c000102: 0x20, 0x8c000104: 0x10})
        for want in ("switch (r2)", "case 0:", "case 2u:", "case 4u:"):
            self.assertIn(want, out)
        for gone in ("sw_", "default", "0x8c000010", "rd16"):
            self.assertNotIn(gone, out)

    def test_a_label_the_table_misses_stays(self):
        self.assertIn("switch (sw_)", self.run_on({0x8c000100: 0x10}))


class SelfCalls(unittest.TestCase):
    """core/rtx/selfcall: the helper becomes the function, each self-call an ordinary call of it."""
    RAW = """static u32 body(u32 sp, u32 ret_addr, s32 param_1)
{
    u32 r0 = 0, r4 = 0, r15 = 0, pr = 0;
    r15 = sp;
    pr = ret_addr;
    r4 = (u32)param_1;
    r0 = (u32)body(r15, 0x8c00011eu, (s32)r4);
    return r0;
}
u32 fn_8c000100(s32 param_1)
{
    return body(0x8cffe000u, 0x8cfff000u, param_1);
}
"""

    def job(self, *storage):
        return types.SimpleNamespace(raw_params=[(f"param_{i}", st, -1) for i, st in enumerate(storage)])

    def test_the_helper_becomes_the_function(self):
        out = selfcall.unwrap(self.RAW, self.job("r4"))
        for want in ("u32 fn_8c000100(s32 param_1)", "a.r[0] = (s32)r4;", "r0 = tc_call(0x8c000100u, &a);", "r15 = 0x8cffe000u;",
                     "pr = 0x8cfff000u;", "TcArgs a"):
            self.assertIn(want, out)
        self.assertNotIn("body(", out)

    def test_a_form_it_does_not_know_stays(self):
        odd = self.RAW.replace("body(r15, 0x8c00011eu, (s32)r4)", "body(r15 + 4u, r4, (s32)r4)")
        self.assertEqual(selfcall.unwrap(odd, self.job("r4")), odd)
        self.assertEqual(selfcall.unwrap(self.RAW, self.job("r4", "r5")), self.RAW)  # the storages do not fit


class FrameBuffers(unittest.TestCase):
    """core/rtx/framebuf: a frame address used as a value becomes an array, bounded by the saves above it."""

    def test_the_array_stops_at_a_saved_register(self):
        sp = types.SimpleNamespace(loads={"rd32": "u32"}, stores={"wr32": "u32"})
        b = body("{ wr32(__sp - 4u, __in_r8); r7 = __sp - 16u; f(r7); r1 = rd32(__sp - 12u); r2 = rd32(__sp - 4u); }")
        arrays = {}
        framebuf.bind(b, types.SimpleNamespace(spec=sp), {}, arrays)
        self.assertEqual(arrays, {"frame_buf16": ("u32", "[3]")})
        got = " ".join(lines(b))
        self.assertIn("r7 = (u32)frame_buf16;", got)
        self.assertIn("rd32((u32)frame_buf16 + 4)", got)
        self.assertIn("rd32(__sp - 4u)", got)  # the saved register stays a slot

    def test_no_address_taken_no_array(self):
        sp = types.SimpleNamespace(loads={"rd32": "u32"}, stores={"wr32": "u32"})
        arrays = {}
        framebuf.bind(body("{ wr32(__sp - 8u, r1); r2 = rd32(__sp - 8u); }"), types.SimpleNamespace(spec=sp), {}, arrays)
        self.assertEqual(arrays, {})


class Adoption(unittest.TestCase):
    """draft_run.before_agent with the readable pass's machine file (judge faked)."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="rtx", dir=TMP))
        (self.d / "src").mkdir()
        self.patches = [mock.patch.object(refine, "LEDGER", self.d / "readable.json"),
                        mock.patch.object(readable, "SRC", self.d / "src"), mock.patch.object(paths, "RUNS", self.d)]
        for p in self.patches:
            p.start()
        self.machine, self.draft = self.d / "machine.c", self.d / "draft.c"
        self.machine.write_text(LONG, encoding="utf-8")
        self.draft.write_text("/* draft */", encoding="utf-8")
        self.drafts = 0

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def hook(self, machine, draft=(BAD, []), mode="start"):
        def judge(*a):
            self.drafts += 1
            return (self.draft,) + draft
        with mock.patch.object(draft_run, "judge_machine", lambda *a: machine and (self.machine,) + machine), \
                mock.patch.object(draft_run, "judge", judge):
            return draft_run.before_agent({}, None, "8c010000", {}, mode, lambda m: None)

    def test_machine_file_of_any_length_adopted(self):
        self.assertEqual(self.hook((OK, [])), ("adopted", "readable"))
        self.assertEqual(self.drafts, 0)  # a passing machine file: no draft made
        row = refine.load_ledger()["8c010000"]
        self.assertEqual((row["status"], row["source"], row["readable_by"]), ("readable", "draft", "machine"))
        self.assertEqual((row["draft"]["kind"], row["attempts"][-1]["kind"]), ("machine", "machine"))
        self.assertEqual((self.d / "src" / "8c010000.c").read_text(encoding="utf-8"), LONG)

    def test_gate_refusal_is_the_agents_start(self):
        got = self.hook((OK, ["31u is LIB_ANIM_MAX_JOINTS"]))
        self.assertEqual(got[0], "start")
        self.assertEqual(got[1][0], self.machine)
        self.assertIn("already passes the check", got[1][1])
        self.assertIn("31u is LIB_ANIM_MAX_JOINTS", got[1][1])
        self.assertEqual(self.drafts, 0)
        self.assertFalse((self.d / "src" / "8c010000.c").exists())
        self.assertEqual(self.hook((OK, ["r4"]))[0], "start")  # register names left: never adopted
        self.assertIsNone(self.hook((OK, ["4u is X"]), mode="adopt"))  # adopt mode: no start

    def test_failing_machine_file_falls_back_to_the_draft(self):
        got = self.hook((BAD, []), (BAD, []))
        self.assertEqual(got[1][0], self.draft)  # neither passes: the draft starts, as before the readable pass
        self.assertEqual(self.hook((BAD, []), (OK, [])), ("adopted", "readable"))
        row = refine.load_ledger()["8c010000"]
        self.assertEqual((row["draft"]["kind"], row["readable_by"]), ("draft", "machine"))

    def test_no_machine_file(self):
        self.assertEqual(self.hook(None, (BAD, []))[1][0], self.draft)
        self.assertEqual(self.drafts, 1)

    def test_gate_has_no_length_limit_by_default(self):
        self.assertIsNone(draft_gate.MAX_LINES)
        self.assertEqual(draft_gate.reasons(LONG, {}), [])
        self.assertEqual(draft_gate.reasons(LONG, {}, max_lines=6), ["43 lines (drafts are adopted up to 6)"])
        self.assertEqual(draft_gate.reasons(LONG.replace("g_b", "8u"), {8: ["EIGHT"]}), ["8u is EIGHT"])

    def test_config_off(self):
        self.assertIsNone(draft_run.judge_machine({"readable_machine": False}, None, "8c010000", {}))

    def test_an_agent_is_told_the_file_is_unnamed(self):
        self.assertIn("No agent has named this file", refine.MACHINE_NOTE)


def body(text):
    return cast.parse_body(text, ())


def lines(b):
    return [ln.strip() for ln in cast.body_lines(b.body, "")]


class Lift(unittest.TestCase):
    """core/rtx's dataflow keeps the raw C's meaning."""

    @classmethod
    def setUpClass(cls):
        cls.sp = source.spec("dreamcast_sh4")

    def test_a_call_is_never_moved_where_it_may_not_run(self):
        b = body("{ r1 = f(); r2 = g(); if (r1 == 0u && r2 == 0u) { return 0u; } return 1u; }")
        flow.simplify(b, self.sp)
        got = lines(b)
        self.assertIn("r2 = g();", got)  # g() runs whatever f() returned
        self.assertNotIn("g()", " ".join(x for x in got if x.startswith("if")))

    def test_constant_conditions(self):
        b = body("{ r1 = 0u; if (0x8c000010u >= 0x8c000020u) { r1 = 5u; } return r1; }")
        flow.simplify(b, self.sp)
        self.assertEqual(lines(b), ["return 0u;"])
        self.assertEqual(cast.pr(flow.fold(cast.parse_expr("0x80000000u < 1u"))), "0")
        e = flow.fold(cast.parse_expr("-1 < 0"))  # signed: not folded as unsigned
        self.assertNotEqual(cast.pr(e), "0")
        self.assertEqual(cast.pr(flow.fold(cast.parse_expr("0x80000000u == 0"))), "0")  # equal bits either way

    def test_vector_state_registers_stay(self):
        # fr12..fr15 carry the vector library's values across calls: kept as the raw C reads and writes them
        sp = self.sp
        self.assertIn("fr13", sp.state_regs)
        self.assertTrue(sp.is_load("sh4_fr"))  # a read of state: not moved across a call, dropped when unused
        self.assertEqual(sp.handover_put("fr12"), ("sh4_set_fr", 12))

    def test_one_variable_per_value(self):
        b = body("{ r1 = a; f(r1); r1 = b; g(r1); }")
        types = {"r1": "u32"}
        new = webs.split(b, {"r1"}, types)
        self.assertEqual(list(new.values()), ["r1"])
        self.assertEqual(lines(b), ["r1 = a;", "f(r1);", "r1__w1 = b;", "g(r1__w1);"])

    def test_dead_result_keeps_the_call(self):
        b = body("{ r1 = f2u(h(x)); r2 = k(); return 1u; }")
        flow.simplify(b, self.sp)
        self.assertEqual(lines(b), ["h(x);", "k();", "return 1u;"])

    def test_scaled_index(self):
        x, k = fields._scale(cast.parse_expr("((n << 1) + n) << 4"))
        self.assertEqual((cast.pr(x), k), ("n", 48))

    def test_array_members_index_each_dimension(self):
        faces = fields.Field(0x68, "faces", "s32", 96, 24, shape=(6, 4))
        self.assertEqual(faces.indexes(9), [2, 1])  # faces[2][1], not faces[9] (a row)
        self.assertTrue(fields.Field(0xac, "cells", "GridCell", 16, 1, shape=(1,)).array)  # cells[0], not cells
        self.assertFalse(fields.Field(0, "count", "s32", 4).array)

    def test_a_void_call_still_runs(self):
        b = body("{ u32 r0 = 0; (void)r0; (void)tc_call(0x8c010000u, &a); return; }")
        lift.declarations(b)
        self.assertEqual(lines(b), ["tc_call(0x8c010000u, &a);", "return;"])


class PointerCalls(unittest.TestCase):
    """A call through a computed pointer lifts to a function-pointer call typed by exactly the registers the original
    set (the vectors' CA, as the judge decodes it: the integer ones first, then the float ones); where the vectors
    do not agree on them, or the target has no such form (controls), it still refuses."""

    class Job:
        names = type("N", (), {"callees": {}})()
        callee_types, callee_ret = {}, {}
        kind = staticmethod(lambda t: "f" if t == "f32" else "i")
        pointer_ca, call_total = {0: ["r4", "r6", "fr4"]}, 1

    def lift_calls(self, text, spec, job=None):
        b = body(text)
        job = job or self.Job()
        job.spec = spec
        types = lift.declarations(b)
        lift.calls(b, job, types)
        return lines(b)

    def test_the_vectors_registers_type_the_call(self):
        sp = source.spec("dreamcast_sh4")
        got = self.lift_calls("{ u32 t; u32 r0; r0 = tc_call(t, &a); return r0; }", sp)
        self.assertEqual(got[0], "r0 = ((u32 (*)(u32, u32, f32))(uintptr_t)t)(0, 0, 0.0f);")  # never set: the block's 0
        got = self.lift_calls("{ u32 t; u32 r0; u32 __a_r0; u32 __a_r2; f32 __a_fr0; r0 = tc_call(t, &a); return r0; }", sp)
        self.assertEqual(got[0], "r0 = ((u32 (*)(u32, u32, f32))(uintptr_t)t)(__a_r0, __a_r2, __a_fr0);")
        got = self.lift_calls("{ u32 t; f32 f; f = tc_callf(t, &a); return; }", sp)
        self.assertIn("f = ((f32 (*)(u32, u32, f32))(uintptr_t)t)(", got[0])

    def test_controls(self):
        sp = source.spec("dreamcast_sh4")
        text = "{ u32 t; u32 r0; r0 = tc_call(t, &a); return r0; }"
        none = type("J", (self.Job,), {"pointer_ca": {}, "call_total": None})
        with self.assertRaises(flow.Unsupported):  # the vectors disagree
            self.lift_calls(text, sp, none())
        stack = type("J", (self.Job,), {"pointer_ca": {0: ["r4", "s0"]}})
        with self.assertRaises(flow.Unsupported):  # a stack argument the C call cannot pass
            self.lift_calls(text, sp, stack())
        count = type("J", (self.Job,), {"call_total": 2})
        with self.assertRaises(flow.Unsupported):  # the text has one call, the vectors two
            self.lift_calls(text, sp, count())
        bare = type("Sp", (), {"call_fns": ("tc_call", "tc_callf")})()
        with self.assertRaises(flow.Unsupported):  # a target with no such form
            self.lift_calls(text, bare)

    def test_pointer_ca_needs_every_vector_to_agree(self):
        from core.rtx import pointer as job
        run = lambda calls, ca: {"calls": calls, "call_args": ca}  # noqa: E731
        same = {0: run([[1], [2]], {1: ["r4", "fr4"]}), 1: run([[3], [4]], {1: ["r4", "fr4"]})}
        self.assertEqual(job.pointer_ca(same), ({0: [], 1: ["r4", "fr4"]}, 2))
        prefix = {0: run([[1]], {0: ["r4"]}), 1: run([[1]], {0: ["r4", "r5"]}), 2: run([[1]], {})}
        self.assertEqual(job.pointer_ca(prefix), ({None: ["r4", "r5", "r6", "r7"]}, 1))
        longer = {0: run([[1]], {0: ["r4"]}), 1: run([[1], [2]], {0: ["r4", "r5"]})}
        self.assertEqual(job.pointer_ca(longer), ({None: ["r4", "r5", "r6", "r7"]}, None))  # any call, any count
        other = {0: run([[1]], {0: ["r4", "fr4"]}), 1: run([[1]], {0: ["r4", "r5"]})}
        self.assertEqual(job.pointer_ca(other), ({}, 1))              # floats that differ: no type for that call
        floats = {0: run([[1]], {0: ["r4", "fr4"]}), 1: run([[1], [2]], {0: ["r4", "fr4"]})}
        self.assertEqual(job.pointer_ca(floats), ({}, None))          # floats and a differing call count


class Constants(unittest.TestCase):
    """core/rtx/usage: a literal takes the header name the other readable files write at the same place."""

    VALUES = {1: ["XMTRX_STACK_SCENE", "ONE"], 2: ["XMTRX_STACK_CAMERA", "ADXT_RMODE_REPLAY", "MODE_TWO"],
              3: ["MODE_THREE", "STATE_RUN"], 4: ["MODE_FOUR", "MODE_ALSO_FOUR"]}

    def usage(self, files):
        from core.rtx import usage
        d = pathlib.Path(tempfile.mkdtemp(prefix="use", dir=TMP))
        for a, text in files.items():
            (d / f"{a}.c").write_text(text, encoding="utf-8")
        return usage.Usage(self.VALUES, d)

    def test_a_callee_argument(self):
        u = self.usage({"8c000010": "void A(void) { Xmtrx_SelectStack(XMTRX_STACK_SCENE); /* (MODE_TWO) */ }"})
        site = ("arg", "Xmtrx_SelectStack", 0)
        self.assertEqual(u.name(site, 2, "8c000020"), "XMTRX_STACK_CAMERA")  # the family in use there, not the first
        self.assertEqual(u.name(site, 1, "8c000020"), "XMTRX_STACK_SCENE")
        self.assertIsNone(u.name(site, 2, "8c000010"))  # never from the function's own file
        self.assertIsNone(u.name(("arg", "Other", 0), 2, "8c000020"))  # no use there: no name

    def test_only_one_name_fits(self):
        u = self.usage({"8c000010": "void A(void) { f((u32)MODE_TWO, x); }", "8c000030": "void B(void) { f(MODE_THREE); }"})
        self.assertEqual(u.name(("arg", "f", 0), 2, "8c000020"), "MODE_TWO")
        self.assertIsNone(u.name(("arg", "f", 0), 4, "8c000020"))  # MODE_FOUR or MODE_ALSO_FOUR: left for an agent

    def test_a_member_of_one_family_only(self):
        u = self.usage({"8c000010": "void A(T *p) { p->mode = MODE_TWO; if (q.state == STATE_RUN) {} }",
                        "8c000030": "void B(T *p) { if (MODE_THREE != p->mode) {} p->kind = MODE_TWO; p->kind = ONE; }"})
        self.assertEqual(u.name(("member", "mode"), 4, "8c000020"), None)  # two names of 4 in the family
        self.assertEqual(u.name(("member", "mode"), 3, "8c000020"), "MODE_THREE")
        self.assertEqual(u.name(("member", "state"), 3, "8c000020"), "STATE_RUN")
        self.assertIsNone(u.name(("member", "kind"), 2, "8c000020"))  # MODE_ and ONE: a member many structs share


class Specs(unittest.TestCase):
    """The targets' readable passes (targets/<target>/rtx.py)."""

    def test_each_target_names_its_ram(self):
        for t, lo in (("dreamcast_sh4", 0x8C000000), ("saturn_sh2", 0x06000000), ("gamecube_ppc", 0x80000000)):
            self.assertEqual(source.spec(t).ram[0], lo)

    def test_mirrors_are_the_targets_address_mirrors(self):
        import json
        for t in ("dreamcast_sh4", "saturn_sh2", "gamecube_ppc"):
            tj = json.loads((pathlib.Path(lift.__file__).parents[2] / "targets" / t / "target.json").read_text())
            for hi, (canon, op, k) in getattr(source.spec(t), "mirrors", {}).items():
                self.assertEqual(tj["address_mirrors"][f"{hi:02x}"], f"{canon:02x}")
                a = (canon << 24) | 0x123456
                self.assertEqual(a & k if op == "&" else a | k, (hi << 24) | 0x123456)  # mirror = address op k

    def test_ppc_stack_constants_become_the_stack_pointer(self):
        sp = source.spec("gamecube_ppc")
        b = body("{ wr32(0x817fdfe0u, 0x817fe000u); wr32(0x817fe004u, 0x817ff000u); a.r[1] = 0x817fdfe8u; return; }")
        sp.prepare(b)  # the back chain goes; the saved link register is lr; a stack address is r1 + offset
        self.assertEqual(lines(b), ["r1 = 0x817fe000u;", "wr32(r1 + 0x4u, lr);", "a.r[1] = r1 - 0x18u;", "return;"])

    def test_ppc_single_store_only_of_a_single(self):
        sp = source.spec("gamecube_ppc")
        got = sp.store_value("wrf32d", cast.parse_expr("(f64)(f32)(x - y)"))
        self.assertEqual(cast.pr(got), "(f32)(x - y)")
        with self.assertRaises(flow.Unsupported):  # stfs of any double is not C's conversion
            sp.store_value("wrf32d", cast.parse_expr("x - y"))

    def test_saturn_refuses_hardware(self):
        sp = source.spec("saturn_sh2")
        with self.assertRaises(flow.Unsupported):
            sp.check(body("{ tc_hw_write32(0x25fe00b0u, 1u); return; }"))
        self.assertEqual(sp.sp_init, 0x060FE000)


if __name__ == "__main__":
    unittest.main()

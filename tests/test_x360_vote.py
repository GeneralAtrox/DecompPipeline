"""The Xbox 360 instruction vote (tools/x360_vote.py): the corpus reader and the encoder on self-written cases, the
vote's classes on made-up legs, the settlement rules on known answers, and leg A's runner (one cl build) rejecting a
planted wrong corpus value and a planted wrong C function while passing the true ones. The runner part prints
"SKIPPED: needs cl" without config.local.json's vcvars.

usage: python tests/test_x360_vote.py
"""
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools"), str(TOOL / "targets" / "xbox360_ppc" / "tools"), str(TOOL)]
import vote_corpus as VC  # noqa: E402
import vote_encode as E  # noqa: E402
import vote_encode_check as K  # noqa: E402
import vote_leg_a as A  # noqa: E402
import vote_settle as S  # noqa: E402
import x360_vote as X  # noqa: E402
from core import toolcfg  # noqa: E402

MINI = """
test_add_1:
  #_ REGISTER_IN r4 1
  #_ REGISTER_IN r5 0x10
  add r3, r4, r5   # a comment
  blr
  #_ REGISTER_OUT r3 0x11
  #_ REGISTER_OUT r4 1
test_add_1_constant:
  li r4, 1
  li r5, 0x10
  add r3, r4, r5
  blr
  #_ REGISTER_OUT r3 17
test_vec:
  #_ REGISTER_IN v3 [00000001, 00000002, 00000003, 00000004]
  #_ REGISTER_IN f1 [3FF0000000000000]
  #_ MEMORY_IN 0x10001000 [AB, CD]
  vor v4, v3, v3
  blr
  #_ REGISTER_OUT v4 [00000001, 00000002, 00000003, 00000004]
  #_ MEMORY_OUT 10001000 ABCD
test_ldbrx:
  #_ REGISTER_IN r4 0x10001000
  ldbrx r3, 0, r4
  blr
  #_ REGISTER_OUT r3 0
test_trap:
  tw 4, r3, r3
  blr
"""


def mini():
    return [dict(c, skip=False) for c in VC.parse_text(MINI, "mini.s")]


class Reader(unittest.TestCase):
    def test_cases(self):
        cs = mini()
        self.assertEqual([c["label"] for c in cs], ["test_add_1", "test_add_1_constant", "test_vec", "test_ldbrx",
                                                     "test_trap"])
        a = cs[0]
        self.assertEqual(a["reg_in"], {"r4": 1, "r5": 0x10})
        self.assertEqual([(k, v) for k, v, _ in a["reg_out"]], [("r3", 0x11), ("r4", 1)])
        v = cs[2]
        self.assertEqual(v["reg_in"]["v3"], (1, 2, 3, 4))
        self.assertTrue(v["bracket_float"] and v["bracket_mem"])
        self.assertEqual(v["mem_in"], [(0x10001000, b"\xab\xcd")])
        self.assertEqual(v["mem_out"], [(0x10001000, b"\xab\xcd")])

    def test_encoder(self):
        for text, word in [("add r3, r4, r5", 0x7C642A14), ("blr", 0x4E800020), ("li r4, 1", 0x38800001),
                           ("vor v4, v3, v3", 0x10831C84), ("fmadds f4, f1, f2, f3", 0xEC8118BA),
                           ("lwz r3, 4(r4)", 0x80640004)]:
            w, op, f = E.encode(text)
            self.assertEqual(w, word, text)
            self.assertIsNone(K.round_trip(w, op, f, K.BASE), text)
        with self.assertRaises(E.EncodeError):
            E.encode("frobnicate r1, r2")

    def test_unsupported_counted(self):
        plan, groups = A.prepare(mini())
        why = [p[2] for p in plan]
        self.assertIsNone(why[0])
        self.assertTrue(why[3])  # ldbrx: not a Xenon instruction (decode.py: illegal)
        self.assertTrue(why[4].startswith("trap"))
        self.assertTrue(why[2].startswith("address made in code"))  # memory but no address among the inputs
        self.assertEqual(len(groups), 2)  # add, li/li/add: one function per distinct word sequence


class Vote(unittest.TestCase):
    def test_classes(self):
        ok, bad1, bad2 = {"status": "agree"}, {"status": "differ", "outs": {"r3": "1"}}, \
            {"status": "differ", "outs": {"r3": "2"}}
        un = {"status": "unsupported"}
        triples = {"all_agree": (ok, ok), "A_suspect": (bad1, ok), "B_suspect": (ok, bad1),
                   "shared_or_corpus": (bad1, dict(bad1)), "three_way": (bad1, bad2), "incomplete": (un, ok)}
        for want, (a, b) in triples.items():
            self.assertEqual(X.vote_one(a, b), want)
        res = X.vote({"cases": {k: v[0] for k, v in triples.items()}}, {"cases": {k: v[1] for k, v in triples.items()}})
        self.assertEqual(res["classes"], {k: 1 for k in triples})


class Settle(unittest.TestCase):
    def test_rules(self):
        one, inf = 0x3FF0000000000000, 0x7FF0000000000000
        self.assertEqual(S.fp_arith("fadd", one, one, None)[0], 0x4000000000000000)
        r, fl = S.fp_arith("fmadd", 0, 0, inf)  # 0 x inf: invalid, the default QNaN
        self.assertEqual((r, fl["VX"]), (S.DEF_NAN, 1))
        self.assertEqual(S.fp_arith("fnmadd", 0, 0xFFF8000000000000, 0)[0], 0xFFF8000000000000)  # NaN not negated
        self.assertEqual(S.fcti(S.bits(2.5), False, False), (2, S.M64))  # ties to even
        self.assertIsNone(S.vshift("vsl", (1, 1, 1, 1), (0, 0, 0, 1)))  # unequal counts: undefined
        self.assertEqual(S.carry("addic", 0xFFFFFFFF, 1), (0, 1))


@unittest.skipUnless(toolcfg.load(TOOL).get("vcvars"), "needs cl")
class Runner(unittest.TestCase):
    def test_planted(self):
        cs = mini()[:2]
        wrong = dict(cs[0], label="planted_value", reg_out=[("r3", 0x12, "bits"), ("r4", 1, "bits")])
        mutant = dict(cs[0], label="planted_c")
        cases = cs + [wrong, mutant]
        plan, groups = A.prepare(cases)
        srcs = [A.translate_group(n, list(g))[0] for n, g in enumerate(groups)]
        bad = re.sub(r"\bvote_0\b", f"vote_{len(srcs)}", srcs[0])
        bad, n = re.subn(r"tcx_r\(3u, ", "tcx_r(3u, 1u ^ ", bad, count=1)
        self.assertEqual(n, 1)
        srcs.append(bad)
        plan[3] = (len(srcs) - 1, plan[3][1], None)
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        d = pathlib.Path(tempfile.mkdtemp(prefix="x360vote_", dir=TOOL / "tests" / ".work"))
        try:
            ok, msg = A.build(d, srcs, toolcfg.load(TOOL))
            self.assertTrue(ok, msg)
            names = [A.named(s) for s in srcs]
            res = X.make_result("A", "mini", cases, plan, A.run(d, cases, plan), lambda fn: names[fn])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        st = {k.split(":")[1]: r["status"] for k, r in res["cases"].items()}
        self.assertEqual(st, {"test_add_1": "agree", "test_add_1_constant": "agree", "planted_value": "differ",
                              "planted_c": "differ"})


if __name__ == "__main__":
    if not toolcfg.load(TOOL).get("vcvars"):
        print("SKIPPED: needs cl (the runner test)")
    unittest.main()

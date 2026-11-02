"""The readable judge's per-function opt-ins (core/readable_optin.py, project.json "readable_judge"). Pass controls: each
opt-in removes its artifact for the listed function. Fail controls: an unlisted function, a raw check (reads None), the
judge with ENABLED False, a guard that does not hold (a tail caller that exports the register, a call not through r0,
a vector whose calls all reach code) and every other difference still fail; a malformed entry is refused. Pure
functions on synthetic data: no Ghidra, no compiler.
Run: python -W error tests/test_readable_optin.py
"""
import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import readable_optin as o  # noqa: E402
from core.compare import CALL_FIELDS, REG_INDEX, compare_vector  # noqa: E402

F, TAIL, KEPT, OTHER, JUNK = "8c020b30", "8c01c7fa", 0x8C061498, 0x8C0614E0, 0x8CC01100
TGT = "8c05e374"
R0 = REG_INDEX["r0"]
IMAGE, VEC = bytes(0x100), {"patches": []}


def call(target, **regs):
    c = [target] + [0] * (CALL_FIELDS - 1)
    for r, v in regs.items():
        c[REG_INDEX[r]] = v
    return c


def run(calls, ro=None):
    r = {"set": "dev", "writes": {}, "calls": calls, "faults": [], "xf": [0] * 16, "cov": set(), "done": True,
         "r0": 0, "fr0": 0}
    if ro is not None:
        r["ro"] = ro
    return r


def cfg(**e):
    return {"readable_judge": {F: {k: dict(v, why="evidence") for k, v in e.items()}}}


class Opt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        h = pathlib.Path(self.tmp.name) / "harness"
        for a, ex in ((TAIL, []), ("8c0aaaaa", ["r5"])):
            (h / a).mkdir(parents=True)
            (h / a / "meta.json").write_text(json.dumps({"r_exports": ex}), encoding="utf-8")
        (h / TGT).mkdir()
        (h / TGT / "meta.json").write_text(json.dumps({"signature": "u32 fn(u32 a, u32 b, u32 c)", "params": [
            {"storage": "r4"}, {"storage": "r5"}, {"storage": "stack+0"}]}), encoding="utf-8")
        ranges = pathlib.Path(self.tmp.name) / "code_ranges.txt"
        ranges.write_text("8c010000 8c0c0000\n", encoding="utf-8")
        self.p = [mock.patch.object(o.paths, "HARNESS", h), mock.patch.object(o.paths, "CODE_RANGES", ranges)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        self.tmp.cleanup()
        o.ENABLED = True

    def judge(self, c, meta, exp, act, reads=frozenset()):
        meta, vecs, e = o.judged(c, dict(meta, addr=F), None if reads is None else {}, {0: VEC},
                                 {0: copy.deepcopy(exp)})
        return compare_vector(meta, IMAGE, VEC, e[0], act) if 0 in vecs else "dropped"

    def test_exit_unread(self):
        meta = {"ret": "void", "callees": [], "r_exports": ["r4", "r5", "r6", "r7"]}
        exp, act = run([], [0x8CFFE054, 1, 2, 3]), run([], [0, 0, 0, 3])
        on = cfg(exit_unread={"regs": ["r4", "r5", "r6", "r7"], "tail_callers": [TAIL]})
        self.assertEqual(self.judge(on, meta, exp, act), [])  # pass control
        self.assertEqual(len(self.judge({}, meta, exp, act)), 3)  # unlisted: r4..r6 differ
        self.assertEqual(len(self.judge(on, meta, exp, act, reads=None)), 3)  # raw check unchanged
        o.ENABLED = False
        self.assertEqual(len(self.judge(on, meta, exp, act)), 3)  # OLD judge
        o.ENABLED = True
        part = cfg(exit_unread={"regs": ["r5", "r6"], "tail_callers": [TAIL]})
        self.assertEqual(len(self.judge(part, meta, exp, act)), 1)  # r4 not listed: still compared
        bad = cfg(exit_unread={"regs": ["r4", "r5", "r6", "r7"], "tail_callers": [TAIL, "8c0aaaaa"]})
        self.assertEqual(len(self.judge(bad, meta, exp, act)), 3)  # a tail caller exports r5: guard fails
        gone = cfg(exit_unread={"regs": ["r4", "r5", "r6", "r7"], "tail_callers": ["8c0bbbbb"]})
        self.assertEqual(len(self.judge(gone, meta, exp, act)), 3)  # no harness for the tail caller: guard fails

    def test_r0_kept(self):
        meta = {"ret": "void", "callees": [{"addr": "8c061498", "regs": ["r4"]}]}
        exp = run([call(KEPT, r4=5, r0=KEPT), call(JUNK, r4=6, r0=JUNK), call(OTHER, r4=7)])
        act = run([call(KEPT, r4=5), call(KEPT, r4=6), call(OTHER, r4=7)])
        on = cfg(r0_kept={"callee": "8c061498"})
        self.assertEqual(self.judge(on, meta, exp, act), [])  # pass control
        self.assertIn("expected target", self.judge({}, meta, exp, act)[0])  # unlisted
        wrong = run([call(KEPT, r4=5), call(KEPT, r4=9), call(OTHER, r4=7)])
        self.assertIn("argument r4", self.judge(on, meta, exp, wrong)[0])  # the call's own argument still compared
        notr0 = run([call(KEPT, r4=5, r0=KEPT), call(JUNK, r4=6, r0=1), call(OTHER, r4=7)])
        self.assertIn("expected target", self.judge(on, meta, notr0, act)[0])  # not a call through r0
        first = run([call(KEPT, r4=5, r0=2), call(JUNK, r4=6, r0=JUNK), call(OTHER, r4=7)])
        self.assertIn("expected target", self.judge(on, meta, first, act)[0])  # the first call not through r0
        other = cfg(r0_kept={"callee": "8c0614e0"})
        self.assertIn("expected target", self.judge(other, meta, exp, act)[0])  # another callee listed

    def test_drop_vectors(self):
        meta = {"ret": "void", "callees": []}
        exp, act = run([call(JUNK, r4=5)]), run([call(JUNK, r4=0)])
        exp["call_args"] = {0: ["r4"]}
        self.assertEqual(self.judge(cfg(drop_vectors={"ids": [0]}), meta, exp, act), "dropped")  # pass control
        self.assertTrue(self.judge(cfg(drop_vectors={"ids": [1]}), meta, exp, act))  # another vector listed
        self.assertTrue(self.judge({}, meta, exp, act))  # unlisted
        self.assertTrue(self.judge(cfg(drop_vectors={"ids": [0]}), meta, exp, act, reads=None))  # raw check
        code = run([call(OTHER, r4=5)])
        code["call_args"] = {0: ["r4"]}
        self.assertTrue(self.judge(cfg(drop_vectors={"ids": [0]}), meta, code, run([call(OTHER, r4=0)])))  # all code
        # known_callees: a code address that is no callee (a table index past its end) drops the vector as well
        kc = cfg(drop_vectors={"ids": [0], "known_callees": True})
        self.assertEqual(self.judge(kc, meta, code, run([call(OTHER, r4=0)])), "dropped")  # pass control
        known = dict(meta, callees=[{"addr": "8c0614e0", "regs": ["r4"], "signature": "s"}])
        self.assertTrue(self.judge(kc, known, code, run([call(OTHER, r4=0)])))  # a callee: judged
        tgt = cfg(drop_vectors={"ids": [0], "known_callees": True}, out_targets={"targets": ["8c0614e0"]})
        self.assertTrue(self.judge(tgt, meta, code, run([call(OTHER, r4=0)])))  # an out_targets function: judged
        self.assertTrue(self.judge(kc, meta, code, run([call(OTHER, r4=0)]), reads=None))  # raw check unchanged
        self.assertTrue(self.judge(cfg(drop_vectors={"ids": [0], "known_callees": False}), meta, code,
                                   run([call(OTHER, r4=0)])))  # off: only the code ranges count

    def test_out_targets(self):
        meta = {"addr": F, "ret": "void", "callees": [{"addr": "8c0aaaaa", "regs": ["r4"], "signature": "s"}]}
        on = cfg(out_targets={"targets": [TGT]})
        got = o.out_meta(on, meta)  # pass control: a record from the target's own harness, the old one kept
        self.assertEqual([c["addr"] for c in got["callees"]], ["8c0aaaaa", TGT])
        self.assertEqual(got["callees"][1], {"addr": TGT, "regs": ["r4", "r5"], "signature": "u32 fn(u32 a, u32 b, u32 c)"})
        self.assertEqual(len(meta["callees"]), 1)  # the caller's meta is not changed
        self.assertIs(o.out_meta({}, meta), meta)  # unlisted function
        other = dict(meta, addr="8c000000")
        self.assertIs(o.out_meta(on, other), other)  # another function: nothing
        listed = dict(meta, callees=meta["callees"] + [{"addr": TGT, "regs": [], "signature": "x"}])
        self.assertIs(o.out_meta(on, listed), listed)  # already a callee of the harness: left as it is
        self.assertIs(o.out_meta(cfg(out_targets={"targets": ["8c0bbbbb"]}), meta), meta)  # no harness: no record
        o.ENABLED = False
        self.assertIs(o.out_meta(on, meta), meta)  # OLD judge
        o.ENABLED = True
        for bad in ({"targets": [], "why": "x"}, {"targets": ["5e374"], "why": "x"}, {"targets": [TGT]}):
            with self.assertRaises(ValueError):
                o.entry({"readable_judge": {F: {"out_targets": bad}}}, F)

    def test_image_base_and_refusals(self):
        c = cfg(image_base={"base": "0x5b000000"})
        self.assertEqual(o.build_cfg(c, F)["readable_image_base"], "0x5b000000")
        self.assertIs(o.build_cfg(c, "8c000000"), c)
        o.ENABLED = False
        self.assertIs(o.build_cfg(c, F), c)
        o.ENABLED = True
        for bad in ({F: {"exit_unread": {"regs": ["r4"], "tail_callers": [TAIL]}}},  # no why
                    {F: {"nothing": {"why": "x"}}},
                    {F: {"exit_unread": {"regs": ["r8"], "tail_callers": [TAIL], "why": "x"}}},
                    {F: {"r0_kept": {"callee": "61498", "why": "x"}}},
                    {F: {"drop_vectors": {"ids": ["97"], "why": "x"}}},
                    {F: {"image_base": {"base": "0x5b001000", "why": "x"}}}):
            with self.assertRaises(ValueError):
                o.entry({"readable_judge": bad}, F)


if __name__ == "__main__":
    unittest.main()

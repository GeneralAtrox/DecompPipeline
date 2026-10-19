"""The float gaps (docs/PROOF_GATES.md section 3). One pass and one fail control each:
1. core/ret_reg: `rts; fldi0 fr0` (TC 8c01becc) returns in fr0, a clamp that compares fr0 first does too; a scratch
   fr0 stored before the return, an r0 written after fr0, fr0 under frchg (XF0) and a call's result do not; the
   judge's meta gains fr0_result only for a function in state/fr0_results.txt whose signature is not f32, and
   compare_return then compares fr0 with a raw_ret note; ret_use lists a function as a raw_ret candidate (fr0_exit)
   only when a caller reads fr0 and none reads r0;
2. core/float_lits: the words a pool `mova` gives an `fmov.s` (and a `mov.l` noted f32) are the original's
   constants; a C text that writes 1.0f where the pool has 1e-06 (TC 8c035a0c) misses it, one that writes it as a
   literal, a header name, a folded quotient or an integer of the same value does not, a comment does not count;
3. the oracle side (TcFret, TcFguide): locked, TcFret wired into TcOracle and TcPrepare, TcFguide into TcVectors
   after TcGuide and before the hints, every script at 400 lines or fewer, and selective_restamp --change fret takes
   the harnesses with an f32 callee record.
The oracle itself needs Ghidra: the regress gate judges it.
Synthetic data. Run: python -W error tests/test_float_gaps.py.
"""
import json
import pathlib
import struct
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from core import compare, float_lits, ret_reg, ret_use, sh4_asm  # noqa: E402
import float_gaps  # noqa: E402
import selective_restamp as sr  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
BASE = 0x8c010000


def listing(lines, at=0x8c010100):
    """asm.txt text: one instruction per entry at 2-byte steps; an entry ending in ':' is a label line."""
    out = []
    for ln in lines:
        if ln.endswith(":"):
            out.append(ln + "\n")
            continue
        out.append(f"  {at:08x}  {ln}\n")
        at += 2
    return "".join(out)


def insns(lines):
    return sh4_asm.parse(listing(lines))


class RetReg(unittest.TestCase):
    def test_fr0_results(self):
        self.assertEqual(ret_reg.exits(insns(["rts", "_fldi0 fr0"])), {"fr0"})  # TC 8c01becc
        self.assertTrue(ret_reg.fr0_result(insns(["rts", "_fldi0 fr0"])))
        clamp = ["fmov.s @r4,fr0", "fcmp/gt fr1,fr0", "bt 0x8c01010a", "fmov fr1,fr0", "LAB_8c01010a:", "rts", "_nop"]
        self.assertTrue(ret_reg.fr0_result(insns(clamp)))

    def test_not_fr0_results(self):
        scratch = ["mov #1,r0", "fldi0 fr0", "fmov.s fr0,@r4", "rts", "_nop"]
        self.assertEqual(ret_reg.exits(insns(scratch)), {"fr0_read"})
        self.assertFalse(ret_reg.fr0_result(insns(scratch)))
        self.assertFalse(ret_reg.fr0_result(insns(["fldi1 fr0", "rts", "_mov #0,r0"])))  # r0 last
        xmtrx = ["frchg", "fmov.s @r4+,fr1", "fmov.s @r4,fr0", "rts", "_frchg"]  # TC 8c0613cc: fr0 is XF0 there
        self.assertEqual(ret_reg.exits(insns(xmtrx)), {"none"})
        call = ["sts.l pr,@-r15", "jsr @r1  ; call 8c010200", "_nop", "lds.l @r15+,pr", "rts", "_nop"]
        self.assertEqual(ret_reg.exits(insns(call)), {"call"})
        self.assertFalse(ret_reg.fr0_result(insns(call)))
        self.assertTrue(ret_reg.fr0_result(insns(call), ret_of=lambda t: "fr0"))  # a callee known to return f32

    def test_judge(self):
        with tempfile.TemporaryDirectory() as t:
            st = pathlib.Path(t)
            self.assertEqual(ret_reg.listed(st), set())  # no list: the judge compares as before
            (st / ret_reg.LIST).write_text("# comment\n8c010100\n", encoding="utf-8")
            addrs = ret_reg.listed(st)
            meta = {"addr": "8c010100", "ret": "u32", "signature": "u32 fn_8c010100(void)"}
            self.assertTrue(ret_reg.judged(meta, addrs)["fr0_result"])
            self.assertNotIn("fr0_result", ret_reg.judged(dict(meta, ret="f32"), addrs))
            self.assertNotIn("fr0_result", ret_reg.judged(dict(meta, addr="8c010200"), addrs))
        exp, act = {"r0": 0, "fr0": 0x3f800000}, {"r0": 0, "fr0": 0}
        self.assertEqual(compare.compare_return({"ret": "u32"}, exp, act), [])  # before: fr0 never compared
        d = compare.compare_return({"ret": "u32", "fr0_result": True}, exp, act)
        self.assertEqual(len(d), 1)
        self.assertIn("raw_ret f32", d[0])
        self.assertEqual(compare.compare_return({"ret": "void", "fr0_result": True}, exp, dict(act, fr0=0x3f800000)),
                         [])

    def test_raw_ret_candidate(self):  # the callers decide: TC 8c04ea84's listing "returns fr0", its callers read r0
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t)
            caller = ["jsr @r1  ; call 8c010100", "_nop", "fcmp/gt fr0,fr3", "jsr @r2  ; call 8c010300", "_nop",
                      "tst r0,r0", "rts", "_nop"]
            for a, asm, ret in (("8c010100", ["rts", "_fldi0 fr0"], "u32"), ("8c010200", ["rts", "_mov #0,r0"], "u32"),
                                ("8c010300", ["rts", "_fldi0 fr0"], "u32"), ("8c010400", caller, "void")):
                callees = [{"addr": c, "regs": ["r4"]} for c in ("8c010100", "8c010300")] if a == "8c010400" else []
                (p / "harness" / a).mkdir(parents=True)
                (p / "harness" / a / "meta.json").write_text(json.dumps({"addr": a, "ret": ret, "callees": callees}),
                                                             encoding="utf-8")
                (p / "packets" / a).mkdir(parents=True)
                (p / "packets" / a / "asm.txt").write_text(listing(asm, int(a, 16)), encoding="utf-8")
            rp = ret_use.Project(p)
            self.assertTrue(ret_use.fr0_confirmed(rp.evidence("8c010100")))
            self.assertFalse(ret_use.fr0_confirmed(rp.evidence("8c010300")))
            c = rp.candidates()
            self.assertEqual([(x["addr"], x["case"], x["suggest"]) for x in c], [("8c010100", "fr0_exit", "f32")])


class FloatLits(unittest.TestCase):
    def image(self):
        img = bytearray(0x400)
        struct.pack_into("<f", img, 0x200, 1e-06)
        struct.pack_into("<f", img, 0x204, 0.5)
        struct.pack_into("<I", img, 0x208, 0x8c010300)  # a pointer: never an f32 constant
        return bytes(img)

    def test_pool(self):
        lines = ["mova 0x8c010200,r0", "fmov.s @r0+,fr3", "fmov.s @r0,fr4", "fcmp/gt fr0,fr3",
                 "mov.l 0x8c01020c,r1  ; = 0x40400000  (1077936128 / f32 3.0)",
                 "mova 0x8c010208,r0", "mov.l @r0,r2"]
        fpu = set()
        got = float_lits.pool_floats(insns(lines), self.image(), BASE, [(0x8c010000, 0x8c010200)], fpu)
        self.assertEqual(got, {0x8c010200: 0x358637bd, 0x8c010204: 0x3f000000, 0x8c01020c: 0x40400000})
        self.assertEqual(fpu, {0x8c010200, 0x8c010204})
        self.assertEqual(float_lits.pool_floats(insns(lines), self.image(), BASE, [(0x8c010000, 0x8c010400)]),
                         {0x8c01020c: 0x40400000})  # a mova into code (a jump table) is no pool

    def test_missing(self):
        c = {0x8c010200: 0x358637bd}  # 1e-06 (TC 8c035a0c)
        self.assertEqual(float_lits.missing(c, "if (d < 1.0f) {"), c)
        self.assertEqual(float_lits.missing(c, "/* 1e-06f */ if (d < 1.0f) {"), c)  # a comment does not count
        self.assertEqual(float_lits.missing(c, "if (d < 1e-06f) {"), {})
        self.assertEqual(float_lits.missing(c, "if (d < 0.000001f) {"), {})
        self.assertEqual(float_lits.missing(c, "d = rdf32(0x8c010200u);"), {})  # read from the pool itself
        defs = float_lits.defines(["#define SPEED_EPS 1.0e-6f\n", "static const f32 kHalf = 0.5f;\n"])
        self.assertEqual(float_lits.missing(c, "if (d < SPEED_EPS) {", defs), {})
        self.assertEqual(float_lits.missing({1: 0x39800000}, "x = (f32)y * (1.0f / 4096.0f);"), {})
        self.assertEqual(float_lits.missing({1: 0x40000000}, "x = y * 2;"), {})
        self.assertEqual(float_lits.missing({1: 0xc0c00000}, "x = -6.0f;"), {})
        self.assertEqual(float_lits.missing({1: 0xc0c00000}, "KC = 0.5f;\nwr32(F + 6u, r6);"),
                         {1: 0xc0c00000})  # TC 8c078f94: an integer 6 elsewhere is not -6.0f
        self.assertEqual(float_lits.missing({1: 0x40e00000}, "a.fr[7] = 0.0f;"), {1: 0x40e00000})
        step = float_lits.defines(["#define BALLISTIC_STEP 16        /* ticks per step */\n"])
        self.assertEqual(float_lits.missing({1: 0x41800000}, "const f32 step = (f32)BALLISTIC_STEP;", step), {})
        self.assertEqual(float_lits.missing({1: 0x3e199999}, "x = y * 0.15f;"), {1: 0x3e199999})  # one ulp off


class Wiring(unittest.TestCase):
    def test_scripts(self):
        locked = json.loads((SH4 / "target.json").read_text(encoding="utf-8"))["locked_scripts"]
        self.assertIn("TcFret.java", locked)
        for p in (SH4 / "ghidra_scripts").glob("*.java"):
            self.assertLessEqual(len(p.read_text(encoding="utf-8").splitlines()), 400, p.name)
        oracle = (SH4 / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        self.assertIn("v.rets[calls] = TcFret.at(floatRet, pc, v.rets[calls])", oracle)
        prep = (SH4 / "ghidra_scripts" / "TcPrepare.java").read_text(encoding="utf-8")
        self.assertIn('if (cp.ret.equals("f32")) floatRet.add(c.getEntryPoint().getOffset());', prep)
        self.assertIn("TcFguide.java", locked)
        vec = (SH4 / "ghidra_scripts" / "TcVectors.java").read_text(encoding="utf-8")
        g, f, h = (vec.index(x) for x in ("TcGuide.guide(v,", "TcFguide.guide(v, proto, listing, body, mem, seed, id, "
                                                               "idTry, rescue);", "applyHints(v, hints);"))
        self.assertLess(g, f)
        self.assertLess(f, h)

    def test_restamp_candidates(self):
        with tempfile.TemporaryDirectory() as t:
            h = pathlib.Path(t)
            for a, rec in (("8c010100", {"addr": "8c010200", "signature": "f32 fn_8c010200(void)"}),
                           ("8c010300", {"addr": "8c010200", "signature": "u32 fn_8c010200(void)"}),
                           ("8c010400", {"addr": "8c010200", "signature": "u32 fn_8c010200(void)",
                                         "ghidra_signature": "f32 fn_8c010200(void)"})):
                (h / a).mkdir()
                (h / a / "meta.json").write_text(json.dumps({"addr": a, "callees": [rec]}), encoding="utf-8")
            self.assertEqual(sr.fret_candidates(h), {"8c010100", "8c010400"})

    def test_f32_callee_reads(self):
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t)
            rec = {"addr": "8c010200", "regs": ["r4"], "signature": "f32 fn_8c010200(void)"}
            for a, asm in (("8c010100", ["jsr @r1", "_nop", "fcmp/gt fr0,fr3", "rts", "_nop"]),  # reads fr0
                           ("8c010300", ["jsr @r1", "_nop", "fldi0 fr0", "rts", "_nop"])):  # overwrites it
                (p / "harness" / a).mkdir(parents=True)
                (p / "harness" / a / "meta.json").write_text(json.dumps({"addr": a, "ret": "void", "callees": [rec]}),
                                                             encoding="utf-8")
                (p / "packets" / a).mkdir(parents=True)
                text = listing(asm, int(a, 16)).replace("jsr @r1", "jsr @r1  ; call 8c010200")
                (p / "packets" / a / "asm.txt").write_text(text, encoding="utf-8")
            rp = ret_use.Project(p)
            self.assertEqual(float_gaps.f32_callee_reads(rp, "8c010100", rp.meta["8c010100"]), ["8c010200"])
            self.assertEqual(float_gaps.f32_callee_reads(rp, "8c010300", rp.meta["8c010300"]), [])


if __name__ == "__main__":
    unittest.main()

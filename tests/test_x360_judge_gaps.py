"""The Xbox 360 judge gaps (branch x360-judge-gaps; docs/proofs/x360-judge-gaps.md), without Ghidra:
1. Registers kept across a call: under LTCG a caller keeps a volatile register live across a bl whose callee never
   writes it (Too Human 82976860 keeps r9 and f1..f4 across 82817340; 824c0a50 its loop counter r4 across
   824c0070). The mock zeroes only what the callee may write (TcFlow.clobbers), meta callees list the rest as
   "keeps", the agent text says so. Checked here: the wiring and the agent text (TcFlow needs Ghidra: the proof
   re-prepares), and TcFlow.literalZero: the r0 that p-code reads for a trap with TO = 31 or an AltiVec (RA|0)
   operand is no input (kept r0 made it one: 8277d310, 82d24cf0).
2. GameCube's judge rules, ported (judge-gaps: tests/test_track.py, test_gc_paint.py, test_gc_lastf.py):
   - TcTrack (tests/java/X360TrackStub, compiled against the Xbox 360 scripts): a listed callee gets no CA; an
     unlisted one gets the registers set, in order up to the first gap (r3..r10, f1..f13), its own parameters set at
     entry; after a call r3 and f1 are set, a zeroed register is not and a kept one stays as it was; float bits
     through lwz/ld, mr (`or d,s,s`) and stw/std, none left in a zeroed register; the step's word reads past the
     bank's load log;
   - x360_compare: CA and FN parsed; an integer NaN pattern compared exactly, a listed float NaN tolerant; CA
     registers compared, none without CA (as before);
   - the runner paints the stack before each vector (unset TcArgs slots read 0xa5..., two runs give one output);
   - tc_lastr/tc_lastf: the last call's other result, no call.
3. TcNaN (the shared PowerPC NaN rules, tested by judge-gaps' test_gc_nan.py): TcFixups' Xenon wiring.
Skipped parts: without ghidra_home and a JDK (TcTrack), without vcvars (the runtime).
usage: python tests/test_x360_judge_gaps.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import TOOL, X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402
from test_tail import CP, jdk  # noqa: E402

import x360_check  # noqa: E402
import x360_compare as cmp  # noqa: E402

SCRIPTS = X360 / "ghidra_scripts"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"
RT = X360 / "runtime"


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Track(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tmpdir("x360track_")
        src = os.pathsep.join(str(p) for p in (SCRIPTS, SHARED, X360 / "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-J-Xmx512m", "-d", str(cls.out), "-cp", CP,
                            "-sourcepath", src, str(TOOL / "tests" / "java" / "X360TrackStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            shutil.rmtree(cls.out, ignore_errors=True)
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-Xmx256m", "-cp", str(self.out) + os.pathsep + CP, "X360TrackStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return [ln.split(": ", 1)[1] if ": " in ln else "" for ln in r.stdout.splitlines()]

    def test_pointer_calls(self):
        vol = "r0,r4,r5,r6,r7,r8,r9,r10,r11,r12,f0,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,ctr"
        got = self.run_stub(f"""
T 82001000 r3,r4,f1
C 82002000 {vol}               # its own parameters, up to the first gap; a call through a register zeroes all
D r4,r6,f2
C 82001000 {vol}               # a listed callee: no CA
D r4,r5,f2,f13
C 82002000 r0,r5,r6,r7,r8,r10,r11,r12,f0,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,ctr  # keeps r4, r9, f2
C 82002000 -                   # r4 and f2 kept across the last call: still set
N 7fc00000
N ffffffff
N 7f800000
""")
        self.assertEqual(got, ["|CA r3 r4 f1; f1,r3; ", "; f1,r3; ", "|CA r3 r4 r5 f1 f2; f1,f2,r3,r4; ",
                               "|CA r3 r4 f1 f2; f1,f2,r3,r4; ", "true", "true", "false"])

    def test_literal_zero(self):
        # TcFlow.literalZero: a trap with TO = 31 and an AltiVec (RA|0) operand with RA = 0 read no register; the
        # words are Too Human's 8278fdf8, 82a45614 and 82d24a08 and their neighbours
        got = self.run_stub("""
Z 0fe00016    # twi 31,r0,0x16
Z 0fe50008    # twi 31,r5,8
Z 7fe32008    # tw 31,r3,r4
Z 0cda0000    # twi 6,r26,0: a condition, r26 is read
Z 7c832008    # tw 4,r3,r4
Z 7da0200c    # lvsl v13,r0,r4
Z 7c2029ce    # stvx v1,r0,r5
Z 7da5200c    # lvsl v13,r5,r4
Z 7c60202e    # lwzx r3,r0,r4: Ghidra reads (RA|0) itself
Z 7c602214    # add r3,r0,r4: r0 is r0
""")
        self.assertEqual(got, ["r0", "r5", "r3,r4", "", "", "r0", "r0", "", "", ""])

    def test_float_bits(self):
        got = self.run_stub("""
T - -
B r31                          # an lwz of a word a float store wrote
F stw r31 - - -                # a float store
F stw r30 - - -                # an integer store
F or r30 r31 r31 r30           # mr r30,r31: r30 holds float bits
F stdu r30 - - r1
F addi r30 - - r30             # r30 an integer now
F or r3 r31 r4 r3              # or of two registers: not a move
F or r3 r31 r31 r3
C 82002000 r0,r3,r4,r5,r6,r7,r8,r9,r10,r11,r12   # r3 zeroed (and the result), r31 kept; nothing set: CA
F stfs f1 - - -                # a float register store (TcTrack.store's, not flow's)
F or r31 r4 r4 r31             # mr of an integer
W
""")
        self.assertEqual(got, ["true r31", "false r31", "false r30,r31", "true r30,r31", "false r31", "false r31",
                               "false r3,r31", "|CA; f1,r3; r31", "false r31", "false ", "word 7fc00001 reads 1 -> 1"])

    def test_step_reads_past_the_log(self):
        step = text(SCRIPTS / "TcTrack.java").split("static void step(", 1)[1].split("\n    }\n", 1)[0]
        self.assertNotIn("word(emu", step)
        self.assertEqual(step.count("word(bank, "), 2)


def res(**kw):
    out = {"done": True, "faults": [], "r3": 0, "f1": 0, "writes": {}, "calls": [], "cov": set()}
    out.update(kw)
    return out


def words(a, data):
    return {a + i: b for i, b in enumerate(data)}


def call(target, *regs):
    rec = [target] + [0] * (cmp.CALL_FIELDS - 1)
    for r, v in regs:
        rec[cmp.REG_INDEX[r]] = v
    return rec


class Judge(unittest.TestCase):
    def judge(self, exp, act, callees=()):
        meta = {"ret": "u64", "callees": list(callees), "params": []}
        return cmp.compare_vector(meta, bytes(0x100), {"patches": []}, exp, act)

    def test_parse(self):
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t) / "expected.txt"
            p.write_text("V 0 dev\nRET 0 0\nCALL 82002000" + " 1" * (cmp.CALL_FIELDS - 1) + "\nCA r3 r4 f1 f13\n"
                         "FN 40000010 40000014\nE\n", encoding="utf-8")
            r = cmp.parse_results(p)
        self.assertEqual((r[0]["call_args"], r[0]["float_nan"]), ({0: ["r3", "r4", "f1", "f13"]},
                                                                  {0x40000010, 0x40000014}))

    def test_rules(self):
        w = 0x40000010
        neg1, neg5 = res(writes=words(w, b"\xff\xff\xff\xff")), res(writes=words(w, b"\xff\xff\xff\xfb"))
        self.assertTrue(self.judge(neg1, neg5))  # an integer -1 against -5: was taken as two NaNs
        self.assertEqual(self.judge(dict(neg1, float_nan={w}), neg5), [])
        self.assertTrue(self.judge(dict(neg1, float_nan={w + 4}), neg5))
        one = 0x3FF0000000000000
        exp = res(calls=[call(0x82002000, ("r3", 5), ("f13", one))], call_args={0: ["r3", "f13"]})
        self.assertEqual(self.judge(exp, res(calls=[call(0x82002000, ("r3", 5), ("f13", one))])), [])
        self.assertTrue(self.judge(exp, res(calls=[call(0x82002000, ("f13", one))])))
        self.assertTrue(self.judge(exp, res(calls=[call(0x82002000, ("r3", 5))])))
        self.assertEqual(self.judge(dict(exp, call_args={}), res(calls=[call(0x82002000)])), [])
        listed = [{"addr": "82002000", "regs": ["r4"]}]  # a listed callee's registers win over CA
        self.assertEqual(self.judge(exp, res(calls=[call(0x82002000)]), listed), [])


PAINT_C = r"""#include "tc_rt.h"
void fn_82010000(void)
{
    TcArgs a;
    a.r[0] = 7;
    (void)tc_call(0x82001000u, &a);
}
"""
LASTF_C = r"""#include "tc_rt.h"
u64 fn_82010000(void)
{
    TcArgs a = {0};
    u64 before = tc_lastr();
    f64 b = tc_lastf();
    u64 r = tc_call(0x82001000u, &a);
    f64 f = tc_lastf();
    f64 g = tc_callf(0x82002000u, &a);
    wr64(0x40000000u, before);
    wrf64(0x40000008u, b);
    wr64(0x40000010u, r);
    wrf64(0x40000018u, f);
    wrf64(0x40000020u, g);
    wr64(0x40000028u, tc_lastr());
    return 0;
}
"""
META = {"name": "fn_82010000", "params": []}
ZEROS = " ".join(["0"] * 13)
VECTORS = (f"V 0 dev\nR 0 0 0 0 0 0 0 0\nF {ZEROS}\nC 11 22\nD 4000000000000000 4008000000000000\nE\n"
           f"V 1 dev\nR 1 2 3 4 5 6 7 8\nF {ZEROS}\nE\n")


@unittest.skipUnless(vcvars64(), "no vcvars (MSVC) on this machine")
class Runtime(unittest.TestCase):
    def run_c(self, work_c, ret, runs=1):
        d = tmpdir("x360jg_")
        try:
            (d / "work.c").write_text(work_c, encoding="utf-8")
            (d / "thunk.c").write_text(x360_check.gen_thunk(dict(META, ret=ret, signature=f"{ret} fn_82010000(void)")),
                                       encoding="utf-8")
            srcs = " ".join(f'"{RT / s}"' for s in x360_check.SOURCES)
            rc, out = cl(d, f'/DTC_FMA_FUSED=0 /I"{RT}" /Ferunner.exe work.c thunk.c {srcs}')
            self.assertEqual(rc, 0, out[-3000:])
            (d / "image.bin").write_bytes(bytes(0x10400))
            (d / "code.txt").write_text("82010000 82010010\n", encoding="utf-8")
            (d / "vectors.txt").write_text(VECTORS, encoding="utf-8")
            outs = []
            for k in range(runs):
                run = subprocess.run([str(d / "runner.exe"), "image.bin", "code.txt", "vectors.txt", "all",
                                      f"out{k}.txt"], cwd=d, capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stderr)
                outs.append((d / f"out{k}.txt").read_text(encoding="utf-8"))
            return outs
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_painted_stack(self):
        outs = self.run_c(PAINT_C, "void", runs=2)
        self.assertEqual(outs[0], outs[1])
        calls = [ln.split() for ln in outs[0].splitlines() if ln.startswith("CALL")]
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertEqual(c[1:3], ["82001000", "0000000000000007"])
            self.assertEqual(c[3:], ["a5a5a5a5a5a5a5a5"] * (cmp.CALL_FIELDS - 2))  # r4..r10, f1..f13, s0..s7

    def test_last_results(self):
        got = self.run_c(LASTF_C, "u64")[0].split("V ")[1].splitlines()
        self.assertEqual([ln for ln in got if ln.startswith("W ")],
                         ["W 40000000 " + "00" * 16 + "0000000000000011" + "4000000000000000" + "4008000000000000"
                          + "0000000000000022"])
        self.assertEqual(sum(ln.startswith("CALL") for ln in got), 2)  # tc_lastr/tc_lastf are not calls

    def test_wiring(self):
        src = text(RT / "runner.c")
        self.assertLess(src.index("paint_stack();"), src.index("int code = run_one("))
        h = text(RT / "tc_rt.h")
        self.assertIn("u64 tc_lastr(void);", h)
        self.assertIn("f64 tc_lastf(void);", h)
        self.assertIn("A call through a function pointer (a target context.md does not list)", h)
        self.assertIn("except those context.md says the callee keeps", h)


class Wiring(unittest.TestCase):
    def test_locked(self):
        locked = json.loads(text(X360 / "target.json"))["locked_scripts"]
        for n in ("TcTrack.java", "TcNaN.java"):
            self.assertIn(n, locked)
        self.assertTrue((SHARED / "TcNaN.java").is_file())

    def test_hooks(self):
        o = text(SCRIPTS / "TcOracle.java")
        self.assertIn("res.tr = TcTrack.start();", o)
        self.assertIn("Set<String> zeroed = clobbered(prev, pca);\n"
                      "                    res.calls.add(callRecord(emu, pc) + TcTrack.call(pc, zeroed));", o)
        # (the vector phase runs both only when the emulator stepped without a fault)
        self.assertIn("if (stepped && fault[0] == null) {\n                        fix.after(emu, ram, bank.stored, bank.reads);\n"
                      "                        TcTrack.step(emu, in, bank);", o)
        self.assertIn("TcTrack.exit(emu);", o)
        self.assertIn("direct ? flow.clobbers(pca) : TcFlow.VOLATILE", o)
        self.assertIn("eout.append(TcTrack.exit(r.tr));", text(SCRIPTS / "TcVectors.java"))
        p = text(SCRIPTS / "TcPrepare.java")
        self.assertLess(p.index("Proto proto = prototypeOf(di, fn);"),
                        p.index("TcTrack.on = TcTrack.of(listing, body, calledBy(), proto, flow);"))
        self.assertIn('",\\"keeps\\":" + jsonList(keeps)', p)
        self.assertIn("stores.add(stored);", text(SCRIPTS / "TcBank.java"))
        f = text(SCRIPTS / "TcFlow.java")
        self.assertIn("b.def.addAll(code == PcodeOp.CALL ? clobbers(op.getInput(0).getAddress()) : VOLATILE);", f)
        x = text(SCRIPTS / "TcFixups.java")
        for k in ('kind = "sign";', 'kind = "lfs";', "TcNaN.arith(op, srcBits)", "TcNaN.arith(op, fma)"):
            self.assertIn(k, x)

    def test_agent_keeps(self):
        from unittest import mock
        import x360_agent
        self.assertIn("tc_lastr() (r3) or tc_lastf() (f1)", text(X360 / "x360_agent.py"))
        meta = {"addr": "82976860", "signature": "void fn_82976860(void)", "params": [], "ret": "void", "globals": [],
                "callees": [{"addr": "82817340", "regs": ["r3"], "signature": "void f(u64)",
                             "keeps": ["r9", "f2", "f3", "f4"]},
                            {"addr": "82817400", "regs": [], "signature": "void g(void)"}]}
        with mock.patch.object(x360_agent, "packet_files", lambda d: []):
            md = x360_agent.context_md(meta, {})
        self.assertIn("- 0x82817340: Ghidra's guess `void f(u64)`. Argument registers compared: r3. Keeps r9, f2, f3, f4 "
                      "(unchanged by the call)\n", md)
        self.assertIn("- 0x82817400: Ghidra's guess `void g(void)`. Argument registers compared: none\n", md)
        self.assertIn("except the ones a callee above keeps", md)


class Translator(unittest.TestCase):
    """The two translator fixes the Mega Man 10 run found (xenontranslate; no judge, no Ghidra)."""

    def test_float_condition_is_not_turned_around(self):
        """`if (f0 < c) goto L` falls through for a NaN, so the structured `if` must read `!(f0 < c)`, never
        `f0 >= c` (false for a NaN); an integer comparison still turns around."""
        sys.path.insert(0, str(X360 / "translator"))
        try:
            from xenontranslate import expr as X
            from xenontranslate import structure as S
        finally:
            sys.path.remove(str(X360 / "translator"))
        f = X.render(X.compare("<", X.Var("f0", "f64"), X.Var("f1", "f64")))
        self.assertEqual(S.neg(f), f"!({f})")
        self.assertEqual(S.neg(f"({f})"), f"!({f})")
        both = f"{f} && r3 == 0u"
        self.assertEqual(S.neg(both), f"!({f}) || r3 != 0u")
        i = X.render(X.compare("<", X.Var("r3", "s32"), X.Var("r4", "s32")))
        self.assertEqual(S.neg(i), "r3 >= r4")
        e = X.render(X.compare("==", X.Var("f0", "f64"), X.Var("f1", "f64")))
        self.assertEqual(S.neg(e), "f0 != f1")  # == and != turn around exactly, NaN included

    def test_kept_registers_after_a_call(self):
        """A callee that never writes a volatile register keeps it in the judge's mock (meta "keeps"): the translator
        must not zero it after the call, or a caller that reads r5 after calling 8220a038 reads 0."""
        sys.path.insert(0, str(X360 / "translator"))
        try:
            from xenontranslate.translate import Signature
        finally:
            sys.path.remove(str(X360 / "translator"))
        meta = {"name": "fn_1", "signature": "void fn_1(void)", "params": [], "ret": "void",
                "callees": [{"addr": "8220a038", "regs": ["r3", "r4"], "signature": "u64 f(u64 a, u32 b)",
                             "keeps": ["r0", "r5", "f2", "ctr"]},
                            {"addr": "82200000", "regs": ["r3"], "signature": "void g(u64 a)"}]}
        sig = Signature.from_meta(meta)
        self.assertEqual(sig.keeps, {0x8220A038: {"r0", "r5", "f2", "ctr"}})
        self.assertEqual(sig.callees[0x8220A038], ["r3", "r4"])
        self.assertIsNone(Signature.generic_for(0x82000000).keeps)


if __name__ == "__main__":
    unittest.main()

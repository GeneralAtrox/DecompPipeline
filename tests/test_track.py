"""Judge holes 4 and 5 (TcTrack, on the Dreamcast, Saturn and GameCube targets).
4. NaN-looking integer words: the judges took any two memory words whose bit patterns are NaNs as equal, so -1 and -5
   in a stored word passed. Now only the words a float store of the original left a NaN in (expected.txt FN) are
   NaN-tolerant (a float store: from a float register, or of float bits moved through the integer registers); every
   other word compares bit for bit. Float registers keep the rule (their payload is the FPU's).
5. Calls through function pointers: a call to a target meta's callees do not list had no argument compared. Now the
   oracle lists the argument registers the original set since its entry or its last call, in order up to the first
   gap (expected.txt CA after the CALL line), and the judges compare them as a listed callee's; the readable runtime
   logs them through a prototype built from CA (readable_check.pointer_protos).
Pass and fail controls:
1. TcTrack (tests/java/TcTrackStub, TcTrackSh2Stub, compiled against each target's scripts): a listed callee gets no
   CA; an unlisted one gets the registers set, in order up to the first gap (SH-4 floats by pairs), its own
   parameters set at entry, nothing carried past a call (GameCube: r3 and f1, the callee's results); NaN patterns;
   the SH-4 register split (drN, fvN); float bits through the integer registers (TcTrackBitsStub, TcTrackPpcBitsStub:
   sts FPUL, mov and mov.l; mr and stw), none left in a register a call clobbers; the fmov store encodings; an SH-4
   step's set registers from the p-code path its FPSCR SZ/PR bits take (TcTrackPathStub: fmov.s @r0,fr6 with SZ 0
   sets fr6, not dr6's fr7), every output where a condition reads anything else; the GameCube step's own word
   reads past the bank's load log (TcTrackPpcBankStub);
2. the judges (core/compare.py, ppc_compare.py): CA and FN parsed; an integer NaN pattern compared exactly, a listed
   float NaN tolerant; CA registers compared, none without CA (as before);
3. readable_check.pointer_protos and the call trap's '?' result;
4. the wiring: TcTrack locked on each target, its hooks in the oracles, the agent-facing notes.
The oracles need Ghidra: the re-prepares in docs/proofs/judge-gaps.md judge them.
Synthetic data. Run: python -W error tests/test_track.py.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import compare, readable_check  # noqa: E402
from test_tail import CP, jdk  # noqa: E402

T = ROOT / "targets"
DC, SAT, GC = T / "dreamcast_sh4", T / "saturn_sh2", T / "gamecube_ppc"
spec = importlib.util.spec_from_file_location("ppc_compare_t", GC / "ppc_compare.py")
ppc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ppc)

SPLIT = """public class TcSplitStub {
    public static void main(String[] a) {
        for (String r : a) System.out.println(String.join(",", TcTrack.split(r)));
    }
}
"""


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Track(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="track_", dir=ROOT / "tests" / ".work")
        base = pathlib.Path(cls.tmp.name)
        (base / "TcSplitStub.java").write_text(SPLIT, encoding="utf-8")
        cls.out = {}
        dc = ["TcTrackStub.java", "TcTrackBitsStub.java", "TcTrackPathStub.java", str(base / "TcSplitStub.java")]
        for name, tgt, stubs in (("dc", DC, dc),
                                 ("gc", GC, ["TcTrackStub.java", "TcTrackPpcBitsStub.java", "TcTrackPpcBankStub.java"]),
                                 ("sat", SAT, ["TcTrackSh2Stub.java"])):
            out = base / name
            out.mkdir()
            src = os.pathsep.join(str(tgt / d) for d in ("ghidra_scripts", "template"))
            files = [s if os.path.isabs(s) else str(ROOT / "tests" / "java" / s) for s in stubs]
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(out), "-cp", CP, "-sourcepath", src,
                                *files], capture_output=True, text=True, timeout=300)
            if r.returncode:
                raise AssertionError(name + r.stdout + r.stderr)
            cls.out[name] = out

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_java(self, target, main, args):
        r = subprocess.run([jdk("java"), "-cp", str(self.out[target]) + os.pathsep + CP, main, *args],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.splitlines()

    def run_stub(self, target, cases, main="TcTrackStub"):
        f = self.out[target] / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        return [ln.split(": ", 1)[1] if ": " in ln else "" for ln in self.run_java(target, main, [str(f)])]

    def test_sh4(self):
        got = self.run_stub("dc", """
T 8c001000 r4
C 8c001000                     # a listed callee: no CA (its prototype's registers are compared)
D r5
C 8c002000                     # r4 is the callee's now and r5 follows a gap: nothing
D r4,r5,r7,fr5,fr8
C 8c002000                     # r6 missing: r7 is no argument; fr4/fr5 a pair, fr6/fr7 none: fr8 no argument
C 8c002000                     # nothing set since the last call
T - r4,r5,fr4
C 0                            # its own parameters count as set at entry (a NULL target too)
T - -
D fr6,fr4
C 8c003000
N 7fbfffff
N ffffffff                     # -1: a NaN's pattern (an integer word holding it compares exactly)
N 7f800000
N 00000000
""")
        self.assertEqual(got, ["", "|CA", "|CA r4 r5 fr5", "|CA", "|CA r4 r5 fr4", "|CA fr4 fr6",
                               "true", "true", "false", "false"])
        self.assertEqual(self.run_java("dc", "TcSplitStub", ["dr4", "fv8", "R5", "fr11", "xd2"]),
                         ["fr4,fr5", "fr8,fr9,fr10,fr11", "r5", "fr11", "xd2"])

    def test_sh2(self):
        got = self.run_stub("sat", "T 06001000 r4\nC 06001000\nD r5,r6\nC 06002000\nD r4,r6\nC 06002000\n"
                                   "T - r4,r5\nD r7\nC 06003000\n", main="TcTrackSh2Stub")
        self.assertEqual(got, ["", "|CA", "|CA r4", "|CA r4 r5"])

    def test_ppc(self):
        got = self.run_stub("gc", """
T 80001000 r3,f1
C 80002000                     # its own parameters
C 80002000                     # after a call: its results r3 and f1
D r4,r6,f2
C 80001000                     # a listed callee: no CA
D r5,f3
C 80002000                     # r4 and f2 were set before the last call: r3 and f1 only (r5, f3 follow gaps)
N 7fc00000
N ffffffff
""")
        self.assertEqual(got, ["|CA r3 f1", "|CA r3 f1", "", "|CA r3 f1", "true", "true"])

    def test_sh4_float_bits(self):
        got = self.run_stub("dc", """
F 035a r3                      # sts FPUL,r3: r3 holds float bits
F 6533 r5                      # mov r3,r5: so does r5
F 2452 -                       # mov.l r5,@r4: a float store to @r4
F 2f56 r15                     # mov.l r5,@-r15
F 0456 -                       # mov.l r5,@(r0,r4)
F 1452 -                       # mov.l r5,@(8,r4): 2 + 8
F e501 r5                      # mov #1,r5: an integer now
F 2452 -                       # mov.l r5,@r4: an integer store
F 4f52 r15                     # sts.l FPUL,@-r15
F 6033 r0                      # mov r3,r0
F 095a r9                      # sts FPUL,r9
C 8c002000                     # a call: r0..r7 are the callee's, r9 is kept
S f57a                         # fmov.s fr7,@r5
S fce7                         # fmov fr14,@(r0,r12)
S ff3b                         # fmov.s fr3,@-r15
S f758                         # fmov.s @r5,fr7: a load
S 4f52                         # sts.l: flow's
""", main="TcTrackBitsStub")
        self.assertEqual(got, ["-1 r3", "-1 r3,r5", "0 r3,r5", "0 r3,r5", "1 r3,r5", "10 r3,r5", "-1 r3", "-1 r3",
                               "0 r3", "-1 r0,r3", "-1 r0,r3,r9", "r9", "5", "28", "15", "-1", "-1"])

    def test_sh4_mode_paths(self):
        got = self.run_stub("dc", """
P fmov 0 0                     # fmov.s @r4,fr6 with SZ 0: fr6 only (was fr6 and dr6's fr7)
P fmov 1 0                     # SZ 1: dr6
P fmov 0 1                     # PR does not choose an fmov's width
P skip 0 0                     # fldi1 fr4 runs with PR 0
P skip 0 1                     # and does nothing with PR 1
P next 0 0
P next 0 1                     # goto inst_next: nothing
P const 0 1                    # the double form's decode-time condition is false: fr5 even with PR 1
P const 0 0
P other 0 0                    # a branch on T: null (every output counts, as before)
P plain 1 1                    # no branch: r4, fv8 split
""", main="TcTrackPathStub")
        self.assertEqual(got, ["fr6", "fr6,fr7", "fr6", "fr4", "", "fr8", "", "fr5", "fr5", "null",
                               "fr10,fr11,fr8,fr9,r4"])

    def test_ppc_float_bits(self):
        got = self.run_stub("gc", """
B r31                          # an lwz of a word a float store wrote
F stw r31 - -                  # a float store
F stw r30 - -                  # an integer store
F mr r30 r31 r30               # mr r30,r31: r30 holds float bits
F stwu r30 - r1
F addi r30 - r30               # r30 an integer now
F mr r3 r31 r3
C 80002000                     # a call: r0 and r3..r12 are the callee's, r31 is kept
F stfs f1 - -                  # a float register store (TcTrack.store's, not flow's)
F mr r31 r4 r31                # mr of an integer
""", main="TcTrackPpcBitsStub")
        self.assertEqual(got, ["true r31", "false r31", "false r30,r31", "true r30,r31", "false r31", "false r3,r31",
                               "r31", "false r31", "false "])

    def test_ppc_word_unlogged(self):
        # an lwz of a float-stored word: TcTrack's own read leaves the step's loads alone (Skies 80100bf8 threw a
        # ConcurrentModificationException on every vector, and the extra loads joined the load log and rule R8)
        self.assertEqual(self.run_java("gc", "TcTrackPpcBankStub", []), ["word 7fc00001 reads 1 -> 1 uncached 7fc00001"])
        step = text(GC / "ghidra_scripts" / "TcTrack.java").split("static void step(", 1)[1].split("\n    }\n", 1)[0]
        self.assertNotIn("word(emu", step)
        self.assertEqual(step.count("word(bank, "), 2)


def result(**kw):
    out = {"done": True, "faults": [], "r0": 0, "fr0": 0, "writes": {}, "calls": [], "xf": [0] * 16, "cov": set()}
    out.update(kw)
    return out


def words(a, data):
    return {a + i: b for i, b in enumerate(data)}


def call(target, *regs):
    rec = [target] + [0] * (compare.CALL_FIELDS - 1)
    for r, v in regs:
        rec[compare.REG_INDEX[r]] = v
    return rec


class Judge(unittest.TestCase):
    def judge(self, exp, act, callees=()):
        return compare.compare_vector({"ret": "void", "callees": list(callees)}, b"", {"patches": []}, exp, act)

    def test_parse(self):
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t) / "expected.txt"
            p.write_text("V 0 dev\nRET 0 0\nCALL 8c001000 1 2\nCALL 8c002000 3 4\nCA r4 fr4\nFN 8c100000 8c100008\n"
                         "E\nV 1 dev\nRET 0 0\nCALL 8c002000\nCA\nE\n", encoding="utf-8")
            res = compare.parse_results(p)
        self.assertEqual(len(res[0]["calls"]), 2)
        self.assertEqual(res[0]["call_args"], {1: ["r4", "fr4"]})
        self.assertEqual(res[0]["float_nan"], {0x8c100000, 0x8c100008})
        self.assertEqual((res[1]["call_args"], res[1].get("float_nan")), ({0: []}, None))

    def test_nan_words(self):
        w = 0x8c100000
        neg1, neg5 = result(writes=words(w, b"\xff\xff\xff\xff")), result(writes=words(w, b"\xfb\xff\xff\xff"))
        self.assertTrue(self.judge(neg1, neg5))                                  # -1 and -5: integers, exact
        self.assertEqual(self.judge(dict(neg1, float_nan={w}), neg5), [])        # a float NaN the original stored
        qnan, dnan = result(writes=words(w, b"\x00\x00\xc0\x7f")), result(writes=words(w, b"\xff\xff\xbf\x7f"))
        self.assertTrue(self.judge(qnan, dnan))
        self.assertEqual(self.judge(dict(qnan, float_nan={w}), dnan), [])
        self.assertTrue(self.judge(dict(qnan, float_nan={w + 4}), dnan))         # another word listed
        self.assertEqual(self.judge(neg1, neg1), [])
        self.assertTrue(self.judge(dict(neg1, float_nan={w}), result(writes=words(w, b"\x00\x00\x80\x3f"))))

    def test_pointer_calls(self):
        exp = result(calls=[call(0x8c002000, ("r4", 7), ("r5", 9), ("fr4", 0x3f800000))], call_args={0: ["r4", "fr4"]})
        self.assertEqual(self.judge(exp, result(calls=[call(0x8c002000, ("r4", 7), ("fr4", 0x3f800000))])), [])
        self.assertTrue(self.judge(exp, result(calls=[call(0x8c002000, ("fr4", 0x3f800000))])))    # r4 left out
        self.assertTrue(self.judge(exp, result(calls=[call(0x8c002000, ("r4", 7))])))               # fr4 left out
        no_ca = dict(exp, call_args={})
        self.assertEqual(self.judge(no_ca, result(calls=[call(0x8c002000)])), [])  # no CA: nothing, as before
        listed = [{"addr": "8c002000", "regs": ["r5"]}]                           # a listed callee: its own registers
        self.assertTrue(self.judge(exp, result(calls=[call(0x8c002000, ("r4", 7), ("fr4", 0x3f800000))]), listed))

    def test_readable_protos(self):
        exp = {0: result(calls=[call(0x8c002000), call(0x8c003000), call(0x8c004000)],
                         call_args={0: ["r4", "r5", "fr4", "fr5"], 1: [], 2: ["r4"]}),
               1: result(calls=[call(0x8c004000)], call_args={0: ["r4", "r5"]})}
        self.assertEqual(readable_check.pointer_protos(exp),
                         {0x8c002000: ("iiff", "?", "45ab"), 0x8c004000: ("ii", "?", "45")})
        veh = text(DC / "runtime_rd" / "rd_veh.c")
        self.assertIn("char ret = p && p->ret != '?' ? p->ret : guess_ret(", veh)
        src = text(ROOT / "core" / "readable_check.py")
        self.assertIn('for a, v in pointer_protos(check.parse_results(harness / "expected.txt")).items():\n'
                      "            protos.setdefault(a, v)", src)


class PpcJudge(unittest.TestCase):
    def judge(self, exp, act, callees=()):
        return ppc.compare_vector({"ret": "void", "callees": list(callees), "params": []}, [], {"patches": []}, exp, act)

    def res(self, **kw):
        out = {"done": True, "faults": [], "r3": 0, "r4": 0, "f1": 0, "writes": {}, "calls": [], "cov": set()}
        out.update(kw)
        return out

    def call(self, target, *regs):
        rec = [target] + [0] * (ppc.CALL_FIELDS - 1)
        for r, v in regs:
            rec[ppc.REG_INDEX[r]] = v
        return rec

    def test_parse(self):
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t) / "expected.txt"
            p.write_text("V 0 dev\nRET 0 0 0\nCALL 80002000" + " 1" * 24 + " nofp\nCA r3 f1\nFN 80100000\nE\n",
                         encoding="utf-8")
            res = ppc.parse_results(p)
        self.assertEqual((res[0]["call_args"], res[0]["float_nan"], res[0]["nofp"]), ({0: ["r3", "f1"]}, {0x80100000},
                                                                                       {0}))

    def test_rules(self):
        w = 0x80100000
        neg1, neg5 = self.res(writes=words(w, b"\xff\xff\xff\xff")), self.res(writes=words(w, b"\xff\xff\xff\xfb"))
        self.assertTrue(self.judge(neg1, neg5))
        self.assertEqual(self.judge(dict(neg1, float_nan={w}), neg5), [])
        exp = self.res(calls=[self.call(0x80002000, ("r3", 5), ("f1", 0x3ff0000000000000))], call_args={0: ["r3", "f1"]})
        self.assertEqual(self.judge(exp, self.res(calls=[self.call(0x80002000, ("r3", 5), ("f1", 0x3ff0000000000000))])),
                         [])
        self.assertTrue(self.judge(exp, self.res(calls=[self.call(0x80002000, ("f1", 0x3ff0000000000000))])))
        self.assertEqual(self.judge(dict(exp, nofp={0}), self.res(calls=[self.call(0x80002000, ("r3", 5))])), [])
        self.assertEqual(self.judge(dict(exp, call_args={}), self.res(calls=[self.call(0x80002000)])), [])


class Wiring(unittest.TestCase):
    def test_locked(self):
        for tgt in (DC, SAT, GC):
            self.assertIn("TcTrack.java", json.loads(text(tgt / "target.json"))["locked_scripts"], tgt.name)
        for name in ("TcTrack.java", "TcOracle.java", "TcPrepare.java", "TcExit.java", "TcHw.java"):
            self.assertLessEqual(len(text(DC / "ghidra_scripts" / name).splitlines()), 400, name)

    def test_hooks(self):
        o = text(DC / "ghidra_scripts" / "TcOracle.java")
        self.assertIn("res.fs = TcFpscr.start(); res.tr = TcTrack.start();", o)
        self.assertIn("res.calls.add(callRecord(emu, pc) + TcTrack.call(pc)); TcFpscr.call(emu);", o)
        self.assertIn("String fpuWhy = fpu.after(emu, pca, zeroed); TcTrack.step(emu, loadView, pca, specs, pend);", o)
        self.assertIn("TcFpscr.exit(emu); TcTrack.exit(emu);", text(DC / "ghidra_scripts" / "TcExit.java"))
        self.assertIn("+ TcFpscr.exit(r.fs) + TcTrack.exit(r.tr);", text(DC / "ghidra_scripts" / "TcHw.java"))
        p = text(DC / "ghidra_scripts" / "TcPrepare.java")
        self.assertLess(p.index("TcTrack.on = null;"), p.index("Proto proto = prototypeOf(di, fn);"))
        self.assertLess(p.index("Proto proto = prototypeOf(di, fn);"),
                        p.index("TcTrack.on = TcTrack.of(listing, body, calledBy(fn, body), proto);"))
        o = text(SAT / "ghidra_scripts" / "TcOracle.java")
        self.assertIn("res.tr = TcTrack.start();", o)
        self.assertIn("res.calls.add(callRecord(emu, pc) + TcTrack.call(pc));", o)
        self.assertIn("TcTrack.step(loadView, pca);", o)
        p = text(SAT / "ghidra_scripts" / "TcPrepare.java")
        self.assertLess(p.index("TcTrack.on = null;"),
                        p.index("TcTrack.on = TcTrack.of(listing, body, calledBy(fn, body), proto);"))
        o = text(GC / "ghidra_scripts" / "TcOracle.java")
        self.assertIn('(prev >= 0 && noFloatArgs(prev) ? " nofp" : "") + TcTrack.call(pc));', o)
        self.assertIn("fix.after(emu, ram, bank.stored, bank.reads);\n                    TcTrack.step(emu, in, bank);", o)
        self.assertIn("TcTrack.exit(emu);", o)
        self.assertIn("eout.append(TcTrack.exit(r.tr));", text(GC / "ghidra_scripts" / "TcVectors.java"))
        self.assertIn("TcTrack.on = TcTrack.of(listing, body, calledBy(), proto);",
                      text(GC / "ghidra_scripts" / "TcPrepare.java"))
        self.assertIn("stores.add(stored);", text(GC / "ghidra_scripts" / "TcBank.java"))

    def test_notes(self):
        for tgt in (DC, SAT, GC):
            self.assertIn("A call through a function pointer (a target\n * context.md does not list)"
                          if tgt is GC else "A call through a function pointer (a target context.md does not list)",
                          text(tgt / "runtime" / "tc_rt.h"), tgt.name)


if __name__ == "__main__":
    unittest.main()

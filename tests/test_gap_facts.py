"""Mocked callees that write through a pointer, the two Toy Commander gaps (project.json "mock_write_facts"):
- 8c04b56a SoundVoice_UpdateChannels reads back the volume and pitch 8c04b328 SoundPart_Evaluate stores at @r5 and
  @r6 (fmov.s on every path: 8c04b378/8c04b37e, 8c04b3e2/8c04b3e8): a plain fact {"r5": 4, "r6": 4}, no code change;
- 8c048a28 Track_FindLabel (and 8c048ad8) keeps its script cursor at @r15 and hands it to the operand readers
  8c048914 Script_ReadIntConst / 8c0489a8 Script_ReadFloatConst (r4 = r15), which move it past the operand by an
  amount the bytes decide (align 2, a 2-byte op, then c2 +1, c3 align 2 +2, c4 align 4 +4; b6 align 4 +4). Mocked,
  the cursor stayed, so readable C that never took it back passed. The fact's new kind, an advance, moves the word
  as the callee does, on both sides.
Pass and fail controls:
1. the listing (core/strict_judge): a plain fact as before; an advance's token; every malformed one refused, and an
   advance off the Dreamcast; the facts lines; selective_restamp --change mockwrite takes only the listed callees'
   callers, and its baselines' facts are those without the list;
2. the oracle (compiled TcStrict): the facts' parse, header lines U and meta "mock_writes" (byte for byte as before
   for a plain fact), the little-endian word; the scripts stay at 400 lines;
3. both runners (raw and readable, compiled from source) on an operand-reader loop: the cursor moves as a Python
   model of the callee says (and as hand-worked offsets say), nothing for another callee, no line U, an op outside
   RAM; with no advance line the raw runner's output is byte-identical to run-flag's runtime (the branch base), with
   the strict judge's other lines too;
4. the agents' note names the callee only for an advance.
The oracle's own run needs Ghidra: the re-prepare of 8c048a28, 8c048ad8 and 8c04b56a on Toy Commander's copy judges
it (coord/s/cli/gap-facts.result.md). Synthetic data. Run: python -W error tests/test_gap_facts.py.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_tail import CP, jdk  # noqa: E402
import selective_restamp as sr  # noqa: E402
from core import agent, check, paths, readable_check, strict_judge, toolcfg  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"
WORK = ROOT / "tests" / ".work"
VC64 = toolcfg.load(ROOT).get("vcvars")
BASE = "7119d5d25781ec4cf13b5b15bb353123b154529b"  # run-flag, the base (a commit: a clone may lack the branch)
INT = {"advance": {"align": 2, "op": 2, "operands": {"c2": [1, 1], "c3": [2, 2], "c4": [4, 4]}}}
FLT = {"advance": {"align": 2, "op": 2, "operands": {"0xb6": [4, 4]}}}
FACTS = {"8c04b328": {"r5": 4, "r6": 4, "why": "fmov.s stores at @r5 and @r6 on every path"},
         "8c048914": {"r4": INT, "why": "8c048918..8c048930 and the c2/c3/c4 tails move @r4"},
         "8c0489a8": {"r4": FLT, "why": "8c0489ac..8c0489c4 and the b6 tail move @r4"}}
CFG = {"target": "dreamcast_sh4", "abi": {"int_args": ["r4", "r5", "r6", "r7"]}, "mock_writes": True,
       "mock_write_facts": FACTS}
INT_TOK, FLT_TOK = "a2.2:c2.1.1:c3.2.2:c4.4.4", "a2.2:b6.4.4"


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


def model(mem, pc, align, opb, ops):
    """The callee's cursor (core/strict_judge.advance): mem {addr: byte}; None when the op is not there."""
    up = (lambda v, a: (v + a - 1) & -a & 0xFFFFFFFF)
    pc = up(pc, align)
    if any(pc + k not in mem for k in range(opb)):
        return None
    op = int.from_bytes(bytes(mem[pc + k] for k in range(opb)), "little")
    pc += opb
    if op in ops:
        pc = up(pc, ops[op][0]) + ops[op][1]
    return pc & 0xFFFFFFFF


class Listing(unittest.TestCase):
    def test_facts(self):
        self.assertEqual(strict_judge.write_facts(CFG), [("8c048914", "r4", INT_TOK), ("8c0489a8", "r4", FLT_TOK),
                                                         ("8c04b328", "r5", 4), ("8c04b328", "r6", 4)])
        self.assertEqual(strict_judge.facts_lines(CFG)[1:], [f"mock_write 8c048914 r4 {INT_TOK}",
                                                             f"mock_write 8c0489a8 r4 {FLT_TOK}",
                                                             "mock_write 8c04b328 r5 4", "mock_write 8c04b328 r6 4"])
        plain = dict(CFG, mock_write_facts={"8c04b328": FACTS["8c04b328"]})  # a plain fact: as before
        self.assertEqual(strict_judge.facts_lines(plain), ["mock_writes 1", "mock_write 8c04b328 r5 4",
                                                           "mock_write 8c04b328 r6 4"])
        self.assertEqual(strict_judge.facts_lines(dict(CFG, mock_writes=False)), [])  # off: no line, no stamp move

    def test_refused(self):
        a = INT["advance"]
        for bad in ({"advance": dict(a, align=3)}, {"advance": dict(a, op=8)}, {"advance": dict(a, align=True)},
                    {"advance": dict(a, operands={})}, {"advance": dict(a, operands={"10000": [1, 1]})},
                    {"advance": dict(a, operands={"zz": [1, 1]})}, {"advance": dict(a, operands={"c2": [3, 1]})},
                    {"advance": dict(a, operands={"c2": [1, 257]})}, {"advance": dict(a, operands={"c2": [1]})},
                    {"advance": dict(a, operands={"c2": [1, 1], "0xc2": [2, 2]})},
                    {"advance": {k: v for k, v in a.items() if k != "op"}}, {"advance": dict(a, more=1)},
                    dict(INT, size=4), {"advance": [2, 2]}):
            with self.assertRaises(ValueError, msg=bad):
                strict_judge.write_facts(dict(CFG, mock_write_facts={"8c048914": {"r4": bad, "why": "x"}}))
        for t in ("saturn_sh2", "gamecube_ppc", None):  # the Dreamcast's little-endian words only
            with self.assertRaises(ValueError, msg=t):
                strict_judge.write_facts(dict(CFG, target=t))
        with self.assertRaises(ValueError):  # evidence still required
            strict_judge.write_facts(dict(CFG, mock_write_facts={"8c048914": {"r4": INT}}))

    def test_restamp(self):
        with tempfile.TemporaryDirectory(dir=TMP) as tmp:
            h = pathlib.Path(tmp)
            for fn, callees in (("8c048a28", ["8c048914", "8c0489a8"]), ("8c04b56a", ["0x8C04B328", "8c04b3f4"]),
                                ("8c061aac", ["8c061394"]), ("8c000000", [])):
                (h / fn).mkdir()
                (h / fn / "meta.json").write_text(json.dumps({"callees": [{"addr": c} for c in callees]}))
            self.assertEqual(sr.mockwrite_candidates(CFG, h), {"8c048a28", "8c04b56a"})
            self.assertEqual(sr.mockwrite_candidates(dict(CFG, mock_write_facts={}), h), set())
        src = text(ROOT / "tools" / "selective_restamp.py")
        self.assertIn('"mockwrite"', src)
        self.assertIn('k == "mock_write_facts"', src)  # the baselines' facts: without the list


STUB = """import java.util.*;
public class GapStub {
    public static void main(String[] a) {
        TcFacts f = TcFacts.defaults();
        f.strict = 2;
        int k = a.length > 0 && a[0].startsWith("@") ? 1 : 0;  // "@callee": the function's one direct callee
        if (k == 1) TcStrict.direct = new HashSet<>(List.of(Long.parseLong(a[0].substring(1), 16)));
        for (int i = k; i + 2 < a.length; i += 3) f.mockWrites.add(new String[] {a[i], a[i + 1], a[i + 2]});
        for (long[] w : TcStrict.facts(f)) System.out.println(Arrays.toString(w));
        System.out.print(TcStrict.header(f));
        System.out.println(TcStrict.meta(f).replace("\\n", "~"));
        System.out.println(Long.toHexString(TcStrict.LE.applyAsLong(new byte[] {(byte) 0xc2, 0, 0x05, (byte) 0x8c})) + " "
            + Long.toHexString(TcStrict.LE.applyAsLong(new byte[] {(byte) 0xb6, 0})));
    }
}
"""


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    def test_facts_header_meta(self):
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gapf_", dir=WORK) as tmp:
            d = pathlib.Path(tmp)
            (d / "GapStub.java").write_text(STUB, encoding="utf-8")
            src = os.pathsep.join(str(SH4 / x) for x in ("ghidra_scripts", "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(d), "-cp", CP, "-sourcepath", src,
                                str(d / "GapStub.java")], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            LINES = ["8c048914", "r4", INT_TOK, "8c04b328", "r5", "4", "8c04b328", "r6", "4"]
            runs = [subprocess.run([jdk("java"), "-cp", str(d) + os.pathsep + CP, "GapStub", *args],
                                   capture_output=True, text=True, timeout=120)
                    for args in (LINES, [], ["@8c04b328", *LINES], ["@8c061394", *LINES])]
        for r in runs:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(runs[0].stdout.splitlines(), [
            "[2349107476, 1, -1, 2, 2, 194, 1, 1, 195, 2, 2, 196, 4, 4]",
            "[2349118248, 2, 4]", "[2349118248, 3, 4]",
            "J 2", f"U 8c048914 1 {INT_TOK}", "U 8c04b328 2 4", "U 8c04b328 3 4",
            ',~  "mock_writes": [{"callee": "8c048914", "field": "r4", "advance": "' + INT_TOK + '"}, '
            '{"callee": "8c04b328", "field": "r5", "size": 4}, {"callee": "8c04b328", "field": "r6", "size": 4}]',
            "8c0500c2 b6"])
        self.assertEqual(runs[1].stdout.splitlines(), ["J 2", "", "8c0500c2 b6"])  # control: no fact, no U, no meta
        # Only the function's own direct callees' facts: a caller of 8c04b328 alone sees its two, an unlisted
        # callee's caller nothing (its harness byte for byte as with no list at all).
        self.assertEqual(runs[2].stdout.splitlines(), [
            "[2349118248, 2, 4]", "[2349118248, 3, 4]", "J 2", "U 8c04b328 2 4", "U 8c04b328 3 4",
            ',~  "mock_writes": [{"callee": "8c04b328", "field": "r5", "size": 4}, '
            '{"callee": "8c04b328", "field": "r6", "size": 4}]', "8c0500c2 b6"])
        self.assertEqual(runs[3].stdout, runs[1].stdout)

    def test_scripts(self):
        for name in ("TcStrict.java", "TcOracle.java", "TcPrepare.java"):
            self.assertLessEqual(len(text(SCRIPTS / name).splitlines()), 400, name)
        self.assertEqual(text(SCRIPTS / "TcStrict.java"), text(ROOT / "targets" / "saturn_sh2" / "ghidra_scripts"
                                                               / "TcStrict.java"))  # one file, two targets
        h = text(SH4 / "runtime" / "tc_strict.h")
        for t in ("saturn_sh2", "gamecube_ppc"):
            self.assertEqual(text(ROOT / "targets" / t / "runtime" / "tc_strict.h"), h, t)


# The operand reader's caller, raw form: the cursor word at guest ctx, n calls to the callee, the word returned.
RAW_C = """#include "tc_rt.h"
u32 fn_test(u32 ctx, u32 n);
u32 fn_test(u32 ctx, u32 n)
{
    TcArgs a = {0};
    a.r[0] = ctx;
    for (u32 i = 0; i < n; i++) tc_call(0x8c048914u, &a);
    return rd32(ctx);
}
"""
RAW_THUNK = """#include "tc_rt.h"
u32 fn_test(u32 ctx, u32 n);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)fr;
    (void)out_fr0;
    *out_r0 = fn_test(r[0], r[1]);
}
"""
# Readable form: the cursor in a host local (Track_FindLabel's ScriptCtx), its address the callee's argument.
RD_C = """#include "rd_rt.h"
typedef u32 (*Fn)(u32 *ctx);
u32 fn_test(u32 script, u32 n);
u32 fn_test(u32 script, u32 n)
{
    u32 ctx = script;
    for (u32 i = 0; i < n; i++) ((Fn)(uintptr_t)0x8c048914u)(&ctx);
    return ctx;
}
"""
RD_THUNK = RAW_THUNK.replace("tc_rt.h", "rd_rt.h")
S, CTX = 0x8C050000, 0x8C040000
# From S + 1 (odd): c2 at S+2 (+1: S+5), c3 at S+6 (align 2 +2: S+10), c4 at S+10 (align 4 +4: S+16), c4 at S+16
# (S+18 -> S+20 +4: S+24), an unlisted op at S+24 (S+26), 0xffc2 at S+26 (not c2: S+28).
SCRIPT = bytes.fromhex("0000" "c200" "07" "00" "c300" "0102" "c400" "03040506" "c400" "0000" "0708090a" "5500"
                       "c2ff")
HAND = [S + 1, S + 5, S + 10, S + 16, S + 24, S + 26, S + 28]


def vectors(start, n, head="J 2\nU 8c048914 1 " + INT_TOK + "\n", ctx=CTX):
    mem = f"M {S:08x} {SCRIPT.hex()}\n" + (f"M {ctx:08x} {start.to_bytes(4, 'little').hex()}\n" if ctx else "")
    return (head + "".join(f"V {i} dev\nN 1234 7\n{mem}R {(ctx or start):08x} {k:08x} 0 0\nC 00000000\nE\n"
                           for i, k in enumerate(n)))


def rets(out):
    return [int(ln.split()[1], 16) for ln in out.splitlines() if ln.startswith("RET ")]


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="gapf_rt_", dir=WORK)
        d = cls.d = pathlib.Path(cls.tmp.name)
        srcs = ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")
        base = d / "base_rt"  # the runtime as on the base branch: git show of each runtime file
        base.mkdir()
        for f in (SH4 / "runtime").iterdir():
            rel = f.relative_to(ROOT).as_posix()
            r = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:{rel}"], capture_output=True, timeout=60)
            if r.returncode == 0:
                (base / f.name).write_bytes(r.stdout)
        cmds = []
        for name, rt in (("raw", SH4 / "runtime"), ("base", base)):
            b = d / name
            b.mkdir()
            (b / "work.c").write_text(RAW_C)
            (b / "thunk.c").write_text(RAW_THUNK)
            cmds.append(f'cd /d "{b}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{rt}" /Fe:runner.exe work.c thunk.c '
                        + " ".join(f'"{rt / s}"' for s in srcs))
        vc = str(VC64).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && ' + " && ".join(cmds) + '"', capture_output=True,
                           text=True, errors="replace", timeout=600)
        if r.returncode:
            raise AssertionError(r.stdout[-3000:])
        (d / "rd.c").write_text(RD_C)
        ok, msgs = readable_check.build(paths.config(), [d / "rd.c"], RD_THUNK, {0x8C048914: ("i", "i")}, d / "rd")
        if not ok:
            raise AssertionError(msgs)
        (d / "image.bin").write_bytes(bytes(0x100))
        (d / "fsca.bin").write_bytes(bytes(1 << 20))
        (d / "code.txt").write_text("8c010000 8c010100\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_one(self, exe, vecs):
        (self.d / "v.txt").write_text(vecs)
        r = subprocess.run([str(exe), str(self.d / "image.bin"), str(self.d / "code.txt"), str(self.d / "fsca.bin"),
                            str(self.d / "v.txt"), "all", str(self.d / "out.txt")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.d / "out.txt").read_text()

    def forms(self):
        """(runner, ctx): the raw one with the cursor in guest memory, the readable one with it in a host local."""
        return ((self.d / "raw" / "runner.exe", CTX), (self.d / "rd" / "rd_runner.exe", 0))

    def test_cursor_moves(self):
        mem = {S + k: b for k, b in enumerate(SCRIPT)}
        want = [S + 1]
        for _ in range(6):
            want.append(model(mem, want[-1], 2, 2, {0xC2: (1, 1), 0xC3: (2, 2), 0xC4: (4, 4)}))
        self.assertEqual(want, HAND)  # the model and the hand-worked offsets agree
        for exe, ctx in self.forms():
            self.assertEqual(rets(self.run_one(exe, vectors(S + 1, range(7), ctx=ctx))), HAND, exe.name)
            flt = "J 2\nU 8c048914 1 " + FLT_TOK + "\n"  # b6 is not in the script: each call adds 2 after aligning
            self.assertEqual(rets(self.run_one(exe, vectors(S + 1, (1, 2), flt, ctx))), [S + 4, S + 6], exe.name)

    def test_controls(self):
        for exe, ctx in self.forms():
            heads = ["", "J 0\n", "J 2\nU 8c0489a8 1 " + INT_TOK + "\n",  # off, another callee's
                     "J 2\nU 8c048914 1 x2.2:c2.1.1\nU 8c048914 1 a3.2:c2.1.1\n"]  # malformed: ignored
            if ctx:  # another field's: the raw form's r5 is 0 (a readable call's r5 is whatever the register held)
                heads.append("J 2\nU 8c048914 2 " + INT_TOK + "\n")
            for head in heads:
                self.assertEqual(rets(self.run_one(exe, vectors(S + 1, (3,), head, ctx))), [S + 1], (exe.name, head))
            far = rets(self.run_one(exe, vectors(0x8D000000, (2,), ctx=ctx)))  # the op is past RAM: no write
            self.assertEqual(far, [0x8D000000], exe.name)

    def test_unlisted_as_base(self):  # no advance line: the raw runner's output byte for byte as run-flag's runtime
        heads = ("", "J 15\nU 8c048914 1 8\nU 8c048914 2 4\n", "J 3\nU 8c048914 0 4094\nU 8c048914 1 4\n")
        for head in heads:
            for n in ((0, 1, 3),):
                v = vectors(S + 1, n, head)
                self.assertEqual(self.run_one(self.d / "raw" / "runner.exe", v),
                                 self.run_one(self.d / "base" / "runner.exe", v), head)
        self.assertNotEqual(self.run_one(self.d / "raw" / "runner.exe", vectors(S + 1, (3,))),
                            self.run_one(self.d / "base" / "runner.exe", vectors(S + 1, (3,))))  # an advance: moves


class Note(unittest.TestCase):
    def test_note(self):
        meta = {"params": [], "addr": "8c048a28", "mock_writes": [
            {"callee": "8c048914", "field": "r4", "advance": INT_TOK}, {"callee": "8c04b328", "field": "r5", "size": 4}]}
        note = " ".join(agent.abi_notes(meta))
        self.assertIn("fn_8c048914 (r4)", note)
        self.assertNotIn("fn_8c04b328", note)
        self.assertIn("never keep a local copy across the call", note)
        plain = {"params": [], "addr": "8c04b56a", "mock_writes": [{"callee": "8c04b328", "field": "r5", "size": 4}]}
        self.assertEqual(agent.abi_notes(plain), [])  # control: a plain fact adds no note


if __name__ == "__main__":
    unittest.main()

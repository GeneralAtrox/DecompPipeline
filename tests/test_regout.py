"""A callee's register outputs in r4..r7 (TcOut; Spawn's B18: Lib_RenderModelAlpha 8c05fec2 reads the next mesh header
through the r4 its vertex leaves 8c0602a0 and 8c060ca0 hand back advanced). The mocked callee used to leave r4..r7 as
they were, so C that kept its own r4 passed the judge and faulted in the port. Pass and fail controls:
1. the model (tests/java/TcOutStub, the real TcOut and TcLive.Deltas.St): a return's rK against its entry value is
   kept, a constant set, unknown (from rK) or OTHER; a call in follow mode gives the caller the callee's output of
   its own value at the call (kept, + d, unknown) and OTHER leaves it unknown; TcLive's header writes "K target 4 n d"
   for an r4 entry, its meta leaves a callee with only r4..r7 entries out of r_live; TcOut's lines and meta;
2. the C runner on translator C (compiled, the raw runtime): the caller reads @r4 after a call to a callee whose K
   line hands r4 back + 0x20. With meta r_out the C reads the callee's r4 (sh4_r(4)) and returns the word there; the
   same caller translated without r_out keeps its old r4 and returns the word at the old address. The leaf's side:
   with meta r_exports its C hands r4 back (sh4_set_r), the runner reports it (line RO), and compare.py takes it;
   without r_exports the C leaves r4 and compare.py names the difference;
3. controls: without r_out / r_exports the translator's C is byte-identical to the C before (no new state), and
   the runner's output without lines O and K-for-r4..r7 is byte-identical to master's runtime (fc58b80);
4. the wiring: TcOut is called at prepare, TcStrict.walk counts the exports as read at a return, TcVectors writes
   RO, every script stays at 400 lines or fewer, compare.py reads RO.
TcLive.Deltas over a real program needs Ghidra: the re-prepare of the affected functions judges it (meta r_out of
8c05fec2: 8c0602a0 "r4,r5,r6").
Synthetic data. Run: python -W error tests/test_regout.py.
"""
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "targets" / "dreamcast_sh4" / "translator"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_tail import CP, jdk  # noqa: E402
from sh4translate import translate as tx  # noqa: E402
from core import check, compare, toolcfg  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"
WORK = ROOT / "tests" / ".work"
VC64 = toolcfg.load(ROOT).get("vcvars")

# The caller: push pr, r3 = 8c000100 (pool), jsr @r3, then the next header through r4: mov.l @r4,r0; pop pr; rts.
CALLER = [0x4f22, 0xd303, 0x430b, 0x0009, 0x6042, 0x4f26, 0x000b, 0x0009]
# The leaf: add #0x20,r4; rts (r4 handed back past a 0x20-byte record).
LEAF = [0x7420, 0x000b, 0x0009]


def base_commit():
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "fc58b80"], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def translate(words, sig, pool=None):
    data = b"".join(struct.pack("<H", w) for w in words)
    if pool is not None:
        data += struct.pack("<I", pool)
    src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, sig, tx.TargetFacts())
    return src[0] if isinstance(src, tuple) else src


def caller_sig(r_out=frozenset()):
    return tx.Signature("fn_8c000000", "u32 fn_8c000000(u32 param_r4)", [("param_r4", "u32", "r4")], "u32", None,
                        {0x8c000100: ["r4"]}, r_out=r_out)


def leaf_sig(r_out=frozenset()):
    return tx.Signature("fn_8c000000", "void fn_8c000000(u32 param_r4)", [("param_r4", "u32", "r4")], "void", None,
                        {}, r_out=r_out)


THUNK = """#include "tc_rt.h"
%s
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)fr;
    (void)out_fr0;
    %s
}
"""
CALLER_THUNK = THUNK % ("u32 fn_8c000000(u32 param_r4);", "*out_r0 = fn_8c000000(r[0]);")
LEAF_THUNK = THUNK % ("void fn_8c000000(u32 param_r4);", "fn_8c000000(r[0]);\n    *out_r0 = 0;")


def vectors(head):
    """r4 = 8c010100 at entry; the word there is 11111111, the one 0x20 on 22222222."""
    m = "M 8c010100 11111111\nM 8c010120 22222222\n"
    return head + f"V 0 dev\nR 8c010100 0 0 0\nA {' '.join(['00000000'] * 16)}\n{m}E\n"


def ret(out):
    return [int(ln.split()[1], 16) for ln in out.splitlines() if ln.startswith("RET ")]


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="regout_", dir=WORK)
        d = cls.d = pathlib.Path(cls.tmp.name)
        master = d / "master_rt"
        master.mkdir()
        base = base_commit()
        for f in (SH4 / "runtime").iterdir():
            r = subprocess.run(["git", "-C", str(ROOT), "show", f"{base}:{f.relative_to(ROOT).as_posix()}"],
                               capture_output=True, timeout=60)
            if r.returncode == 0:
                (master / f.name).write_bytes(r.stdout)
        cls.c = {
            "right": translate(CALLER, caller_sig(frozenset({"r4"})), 0x8c000100),
            "stale": translate(CALLER, caller_sig(), 0x8c000100),
            "leaf": translate(LEAF, leaf_sig(frozenset({"r4"}))),
            "leafold": translate(LEAF, leaf_sig()),
        }
        srcs = ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")
        cmds = []
        for name, rt in (("right", SH4 / "runtime"), ("stale", SH4 / "runtime"), ("master", master),
                         ("leaf", SH4 / "runtime"), ("leafold", SH4 / "runtime")):
            b = d / name
            b.mkdir()
            (b / "work.c").write_text(cls.c["stale" if name == "master" else name])
            (b / "thunk.c").write_text(LEAF_THUNK if name.startswith("leaf") else CALLER_THUNK)
            cmds.append(f'cd /d "{b}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{rt}" /Fe:runner.exe work.c thunk.c '
                        + " ".join(f'"{rt / s}"' for s in srcs))
        vc = str(VC64).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && ' + " && ".join(cmds) + '"', capture_output=True,
                           text=True, errors="replace", timeout=600)
        if r.returncode:
            raise AssertionError(r.stdout[-3000:])
        (d / "image.bin").write_bytes(bytes(0x200))
        (d / "fsca.bin").write_bytes(bytes(1 << 20))
        (d / "code.txt").write_text("8c010000 8c010020\n")

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("REGOUT_KEEP"):  # (keep the builds to look at a failure)
            cls.tmp._finalizer.detach()
        else:
            cls.tmp.cleanup()

    def run_one(self, name, vecs):
        (self.d / "v.txt").write_text(vecs)
        r = subprocess.run([str(self.d / name / "runner.exe"), str(self.d / "image.bin"), str(self.d / "code.txt"),
                            str(self.d / "fsca.bin"), str(self.d / "v.txt"), "all", str(self.d / "out.txt")],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.d / "out.txt").read_text()

    def test_callee_r4_counts(self):
        k = "K 8c000100 4 1 00000020\n"
        self.assertIn("r4 = sh4_r(4);", self.c["right"])
        self.assertEqual(ret(self.run_one("right", vectors(k))), [0x22222222])   # the callee's r4: the next header
        self.assertEqual(ret(self.run_one("stale", vectors(k))), [0x11111111])   # the caller's old r4
        self.assertEqual(ret(self.run_one("right", vectors(""))), [0x11111111])  # no K line: the callee keeps r4
        unknown = "K 8c000100 4 0\n"  # unknown: 0x20 * (1 + draw % 3), draw 0 here: + 0x20 as well
        self.assertEqual(ret(self.run_one("right", vectors(unknown))), [0x22222222])

    def test_leaf_hands_r4_back(self):
        self.assertRegex(self.c["leaf"], r"sh4_set_r\(4, \w+\);")
        self.assertNotIn("sh4_set_r(4", self.c["leafold"])
        out = self.run_one("leaf", vectors("O 1\n"))
        self.assertIn("RO 8c010120 00000000 00000000 00000000", out)
        old = self.run_one("leafold", vectors("O 1\n"))
        self.assertIn("RO 8c010100 00000000 00000000 00000000", old)
        self.assertNotIn("RO", self.run_one("leaf", vectors("")))  # no line O: no RO
        meta = {"r_exports": ["r4"]}
        want = {"ro": [0x8c010120, 0, 0, 0]}
        self.assertEqual(compare.compare_outputs(meta, want, {"ro": [0x8c010120, 0, 0, 0]}), [])
        diff = compare.compare_outputs(meta, want, {"ro": [0x8c010100, 0, 0, 0]})
        self.assertEqual(len(diff), 1)
        self.assertIn("r4 at exit", diff[0])
        self.assertEqual(compare.compare_outputs({}, want, {"ro": [0, 1, 2, 3]}), [])  # no r_exports: nothing

    def test_without_lines_as_master(self):  # no line O, no K for r4..r7: byte for byte as master's runtime
        for head in ("", "K 8c000100 14 1 00000020\n", "J 15\nU 8c000100 0 4094\n"):
            self.assertEqual(self.run_one("stale", vectors(head)), self.run_one("master", vectors(head)), head)


RD_CALLER = """#include "rd_rt.h"
typedef void (*Fn)(u32);
u32 fn_8c000000(u32 p);
u32 fn_8c000000(u32 p)
{
    ((Fn)(uintptr_t)0x8c000100u)(p);
    return *(volatile u32 *)(uintptr_t)sh4_r(%s);
}
"""


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Readable(unittest.TestCase):
    """The readable runtime as the raw one: after a call, sh4_r(4) is the callee's r4 (the argument it got, moved by
    its K line); line O reports RO."""

    def test_readable_reads_callee_r4(self):
        from core import paths, readable_check
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="regout_rd_", dir=WORK) as t:
            d = pathlib.Path(t)
            (d / "image.bin").write_bytes(bytes(0x200))
            (d / "fsca.bin").write_bytes(bytes(1 << 20))
            (d / "code.txt").write_text("8c010000 8c010020\n")
            (d / "rd.c").write_text(RD_CALLER % "4")
            ok, msgs = readable_check.build(paths.config(), [d / "rd.c"], CALLER_THUNK.replace("tc_rt.h", "rd_rt.h"),
                                            {0x8C000100: ("i", "v")}, d / "rd")
            self.assertTrue(ok, msgs)

            def run(vecs):
                (d / "v.txt").write_text(vecs)
                r = subprocess.run([str(d / "rd" / "rd_runner.exe"), str(d / "image.bin"), str(d / "code.txt"),
                                    str(d / "fsca.bin"), str(d / "v.txt"), "all", str(d / "out.txt")],
                                   capture_output=True, text=True, timeout=20)
                self.assertEqual(r.returncode, 0, r.stderr)
                return (d / "out.txt").read_text()
            self.assertEqual(ret(run(vectors("K 8c000100 4 1 00000020\n"))), [0x22222222])  # the callee's r4
            self.assertEqual(ret(run(vectors(""))), [0x11111111])  # it kept r4: the argument
            self.assertIn("RO 8c010120 00000000 00000000 00000000", run(vectors("O 1\nK 8c000100 4 1 00000020\n")))
            self.assertNotIn("RO", run(vectors("K 8c000100 4 1 00000020\n")))


class Notes(unittest.TestCase):  # the raw and readable context name the outputs; none without the metas
    def test_notes(self):
        from core import agent, readable_context
        m = {"addr": "8c05fec2", "params": [], "callees": [], "r_out": {"8c0602a0": "r4,r5,r6"}, "r_exports": ["r4"]}
        for text in ("\n".join(agent.abi_notes(m)), "\n".join(readable_context.state_notes(m))):
            self.assertIn("fn_8c0602a0 r4,r5,r6", text)
            self.assertIn("sh4_r(N) (N = 4..7)", text)
            self.assertIn("## Results handed back in r4", text)
        m = {"addr": "8c05fec2", "params": [], "callees": []}
        self.assertEqual(readable_context.out_notes(m), [])


class TranslatorControls(unittest.TestCase):
    def test_identical_without_meta(self):
        before = translate(CALLER, caller_sig(), 0x8c000100)
        self.assertNotIn("sh4_r(4)", before)
        self.assertNotIn("sh4_set_r(4", before)
        self.assertEqual(tx.Signature.from_meta(META).r_out, frozenset())
        self.assertEqual(tx.Signature.from_meta(dict(META, r_out={"8c000100": "r4,r6"}, r_exports=["r5"])).r_out,
                         frozenset({"r4", "r5", "r6"}))

    def test_port_form_keeps_in_regs(self):  # the port form still takes a non-parameter r4..r7 as in_REG
        sig = tx.Signature("fn_8c000000", "u32 fn_8c000000(void)", [], "u32", None, {0x8c000100: ["r4"]},
                           r_out=frozenset({"r4"}))
        data = b"".join(struct.pack("<H", w) for w in CALLER) + struct.pack("<I", 0x8c000100)
        src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, sig, tx.TargetFacts(), sp_input=True)
        src = src[0] if isinstance(src, tuple) else src
        self.assertIn("in_r4", src)


META = {"name": "fn_8c000000", "signature": "u32 fn_8c000000(void)", "params": [], "ret": "u32"}


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Model(unittest.TestCase):
    def test_stub(self):
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="regout_j_", dir=WORK) as t:
            src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", t, "-cp", CP, "-sourcepath", src,
                                str(ROOT / "tests" / "java" / "TcOutStub.java")], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", t + os.pathsep + CP, "TcOutStub"], capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.split("\n")[:len(STUB)], STUB, r.stdout)


STUB = [
    "classify kept=null const=[32] set=[16, 32] unknown=[] other=OTHER",
    "follow kept=4+0 const=4+40 unknown=20+0 other=-1+0 fromtop=-1+0",
    "follow r8base const=8+32 unknown=-1+0",
    "K 8c0602a0 4 0|K 8c060600 8 1 00000020|K 8c060600 6 1 00000010|",
    "r_live {\"8c060600\":{\"r8\":\"0x20\"}}",
    "O 5|RO 8c010120 00000000 00000002 00000000",
]


class Wiring(unittest.TestCase):
    def test_scripts(self):
        for f in SCRIPTS.glob("*.java"):
            self.assertLessEqual(len(f.read_text().splitlines()), 400, f.name)
        prep = (SCRIPTS / "TcPrepare.java").read_text()
        self.assertLess(prep.index("TcOut.prepare(this"), prep.index("TcLive.of(this"))
        self.assertIn("TcOut.header()", prep)
        self.assertIn("TcOut.meta()", prep)
        self.assertIn("live |= exitLive & ~w", (SCRIPTS / "TcStrict.java").read_text())
        self.assertIn("TcOut.exp(r)", (SCRIPTS / "TcVectors.java").read_text())
        self.assertIn("res.ro[i]", (SCRIPTS / "TcExit.java").read_text())

    def test_runner_and_compare(self):
        runner = (SH4 / "runtime" / "runner.c").read_text()
        self.assertIn("line[0] == 'O'", runner)
        self.assertIn("tcrt_report_ro(out)", runner)
        self.assertIn('elif k == "RO"', (ROOT / "core" / "compare.py").read_text())
        self.assertIn("reg < 4 || reg > 14", (SH4 / "runtime" / "tc_rt.c").read_text())


if __name__ == "__main__":
    unittest.main()

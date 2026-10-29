"""The T flag handed back across a call (TcTflag; Power Stone 2's drawer Lib_RenderModelA 8c188420 reads T right after
its jsr to 8c1967c0 and 8c197240). On the console T after a call is what the callee left; the mocked callee left the
caller's own T, so C that kept it passed the judge. Pass and fail controls:
1. the model (tests/java/TcTflagStub, the real TcTflag and TcLive): a caller's entry 11 writes header line
   "K target 16 0" (beside an r4 entry of the same callee), r_live stays without it; TcTflag writes header "t", exit
   "RT v" and meta t_out / t_export only when set, and no RT for a halted run;
2. the C runner on translator C (compiled, the raw runtime): the caller returns T after a call (movt r0). With meta
   t_out the C reads the callee's T (sh4_t) and returns the draw's bit 8; translated without it the C returns its own
   old T (0). The leaf's side: with meta t_export its C hands T back (sh4_set_t), the runner reports RT after a line
   t, and compare.py takes it; without t_export the C leaves T and compare.py names the difference. The readable
   runtime gives the same T;
3. controls: without t_out / t_export the translator's C is byte-identical to the C before (no new state), and the
   runner's output without lines t and K-for-16 is byte-identical to the runtime before (14a59a7);
4. the wiring: TcTflag is called at prepare before TcLive.of, TcVectors writes RT, TcExit takes T, every script stays
   at 400 lines or fewer, compare.py reads RT.
TcTflag.walk and changes over a real program need Ghidra: the re-prepare of 8c188420 judges them (meta t_out).
Synthetic data. Run: python -W error tests/test_tflag.py.
"""
import os
import pathlib
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
BASE = "14a59a7"  # the runtime before the change

# The caller: push pr, r3 = 8c000100 (pool), jsr @r3, then movt r0 (T as the result); pop pr; rts.
CALLER = [0x4f22, 0xd303, 0x430b, 0x0009, 0x0029, 0x4f26, 0x000b, 0x0009]
# The leaf: sett; rts (T handed back 1).
LEAF = [0x0018, 0x000b, 0x0009]


def base_commit():
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", BASE], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def translate(words, sig, pool=None):
    data = b"".join(struct.pack("<H", w) for w in words)
    if pool is not None:
        data += struct.pack("<I", pool)
    src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, sig, tx.TargetFacts())
    return src[0] if isinstance(src, tuple) else src


def caller_sig(t=False):
    return tx.Signature("fn_8c000000", "u32 fn_8c000000(u32 param_r4)", [("param_r4", "u32", "r4")], "u32", None,
                        {0x8c000100: ["r4"]}, t_state=t)


def leaf_sig(t=False):
    return tx.Signature("fn_8c000000", "void fn_8c000000(u32 param_r4)", [("param_r4", "u32", "r4")], "void", None,
                        {}, t_state=t)


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


def vectors(head, draw=0x100):
    """One vector; the first call's draw is `draw` (T = its bit 8)."""
    return head + f"V 0 dev\nR 8c010100 0 0 0\nA {draw:08x} {' '.join(['00000000'] * 15)}\nE\n"


def ret(out):
    return [int(ln.split()[1], 16) for ln in out.splitlines() if ln.startswith("RET ")]


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="tflag_", dir=WORK)
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
            "right": translate(CALLER, caller_sig(True), 0x8c000100),
            "stale": translate(CALLER, caller_sig(), 0x8c000100),
            "leaf": translate(LEAF, leaf_sig(True)),
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
        if os.environ.get("TFLAG_KEEP"):  # (keep the builds to look at a failure)
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

    def test_callee_t_counts(self):
        k = "K 8c000100 16 0\n"
        self.assertIn("t = sh4_t();", self.c["right"])
        self.assertEqual(ret(self.run_one("right", vectors(k))), [1])          # the callee's T: draw bit 8
        self.assertEqual(ret(self.run_one("right", vectors(k, 0xff))), [0])    # bit 8 clear: 0
        self.assertEqual(ret(self.run_one("stale", vectors(k))), [0])          # the caller's old T
        self.assertEqual(ret(self.run_one("right", vectors(""))), [0])         # no K line: the callee keeps T

    def test_leaf_hands_t_back(self):
        self.assertIn("sh4_set_t(t);", self.c["leaf"])
        self.assertNotIn("sh4_set_t", self.c["leafold"])
        self.assertIn("RT 1", self.run_one("leaf", vectors("t\n")))
        self.assertIn("RT 0", self.run_one("leafold", vectors("t\n")))
        self.assertNotIn("RT", self.run_one("leaf", vectors("")))  # no line t: no RT
        meta = {"t_export": True}
        self.assertEqual(compare.compare_outputs(meta, {"rt": 1}, {"rt": 1}), [])
        diff = compare.compare_outputs(meta, {"rt": 1}, {"rt": 0})
        self.assertEqual(len(diff), 1)
        self.assertIn("T at exit", diff[0])
        self.assertIn("got nothing", compare.compare_outputs(meta, {"rt": 1}, {})[0])
        self.assertEqual(compare.compare_outputs({}, {"rt": 1}, {"rt": 0}), [])  # no t_export: nothing

    def test_without_lines_as_master(self):  # no line t, no K for 16: byte for byte as the runtime before
        for head in ("", "K 8c000100 14 1 00000020\n", "K 8c000100 4 1 00000020\nO 1\n", "J 15\nU 8c000100 0 4094\n"):
            self.assertEqual(self.run_one("stale", vectors(head)), self.run_one("master", vectors(head)), head)


RD_CALLER = """#include "rd_rt.h"
typedef void (*Fn)(u32);
u32 fn_8c000000(u32 p);
u32 fn_8c000000(u32 p)
{
    sh4_set_t(0);
    ((Fn)(uintptr_t)0x8c000100u)(p);
    return sh4_t();
}
"""


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Readable(unittest.TestCase):
    """The readable runtime as the raw one: after a call, sh4_t() is the callee's T (its K line's draw)."""

    def test_readable_reads_callee_t(self):
        from core import paths, readable_check
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tflag_rd_", dir=WORK) as t:
            d = pathlib.Path(t)
            (d / "image.bin").write_bytes(bytes(0x200))
            (d / "fsca.bin").write_bytes(bytes(1 << 20))
            (d / "code.txt").write_text("8c010000 8c010020\n")
            (d / "rd.c").write_text(RD_CALLER)
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
            self.assertEqual(ret(run(vectors("K 8c000100 16 0\n"))), [1])  # the callee's T
            self.assertEqual(ret(run(vectors(""))), [0])  # it kept T
            self.assertIn("RT 1", run(vectors("t\nK 8c000100 16 0\n")))
            self.assertNotIn("RT", run(vectors("K 8c000100 16 0\n")))


class Notes(unittest.TestCase):  # the raw and readable context name the T callees; none without the metas
    def test_notes(self):
        from core import agent, readable_context
        m = {"addr": "8c188420", "params": [], "callees": [], "t_out": ["8c1967c0", "8c197240"], "t_export": True}
        for text in ("\n".join(agent.abi_notes(m)), "\n".join(readable_context.state_notes(m))):
            self.assertIn("fn_8c1967c0, fn_8c197240", text)
            self.assertIn("t = sh4_t()", text)
            self.assertIn("## T handed back", text)
        self.assertEqual(readable_context.out_notes({"addr": "8c188420", "params": [], "callees": []}), [])


META = {"name": "fn_8c000000", "signature": "u32 fn_8c000000(void)", "params": [], "ret": "u32"}


class TranslatorControls(unittest.TestCase):
    def test_identical_without_meta(self):
        before = translate(CALLER, caller_sig(), 0x8c000100)
        self.assertNotIn("sh4_t", before)
        self.assertFalse(tx.Signature.from_meta(META).t_state)
        self.assertTrue(tx.Signature.from_meta(dict(META, t_out=["8c000100"])).t_state)
        self.assertTrue(tx.Signature.from_meta(dict(META, t_export=True)).t_state)
        # the same C as a signature that never heard of T (the field's default)
        old = tx.Signature("fn_8c000000", "u32 fn_8c000000(u32 param_r4)", [("param_r4", "u32", "r4")], "u32", None,
                           {0x8c000100: ["r4"]})
        self.assertEqual(translate(CALLER, old, 0x8c000100), before)

    def test_entry_t_when_kept(self):  # an exported function that keeps T on a path hands back the T it got
        # bf L (to the rts); sett; L: rts; nop
        src = translate([0x8b00, 0x0018, 0x000b, 0x0009], leaf_sig(True))
        self.assertIn("t = sh4_t();", src)
        self.assertIn("sh4_set_t(t);", src)

    def test_port_form(self):
        data = b"".join(struct.pack("<H", w) for w in CALLER) + struct.pack("<I", 0x8c000100)
        src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, caller_sig(True), tx.TargetFacts(),
                                    sp_input=True)
        src = src[0] if isinstance(src, tuple) else src
        self.assertIn("t = sh4_t();", src)


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Model(unittest.TestCase):
    def test_stub(self):
        self.assertEqual(stub_lines()[:len(STUB)], STUB)


def stub_lines():
    """tests/java/TcTflagStub's output lines (shared with tests/test_zeroread.py)."""
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tflag_j_", dir=WORK) as t:
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", t, "-cp", CP, "-sourcepath", src,
                            str(ROOT / "tests" / "java" / "TcTflagStub.java")], capture_output=True, text=True,
                           timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        r = subprocess.run([jdk("java"), "-cp", t + os.pathsep + CP, "TcTflagStub"], capture_output=True, text=True,
                           timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout.splitlines()


STUB = [
    "K 8c1967c0 16 0|K 8c197240 4 1 00000020|K 8c197240 16 0|",
    "r_live {}",
    "off [][][]",
    "on t|\nRT 1,\n  \"t_out\": [\"8c1967c0\",\"8c197240\"],\n  \"t_export\": true".replace("\n", "|"),
    "halted []",
]


class Wiring(unittest.TestCase):
    def test_scripts(self):
        for f in SCRIPTS.glob("*.java"):
            self.assertLessEqual(len(f.read_text().splitlines()), 400, f.name)
        prep = (SCRIPTS / "TcPrepare.java").read_text()
        self.assertLess(prep.index("TcTflag.prepare(this"), prep.index("TcLive.of(this"))
        self.assertIn("TcTflag.header()", prep)
        self.assertIn("TcTflag.meta()", prep)
        self.assertIn("TcTflag.exp(r)", (SCRIPTS / "TcVectors.java").read_text())
        self.assertIn('res.ro[4] = o.reg(emu, "T")', (SCRIPTS / "TcExit.java").read_text())
        self.assertIn("TcTflag.apply(o, emu, draws[k % 16])", (SCRIPTS / "TcLive.java").read_text())

    def test_runner_and_compare(self):
        for runner in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
            text = runner.read_text()
            self.assertIn("line[0] == 't'", text)
            self.assertIn("tcrt_report_t(out)", text)
        self.assertIn('elif k == "RT"', (ROOT / "core" / "compare.py").read_text())
        for rt in (SH4 / "runtime" / "tc_rt.c", SH4 / "runtime_rd" / "rd_sh4.c", ROOT / "port" / "runtime" / "sh4_rt.c"):
            self.assertIn("u32 sh4_t(void)", rt.read_text())


def asm(rows):
    """A packet asm file: rows of (address, text), padded as the packet writer pads them."""
    return "; part 1 of 1 of asm.txt\n" + "".join(f"  {a:08x}  {t:<32} \n" for a, t in rows)


class RestampKind(unittest.TestCase):
    """selective_restamp --change tflag: callers reading T after a call and those callees are listed; a caller that
    writes T first is not (the made-up miss)."""

    def test_scan(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("srs", ROOT / "tools" / "selective_restamp.py")
        sr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sr)
        call = "jsr @r3                              ; call 8c1967c0"
        fns = {
            "8c188420": [(0x8c188420, call), (0x8c188422, "_nop"), (0x8c188424, "bt 0x8c18842a"),
                         (0x8c188426, "rts"), (0x8c188428, "_nop")],                         # reads T: listed
            "8c010000": [(0x8c010000, call.replace("8c1967c0", "8c197240")), (0x8c010002, "_nop"),
                         (0x8c010004, "cmp/eq r1,r2"), (0x8c010006, "bt 0x8c01000a"), (0x8c010008, "rts"),
                         (0x8c01000a, "_nop")],                                               # writes T first: not
            "8c020000": [(0x8c020000, call.replace("8c1967c0", "8c197240")), (0x8c020002, "_nop"),
                         (0x8c020004, "bra 0x8c02000a"), (0x8c020006, "_movt r0"), (0x8c020008, "rts"),
                         (0x8c02000a, "_nop")],                                               # the bra's slot: listed
            "8c030000": [(0x8c030000, "bsr 0x8c040000                       ; call 8c040000"), (0x8c030002, "_nop"),
                         (0x8c030004, "bra 0x8c03000c"), (0x8c030006, "_nop"), (0x8c030008, "rts"),
                         (0x8c03000a, "_nop"), (0x8c03000c, "clrt"), (0x8c03000e, "movt r0"),
                         (0x8c030010, "rts"), (0x8c030012, "_nop")],                          # clrt first: not
            "8c050000": [(0x8c050000, "bsr 0x8c060000                       ; call 8c060000"), (0x8c050002, "_nop"),
                         (0x8c050004, "bra 0x8c05000c"), (0x8c050006, "_nop"), (0x8c050008, "rts"),
                         (0x8c05000a, "_nop"), (0x8c05000c, "addc r1,r2"), (0x8c05000e, "rts"),
                         (0x8c050010, "_nop")],                                               # followed bra: listed
            # PS2's chain: 8c0b1f60 reads T after calling 8c179600, which tail-calls 8c179560, which returns with
            # 8c179630's T: all three hand it on, so each is listed with its callee
            "8c0b1f60": [(0x8c0b1f60, "jsr @r3                              ; call 8c179600"), (0x8c0b1f62, "_nop"),
                         (0x8c0b1f64, "movt r0"), (0x8c0b1f66, "rts"), (0x8c0b1f68, "_nop")],
            "8c179600": [(0x8c179600, "fcmp/gt fr2,fr3"), (0x8c179602, "bra 0x8c179560                       ; call "
                          "8c179560"), (0x8c179604, "_fmov.s @r15+,fr15")],
            "8c179560": [(0x8c179560, "bsr 0x8c179630                       ; call 8c179630"), (0x8c179562, "_nop"),
                         (0x8c179564, "neg r0,r0"), (0x8c179566, "rts"), (0x8c179568, "_nop")],
            # hands T on too, but nobody reads its T: not listed (the made-up miss for the chain)
            "8c0a0000": [(0x8c0a0000, "bsr 0x8c0a1000                       ; call 8c0a1000"), (0x8c0a0002, "_nop"),
                         (0x8c0a0004, "rts"), (0x8c0a0006, "_nop")],
        }
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tflag_scan_", dir=WORK) as t:
            for a, rows in fns.items():
                (pathlib.Path(t) / a).mkdir()
                (pathlib.Path(t) / a / "asm_1.txt").write_text(asm(rows))
            scan = sr.tflag_scan(t)
            self.assertEqual(scan, {"8c188420": {"8c1967c0": ["8c188420"]}, "8c020000": {"8c197240": ["8c020000"]},
                                    "8c050000": {"8c060000": ["8c050000"]}, "8c0b1f60": {"8c179600": ["8c0b1f60"]},
                                    "8c179600": {"8c179560": ["8c179602"]}, "8c179560": {"8c179630": ["8c179560"]}})
            cand = sr.tflag_candidates(t)
            self.assertIn("8c188420", cand)
            self.assertIn("8c1967c0", cand)
            self.assertTrue({"8c179560", "8c179600", "8c179630"} <= cand)
            self.assertNotIn("8c010000", cand)  # the made-up miss
            self.assertNotIn("8c030000", cand)
            self.assertFalse({"8c0a0000", "8c0a1000"} & cand)


if __name__ == "__main__":
    unittest.main()

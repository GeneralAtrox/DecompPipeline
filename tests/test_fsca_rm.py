"""FSCA's table follows the rounding the FPU runs in (FPSCR.RM: 0 round to nearest, 1 toward zero), on every side:
1. the C sides pick by MXCSR.RC: tc_rt.c's and rd_sh4.c's sh4_fsca (the judge's runtimes; xemu calls tc_rt.c's and
   sets MXCSR.RC at every lds to FPSCR) and the port kit's (sh4_rt.c), each compiled from its source: nearest takes
   table 0, toward zero table 1, and up or down (no FPSCR.RM rounds so) ends the run with a message;
2. the table file's readers refuse the one-table file of before (512 KB): rd_sh4_init and the kit's sh4_rt_init
   compiled from their source (the kit's also compares both halves entry by entry), tc_rt.c's and TcPrepare's checks
   pinned by their text;
3. the oracle (TcFpu.fscaTable, run on a stub: tests/java/TcFscaStub.java): FPSCR.RM under fpscr_model, an RM other
   than the entry's rejects the vector, and without the model round to nearest whatever RM holds.
Tables: the two tables themselves (pinned SHA-256s, the state file's and the kit's layouts, +0 signs).
RegressTable: tools/regress_project's private state gets the current file, not the live project's (which may be the
one-table file of before): copied, it failed every preparation of a regress or probe run.
Heal: fsca.ensure rewrites a missing or stale table (the file's SHA-256 is pinned) with one line, and leaves a current
one alone; ghidra.prepare (Ghidra faked, tests/test_prepare_timing) heals a stale 512 KB table before Ghidra starts
and the prepare runs; a failed run's error names its cause on its first line (TcPrepare's own table message).
The oracle's callbacks themselves need Ghidra: their wiring is pinned by text. Synthetic data.
Run: python -W error tests/test_fsca_rm.py
"""
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "port" / "tools"), str(ROOT / "tests")]
import game_config  # noqa: E402
from test_prepare_timing import GHIDRA_LINES, Prepare  # noqa: E402  (sets DECOMP_PROJECT when run alone)
from core import fsca, ghidra, paths  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"
TC_RT, RD_SH4 = SH4 / "runtime" / "tc_rt.c", SH4 / "runtime_rd" / "rd_sh4.c"
KIT_RT = ROOT / "port" / "runtime" / "sh4_rt.c"
HAVE_VC = os.name == "nt" and game_config.vcvars32()


def text(p):
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def function(path, head):
    """The C function starting with `head` (its signature line) in path, through its closing brace."""
    return re.search(re.escape(head) + r"\n\{\n.*?\n\}\n", text(path), re.S).group(0)


HEAD = """#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <xmmintrin.h>
typedef unsigned int u32;
typedef float f32;
f32 u2f(u32 u) { f32 f; memcpy(&f, &u, 4); return f; }
void port_exit(const char *why, int code) { fprintf(stderr, "%s\\n", why); exit(code + 2); }
void port_log(const char *fmt, ...) { va_list a; va_start(a, fmt); vfprintf(stderr, fmt, a); va_end(a); }
"""

PICK_MAIN = """
int main(int argc, char **argv)
{
    f32 s, c;
    u32 a, b;
    if (argc < 2) return 2;
    %s
    _mm_setcsr((_mm_getcsr() & ~0x6000u) | (u32)strtoul(argv[1], NULL, 16));
    sh4_fsca(0x12345u, &s, &c); /* angle 0x2345 */
    memcpy(&a, &s, 4);
    memcpy(&b, &c, 4);
    printf("%%08x %%08x\\n", a, b);
    return 0;
}
"""

# Each side's table, filled with marks: table t's word for angle i is t << 28 | i (a pairs table's cosine word also
# has bit 24). The kit's table holds sines only: its cosine is the sine a quarter turn on.
FILL_PAIRS = "for (u32 t = 0; t < 2; t++) for (u32 i = 0; i < 65536; i++) { g_fsca[t][i][0] = t << 28 | i; " \
             "g_fsca[t][i][1] = t << 28 | 1u << 24 | i; }"
SIDES = {
    "tc_rt.c": (TC_RT, "static u32 g_fsca[2][65536][2];", FILL_PAIRS, "%08x %08x" % (0x2345, 0x1002345)),
    "rd_sh4.c": (RD_SH4, "static u32 (*g_fsca)[65536][2];",
                 "g_fsca = (u32(*)[65536][2])malloc(2 * sizeof *g_fsca); " + FILL_PAIRS,
                 "%08x %08x" % (0x2345, 0x1002345)),
    "sh4_rt.c": (KIT_RT, "static unsigned int port_fsca_sin[2][65536];",
                 "for (u32 t = 0; t < 2; t++) for (u32 i = 0; i < 65536; i++) port_fsca_sin[t][i] = t << 28 | i;",
                 "%08x %08x" % (0x2345, 0x6345)),
}


def compile_c(d, name, source, more=""):
    (d / f"{name}.c").write_text(source, encoding="utf-8")
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /W4 /WX /O1 {name}.c {more}"', cwd=d,
                       capture_output=True, text=True, errors="replace", timeout=300)
    return r, d / f"{name}.exe"


@unittest.skipUnless(HAVE_VC, "no 32-bit Visual Studio tools on this machine")
class Tables(unittest.TestCase):
    def test_tables(self):
        """tools/fsca_rom.py's two tables are the pinned ones; state/fsca_table.bin (fsca.file(), 1 MB) holds the
        round-to-nearest pairs, then the round-toward-zero ones, each half's sines and its cosines (the sine a quarter
        turn on) at the pinned SHA-256; the kit's C source holds both, in that order. +0 at every zero crossing."""
        import hashlib
        import re
        import struct
        f = fsca.file()
        self.assertEqual(len(f), fsca.SIZE)
        self.assertEqual(fsca.SIZE, 1 << 20)
        words = struct.unpack("<262144I", f)
        c_words = tuple(int(x, 16) for x in re.findall(r"0x([0-9a-f]{8})u", fsca.table_c()))
        self.assertEqual(len(c_words), 2 * 65536)
        for rz in (False, True):
            s = fsca.sines(rz)
            self.assertEqual(hashlib.sha256(struct.pack("<65536I", *s)).hexdigest(), fsca.SHA256[rz])
            half = words[rz * 131072:(rz + 1) * 131072]
            self.assertEqual(hashlib.sha256(struct.pack("<65536I", *half[0::2])).hexdigest(), fsca.SHA256[rz])
            self.assertEqual(half[1::2], tuple(s[(a + 0x4000) & 0xffff] for a in range(65536)))
            self.assertEqual(c_words[rz * 65536:(rz + 1) * 65536], s)
            self.assertEqual((s[0], s[0x8000]), (0, 0))
            self.assertNotIn(0x80000000, s)
        self.assertEqual([fsca.sines()[a] for a in (0x4000, 0xc000)], [0x3f800000, 0xbf800000])
        self.assertEqual([fsca.sines(True)[a] for a in (0x4000, 0xc000)], [0x3f7fffff, 0xbf7fffff])


class RegressTable(unittest.TestCase):
    def test_current_file(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import regress_project as rp
        with tempfile.TemporaryDirectory() as d:
            live, work = pathlib.Path(d, "live"), pathlib.Path(d, "work")
            live.mkdir()
            work.mkdir()
            rp.fsca_table(live, work / "fsca_table.bin")  # no live table: none in the private state either
            self.assertFalse((work / "fsca_table.bin").exists())
            (live / "fsca_table.bin").write_bytes(fsca.pairs())  # the one-table file of before (512 KB)
            with mock.patch("sys.stderr"):
                rp.fsca_table(live, work / "fsca_table.bin")
            self.assertEqual((work / "fsca_table.bin").read_bytes(), fsca.file())
        src = text(ROOT / "tools" / "regress_project.py")
        self.assertIn('    fsca_table(live["state"], paths.FSCA_TABLE)\n', src)
        self.assertNotIn('"fsca_table.bin", "msvc_env.json"', src)


class Heal(unittest.TestCase):
    def test_ensure(self):
        self.assertEqual(hashlib.sha256(fsca.file()).hexdigest(), fsca.FILE_SHA256)
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "fsca_table.bin")
            with mock.patch("sys.stderr"):
                self.assertIn(": missing, not the current table (core/fsca); rewritten", fsca.ensure(p))
                self.assertEqual(p.read_bytes(), fsca.file())
                self.assertIsNone(fsca.ensure(p))  # current: left alone, no line
                p.write_bytes(fsca.pairs())  # the one-table file of before c346302
                self.assertIn(": 524288 bytes, SHA-256 ", fsca.ensure(p))
            self.assertEqual(p.read_bytes(), fsca.file())
            self.assertEqual(sorted(x.name for x in pathlib.Path(d).iterdir()), ["events.log", "fsca_table.bin"])
            log = pathlib.Path(d, "events.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual([("missing" in x, "524288 bytes" in x) for x in log], [(True, False), (False, True)])


SCRIPT_ERROR = ("ERROR REPORT SCRIPT ERROR:  (HeadlessAnalyzer) java.lang.IllegalStateException: fsca table "
                "/p/state/fsca_table.bin: 524288 bytes, not 1048576 (both tables: python -m core.fsca)")


class PrepareHeals(Prepare):
    test_steps_in_order = test_failure_recorded = None  # Prepare's own tests run from test_prepare_timing

    def setUp(self):
        super().setUp()
        self.table = self.state / "fsca_table.bin"
        self.q = mock.patch.object(paths, "FSCA_TABLE", self.table)
        self.q.start()
        self.state.mkdir(parents=True, exist_ok=True)
        self.table.write_bytes(fsca.pairs())  # stale: the one-table file every reader refuses

    def tearDown(self):
        self.q.stop()
        super().tearDown()

    def test_stale_table_healed(self):
        seen = []
        fake = self.fake_run()

        def run(cmd, timeout, **k):
            seen.append(pathlib.Path(cmd[cmd.index("TcPrepare.java") + 7]).read_bytes())  # the table TcPrepare reads
            return fake(cmd, timeout, **k)
        with mock.patch.object(type(self), "fake_run", lambda s, fail=False: run), mock.patch("sys.stderr"):
            meta, summary = self.prepare({})
        self.assertEqual(summary, {"dev_valid": 48})
        self.assertEqual([len(x) for x in seen], [fsca.SIZE])
        self.assertEqual(seen[0], fsca.file())
        self.assertIn("524288 bytes", (self.state / "events.log").read_text(encoding="utf-8"))

    def test_cause_named(self):
        def run(cmd, timeout, on_line=None, **k):
            for line in GHIDRA_LINES[:8] + [SCRIPT_ERROR, "\tat TcPrepare.run(TcPrepare.java:79)"]:
                on_line(line + "\n")
            return 0, "\n".join(GHIDRA_LINES[:8] + [SCRIPT_ERROR, "\tat TcPrepare.run(TcPrepare.java:79)"]), None
        cfg = dict(paths.config(), ghidra_home=str(self.root), java_home=str(self.root), ghidra_timeout_s=60,
                   vectors_dev=48, vectors_hidden=48,
                   ghidra_broker={"slots": 0})  # Ghidra never runs here: no machine-wide token
        with mock.patch.object(paths, "run_bounded", run), mock.patch("sys.stderr"), \
                self.assertRaises(RuntimeError) as cm:
            ghidra.prepare(cfg, "8c010000", slot_index=1, staging=self.staging())
        self.assertEqual(str(cm.exception).splitlines()[0], "Ghidra failed (rc=0): java.lang.IllegalStateException: "
                         "fsca table /p/state/fsca_table.bin: 524288 bytes, not 1048576 (both tables: python -m "
                         "core.fsca)")
        self.assertEqual(ghidra._cause("INFO  x\njava.io.IOException: disk full\n\tat X.y(X.java:1)"),
                         "java.io.IOException: disk full")
        self.assertTrue(ghidra._cause("INFO  nothing\n").startswith("no script error"))
        src = text(ROOT / "core" / "ghidra.py")
        ensure = src.index("    paths.fsca_table()  # TcPrepare refuses")
        self.assertLess(ensure, src.index("    if ghidra_server.serves(cfg, slot_index, staging):"))  # served too


del Prepare  # imported for subclassing only: its tests run from test_prepare_timing


class CSides(unittest.TestCase):
    def setUp(self):
        work = ROOT / "tests" / ".work"
        self.d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_pick_by_rounding(self):
        """The same fsca under MXCSR.RC nearest, toward zero, up and down, on each C side."""
        for name, (path, decl, fill, nearest) in SIDES.items():
            fn = function(path, "void sh4_fsca(u32 fpul, f32 *s, f32 *c)")
            r, exe = compile_c(self.d, "pick", HEAD + decl + "\n" + fn + PICK_MAIN % fill)
            self.assertEqual(r.returncode, 0, name + r.stdout[-2000:])
            zero = " ".join("%08x" % (1 << 28 | int(w, 16)) for w in nearest.split())
            for rc, want in (("0", nearest), ("6000", zero)):
                out = subprocess.run([str(exe), rc], capture_output=True, text=True, timeout=60)
                self.assertEqual((out.returncode, out.stdout.strip()), (0, want), (name, rc))
            for rc in ("2000", "4000"):  # round down, round up: no FPSCR.RM rounds so
                out = subprocess.run([str(exe), rc], capture_output=True, text=True, timeout=60)
                self.assertEqual((out.returncode, out.stdout), (3, ""), (name, rc))
                self.assertIn("rounds up or down", out.stderr, (name, rc))

    def table_files(self):
        full = fsca.file()
        flip = bytearray(full)
        flip[fsca.SIZE // 2 + 8 * 0x1234] ^= 1  # one toward-zero sine
        files = {"full": full, "old": fsca.pairs(), "long": full + b"\0" * 8, "flip": bytes(flip)}
        for k, v in files.items():
            (self.d / f"{k}.bin").write_bytes(v)
        return files

    def test_rd_init_size(self):
        """rd_sh4_init takes the 1 MB file and refuses the 512 KB one of before (and a longer one), saying so."""
        self.table_files()
        fn = function(RD_SH4, "int rd_sh4_init(const char *fsca_table)")
        main = "\nint main(int argc, char **argv)\n{\n    return argc > 1 ? !rd_sh4_init(argv[1]) : 2;\n}\n"
        r, exe = compile_c(self.d, "rdinit", HEAD + "static u32 (*g_fsca)[65536][2];\n" + fn + main)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        for k, rc in (("full", 0), ("old", 1), ("long", 1), ("flip", 0)):  # rd_sh4_init does not compare values
            out = subprocess.run([str(exe), str(self.d / f"{k}.bin")], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, rc, k)
            self.assertEqual("not 1048576 bytes" in out.stderr, bool(rc), k)

    def test_kit_check(self):
        """The kit's sh4_rt_init (port --check-fsca) against core/fsca.py's file: equal, short (the one-table file),
        long, and one toward-zero sine off; it compares both halves, cosines included."""
        self.table_files()
        (self.d / "fsca_table.c").write_text(fsca.table_c(), encoding="utf-8")
        fn = function(KIT_RT, "int sh4_rt_init(const char *check_table)")
        main = "\nint main(int argc, char **argv)\n{\n    return argc > 1 ? !sh4_rt_init(argv[1]) : 2;\n}\n"
        src = HEAD + "extern const unsigned int port_fsca_sin[2][65536];\n" + fn + main
        r, exe = compile_c(self.d, "kitinit", src,
                           "fsca_table.c")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        for k, rc, said in (("full", 0, "0 of 131072"), ("old", 1, "not 2 x 65,536"), ("long", 1, "not 2 x 65,536"),
                            ("flip", 1, "1 of 131072")):
            out = subprocess.run([str(exe), str(self.d / f"{k}.bin")], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, rc, k)
            self.assertIn(said, out.stderr, k)


class Wiring(unittest.TestCase):
    def test_readers(self):
        tc = text(TC_RT)
        self.assertIn("static u32 g_fsca[2][65536][2];", tc)
        self.assertIn("if (!tab || size != (long)sizeof g_fsca) {\n        fprintf(stderr, \"fsca table %s:", tc)
        prep = text(SCRIPTS / "TcPrepare.java")
        self.assertIn("if (tb.length != 2 * 65536 * 8)\n            throw new IllegalStateException(", prep)
        self.assertIn("fscaSin[t][i] = bb.getInt();", prep)
        oracle = text(SCRIPTS / "TcOracle.java")
        self.assertIn("long rm = facts.fpscrModel ? emu.readRegister(\"FPSCR_RM\").longValue() : 0;", oracle)
        self.assertIn("tables[TcFpu.fscaTable(facts.fpscrModel, rm, facts.fpscrEntry, reject)]", oracle)
        self.assertIn("fscaCallbacks(emu, ms, fault, fscaRm);", oracle)
        self.assertIn("res.reason = fpuWhy != null ? fpuWhy : fscaRm[0];", oracle)


def jdk(tool):
    from core import toolcfg
    home = toolcfg.load(ROOT).get("java_home")
    exe = pathlib.Path(home, "bin", tool + (".exe" if os.name == "nt" else "")) if home else None
    return str(exe) if exe and exe.is_file() else shutil.which(tool)


def classpath():
    from core import toolcfg
    home = toolcfg.load(ROOT).get("ghidra_home")
    if not home or not pathlib.Path(home, "Ghidra").is_dir():
        return None
    dirs = sorted({p.parent for p in pathlib.Path(home, "Ghidra").glob("**/lib/*.jar")})
    return os.pathsep.join(str(d / "*") for d in dirs)


CP = classpath()


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    def test_table_by_rm(self):
        work = ROOT / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fscarm_", dir=work) as out:
            src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(ROOT / "tests" / "java" / "TcFscaStub.java")], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcFscaStub"], capture_output=True,
                               text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rej = "FPSCR mode RM=%d at fsca (not the entry's)"
        self.assertEqual(r.stdout.splitlines(), [
            "0 0 40001 -> 0 null",                # no fpscr_model: round to nearest throughout
            "0 1 40001 -> 0 null",
            "1 1 40001 -> 1 null",                # the model, RM=1 at entry and at the fsca: toward zero
            "1 0 40001 -> 0 " + rej % 0,          # RM changed since the entry: rejected
            "1 0 40000 -> 0 null",                # an RM=0 game: round to nearest
            "1 1 40000 -> 1 " + rej % 1,
            "1 2 40001 -> 0 " + rej % 2,          # RM 2 (reserved): rejected
        ])


if __name__ == "__main__":
    unittest.main()

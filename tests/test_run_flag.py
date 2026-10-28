"""A run flag a callee clears (TcCalls; project.json "run_flags"). Power Stone 2's Game_Main 8c02d2a8 stores 1 into
its own run flag 8c5423d4 (8c02d2ee) and loops while it is non-zero (8c02d42e..8c02d434); only a callee clears it
(8c02d930 -> 8c05b54e -> 8c05b32a, the store at 8c05b36c). Mocked callees stored nothing, so every vector ran to the
call limit and the function had no valid vector. Now, opt-in per function with evidence, the Nth mocked call to the
listed callee stores 0 into the word, in the oracle and in every runtime. Pass and fail controls:
1. the listing: {"word", "callee", "call", "why"}; no evidence, a bad address, an unaligned word or a count outside
   1..64 is refused; script arg 12 "rf:WORD_CALLEE_N" only for a listed function; an unlisted function's stamp is
   as without the list, a listed one's differs and the count is an input; selective_restamp --change runflag takes
   the listed functions and no other;
2. the wiring: core/ghidra.py appends arg 12 only for a listed function (every other prepare's arguments as
   before), TcOracle calls TcCalls.flagCall right after TcStrict.call, TcPrepare reads arg 12 and writes line f and
   meta run_flag, the scripts stay at 400 lines and TcCalls uses only locked scripts; the agents' note names it;
3. the oracle side (compiled TcCalls): the argument's parse (folded addresses), line f and meta; none without it;
4. both runners (raw and readable, compiled from their source) on a Game_Main-shaped loop: with line f the loop
   ends after N passes and the word is written 0; without it, or with another callee, it runs to the C's own cap;
   the raw runner's output without line f is byte-identical to the runtime before run-flag (BASE: no harness of an
   unlisted function changes how it runs).
The oracle's run itself needs Ghidra: the re-prepare of 8c02d2a8 on Power Stone 2's copy judges it.
Synthetic data. Run: python -W error tests/test_run_flag.py.
"""
import json
import os
import pathlib
import re
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
from core import agent, check, compare, oracle_stamp, paths, readable_check, toolcfg  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"
WORK = ROOT / "tests" / ".work"
VC64 = toolcfg.load(ROOT).get("vcvars")
BASE = "75c2f25da3714ee7cc8af94f532d5a50241a3b07"  # master before run-flag (a commit: master moves on)
WHY = ("8c02d2ee stores 1; the loop 8c02d358..8c02d434 runs while it is non-zero; 8c02d930 -> 8c05b54e -> 8c05b32a "
       "stores 0 at 8c05b36c, the only other store to it")
LISTED = {"run_flags": {"8c02d2a8": {"word": "8c5423d4", "callee": "8c02d930", "call": 3, "why": WHY}}}


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


class Listing(unittest.TestCase):
    def test_listed(self):
        self.assertEqual(compare.run_flag(LISTED, "8C02D2A8"), (0x8C5423D4, 0x8C02D930, 3))
        self.assertIsNone(compare.run_flag(LISTED, "8c02d630"))
        self.assertIsNone(compare.run_flag({}, "8c02d2a8"))
        e = LISTED["run_flags"]["8c02d2a8"]
        for bad in ([e], {"8c02d2a8": dict(e, why="")}, {"8c02d2a8": dict(e, why=None)},
                    {"8c02d2a8": {k: v for k, v in e.items() if k != "why"}}, {"8c02d2a8": dict(e, word="zz")},
                    {"8c02d2a8": dict(e, word="8c5423d6")}, {"8c02d2a8": dict(e, call=0)},
                    {"8c02d2a8": dict(e, call=65)}, {"8c02d2a8": dict(e, call="3")}, {"8c02d2a8": dict(e, call=True)},
                    {"8c02d2a8": {k: v for k, v in e.items() if k != "callee"}}, {"8c02d2a8": "8c5423d4"}):
            with self.assertRaises(ValueError, msg=bad):
                compare.run_flag({"run_flags": bad}, "8c02d2a8")

    def test_arg(self):  # script arg 12
        self.assertEqual(compare.run_flag_arg(LISTED, "8c02d2a8"), "rf:8c5423d4_8c02d930_3")
        self.assertEqual(compare.run_flag_arg(LISTED, "8c02d630"), "-")
        self.assertNotRegex(compare.run_flag_arg(LISTED, "8c02d2a8"), r"[,;= ]")  # analyzeHeadless.bat splits there

    def test_stamp(self):
        def s(cfg, addr):
            return oracle_stamp.stamp_of(dict(cfg, binary_sha256="ab"), addr, {}, "-", None, 48, 48)
        self.assertEqual(s(LISTED, "8c02d630"), s({}, "8c02d630"))  # unlisted: as without the list
        self.assertNotEqual(s(LISTED, "8c02d2a8"), s({}, "8c02d2a8"))  # listed: re-prepared
        two = {"run_flags": {"8c02d2a8": dict(LISTED["run_flags"]["8c02d2a8"], call=2)}}
        self.assertNotEqual(s(LISTED, "8c02d2a8"), s(two, "8c02d2a8"))  # the count is an input

    def test_restamp(self):
        self.assertEqual(sr.runflag_candidates(LISTED), {"8c02d2a8"})
        self.assertEqual(sr.runflag_candidates({}), set())
        self.assertIn('"runflag"', text(ROOT / "tools" / "selective_restamp.py"))


class Wiring(unittest.TestCase):
    def test_scripts(self):
        oracle = text(SCRIPTS / "TcOracle.java")
        self.assertIn("TcStrict.call(res, emu, calls); TcCalls.flagCall(this, res, emu, pc);", oracle)
        prep = text(SCRIPTS / "TcPrepare.java")
        self.assertIn('TcCalls.flag = TcCalls.parseFlag(a.length > 12 ? a[12] : "-");', prep)
        self.assertIn("String head = fpscrLine() + TcHw.header() + TcCalls.flagHeader() + (live", prep)
        self.assertIn("extra.append(TcCalls.flagMeta());", prep)
        for name in ("TcOracle.java", "TcPrepare.java", "TcCalls.java"):
            self.assertLessEqual(len(text(SCRIPTS / name).splitlines()), 400, name)
        locked = set(json.loads(text(SH4 / "target.json"))["locked_scripts"])
        have = {p.stem for p in SCRIPTS.glob("*.java")}
        used = set(re.findall(r"\b(Tc[A-Z]\w*)\b", re.sub(r"//.*", "", text(SCRIPTS / "TcCalls.java")))) & have
        self.assertEqual(sorted(u for u in used if u + ".java" not in locked), [])

    def test_ghidra_arg(self):  # arg 12 only for a listed function: every other prepare's arguments as before
        g = text(ROOT / "core" / "ghidra.py")
        self.assertIn("    if compare.run_flag(cfg, addr):  # script arg 12: the run flag project.json \"run_flags\" "
                      "lists (TcCalls)\n        tc_args.append(compare.run_flag_arg(cfg, addr))\n", g)

    def test_runtimes(self):
        for p in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
            self.assertIn("tcrt_set_run_flag(word, callee, (u32)strtoul(p, &p, 10));", text(p), p.name)
        self.assertIn("    flag_apply(tcrt_fold(target));\n    g_ncalls++;", text(SH4 / "runtime" / "tc_rt.c"))
        rd = text(SH4 / "runtime_rd" / "rd_veh.c")
        self.assertEqual(rd.count("    flag_apply("), 2)  # the raw-C call API and the trapped call

    def test_note(self):
        meta = {"params": [], "addr": "8c02d2a8",
                "run_flag": {"word": "8c5423d4", "callee": "8c02d930", "call": 3}}
        note = " ".join(agent.abi_notes(meta))
        self.assertIn("0x8c5423d4 is a run flag", note)
        self.assertIn("call 3 to fn_8c02d930 stores 0", note)
        self.assertEqual(agent.abi_notes({"params": [], "addr": "8c02d2a8"}), [])  # control: no note without it


STUB = """public class FlagStub {
    public static void main(String[] a) {
        for (String s : a) {
            TcCalls.flag = TcCalls.parseFlag(s);
            System.out.println((TcCalls.flag == null ? "null" : TcBase.hex(TcCalls.flag[0]) + " "
                + TcBase.hex(TcCalls.flag[1]) + " " + TcCalls.flag[2]) + " | " + TcCalls.flagHeader().trim() + " | "
                + TcCalls.flagMeta().replace("\\n", "~"));
        }
    }
}
"""


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    def test_parse_header_meta(self):
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rflag_", dir=WORK) as tmp:
            d = pathlib.Path(tmp)
            (d / "FlagStub.java").write_text(STUB, encoding="utf-8")
            src = os.pathsep.join(str(SH4 / x) for x in ("ghidra_scripts", "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(d), "-cp", CP, "-sourcepath", src,
                                str(d / "FlagStub.java")], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", str(d) + os.pathsep + CP, "FlagStub", "rf:8c5423d4_8c02d930_3",
                                "rf:0c5423d4_0c02d930_1", "-"], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        meta = ',~  "run_flag": {"word": "8c5423d4", "callee": "8c02d930", "call": %d}'
        self.assertEqual(lines, ["8c5423d4 8c02d930 3 | f 8c5423d4 8c02d930 3 | " + meta % 3,
                                 "8c5423d4 8c02d930 1 | f 8c5423d4 8c02d930 1 | " + meta % 1,  # the P0 form folds
                                 "null |  | "])  # control: nothing listed, no line f, no meta key


# Game_Main's shape: set the flag, then call the listed callee and another each pass while the flag is non-zero. The
# C's own cap (CAP passes) ends a run the flag never ends.
CAP = 40
RAW_C = """#include "tc_rt.h"
u32 fn_test(void);
u32 fn_test(void)
{
    TcArgs a = {0};
    u32 passes = 0;
    wr32(0x8c5423d4u, 1u);
    do {
        tc_call(0x8c02d930u, &a);
        tc_call(0x8c05ac64u, &a);
        passes++;
    } while (rd32(0x8c5423d4u) != 0 && passes < %du);
    return passes;
}
""" % CAP
RAW_THUNK = """#include "tc_rt.h"
u32 fn_test(void);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)r;
    (void)fr;
    (void)out_fr0;
    *out_r0 = fn_test();
}
"""
RD_C = """#include "rd_rt.h"
typedef void (*Fn)(void);
u32 fn_test(void);
u32 fn_test(void)
{
    volatile u32 *flag = (volatile u32 *)(uintptr_t)0x8c5423d4u;
    u32 passes = 0;
    *flag = 1u;
    do {
        ((Fn)(uintptr_t)0x8c02d930u)();
        ((Fn)(uintptr_t)0x8c05ac64u)();
        passes++;
    } while (*flag != 0 && passes < %du);
    return passes;
}
""" % CAP
RD_THUNK = RAW_THUNK.replace("tc_rt.h", "rd_rt.h")


def vectors(head=""):
    return head + "V 0 dev\nR 0 0 0 0\nE\nV 1 hidden\nR 0 0 0 0\nE\n"


def ret_and_flag(out):
    """[(RET, the W bytes at 8c5423d4)] per vector."""
    got, cur = [], None
    for line in out.splitlines():
        t = line.split()
        if t[0] == "RET":
            cur = [int(t[1], 16), None]
            got.append(cur)
        elif t[0] == "W" and int(t[1], 16) == 0x8C5423D4:
            cur[1] = t[2]
    return [tuple(x) for x in got]


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="rflag_rt_", dir=WORK)
        d = cls.d = pathlib.Path(cls.tmp.name)
        srcs = ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")
        master = d / "master_rt"  # the runtime before run-flag (BASE): git show of each runtime file
        master.mkdir()
        for f in (SH4 / "runtime").iterdir():
            rel = f.relative_to(ROOT).as_posix()
            r = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:{rel}"], capture_output=True, timeout=60)
            if r.returncode == 0:
                (master / f.name).write_bytes(r.stdout)
        cmds = []
        for name, rt in (("raw", SH4 / "runtime"), ("master", master)):
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
        ok, msgs = readable_check.build(paths.config(), [d / "rd.c"], RD_THUNK,
                                        {0x8C02D930: ("", "v"), 0x8C05AC64: ("", "v")}, d / "rd")
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

    def exes(self):
        """Each runner and its W bytes at the word when 0 and when 1: the readable runner reports only the bytes that
        differ from the image (zeros here), the raw runner every byte the run wrote."""
        return ((self.d / "raw" / "runner.exe", "00000000", "01000000"), (self.d / "rd" / "rd_runner.exe", None, "01"))

    def test_flag_ends_the_loop(self):
        for exe, zero, _one in self.exes():
            for n in (1, 3):
                got = ret_and_flag(self.run_one(exe, vectors(f"f 8c5423d4 8c02d930 {n}\n")))
                self.assertEqual(got, [(n, zero)] * 2, (exe.name, n))  # each vector: n passes, the word 0
            got = ret_and_flag(self.run_one(exe, vectors("f 0c5423d4 0c02d930 2\n")))  # the P0 form folds
            self.assertEqual(got, [(2, zero)] * 2, exe.name)

    def test_controls(self):
        for exe, _zero, one in self.exes():
            self.assertEqual(ret_and_flag(self.run_one(exe, vectors())), [(CAP, one)] * 2, exe.name)
            other = ret_and_flag(self.run_one(exe, vectors("f 8c5423d4 8c0dead0 1\n")))  # another callee's
            self.assertEqual(other, [(CAP, one)] * 2, exe.name)

    def test_unlisted_as_master(self):  # no line f: the raw runner's output byte for byte as BASE's runtime
        for head in ("", "P 00040001\n", "J 15\nU 8c02d930 0 4094\n"):
            self.assertEqual(self.run_one(self.d / "raw" / "runner.exe", vectors(head)),
                             self.run_one(self.d / "master" / "runner.exe", vectors(head)), head)


if __name__ == "__main__":
    unittest.main()

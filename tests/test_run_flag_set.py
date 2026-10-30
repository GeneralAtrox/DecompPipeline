"""The settable run flag (core/run_flag_set; TcCalls "rs:"; runtime/tc_flag.h): Toy Commander's window loop 8c077c30
clears bit 0x400 of *(r4 + 0x5c) and calls the window callbacks until it is set; on the console a callback sets it.
Opt-in per function: the Nth mocked call to the listed callee sets the bits in the word at (rREG at the entry) +
offset, in the oracle and in both runners. Pass and fail controls:
1. the listing: {"base", "offset", "bits", "callee", "call", "why"}; no evidence, a base outside r4..r7, "word" beside
   "base", an unaligned or large offset, bits 0 or past 32 bits, a count outside 1..64 are refused; script arg 12
   "rs:OFFSET_CALLEE_N_REG_BITS" for it only, the clearing form's "rf:" as before; a listed function's stamp differs
   and every input is in it, an unlisted one's is as without the list; selective_restamp --change runset takes the
   listed functions and no other; the agents' note says the bits are set (the clearing form's note as before);
2. the oracle side (compiled TcCalls): the "rs:" parse (folded callee), line f and meta; a bad register is refused;
3. both runners (raw and readable, compiled from their source) on a window-shaped loop, two vectors with different
   r4: with the settable line f the loop ends after N passes and each vector's own word has the bit set and its other
   bits kept; without it, with another callee, or with the clearing form on that word, it runs to the C's own cap.
tests/test_run_flag.py still holds for the clearing form, including its raw runner byte for byte as before run-flag
without line f. The oracle's run itself needs Ghidra: the prepare of 8c077c30 on TC judges it.
Synthetic data. Run: python -W error tests/test_run_flag_set.py.
"""
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
from core import agent, check, compare, oracle_stamp, paths, readable_check, toolcfg  # noqa: E402
from test_run_flag import LISTED  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
WORK = ROOT / "tests" / ".work"
VC64 = toolcfg.load(ROOT).get("vcvars")
WHY = ("8c077c8c clears bit 0x400 of *(r4+0x5c); the loop 8c077cc6..8c077d36 runs until it is set; it calls 8c07c8d8 "
       "with r12 = r4 each pass, and on the console the window callbacks set the bit when the window closes")
E = {"base": "r4", "offset": "5c", "bits": "400", "callee": "8c07c8d8", "call": 3, "why": WHY}
SET = {"run_flags": {"8c077c30": E}}


class Listing(unittest.TestCase):
    def test_listed(self):
        self.assertEqual(compare.run_flag(SET, "8C077C30"), (0x5C, 0x8C07C8D8, 3, 4, 0x400))
        self.assertIsNone(compare.run_flag(SET, "8c077b6c"))
        self.assertEqual(compare.run_flag(LISTED, "8c02d2a8"), (0x8C5423D4, 0x8C02D930, 3))  # clearing form as before
        for bad in (dict(E, why=""), {k: v for k, v in E.items() if k != "why"}, dict(E, base="r8"),
                    dict(E, base="r3"), dict(E, word="8c5423d4"), dict(E, offset="5e"), dict(E, offset="1000"),
                    dict(E, offset="zz"), dict(E, bits="0"), dict(E, bits="100000000"), dict(E, call=0),
                    dict(E, call=65), dict(E, call="3"), dict(E, call=True),
                    {k: v for k, v in E.items() if k != "callee"}, {k: v for k, v in E.items() if k != "bits"}):
            with self.assertRaises(ValueError, msg=bad):
                compare.run_flag({"run_flags": {"8c077c30": bad}}, "8c077c30")

    def test_arg(self):  # script arg 12
        self.assertEqual(compare.run_flag_arg(SET, "8c077c30"), "rs:0000005c_8c07c8d8_3_4_00000400")
        self.assertEqual(compare.run_flag_arg(LISTED, "8c02d2a8"), "rf:8c5423d4_8c02d930_3")
        self.assertEqual(compare.run_flag_arg(SET, "8c077b6c"), "-")
        self.assertNotRegex(compare.run_flag_arg(SET, "8c077c30"), r"[,;= ]")  # analyzeHeadless.bat splits there

    def test_stamp(self):
        def s(cfg, addr):
            return oracle_stamp.stamp_of(dict(cfg, binary_sha256="ab"), addr, {}, "-", None, 48, 48)
        self.assertEqual(s(SET, "8c077b6c"), s({}, "8c077b6c"))  # unlisted: as without the list
        self.assertNotEqual(s(SET, "8c077c30"), s({}, "8c077c30"))  # listed: re-prepared
        for k, v in (("call", 2), ("bits", "800"), ("offset", "60"), ("base", "r5"), ("callee", "8c07c2b4")):
            other = {"run_flags": {"8c077c30": dict(E, **{k: v})}}
            self.assertNotEqual(s(SET, "8c077c30"), s(other, "8c077c30"), k)  # each one an input

    def test_restamp(self):
        self.assertEqual(sr.runflag_candidates(SET), {"8c077c30"})
        self.assertEqual(sr.runflag_candidates({}), set())
        self.assertIn('"runset"', (ROOT / "tools" / "selective_restamp.py").read_text(encoding="utf-8"))

    def test_note(self):
        meta = {"params": [], "addr": "8c077c30",
                "run_flag": {"base": "r4", "offset": "0000005c", "bits": "00000400", "callee": "8c07c8d8", "call": 3}}
        note = " ".join(agent.abi_notes(meta))
        self.assertIn("## A run flag a callee sets", note)
        self.assertIn("The word at r4 + 0x5c (r4 as the function got it) is a run flag", note)
        self.assertIn("call 3 to fn_8c07c8d8 sets those bits", note)
        old = " ".join(agent.abi_notes({"params": [], "addr": "8c02d2a8",
                                        "run_flag": {"word": "8c5423d4", "callee": "8c02d930", "call": 3}}))
        self.assertIn("call 3 to fn_8c02d930 stores 0", old)  # control: the clearing form's note as before
        self.assertNotIn("sets", old.split("## A run flag")[1].split(".")[0])


STUB = """public class FlagSetStub {
    public static void main(String[] a) {
        for (String s : a) {
            try {
                TcCalls.flag = TcCalls.parseFlag(s);
            } catch (IllegalArgumentException e) {
                System.out.println("refused");
                continue;
            }
            StringBuilder b = new StringBuilder();
            for (long x : TcCalls.flag == null ? new long[0] : TcCalls.flag) b.append(TcBase.hex(x)).append(' ');
            System.out.println(b.toString().trim() + " | " + TcCalls.flagHeader().trim() + " | "
                + TcCalls.flagMeta().replace("\\n", "~"));
        }
    }
}
"""


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    def test_parse_header_meta(self):
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rfset_", dir=WORK) as tmp:
            d = pathlib.Path(tmp)
            (d / "FlagSetStub.java").write_text(STUB, encoding="utf-8")
            src = os.pathsep.join(str(SH4 / x) for x in ("ghidra_scripts", "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(d), "-cp", CP, "-sourcepath", src,
                                str(d / "FlagSetStub.java")], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", str(d) + os.pathsep + CP, "FlagSetStub",
                                "rs:0000005c_0c07c8d8_3_4_00000400", "rs:0000005c_8c07c8d8_3_8_00000400",
                                "rf:8c5423d4_8c02d930_3", "-"], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        meta = (',~  "run_flag": {"base": "r4", "offset": "0000005c", "bits": "00000400", "callee": "8c07c8d8", '
                '"call": 3}')
        self.assertEqual(r.stdout.splitlines(), [
            "0000005c 8c07c8d8 00000003 00000004 00000400 | f 0000005c 8c07c8d8 3 4 00000400 | " + meta,
            "refused",  # control: r8 is no argument register
            '8c5423d4 8c02d930 00000003 | f 8c5423d4 8c02d930 3 | ,~  "run_flag": {"word": "8c5423d4", '
            '"callee": "8c02d930", "call": 3}',  # control: the clearing form as before
            " |  | "])  # control: nothing listed


# 8c077c30's shape: clear bit 0x400 and set 0x8000 in *(w + 0x5c), then call the two callbacks each pass until the bit
# is set. The C's own cap (CAP passes) ends a run the flag never ends.
CAP = 40
RAW_C = """#include "tc_rt.h"
u32 fn_test(u32 w);
u32 fn_test(u32 w)
{
    TcArgs a = {0};
    u32 passes = 0;
    wr32(w + 0x5cu, (rd32(w + 0x5cu) & ~0x400u) | 0x8000u);
    do {
        a.r[0] = w;
        tc_call(0x8c07c8d8u, &a);
        tc_call(0x8c07c2b4u, &a);
        passes++;
    } while ((rd32(w + 0x5cu) & 0x400u) == 0 && passes < %du);
    return passes;
}
""" % CAP
RAW_THUNK = """#include "tc_rt.h"
u32 fn_test(u32 w);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)fr;
    (void)out_fr0;
    *out_r0 = fn_test(r[0]);
}
"""
RD_C = """#include "rd_rt.h"
typedef void (*Fn)(u32);
u32 fn_test(u32 w);
u32 fn_test(u32 w)
{
    volatile u32 *flag = (volatile u32 *)(uintptr_t)(w + 0x5cu);
    u32 passes = 0;
    *flag = (*flag & ~0x400u) | 0x8000u;
    do {
        ((Fn)(uintptr_t)0x8c07c8d8u)(w);
        ((Fn)(uintptr_t)0x8c07c2b4u)(w);
        passes++;
    } while ((*flag & 0x400u) == 0 && passes < %du);
    return passes;
}
""" % CAP
RD_THUNK = RAW_THUNK.replace("tc_rt.h", "rd_rt.h")
W0, W1 = 0x8C100000, 0x8C100100  # each vector's r4


def vectors(head=""):
    return head + f"V 0 dev\nR {W0:08x} 0 0 0\nE\nV 1 hidden\nR {W1:08x} 0 0 0\nE\n"


def run_words(out):
    """[(RET, the word at W0 + 0x5c, the word at W1 + 0x5c)] per vector, from its W lines (unwritten bytes: 0)."""
    res, mem, ret = [], {}, None
    for line in out.splitlines() + ["V end"]:
        t = line.split()
        if t[0] == "V":
            if ret is not None:
                res.append((ret, *(int.from_bytes(bytes(mem.get(w + 0x5C + i, 0) for i in range(4)), "little")
                                   for w in (W0, W1))))
            mem, ret = {}, None
        elif t[0] == "W":
            for i, b in enumerate(bytes.fromhex(t[2]) if len(t) > 2 else b""):
                mem[int(t[1], 16) + i] = b
        elif t[0] == "RET":
            ret = int(t[1], 16)
    return res


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="rfset_rt_", dir=WORK)
        d = cls.d = pathlib.Path(cls.tmp.name)
        rt = SH4 / "runtime"
        b = d / "raw"
        b.mkdir()
        (b / "work.c").write_text(RAW_C)
        (b / "thunk.c").write_text(RAW_THUNK)
        cmd = (f'cd /d "{b}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{rt}" /Fe:runner.exe work.c thunk.c '
               + " ".join(f'"{rt / s}"' for s in ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")))
        vc = str(VC64).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cmd}"', capture_output=True, text=True,
                           errors="replace", timeout=600)
        if r.returncode:
            raise AssertionError(r.stdout[-3000:])
        (d / "rd.c").write_text(RD_C)
        ok, msgs = readable_check.build(paths.config(), [d / "rd.c"], RD_THUNK,
                                        {0x8C07C8D8: ("i", "v"), 0x8C07C2B4: ("i", "v")}, d / "rd")
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
        return run_words((self.d / "out.txt").read_text())

    def exes(self):
        return self.d / "raw" / "runner.exe", self.d / "rd" / "rd_runner.exe"

    def test_flag_ends_the_loop(self):
        for exe in self.exes():
            for n in (1, 3):
                got = self.run_one(exe, vectors(f"f 0000005c 8c07c8d8 {n} 4 00000400\n"))
                self.assertEqual(got, [(n, 0x8400, 0), (n, 0, 0x8400)], (exe.name, n))  # each vector its own word
            got = self.run_one(exe, vectors("f 0000005c 0c07c2b4 2 4 00000400\n"))  # the other callee, P0 form
            self.assertEqual(got, [(2, 0x8400, 0), (2, 0, 0x8400)], exe.name)

    def test_controls(self):
        for exe in self.exes():
            cap = [(CAP, 0x8000, 0), (CAP, 0, 0x8000)]
            self.assertEqual(self.run_one(exe, vectors()), cap, exe.name)  # no line f
            self.assertEqual(self.run_one(exe, vectors("f 0000005c 8c0dead0 1 4 00000400\n")), cap, exe.name)
            self.assertEqual(self.run_one(exe, vectors("f 0000005c 8c07c8d8 1 4 00000800\n")),  # other bits
                             [(CAP, 0x8800, 0), (CAP, 0, 0x8800)], exe.name)
            cleared = self.run_one(exe, vectors(f"f {W0 + 0x5C:08x} 8c07c8d8 1\n"))  # the clearing form: never set
            self.assertEqual([x[0] for x in cleared], [CAP, CAP], exe.name)
            self.assertEqual(cleared[0][1], 0, exe.name)


if __name__ == "__main__":
    unittest.main()

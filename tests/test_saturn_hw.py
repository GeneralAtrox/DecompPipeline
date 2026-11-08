"""Saturn hardware reads narrower than a word (core/hw_ops.width, targets/saturn_sh2 TcHw.java, tc_rt.c, tc_p4.c).

Grandia 060398ae and 060398cc busy-wait on bit 7 (ICF) of the FRT's FTCSR, a byte register at 0xfffffe11, read with
mov.b. Listed only as a 4-byte register, its value sat in the word's top byte and the byte load read 0x00 (big
endian), so the loop never ended: 0/384 valid vectors. A register now gives its width (target.json "hw_reads"
[value, evidence, 1|2]); only widths the target's runtime serves ("hw_read_sizes": Saturn [1, 2, 4], else [4]) are
accepted. The test proves:
1. the listing: a width is refused unless the target serves it, a value must fit, a 4-byte register's arg (and so its
   oracle stamp) is what it was;
2. TcHw (compiled against the Ghidra jars, tests/java/TcHwStub.java): a load is served only inside the register's own
   bytes, big endian; a 4-byte register's bytes, meta value and Y line are as before; a narrower one's Y line ends with
   its width;
3. the runtime (compiled from its source): tc_hw_read8 / 16 / 32 return those same bytes and fault elsewhere, an old Y
   line (no width) is a 4-byte register, and the listed stores (tc_hw_write8 / 16 / 32) log their bytes big endian, as
   the oracle's TcExit logs memory (the Saturn copy of tc_p4.c logged them little endian, the Dreamcast's order);
   TcExit reads back the bytes stored in a register listed for both a read and a write (SMPC SF, VDP2 TVMD), never the
   listed read value;
4. the agent's text names the read for the register's width, also with no special instruction in the function.
Run: python -W error tests/test_saturn_hw.py.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "port" / "tools")]
import game_config  # noqa: E402
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_tail import CP, jdk  # noqa: E402
from core import agent, compare, hw_ops, oracle_stamp  # noqa: E402

SAT = ROOT / "targets" / "saturn_sh2"
SIZES = {"hw_read_sizes": [1, 2, 4]}
FTCSR = {"fffffe11": ["80", "FRT FTCSR, ICF set: the input capture the loop waits for", 1]}
POLL = {"060398cc": {"ops": ["read fffffe11"], "evidence": "mov.b @r5,r0 polls FTCSR.ICF"}}


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


class HardwareWindows(unittest.TestCase):
    def test_a_window_store_is_judged_in_every_function(self):
        """A store to a hardware window is compared whatever the meta says, so C code inventing one fails even where
        the original stores to no hardware (meta "hw_windows" only records that the oracle stored there)."""
        d = pathlib.Path(tempfile.mkdtemp(prefix="satwin_"))
        try:
            (d / "project.json").write_text(json.dumps({"target": "saturn_sh2"}), encoding="utf-8")
            (d / "exp.txt").write_text("V 0 dev\nRET 0 0\nE\n", encoding="utf-8")
            for w in ("25e00010", "06010000"):
                (d / f"{w}.txt").write_text(f"V 0 dev\nRET 0 0\nW {w} deadbeef\nE\n", encoding="utf-8")
            code = ("import sys, pathlib\n"
                    "import core.compare as c\n"
                    "d = pathlib.Path(sys.argv[1])\n"
                    "j = lambda a: c.judged(a, 0x060fd000)\n"
                    "print(j(0x25e00010), j(0xfffffe92), j(0x25fc0000), j(0x25900000))\n"
                    "exp = c.parse_results(str(d / 'exp.txt'))[0]\n"
                    "for w in ('25e00010', '06010000'):\n"
                    "    act = c.parse_results(str(d / (w + '.txt')))[0]\n"
                    "    for meta in ({'ret': 'void', 'callees': []},\n"
                    "                 {'ret': 'void', 'callees': [], 'hw_windows': [['25e00000', '25fbffff']]}):\n"
                    "        diffs = c.compare_vector(meta, b'', {'patches': []}, exp, act, 0x060fd000)\n"
                    "        print(any('deadbeef' in x for x in diffs))\n")
            r = subprocess.run([sys.executable, "-c", code, str(d)], cwd=ROOT, capture_output=True, text=True,
                               timeout=60, env=dict(os.environ, DECOMP_PROJECT=str(d)))
            self.assertEqual(r.stdout.split(), ["True", "True", "False", "False"] + ["True"] * 4, r.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_judge_has_the_oracles_windows(self):
        jm = json.loads((SAT / "target.json").read_text(encoding="utf-8"))["judge_memory"]["hw_windows"]
        java = (SAT / "ghidra_scripts" / "TcBase.java").read_text(encoding="utf-8")
        table = java[java.index("HW_WINDOWS = {"):java.index("};", java.index("HW_WINDOWS = {"))]
        pairs = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"\{0x([0-9a-f]+)L, 0x([0-9a-f]+)L\}", table)]
        self.assertEqual([(int(lo, 16), int(hi, 16) + 1) for lo, hi in jm], pairs)

    def test_the_oracle_and_the_runner_have_the_same_windows(self):
        sat = pathlib.Path(__file__).resolve().parent.parent / "targets" / "saturn_sh2"
        java = (sat / "ghidra_scripts" / "TcBase.java").read_text(encoding="utf-8")
        c = (sat / "runtime" / "tc_rt_internal.h").read_text(encoding="utf-8")
        table = java[java.index("HW_WINDOWS = {"):java.index("};", java.index("HW_WINDOWS = {"))]
        pairs = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"\{0x([0-9a-f]+)L, 0x([0-9a-f]+)L\}", table)]
        cpairs = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"\{0x([0-9a-f]+)u, 0x([0-9a-f]+)u\}", c)]
        self.assertEqual(pairs[:-1], cpairs)
        self.assertEqual(pairs[-1], (0xfffffe00, 0x100000000))
        self.assertIn("a >= 0xfffffe00u", c)


class Listing(unittest.TestCase):
    def test_width(self):
        cfg = dict(SIZES, hw_reads=FTCSR, hw_ops=POLL)
        self.assertEqual(hw_ops.width(cfg, "fffffe11"), 1)
        self.assertEqual(hw_ops.arg(cfg, "060398cc"), "hw:rfffffe11.80")
        self.assertEqual(hw_ops.width(dict(cfg, hw_reads={"fffffe10": ["0080", "x", 2]}), "fffffe10"), 2)
        self.assertEqual(hw_ops.width(dict(cfg, hw_reads={"fffffe10": ["00000080", "x"]}), "fffffe10"), 4)
        for bad in ({"hw_reads": FTCSR, "hw_ops": POLL},  # a target that serves 4-byte registers only (the Dreamcast)
                    dict(SIZES, hw_reads={"fffffe11": ["180", "x", 1]}, hw_ops=POLL),  # does not fit a byte
                    dict(SIZES, hw_reads={"fffffe11": ["80", "x", 3]}, hw_ops=POLL)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                hw_ops.listed(bad)

    def test_word_unchanged(self):  # a 4-byte register: the arg and so the stamp as before the widths
        dc = {"hw_reads": {"a05f689c": ["0000000b", "SB_SBREV"]},
              "hw_ops": {"8c04f4c0": {"ops": ["read a05f689c", "ldc gbr"], "evidence": "x"}}}
        self.assertEqual(hw_ops.arg(dc, "8c04f4c0"), "hw:ra05f689c.0000000b_gbr")
        self.assertEqual(hw_ops.arg(dict(dc, hw_reads={"a05f689c": ["b", "SB_SBREV"]}), "8c04f4c0"),
                         "hw:ra05f689c.0000000b_gbr")
        cfg = dict(SIZES, hw_reads=FTCSR, hw_ops=POLL, binary_sha256="ab")
        s = oracle_stamp.stamp_of(cfg, "060398cc", {}, "-", None, 48, 48)
        self.assertNotEqual(s, oracle_stamp.stamp_of(dict(cfg, hw_reads={"fffffe11": ["00", "x", 1]}), "060398cc",
                                                     {}, "-", None, 48, 48))  # the value is an input

    def test_target(self):
        self.assertEqual(json.loads(text(SAT / "target.json"))["hw_read_sizes"], [1, 2, 4])
        self.assertNotIn("hw_read_sizes", json.loads(text(ROOT / "targets" / "dreamcast_sh4" / "target.json")))


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    def run_stub(self, arg):
        work = ROOT / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="sathw_", dir=work) as out:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath",
                                os.pathsep.join(str(SAT / d) for d in ("ghidra_scripts", "template")), str(ROOT / "tests" / "java" / "TcHwStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcHwStub", arg], capture_output=True,
                               text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.splitlines()
        self.assertIn("logged true", out)  # a W line holds what was stored, not the listed read value
        reads = {(int(o, 16), int(s)): b for _, o, s, b in (ln.split() for ln in out if ln.startswith("read "))}
        meta = next(ln[5:] for ln in out if ln.startswith("meta "))
        header = next(ln[7:] for ln in out if ln.startswith("header "))
        return reads, meta, header

    @staticmethod
    def want(reg, value, w):  # big endian bytes of the register, served only inside it
        b = value.to_bytes(w, "big")
        return {(o, s): b[o - reg:o - reg + s].hex() if reg <= o and o + s <= reg + w else "null"
                for o in range(reg - 2, reg + 5) for s in (1, 2, 4)}

    def test_byte(self):
        reads, meta, header = self.run_stub("hwrfffffe11.80_wfffffe11")
        self.assertEqual(reads, self.want(0xfffffe11, 0x80, 1))
        self.assertEqual(reads[(0xfffffe11, 1)], "80")  # what the poll reads: ICF set
        self.assertIn('"reads": {"fffffe11": "80"}', meta)
        self.assertEqual(header, "Y fffffe11 00000080 1|")

    def test_half(self):
        reads, meta, header = self.run_stub("hwrfffffe10.1234")
        self.assertEqual(reads, self.want(0xfffffe10, 0x1234, 2))
        self.assertIn('"reads": {"fffffe10": "1234"}', meta)
        self.assertEqual(header, "Y fffffe10 00001234 2|")

    def test_word_unchanged(self):
        reads, meta, header = self.run_stub("hwra05f689c.0000000b_gbr")
        self.assertEqual(reads, self.want(0xa05f689c, 0xb, 4))
        self.assertIn('"reads": {"a05f689c": "0000000b"}', meta)
        self.assertEqual(header, "Y a05f689c 0000000b|Z|")  # no width: the line the runtimes always read


MAIN = r"""#include <stdio.h>
#include "tc_rt_internal.h"
int main(void)
{
    tcrt_add_hw(0xfffffe11u, 0x80u, 1);
    tcrt_add_hw(0xfffffe20u, 0x1234u, 2);
    tcrt_add_hw(0x25fe00a4u, 0x11223344u, 4);
    printf("%02x %04x %08x\n", tc_hw_read8(0xfffffe11u), tc_hw_read16(0xfffffe20u), tc_hw_read32(0x25fe00a4u));
    printf("%02x %02x %04x %04x %02x\n", tc_hw_read8(0xfffffe21u), tc_hw_read8(0x25fe00a5u),
           tc_hw_read16(0x25fe00a6u), tc_hw_read16(0x25fe00a5u), tc_hw_read8(0x25fe00a7u));
    tcrt_p4_reset();
    tc_hw_write32(0x25fe00a8u, 0x00000008u);
    tc_hw_write16(0xfffffe92u, 0x1234u);
    tc_hw_write8(0xfffffe11u, 0xabu);
    if (WHICH == 1) tc_hw_read16(0xfffffe11u); /* wider than the byte register */
    if (WHICH == 2) tc_hw_read32(0xfffffe20u);
    if (WHICH == 3) tc_hw_read8(0xfffffe12u); /* past it */
    if (WHICH == 4) tc_hw_read32(0x25fe00a5u);
    tcrt_report(stdout, 0, 0); /* the listed stores' W lines, then any FAULT */
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class Runtime(unittest.TestCase):
    def test_reads_and_writes(self):
        work = ROOT / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        d = pathlib.Path(tempfile.mkdtemp(dir=work))
        try:
            vc = str(game_config.vcvars32()).replace("/", "\\")
            rt = SAT / "runtime"
            (d / "p.c").write_text(MAIN, encoding="utf-8")
            srcs = " ".join(f'"{rt / s}"' for s in ("tc_rt.c", "tc_fpu.c", "tc_p4.c"))
            outs = []
            for which in range(5):
                r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W3 /DWHICH={which} /I"{rt}" '
                                   f'/Fep{which}.exe p.c {srcs}"', cwd=d, capture_output=True, text=True,
                                   errors="replace", timeout=300)
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                outs.append(subprocess.run([str(d / f"p{which}.exe")], capture_output=True, text=True, timeout=60))
            want = ["80 1234 11223344", "34 22 3344 2233 44", "RET 00000000 00000000",
                    "W 25fe00a8 00000008", "W fffffe11 ab", "W fffffe92 1234", "XF" + " 00000000" * 16]
            self.assertEqual(outs[0].stdout.splitlines(), want)
            for which, addr in ((1, "fffffe11"), (2, "fffffe20"), (3, "fffffe12"), (4, "25fe00a5")):
                self.assertEqual(outs[which].stdout.splitlines(), want + [f"FAULT read {addr}"], which)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_runner(self):
        src = text(SAT / "runtime" / "runner.c")
        self.assertIn("tcrt_add_hw(reg, value, size ? size : 4);", src)  # an old Y line: a 4-byte register
        h = text(SAT / "runtime" / "tc_rt.h")
        for decl in ("u32 tc_hw_read32(u32 reg);", "u16 tc_hw_read16(u32 reg);", "u8 tc_hw_read8(u32 reg);",
                     "void tc_hw_write8(u32 reg, u8 value);"):
            self.assertIn(decl, h)


class Agent(unittest.TestCase):
    def test_text(self):
        lines = agent.hw_notes({"reads": {"fffffe11": "80", "fffffe10": "1234", "a05f689c": "0000000b"},
                                "writes": ["fffffe11"], "gbr": False})
        self.assertIn("tc_hw_read8(0xfffffe11u)", lines[0])
        self.assertIn("tc_hw_read16(0xfffffe10u)", lines[1])
        self.assertIn("tc_hw_read32(0xa05f689cu)", lines[2])
        self.assertTrue(lines[2].endswith("(0x0000000b)."))  # a 4-byte register's line as before
        self.assertIn('    elif meta.get("hw_ops"):', text(ROOT / "core" / "agent.py"))


class BodyHoles(unittest.TestCase):
    """Ghidra's blocks can stop short of code the function runs through (a branch into a delay slot): the rest of the
    range they start in fills it. A far range with no block, bytes before the first block and another function's ranges
    do not."""

    def test_body_of(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sat_translate", ROOT / "targets" / "saturn_sh2" / "translate.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "function_bodies.txt"
            f.write_text("06000000 06000000 06000010\n06000000 06000040 06000050\n06000000 05fffff0 06000000\n"
                         "06000020 06000020 06000030\n", encoding="utf-8")
            r = m.body_ranges(str(f))
            meta = {"blocks": [["06000000", "06000003"], ["0600000c", "0600000d"]]}
            body = m.body_of(meta, 0x06000000, r)
            inside = lambda a: any(lo <= a <= hi for lo, hi in body)
            self.assertTrue(inside(0x06000008) and inside(0x0600000f))
            self.assertFalse(inside(0x06000044) or inside(0x05fffff0) or inside(0x06000020))
            meta["callees"] = [{"addr": "06000008"}]
            body = m.body_of(meta, 0x06000000, r)
            self.assertTrue(inside(0x06000007))
            self.assertFalse(inside(0x06000008))
            # with no blocks the translator has no body at all, as before: the file adds nothing
            self.assertEqual(m.body_of({}, 0x06000000, r), [])


if __name__ == "__main__":
    unittest.main()

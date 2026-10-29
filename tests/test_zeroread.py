"""Reads of memory that does not exist return 0 (project.json hw_ops "read area5"; TcHw, TcBase.area5). On a retail
Dreamcast nothing answers in area 5 (external devices, 0x14000000..0x17ffffff in each P0..P3 view) and a read returns 0
(Flycast sh4_mem.cpp ReadMem_extdev_T). Power Stone 2 TeamSetup_ApplyEntries (8c0454a0) indexes the pad table with its
own r15 and reads 0x144f2564 in every vector, so every oracle vector faulted and it stayed a stub. Opt-in per function
with evidence, never global: random input pointers land in area 5 about one time in eight, and serving those would
change the vectors of every function that reads through one. Pass and fail controls:
1. the listing: "read area5" is a known op; script arg 11 carries "z5"; it adds no register read to the classifier;
2. the model (tests/java/TcTflagStub): TcBase.area5 over the views and edges (P0..P3 views in, area 4/6, P4 and RAM
   out, an access running past the area out); TcHw "hw:z5" serves 0 there only, writes header line y and meta "area5",
   a listing without it keeps both as before;
3. the C runner on translator C (compiled, the raw runtime): a mov.w from 0x144f2564 returns 0 with no fault after a
   line y, and faults without it; the P1 view 0x944f2564 reads 0 too; area 6 (0x18000000) still faults; without line
   y the output is byte-identical to the runtime before (14a59a7). The readable runtime reads 0 after a line y;
4. the wiring: both runners parse y, the port's translator-C loads (tx_rt.c) read 0 there, the agent note.
The oracle itself needs Ghidra: the prepare of 8c0454a0 with the listing judges it.
Synthetic data. Run: python -W error tests/test_zeroread.py.
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
from test_tflag import BASE, base_commit, stub_lines  # noqa: E402
from sh4translate import translate as tx  # noqa: E402
from core import agent, check, hw_ops, toolcfg  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
WORK = ROOT / "tests" / ".work"
VC64 = toolcfg.load(ROOT).get("vcvars")
# mov.l @(pool),r1; mov.w @r1,r0; rts; nop; pool: the address
READER = [0xd101, 0x6011, 0x000b, 0x0009]
APPLY = {"8c0454a0": {"ops": ["read area5"], "evidence": "reads @(r6 = r15 * 0x28 + 8c542a54) at 8c0455ae: area 5"}}


def translate(addr):
    data = b"".join(struct.pack("<H", w) for w in READER) + struct.pack("<I", addr)
    sig = tx.Signature("fn_8c000000", "u32 fn_8c000000(void)", [], "u32", None, {})
    src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, sig, tx.TargetFacts())
    return src[0] if isinstance(src, tuple) else src


THUNK = """#include "tc_rt.h"
u32 fn_8c000000(void);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)r;
    (void)fr;
    (void)out_fr0;
    *out_r0 = fn_8c000000();
}
"""


def vectors(head):
    return head + "V 0 dev\nR 0 0 0 0\nE\n"


def ret(out):
    return [int(ln.split()[1], 16) for ln in out.splitlines() if ln.startswith("RET ")]


class Listing(unittest.TestCase):
    def test_listed_and_arg(self):
        self.assertEqual(hw_ops.listed({"hw_ops": APPLY}), {"8c0454a0": ["read area5"]})
        self.assertEqual(hw_ops.arg({"hw_ops": APPLY}, "8c0454a0"), "hw:z5")
        self.assertEqual(hw_ops.arg({"hw_ops": APPLY}, "8c045de0"), "-")
        both = {"hw_ops": {"8c0454a0": {"ops": ["ldc gbr", "read area5"], "evidence": "x"}}}
        self.assertEqual(hw_ops.arg(both, "8c0454a0"), "hw:gbr_z5")
        with self.assertRaises(ValueError):
            hw_ops.listed({"hw_ops": {"8c0454a0": {"ops": ["read area6"], "evidence": "x"}}})
        self.assertTrue(hw_ops.covered(["read area5"], {"hw_refs": 0, "sys_ops": 0}))
        self.assertFalse(hw_ops.covered(["read area5"], {"hw_refs": 1, "sys_ops": 0}))  # not a register read

    def test_note(self):
        hw = {"reads": {}, "gbr": False, "bl": False, "area5": True}
        self.assertIn("returns 0", "\n".join(agent.hw_notes(hw)))
        self.assertNotIn("Area 5", "\n".join(agent.hw_notes(dict(hw, area5=False))))


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="zeroread_", dir=WORK)
        d = cls.d = pathlib.Path(cls.tmp.name)
        master = d / "master_rt"
        master.mkdir()
        base = base_commit()
        for f in (SH4 / "runtime").iterdir():
            r = subprocess.run(["git", "-C", str(ROOT), "show", f"{base}:{f.relative_to(ROOT).as_posix()}"],
                               capture_output=True, timeout=60)
            if r.returncode == 0:
                (master / f.name).write_bytes(r.stdout)
        cls.c = {"a5": translate(0x144f2564), "p1": translate(0x944f2564), "a6": translate(0x18000000)}
        srcs = ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")
        cmds = []
        for name, src, rt in (("a5", "a5", SH4 / "runtime"), ("p1", "p1", SH4 / "runtime"),
                              ("a6", "a6", SH4 / "runtime"), ("master", "a5", master)):
            b = d / name
            b.mkdir()
            (b / "work.c").write_text(cls.c[src])
            (b / "thunk.c").write_text(THUNK)
            cmds.append(f'cd /d "{b}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{rt}" /Fe:runner.exe work.c thunk.c '
                        + " ".join(f'"{rt / s}"' for s in srcs))
        vc = str(VC64).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && ' + " && ".join(cmds) + '"', capture_output=True,
                           text=True, errors="replace", timeout=600)
        if r.returncode:
            raise AssertionError(r.stdout[-3000:])
        (d / "image.bin").write_bytes(bytes(0x200))
        (d / "fsca.bin").write_bytes(bytes(1 << 20))
        (d / "code.txt").write_text("8c010000 8c010010\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_one(self, name, vecs):
        (self.d / "v.txt").write_text(vecs)
        r = subprocess.run([str(self.d / name / "runner.exe"), str(self.d / "image.bin"), str(self.d / "code.txt"),
                            str(self.d / "fsca.bin"), str(self.d / "v.txt"), "all", str(self.d / "out.txt")],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.d / "out.txt").read_text()

    def test_area5_reads_zero(self):
        self.assertIn("rd16(", self.c["a5"])
        out = self.run_one("a5", vectors("y\n"))
        self.assertEqual(ret(out), [0])
        self.assertNotIn("FAULT", out)
        self.assertIn("FAULT read 144f2564", self.run_one("a5", vectors("")))  # without line y: as before
        self.assertNotIn("FAULT", self.run_one("p1", vectors("y\n")))           # the P1 view
        self.assertIn("FAULT read 18000000", self.run_one("a6", vectors("y\n")))  # area 6: not served

    def test_without_line_as_master(self):
        for head in ("", "Y a05f689c 0000000b\n", "J 15\n"):
            self.assertEqual(self.run_one("a5", vectors(head)), self.run_one("master", vectors(head)), head)


RD_READER = """#include "rd_rt.h"
u32 fn_8c000000(void);
u32 fn_8c000000(void)
{
    return *(volatile u16 *)(uintptr_t)0x144f2564u;
}
"""


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Readable(unittest.TestCase):
    def test_readable_reads_zero(self):
        from core import paths, readable_check
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="zeroread_rd_", dir=WORK) as t:
            d = pathlib.Path(t)
            (d / "image.bin").write_bytes(bytes(0x200))
            (d / "fsca.bin").write_bytes(bytes(1 << 20))
            (d / "code.txt").write_text("8c010000 8c010010\n")
            (d / "rd.c").write_text(RD_READER)
            ok, msgs = readable_check.build(paths.config(), [d / "rd.c"], THUNK.replace("tc_rt.h", "rd_rt.h"), {},
                                            d / "rd")
            self.assertTrue(ok, msgs)
            (d / "v.txt").write_text(vectors("y\n"))
            r = subprocess.run([str(d / "rd" / "rd_runner.exe"), str(d / "image.bin"), str(d / "code.txt"),
                                str(d / "fsca.bin"), str(d / "v.txt"), "all", str(d / "out.txt")],
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = (d / "out.txt").read_text()
            self.assertEqual(ret(out), [0], out)
            self.assertNotIn("CRASH", out)


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Model(unittest.TestCase):
    def test_stub(self):
        lines = stub_lines()
        self.assertEqual(lines[5:], STUB, lines)


STUB = [
    "area5 1 1 1 1 0 0 0 0 0 1",
    'hw z5 [0, 0] true true {"reads": {}, "gbr": false, "bl": false, "area5": true}',
    "header y|",
    'gbr true {"reads": {}, "gbr": true, "bl": false}',
    "header Z|",
]


class Wiring(unittest.TestCase):
    def test_runners_and_port(self):
        for runner in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
            self.assertIn("line[0] == 'y'", runner.read_text())
        self.assertIn("tcrt_area5(addr, size)) return 0;", (SH4 / "runtime" / "tc_rt.c").read_text())
        self.assertIn("tcrt_area5(addr, 1)", (SH4 / "runtime_rd" / "rd_veh.c").read_text())
        tx_rt = (ROOT / "port" / "runtime" / "tx" / "tx_rt.c").read_text()
        for fn in ("u8 rd8(u32 addr) { return AREA5(addr) ? 0", "u16 rd16(u32 addr) { return AREA5(addr) ? 0",
                   "    if (AREA5(addr)) return 0;\n    u32 value"):
            self.assertIn(fn, tx_rt)
        self.assertIn("TcBase.area5(off, size)", (SH4 / "ghidra_scripts" / "TcHw.java").read_text())
        self.assertEqual(BASE, "14a59a7")


class RestampKind(unittest.TestCase):
    """selective_restamp --change zeroread: the functions hw_ops lists with "read area5", nothing else; the baseline's
    stamps are taken without those ops."""

    def test_candidates(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("srz", ROOT / "tools" / "selective_restamp.py")
        sr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sr)
        cfg = {"hw_ops": dict(APPLY, **{"8c04f4c0": {"ops": ["ldc gbr"], "evidence": "x"},
                                        "8c058620": {"ops": ["ldc gbr", "read area5"], "evidence": "y"}})}
        self.assertEqual(sr.zeroread_candidates(cfg), {"8c0454a0", "8c058620"})
        self.assertNotIn("8c045de0", sr.zeroread_candidates(cfg))  # the made-up miss
        old = sr.without_area5(cfg)
        self.assertEqual(old["hw_ops"], {"8c04f4c0": {"ops": ["ldc gbr"], "evidence": "x"},
                                         "8c058620": {"ops": ["ldc gbr"], "evidence": "y"}})
        self.assertEqual(hw_ops.arg(old, "8c0454a0"), "-")
        self.assertEqual(sr.zeroread_candidates({}), set())


if __name__ == "__main__":
    unittest.main()

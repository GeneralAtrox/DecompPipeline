"""Reads of the PVR video memory window (project.json hw_ops "read vram"; TcHw, TcBase.vram, tcrt_vram).
A function reading one frame-buffer pixel (Toy Commander 8c0923e0 Lib_ScreenReadPixel: mov.w @r4,r6 with r4 =
0xa5000000 | offset, the P2 view of the 64-bit video memory area) faulted in every oracle vector: nothing is mapped
there in the test run. Listed with its evidence the window reads a fixed function of the address, the same in the
oracle (TcBase.vramWord), the raw runner (tcrt_vram_word) and the readable runner (rd_veh.c). Opt-in per function,
never global (as "read area5", tests/test_zeroread.py). Pass and fail controls:
1. the listing: "read vram" is a known op; script arg 11 carries "zv"; it adds no register read to the classifier;
2. the C runner on translator C (compiled, the raw runtime): a mov.w / mov.l / mov.b from the window reads the
   function's value after a line v and faults without it; addresses just outside the window fault; without line v the
   output is byte-identical to the runtime before (HEAD of the commit this builds on);
3. the readable runtime reads the same value after a line v;
4. the wiring: both runners parse v, TcHw serves the window, selective_restamp --change zeroread lists it.
The oracle itself needs Ghidra: the prepare of 8c0923e0 with the listing judges the Java side against these runners.
Synthetic data. Run: python -W error tests/test_vramread.py.
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
from test_zeroread import THUNK, WORK, VC64, SH4, vectors, ret, RD_READER  # noqa: E402
from sh4translate import translate as tx  # noqa: E402
from core import agent, check, hw_ops  # noqa: E402

APPLY = {"8c0923e0": {"ops": ["read vram"], "evidence": "mov.w @r4,r6 at 8c092440: r4 = 0xa5000000 | pixel offset"}}
ADDR = 0xa5001234
READ = {"w": 0x6011, "l": 0x6012, "b": 0x6010}  # mov.w / mov.l / mov.b @r1,r0 after mov.l @(pool),r1


def word(a):
    return ((((a >> 2) & 0x1fffff) * 0x9e3779b1) + 0x7f4a7c15) & 0xffffffff


def expect(kind, a):
    w = word(a & ~3)
    if kind == "l":
        return w
    if kind == "w":
        v = (w >> (8 * (a & 2))) & 0xffff
        return v | 0xffff0000 if v & 0x8000 else v  # mov.w sign-extends
    v = (w >> (8 * (a & 3))) & 0xff
    return v | 0xffffff00 if v & 0x80 else v


def translate(kind, addr):
    data = b"".join(struct.pack("<H", w) for w in (0xd101, READ[kind], 0x000b, 0x0009)) + struct.pack("<I", addr)
    sig = tx.Signature("fn_8c000000", "u32 fn_8c000000(void)", [], "u32", None, {})
    src = tx.translate_function(tx.Image(data, 0x8c000000), 0x8c000000, sig, tx.TargetFacts())
    return src[0] if isinstance(src, tuple) else src


class Listing(unittest.TestCase):
    def test_listed_and_arg(self):
        self.assertEqual(hw_ops.listed({"hw_ops": APPLY}), {"8c0923e0": ["read vram"]})
        self.assertEqual(hw_ops.arg({"hw_ops": APPLY}, "8c0923e0"), "hw:zv")
        self.assertEqual(hw_ops.arg({"hw_ops": APPLY}, "8c045de0"), "-")
        both = {"hw_ops": {"8c0923e0": {"ops": ["read area5", "read vram"], "evidence": "x"}}}
        self.assertEqual(hw_ops.arg(both, "8c0923e0"), "hw:z5_zv")
        self.assertTrue(hw_ops.covered(["read vram"], {"hw_refs": 0, "sys_ops": 0}))
        self.assertTrue(hw_ops.covered(["read vram"], {"hw_refs": 1, "sys_ops": 0}))  # its window base constant
        self.assertFalse(hw_ops.covered(["read vram"], {"hw_refs": 2, "sys_ops": 0}))  # another register: unlisted
        self.assertFalse(hw_ops.covered(None, {"hw_refs": 1, "sys_ops": 0}))

    def test_note(self):
        hw = {"reads": {}, "gbr": False, "bl": False, "vram": True}
        self.assertIn("Video memory", "\n".join(agent.hw_notes(hw)))
        self.assertNotIn("Video memory", "\n".join(agent.hw_notes(dict(hw, vram=False))))

    def test_content_function(self):
        self.assertEqual(word(0xa5000000), 0x7f4a7c15)
        self.assertEqual(word(0xa5000004), (0x9e3779b1 + 0x7f4a7c15) & 0xffffffff)
        self.assertEqual(word(0xa57ffffc), word(0xa5000000 + 0x7ffffc))
        self.assertEqual(expect("l", ADDR), word(ADDR))


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="vramread_", dir=WORK)
        d = cls.d = pathlib.Path(cls.tmp.name)
        master = d / "master_rt"
        master.mkdir()
        for f in (SH4 / "runtime").iterdir():
            r = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{f.relative_to(ROOT).as_posix()}"],
                               capture_output=True, timeout=60)
            if r.returncode == 0:
                (master / f.name).write_bytes(r.stdout)
        cls.cases = {"w": ADDR, "l": ADDR, "b": ADDR + 1, "lo": 0xa4fffffc, "hi": 0xa5800000, "edge": 0xa57ffffc,
                     "odd": ADDR + 1}
        kinds = {"w": "w", "l": "l", "b": "b", "lo": "l", "hi": "l", "edge": "l", "odd": "w"}
        srcs = ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")
        cmds = []
        for name, rt in [(n, SH4 / "runtime") for n in cls.cases] + [("master", master)]:
            b = d / name
            b.mkdir()
            (b / "work.c").write_text(translate(kinds.get(name, "w"), cls.cases.get(name, ADDR)))
            (b / "thunk.c").write_text(THUNK)
            cmds.append(f'cd /d "{b}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{rt}" /Fe:runner.exe work.c thunk.c '
                        + " ".join(f'"{rt / s}"' for s in srcs))
        vc = str(VC64).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && ' + " && ".join(cmds) + '"', capture_output=True,
                           text=True, errors="replace", timeout=900)
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

    def test_window_reads_the_function(self):
        for name, kind in (("w", "w"), ("l", "l"), ("b", "b"), ("edge", "l")):
            out = self.run_one(name, vectors("v\n"))
            self.assertEqual(ret(out), [expect(kind, self.cases[name])], (name, out))
            self.assertNotIn("FAULT", out)

    def test_without_line_faults(self):
        self.assertIn("FAULT read a5001234", self.run_one("l", vectors("")))

    def test_outside_and_misaligned_fault(self):
        self.assertIn("FAULT read a4fffffc", self.run_one("lo", vectors("v\n")))
        self.assertIn("FAULT read a5800000", self.run_one("hi", vectors("v\n")))
        self.assertIn("misaligned", self.run_one("odd", vectors("v\n")))

    def test_without_line_as_master(self):
        for head in ("", "Y a05f689c 0000000b\n", "J 15\n", "y\n"):
            self.assertEqual(self.run_one("l", vectors(head)), self.run_one("master", vectors(head)), head)


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class Readable(unittest.TestCase):
    def test_readable_reads_the_function(self):
        from core import paths, readable_check
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vramread_rd_", dir=WORK) as t:
            d = pathlib.Path(t)
            (d / "image.bin").write_bytes(bytes(0x200))
            (d / "fsca.bin").write_bytes(bytes(1 << 20))
            (d / "code.txt").write_text("8c010000 8c010010\n")
            (d / "rd.c").write_text(RD_READER.replace("(volatile u16 *)(uintptr_t)0x144f2564u",
                                                      "(volatile u32 *)(uintptr_t)0xa5001234u"))
            ok, msgs = readable_check.build(paths.config(), [d / "rd.c"], THUNK.replace("tc_rt.h", "rd_rt.h"), {},
                                            d / "rd")
            self.assertTrue(ok, msgs)
            (d / "v.txt").write_text(vectors("v\n"))
            r = subprocess.run([str(d / "rd" / "rd_runner.exe"), str(d / "image.bin"), str(d / "code.txt"),
                                str(d / "fsca.bin"), str(d / "v.txt"), "all", str(d / "out.txt")],
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = (d / "out.txt").read_text()
            self.assertEqual(ret(out), [expect("l", ADDR)], out)
            self.assertNotIn("CRASH", out)


class Wiring(unittest.TestCase):
    def test_runners_and_oracle(self):
        for runner in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
            self.assertIn("line[0] == 'v'", runner.read_text())
        self.assertIn("tcrt_vram(a, 4)", (SH4 / "runtime" / "tc_rt.c").read_text())
        self.assertIn("tcrt_vram(addr, 1)", (SH4 / "runtime_rd" / "rd_veh.c").read_text())
        hw = (SH4 / "ghidra_scripts" / "TcHw.java").read_text()
        self.assertIn("TcBase.vram(off, size)", hw)
        self.assertIn('"zv"', hw)
        self.assertIn("vram && v >= 0xa5000000L && v < 0xa5800000L", hw)
        base = (SH4 / "ghidra_scripts" / "TcBase.java").read_text()
        self.assertIn("0x9e3779b1L + 0x7f4a7c15L", base)

    def test_restamp_kind(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import selective_restamp as sr
        cfg = {"hw_ops": {"8c0923e0": {"ops": ["read vram"], "evidence": "x"},
                          "8c0454a0": {"ops": ["read area5"], "evidence": "x"},
                          "8c04f4c0": {"ops": ["ldc gbr"], "evidence": "x"}}}
        self.assertEqual(sr.zeroread_candidates(cfg), {"8c0923e0", "8c0454a0"})
        self.assertEqual(sr.without_area5(cfg)["hw_ops"], {"8c04f4c0": {"ops": ["ldc gbr"], "evidence": "x"}})


if __name__ == "__main__":
    unittest.main()

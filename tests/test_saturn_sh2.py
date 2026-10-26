"""The Sega Saturn SH-2 target (targets/saturn_sh2), without Ghidra:
- satdisc.py on a synthetic disc (cooked 2048-byte and raw 2352-byte sectors): IP.BIN, the first-read file, the file
  list, the code scan, extraction;
- overlay_image.py: a work RAM high image from 0x06000000, its gaps and game ranges;
- the oracle scripts compile (javac against the Ghidra jars), and TcBase's address and byte-order helpers, TcInputs'
  genPut and TcCatalog's hardware rules behave (tests/java/SaturnStub.java); TcFacts accepts the facts core/ghidra
  writes from target.json; the template TcInputs passes core/javalint;
- the C runtime: a work.c built with the runner, as core/check builds one, stores big-endian into both work RAM
  banks, folds the cache-through view, faults outside RAM and reports instruction reads (R8);
- core/compare and core/agent take the judge's memory map and the prompt's CPU text from target.json.

usage: python tests/test_saturn_sh2.py
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
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_tail import CP, jdk  # noqa: E402
from core import agent, check, compare, ghidra, javalint, paths, toolcfg  # noqa: E402

SAT = TOOL / "targets" / "saturn_sh2"
sys.path.insert(0, str(SAT / "tools"))
import overlay_image  # noqa: E402
import satdisc  # noqa: E402

WORK = TOOL / "tests" / ".work"
TARGET = json.loads((SAT / "target.json").read_text(encoding="utf-8"))


def tmpdir(prefix):
    WORK.mkdir(parents=True, exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=WORK))


# ---------- a synthetic Saturn disc ----------

def record(name, lba, size, is_dir=False):
    n = 33 + len(name)
    n += n & 1
    r = bytearray(n)
    r[0] = n
    r[2:6], r[6:10] = lba.to_bytes(4, "little"), lba.to_bytes(4, "big")
    r[10:14], r[14:18] = size.to_bytes(4, "little"), size.to_bytes(4, "big")
    r[25] = 2 if is_dir else 0
    r[32] = len(name)
    r[33:33 + len(name)] = name
    return bytes(r)


def directory(lba, parent, entries):
    d = record(b"\x00", lba, 2048, True) + record(b"\x01", parent, 2048, True) + b"".join(entries)
    return d.ljust(2048, b"\x00")


def code_file():
    """4 KB that scores as SH-2 code: prologues/epilogues/rts and pointers into work RAM high."""
    words = []
    for i in range(1024):
        words.append((0x4F22, 0x0009, 0x4F26, 0x000B)[i % 4] if i % 8 < 4 else 0x0009)
    body = struct.pack(">1024H", *words)
    ptrs = struct.pack(">256I", *(0x0602C000 + 16 * i for i in range(256)))
    return body + ptrs + bytes(1024)


FILES = {"/0": b"\x00\x09" * 64, "/FIELD/FIELD.BIN": code_file(), "/FIELD/DATA.BIN": bytes(range(256)) * 16}


def disc_sectors():
    """[2048-byte sector] of a disc: IP.BIN, the PVD, the root and FIELD directories and three files."""
    s = [bytes(2048)] * 40
    ip = bytearray(2048)
    ip[0x00:0x10] = b"SEGA SEGASATURN "
    ip[0x10:0x20] = b"SEGA TP T-00    "
    ip[0x20:0x2A] = b"T-4507G   "
    ip[0x2A:0x30] = b"V1.002"
    ip[0x30:0x38] = b"19971108"
    ip[0x40:0x4A] = b"J         "
    ip[0x60:0x67] = b"GRANDIA"
    ip[0xE0:0xE4] = (0x1800).to_bytes(4, "big")
    ip[0xF0:0xF4] = (0x0600A000).to_bytes(4, "big")
    ip[0xF4:0xF8] = len(FILES["/0"]).to_bytes(4, "big")
    s[0] = bytes(ip)
    pvd = bytearray(2048)
    pvd[0], pvd[1:6] = 1, b"CD001"
    pvd[156:190] = record(b"\x00", 20, 2048, True)
    s[16] = bytes(pvd)
    s[20] = directory(20, 20, [record(b"0;1", 23, len(FILES["/0"])), record(b"FIELD", 21, 2048, True)])
    s[21] = directory(21, 20, [record(b"DATA.BIN;1", 30, len(FILES["/FIELD/DATA.BIN"])),
                               record(b"FIELD.BIN;1", 24, len(FILES["/FIELD/FIELD.BIN"]))])
    for path, lba in (("/0", 23), ("/FIELD/FIELD.BIN", 24), ("/FIELD/DATA.BIN", 30)):
        data = FILES[path]
        for k in range(0, len(data), 2048):
            s[lba + k // 2048] = data[k:k + 2048].ljust(2048, b"\x00")
    return s


def raw(sector, lba):
    """A Mode 1 raw sector: sync, header (MSF, mode 1), data, EDC/ECC (zero here)."""
    m, rest = divmod(lba + 150, 75 * 60)
    sec, f = divmod(rest, 75)
    bcd = lambda v: (v // 10) << 4 | v % 10  # noqa: E731
    return satdisc.SYNC + bytes([bcd(m), bcd(sec), bcd(f), 1]) + sector + bytes(288)


class SatDisc(unittest.TestCase):
    def setUp(self):
        self.d = tmpdir("satdisc_")
        sectors = disc_sectors()
        (self.d / "cooked.iso").write_bytes(b"".join(sectors))
        (self.d / "raw.bin").write_bytes(b"".join(raw(s, i) for i, s in enumerate(sectors)))
        (self.d / "game.cue").write_text('FILE "raw.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n',
                                         encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_both_sector_formats(self):
        for name, size in (("cooked.iso", 2048), ("raw.bin", 2352), ("game.cue", 2352)):
            disc = satdisc.Disc(self.d / name)
            try:
                self.assertEqual(disc.ss, size, name)
                ip = disc.ip()
                self.assertEqual((ip["product"], ip["version"], ip["first_read_addr"]), ("T-4507G", "V1.002", "0x0600a000"))
                self.assertEqual(ip["first_read_file"], "/0")
                self.assertEqual(sorted(p for p, _, _ in disc.walk()), sorted(FILES))
                for p, lba, n in disc.walk():
                    self.assertEqual(disc.read(lba, n), FILES[p], p)
            finally:
                disc.f.close()

    def test_scan_and_extract(self):
        disc = satdisc.Disc(self.d / "cooked.iso")
        try:
            rows = {r[0]: r for r in satdisc.scan_rows(disc)}
            self.assertTrue(satdisc.is_code(rows["/FIELD/FIELD.BIN"]))
            self.assertFalse(satdisc.is_code(rows["/FIELD/DATA.BIN"]))
        finally:
            disc.f.close()
        out = self.d / "out"
        with mock.patch("sys.stdout"):
            satdisc.main(["extract", str(self.d / "cooked.iso"), str(out), "--code"])
        self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()),
                         ["0", "FIELD/FIELD.BIN"])  # the first-read file and the code
        self.assertEqual((out / "FIELD" / "FIELD.BIN").read_bytes(), FILES["/FIELD/FIELD.BIN"])
        with self.assertRaises(FileExistsError):  # never into an earlier extraction
            satdisc.main(["extract", str(self.d / "cooked.iso"), str(out)])


class OverlayImage(unittest.TestCase):
    def test_build(self):
        with mock.patch("sys.stderr"):
            image, gaps, ranges = overlay_image.build([(0x0602C000, b"\x11" * 8, "FIELD.BIN", False),
                                                       (0x0600A000, b"\x22" * 4, "0", True)])
        self.assertEqual(len(image), 0x2C008)
        self.assertEqual(image[0xA000:0xA004], b"\x22" * 4)
        self.assertEqual(image[0x2C000:], b"\x11" * 8)
        self.assertEqual(gaps, ["06000000-0600a000", "0600a004-0602c000"])
        self.assertEqual(ranges, [["0602c000", "0602c008"]])  # context files are not decompiled
        self.assertEqual(overlay_image.parse("F.BIN@0x2602c000")[1], 0x0602C000)
        with mock.patch("sys.stderr"), self.assertRaises(ValueError):
            overlay_image.build([(0x0602C000, b"\x00" * 8, "A", False), (0x0602C004, b"\x00", "B", False)])


# ---------- the oracle scripts ----------

@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Scripts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tmpdir("satjava_")
        files = [str(p) for p in sorted((SAT / "ghidra_scripts").glob("*.java"))]
        files += [str(SAT / "template" / "TcInputs.java"), str(TOOL / "tests" / "java" / "SaturnStub.java")]
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, *files],
                           capture_output=True, text=True, timeout=600)
        if r.returncode:
            shutil.rmtree(cls.out, ignore_errors=True)
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def stub(self, *args):
        r = subprocess.run([jdk("java"), "-cp", os.pathsep.join([str(self.out), CP]), "SaturnStub", *args],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return dict(ln.split() for ln in r.stdout.splitlines())

    def test_helpers(self):
        v = self.stub()
        self.assertEqual({k: v[k] for k in v if k.startswith("fold_")},
                         {"fold_26001234": "6001234", "fold_60ffffc": "60ffffc", "fold_26100000": "26100000",
                          "fold_20200010": "200010", "fold_2fffff": "2fffff", "fold_25e00000": "25e00000",
                          "fold_5e00000": "5e00000"})  # only the two RAM banks fold; hardware keeps its view
        self.assertEqual((v["canon_26001234"], v["canon_25e00000"], v["canon_40000000"], v["canon_fffffe10"]),
                         ("6001234", "5e00000", "40000000", "fffffe10"))
        self.assertEqual([v[k] for k in ("inRam_high", "inRam_high_end", "inRam_low", "inRam_gap", "inRam_mirror")],
                         ["1", "0", "1", "0", "0"])
        self.assertEqual((v["getBE4"], v["getBE2"], v["putBE"]), ("12345678", "5678", "cafef00d"))
        self.assertEqual((v["genPut"], v["genPut2"]), ("eea1b2c3", "d400"))  # big endian, low bytes of the value
        self.assertEqual([v[k] for k in ("hw_25e00000", "hw_5f80000", "hw_60a0000", "hw_200000", "hw_100001",
                                         "hw_fffffe92", "hw_3f800000")], ["1", "1", "0", "0", "1", "1", "0"])
        self.assertEqual((v["sq"], v["mac"], v["heap"]), ("0", "1", "1"))

    def test_facts(self):
        cfg = dict(TARGET, image_gaps=[])
        f = tmpdir("satfacts_")
        try:
            (f / "facts.txt").write_text(ghidra.facts_body(cfg), encoding="utf-8")
            v = self.stub(str(f / "facts.txt"))  # TcFacts.verify throws on any disagreement with TcBase
        finally:
            shutil.rmtree(f, ignore_errors=True)
        self.assertEqual((v["facts_ram"], v["facts_sp"]), ("6000000", "60fe000"))


class Target(unittest.TestCase):
    def test_inputs_lint(self):
        self.assertEqual(javalint.lint_inputs((SAT / "template" / "TcInputs.java").read_text(encoding="utf-8")), [])

    def test_files(self):
        names = {p.name for p in (SAT / "ghidra_scripts").glob("*.java")}
        self.assertFalse(set(TARGET["locked_scripts"]) - names)
        self.assertIn("TcEmu.java", TARGET["locked_scripts"])  # TcOracle's emulator (no FPU registers)
        self.assertTrue((SAT / TARGET["agent_reminders"]).is_file())
        self.assertEqual(TARGET["ghidra_language"], "SuperH:BE:32:SH-2")

    def test_one_memory_map(self):
        """target.json, the judge's map, TcBase and the C runtime name the same addresses."""
        jm, abi, mem = TARGET["judge_memory"], TARGET["abi"], TARGET["memory"]
        self.assertEqual(jm["entry_sp"], abi["entry_sp"])
        self.assertEqual(jm["stack_lo"], mem["stack"][0])
        self.assertEqual(jm["image_base"], TARGET["load_address"])
        base = (SAT / "ghidra_scripts" / "TcBase.java").read_text(encoding="utf-8")
        rt = (SAT / "runtime" / "tc_rt_internal.h").read_text(encoding="utf-8")
        for name, want in (("SP", abi["entry_sp"]), ("STACK_LO", mem["stack"][0]), ("SENTINEL", abi["return_sentinel"]),
                           ("SCR", mem["scratch"][0]), ("RAM_LO", mem["ram"][0]), ("RAM_HI", mem["ram"][1]),
                           ("LOW_LO", mem["ram_low"][0]), ("LOW_HI", mem["ram_low"][1])):
            self.assertIn(f"{name} = {want}L", base, name)
        self.assertIn(f"#define TCRT_SP {abi['entry_sp']}u", rt)
        self.assertIn(f"#define TCRT_STACK_LO {mem['stack'][0]}u", rt)
        self.assertIn(f"#define TCRT_HIGH_BASE {mem['ram'][0]}u", rt)
        self.assertIn(f"#define TCRT_LOW_BASE {mem['ram_low'][0]}u", rt)

    def test_judge_memory(self):
        """core/compare reads judge_memory of the workspace's target; the Dreamcast's constants without it."""
        d = tmpdir("satjm_")
        try:
            (d / "project.json").write_text(json.dumps({"target": "saturn_sh2"}), encoding="utf-8")
            code = ("import core.compare as c; print(hex(c.IMAGE_BASE), hex(c.SP), hex(c.STACK_LO), c.WORD, "
                    "c.MIRROR_RULE, c.same_word(0x0c001000, 0x8c001000))")
            r = subprocess.run([sys.executable, "-c", code], cwd=TOOL, capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, DECOMP_PROJECT=str(d)))
            self.assertEqual(r.stdout.split(), ["0x6000000", "0x60fe000", "0x60f0000", ">I", "False", "False"],
                             r.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        if json.loads((paths.ROOT / "project.json").read_text(encoding="utf-8"))["target"] == "dreamcast_sh4":
            self.assertEqual((compare.IMAGE_BASE, compare.SP, compare.WORD), (0x8C010000, 0x8CFFE000, "<I"))

    def test_agent_text(self):
        cfg = dict(TARGET)
        with mock.patch.object(paths, "config", lambda: cfg), mock.patch.object(paths, "TARGET", SAT):
            task = agent.for_target(agent.TASK).format(addr="0602c100", box="B", signature="u32 fn_0602c100(void)",
                                                       ret_reg="r0", entry_sp=0x060FE000, previous="")
        self.assertIn("behaves exactly like the original SH-2 code", task)
        self.assertIn("## SH-2 reminders", task)
        self.assertIn("Guest memory is big-endian", task)
        self.assertIn("is 0x060fe000 at entry", task)
        self.assertNotIn("FPSCR.DN", task)
        self.assertIn("## Procedure", task)
        with mock.patch.object(paths, "config", lambda: {}):  # the Dreamcast's text, unchanged
            self.assertEqual(agent.for_target(agent.TASK), agent.TASK)


# ---------- the C runtime ----------

WORK_C = r"""#include "tc_rt.h"
u32 fn_0602c100(u32 a, u32 b)
{
    TcArgs args = {0};
    wr32(a, 0x11223344u);
    wr16(b, 0xabcdu);
    wr8(0x060e0105u, (u8)rd32(0x060e0100u));
    args.r[0] = rd32(0x0602c000u);
    args.r[1] = rd32(0x00300000u);
    tc_call(rd32(0x2602c004u), &args);
    if (a == 0x060e0010u) (void)rd8(0x0602c100u);
    return rd16(0x0602c002u);
}
"""
META = {"name": "fn_0602c100", "signature": "u32 fn_0602c100(u32 a, u32 b)", "ret": "u32",
        "params": [{"storage": "r4", "kind": "u32"}, {"storage": "r5", "kind": "u32"}]}
VECTORS = """V 1 dev
R 060e0010 20200020 0 0
F 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000
X 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000 3f800000
H 3f800000 3f800000 3f800000 3f800000
M 060e0100 deadbeef
E
V 2 dev
R 060ffffe 00200000 0 0
E
"""


def vcvars64():
    vc = toolcfg.load(TOOL).get("vcvars")
    return pathlib.Path(vc) if vc and pathlib.Path(vc).is_file() else None


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Runtime(unittest.TestCase):
    def test_runner(self):
        d = tmpdir("satrt_")
        try:
            (d / "work.c").write_text(WORK_C, encoding="utf-8")
            (d / "thunk.c").write_text(check.gen_thunk(META), encoding="utf-8")
            rt = SAT / "runtime"
            srcs = " ".join(f'"{rt / s}"' for s in check.RUNTIME_SRCS)
            vc = str(vcvars64()).replace("/", "\\")
            flags = " ".join(check.CL_FLAGS)
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl {flags} /TC /I"{rt}" /Ferunner.exe work.c '
                               f'thunk.c {srcs}"', cwd=d, capture_output=True, text=True, errors="replace",
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            image = bytearray(0x2C200)
            image[0x2C000:0x2C008] = bytes.fromhex("12345678" "06030010")
            (d / "image.bin").write_bytes(bytes(image))
            (d / "code.txt").write_text("0602c100 0602c200\n", encoding="utf-8")
            (d / "fsca.bin").write_bytes(bytes(2 * 65536 * 2 * 4))  # unused on the SH-2; the shared runner reads it
            (d / "vectors.txt").write_text(VECTORS, encoding="utf-8")
            out = subprocess.run([str(d / "runner.exe"), "image.bin", "code.txt", "fsca.bin", "vectors.txt", "all",
                                  "out.txt"], cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            got = (d / "out.txt").read_text(encoding="utf-8").split("V ")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        v1 = got[1].splitlines()
        self.assertEqual(v1[1], "RET 00005678 00000000")
        self.assertEqual([ln for ln in v1 if ln.startswith("W ")],  # big endian; low bank through 0x202...
                         ["W 00200020 abcd", "W 060e0010 11223344", "W 060e0105 ef"])
        call = [ln for ln in v1 if ln.startswith("CALL ")][0].split()
        self.assertEqual(call[1:3], ["06030010", "12345678"])  # the target read through the cache-through view
        self.assertIn("FAULT read 00300000", v1)  # past work RAM low
        self.assertIn("CODEREAD 0602c100", v1)
        fpu = [ln for ln in v1 if ln.split()[0] in ("XF", "FH", "QH")]
        self.assertTrue(fpu)  # the SH-2 has no FPU: the shared F/X/H input lines leave its registers 0, as TcEmu does
        self.assertEqual({w for ln in fpu for w in ln.split()[1:]}, {"00000000"})
        v2 = got[2].splitlines()
        self.assertIn("FAULT write 060ffffe", v2)  # a word across the end of work RAM high
        self.assertIn("W 00200000 abcd", v2)
        self.assertNotIn("CODEREAD 0602c100", v2)


if __name__ == "__main__":
    unittest.main()

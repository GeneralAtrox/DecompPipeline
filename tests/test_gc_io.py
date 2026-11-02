"""GameCube: stores into hardware registers (the GX FIFO at 0xcc008000, the video, DMA and audio registers) are logged
in order on both sides and compared, instead of making every vector invalid.

The oracle treated any store outside MEM1 as "write outside RAM", so a function that sends a vertex to the FIFO had no
valid vector at all (36 of the 63 answer-key sample functions with no usable vectors, 2026-10-31: Melee 12, Pikmin 2
10, Pikmin 6, Animal Crossing 4, Twilight Princess 4). Now (target.json memory.mmio):
  - TcBank logs such a store (address, size, value) in order and does not pass it to memory or the write set; a store
    of the same step to the same address and size replaces the entry (TcFixups: the stfs rewrite); at most IO_LIMIT
    entries, more make the vector invalid;
  - expected.txt carries IO lines; the C runtime (tcrt_mmio in tc_rt_internal.h) logs the same stores in the same order
    and reports IO lines; ppc_compare.compare_io compares the sequences (count, address, size, value, order);
  - a read of a hardware register faults on both sides unless the vector scripts the device's answers (vectors.txt H,
    tests/test_gc_r4_hw.py), and a store past the window is still a write outside RAM.
This test checks TcBank on a plain page bank (tests/java/TcBankStub.java), the C runtime built with MSVC, the compare
and parse functions, and that the Java, C and target.json agree on the window and the limit.
Skipped without config.local.json's ghidra_home and a JDK (Java), vcvars (C).

usage: python tests/test_gc_io.py
"""
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]

from test_fmac import vcvars  # noqa: E402
from test_tail import CP, jdk  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
RT = GC / "runtime"
spec = importlib.util.spec_from_file_location("ppc_compare", GC / "ppc_compare.py")
ppc_compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ppc_compare)

C_DRIVER = r"""
#include <stdio.h>
#include "tc_rt.h"
#include "tc_rt_internal.h"

int main(void)
{
    if (!tcrt_init("dummy.dol", "ranges.txt")) return 2; /* an empty image: no code, no data */
    wr32(0xcc008000u, 0x3f800000u);
    wrf32d(0xcc008000u, 2.0);
    wr16(0xcc00001cu, 0xbeefu);
    wr64(0xe0000020u, 0x0102030405060708ull);
    wr8(0xcc000000u, 0x7fu);
    printf("read %08x\n", rd32(0xcc008000u));  /* a hardware register cannot be read back */
    wr32(0xcc010000u, 1u);                      /* one past the window: still a fault */
    tcrt_report(stdout, 0, 0, 0);
    puts("--");
    tcrt_reset();
    tcrt_report(stdout, 0, 0, 0);
    puts("--");
    for (u32 i = 0; i < 4097; i++) wr32(0xcc008000u, i);
    tcrt_report(stdout, 0, 0, 0);
    return 0;
}
"""


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class BankLog(unittest.TestCase):
    def test_bank(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcio_", dir=work) as d:
            src = os.pathsep.join([str(GC / "ghidra_scripts"), str(TOOL / "core" / "ghidra_shared" / "ppc")])
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", d, "-cp", CP, "-sourcepath", src,
                                str(TOOL / "tests" / "java" / "TcBankStub.java")], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", d + os.pathsep + CP, "TcBankStub"], capture_output=True,
                               text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = dict(ln.split(" ", 1) for ln in r.stdout.splitlines())
        self.assertEqual(got["patch"], "11223344")  # an input patch made before the run's stores is memory
        self.assertEqual(got["io"], "cc008000 4 40000000;cc008000 4 40400000;cc00001c 2 beef;"
                                    "e0000020 8 102030405060708")
        self.assertEqual(got["written"], "80002000-80002003;cc010000-cc010003;d0000000-d0000003")
        self.assertEqual(got["readback"], "0")
        self.assertEqual(got["ram"], "deadbeef")
        self.assertEqual(got["overflow"], "true")
        self.assertEqual(got["count"], "4096")


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class RuntimeLog(unittest.TestCase):
    def test_runtime(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcio_", dir=work) as d:
            pathlib.Path(d, "drv.c").write_text(C_DRIVER, encoding="utf-8")
            pathlib.Path(d, "dummy.dol").write_bytes(bytes(0x100))
            pathlib.Path(d, "ranges.txt").write_text("", encoding="utf-8")
            vc = str(vcvars(64)).replace("/", "\\")
            src = " ".join(f'"{RT / n}"' for n in ("tc_rt.c", "tc_ppc.c"))
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /O2 /W4 /WX /I"{RT}" drv.c {src}"',
                               cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            run = subprocess.run([str(pathlib.Path(d, "drv.exe"))], cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            out = run.stdout
        first, second, third = out.split("--\n")
        self.assertEqual([ln for ln in first.splitlines() if ln.startswith(("IO", "FAULT", "read"))], [
            "read 00000000",
            "IO cc008000 4 3f800000", "IO cc008000 4 40000000", "IO cc00001c 2 beef",
            "IO e0000020 8 0102030405060708", "IO cc000000 1 7f",
            "FAULT read cc008000", "FAULT write cc010000"])
        self.assertEqual([ln for ln in second.splitlines() if ln.startswith(("IO", "FAULT"))], [])  # tcrt_reset forgets
        lines = third.splitlines()
        self.assertEqual(sum(ln.startswith("IO") for ln in lines), 4096)
        self.assertIn("FAULT too-many-io", lines)
        self.assertEqual(lines[1], "IO cc008000 4 00000000")


class Compare(unittest.TestCase):
    TEXT = "V 0 dev\nRET 00000000 0000000000000000 00000000\n{io}COV 0\nE\n"
    META = {"ret": "void", "callees": [], "params": []}
    VEC = {"patches": []}

    def run_pair(self, e_io, a_io):
        def parse(io):
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write(self.TEXT.format(io="".join(f"IO {a:08x} {s} {v:0{2 * s}x}\n" for a, s, v in io)))
            try:
                return ppc_compare.parse_results(f.name)[0]
            finally:
                os.unlink(f.name)
        return ppc_compare.compare_vector(self.META, [], self.VEC, parse(e_io), parse(a_io))

    def test_parse(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("V 3 dev\nIO cc008000 4 3f800000\nIO e0000020 8 0102030405060708\nE\nV 4 dev\nE\n")
        try:
            res = ppc_compare.parse_results(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(res[3]["io"], [(0xcc008000, 4, 0x3F800000, False), (0xe0000020, 8, 0x0102030405060708, False)])
        self.assertEqual(res[4]["io"], [])

    def test_equal_and_different(self):
        a = [(0xcc008000, 4, 1), (0xcc008000, 4, 2), (0xcc00001c, 2, 0xbeef)]
        self.assertEqual(self.run_pair(a, a), [])
        self.assertEqual(self.run_pair([], []), [])
        order = self.run_pair(a, [a[1], a[0], a[2]])  # the same stores in another order
        self.assertTrue(order and "access #1" in order[0], order)
        value = self.run_pair(a, [a[0], (0xcc008000, 4, 3), a[2]])
        self.assertTrue(value and "access #2" in value[0] and "0x00000002" in value[0], value)
        size = self.run_pair(a, [a[0], a[1], (0xcc00001c, 4, 0xbeef)])
        self.assertTrue(size and "access #3" in size[0], size)
        addr = self.run_pair(a, [a[0], a[1], (0xcc00001e, 2, 0xbeef)])
        self.assertTrue(addr and "access #3" in addr[0], addr)
        missing = self.run_pair(a, a[:2])
        self.assertTrue(missing and "store #3: missing" in missing[0], missing)
        extra = self.run_pair(a, a + [(0xcc008000, 4, 9)])
        self.assertTrue(extra and "store #4: the original makes only 3" in extra[0], extra)
        self.assertTrue(self.run_pair([], a[:1]))  # the original stores nothing

    def test_agree(self):  # the window and the limit, in Java, C and target.json
        tj = json.loads((GC / "target.json").read_text(encoding="utf-8"))
        win = [(int(lo, 16), int(hi, 16)) for lo, hi in tj["memory"]["mmio"]]
        h = (RT / "tc_rt_internal.h").read_text(encoding="utf-8")
        m = re.search(r"return \(a - (0x[0-9a-f]+)u < (0x[0-9a-f]+)u\) \|\| \(a - (0x[0-9a-f]+)u < (0x[0-9a-f]+)u\);", h)
        self.assertEqual([(int(m.group(1), 16), int(m.group(1), 16) + int(m.group(2), 16)),
                          (int(m.group(3), 16), int(m.group(3), 16) + int(m.group(4), 16))], win)
        c = (RT / "tc_rt.c").read_text(encoding="utf-8")
        j = (GC / "ghidra_scripts" / "TcBank.java").read_text(encoding="utf-8")
        self.assertEqual(int(re.search(r"#define MAX_IO (\d+)", c).group(1)),
                         int(re.search(r"IO_LIMIT = (\d+);", j).group(1)))

    def test_wiring(self):
        orc = (GC / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        self.assertIn("fix.after(emu, ram, bank, bank.stored, bank.reads);", orc)
        self.assertIn("for (long[] e : bank.io) {", orc)
        self.assertIn('eout.append("IO ").append(w)', (GC / "ghidra_scripts" / "TcVectors.java").read_text(encoding="utf-8"))
        self.assertIn("compare_io(exp, act)", (GC / "ppc_compare.py").read_text(encoding="utf-8"))
        # TcTrack reads back the words a float store wrote; a hardware register is not in the bank (its read faults)
        self.assertIn("if (bank.facts.isMmio(st[0])) continue;",
                      (GC / "ghidra_scripts" / "TcTrack.java").read_text(encoding="utf-8"))
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        self.assertIn("if (bank.ioStep >= 0 && bank.io.get(bank.ioStep)[0] == stored[0])", fix)


if __name__ == "__main__":
    unittest.main()

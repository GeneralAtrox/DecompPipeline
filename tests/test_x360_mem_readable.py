"""The Xbox 360 kit answers "is this guest memory readable" from its own page map (runtime/xb_mem.c xb_mem_readable, used by xb_d3d_tex.h
guest_readable), not by asking Windows (VirtualQuery) at every draw.

- Committed ranges read as readable, a range running into a hole or past 4 GB does not.
- Planted: a region is released (xb_mem_uncommit) and then queried in the same frame: the answer must be "not readable". The old way
  (XB_MEM_READABLE=0, VirtualQuery with 16 regions kept for the frame) still says readable there: the check fails before.
- XB_MEM_READABLE=check gives the map's answers and finds no disagreement with a fresh VirtualQuery.

No game data: the functions are cut from the kit's sources and compiled with a 4 GB reserve as the kit makes (as tests/test_x360_kit_pool.py does).
Nearest existing: tests/test_x360_kit_pool.py - checks the kit's pools of command buffers and resources, not which guest memory reads as committed."""
import os
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

RT = X360 / "port" / "runtime"

COMMON = r'''
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
static u8 *tc_base;
static unsigned s_frame = 7;
static void port_log(const char *f, ...) { (void)f; }
'''

MAIN = r'''
int main(void)
{
    tc_base = (u8 *)VirtualAlloc(NULL, 0x100000000ull, MEM_RESERVE, PAGE_NOACCESS);
    if (!tc_base || !commit(0x20000000u, 0x30000u) || !commit(0x20040000u, 0x10000u)) { printf("no reserve\n"); return 2; }   /* a hole at 0x20030000 */
    printf("A %d %d %d %d\n", guest_readable(0x20000000u, 0x30000u), guest_readable(0x2002f000u, 0x2000u), guest_readable(0x20030000u, 4u), guest_readable(0x20040ffcu, 4u));
    printf("B %d\n", guest_readable(0x20010000u, 0x100u));
    xb_mem_uncommit(0x20010000u, 0x10000u);   /* released, then queried in the same frame */
    printf("C %d %d %d\n", guest_readable(0x20010000u, 0x100u), guest_readable(0x20000000u, 0x30000u), guest_readable(0x20020000u, 0x10u));
    printf("D %d\n", guest_readable(0xfffffff0u, 0x20u));
    printf("E %lld %lld\n", (long long)s_readable_calls, (long long)s_readable_mism);
    return 0;
}
'''


def between(path, start, end):
    src = (RT / path).read_text(encoding="utf-8")
    i = src.index(start)
    return src[i:src.index(end, i)]


def source():
    mem = between("xb_mem.c", "/* Which guest pages are committed", "int xb_mem_init(")
    tex = between("xb_d3d_tex.h", "static LONG64 s_region_asks", "/* Element v of stream s")
    return COMMON + mem + tex + MAIN


def test_sources_cut():
    """The cut holds the map, its only writers and guest_readable's switch (any machine)"""
    s = source()
    assert "pages_mark(lo, size, 1);" in s and "pages_mark(lo, hi - lo, 0);" in s and "return xb_mem_readable(a, n);" in s
    mem = (RT / "xb_mem.c").read_text(encoding="utf-8")
    assert mem.count("VirtualAlloc(tc_base") == 1 and mem.count("VirtualFree(tc_base") == 1   # commit() and xb_mem_uncommit only
    for p in RT.glob("*.[ch]"):
        t = p.read_text(encoding="utf-8", errors="replace")
        assert "VirtualProtect" not in t, p.name   # nothing re-protects guest pages behind the map


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Readable(unittest.TestCase):
    def run_c(self, mode):
        d = tmpdir("x360mr_")
        try:
            (d / "t.c").write_text(source(), encoding="utf-8")
            rc, out = cl(d, "/Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            env = {k: v for k, v in os.environ.items() if not k.startswith("XB_")}
            if mode is not None:
                env["XB_MEM_READABLE"] = mode
            return subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=env).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_page_map(self):
        got = self.run_c(None)
        self.assertEqual(got[:4], ["A 1 0 0 1", "B 1", "C 0 0 1", "D 0"])

    def test_old_way_keeps_released_region(self):
        got = self.run_c("0")
        self.assertEqual(got[2], "C 1 1 1")   # the fails-before: the frame's kept region still says readable

    def test_check_agrees(self):
        got = self.run_c("check")
        self.assertEqual(got[:4], ["A 1 0 0 1", "B 1", "C 0 0 1", "D 0"])
        self.assertEqual(got[4], "E 9 0")


if __name__ == "__main__":
    unittest.main()

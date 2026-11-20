"""The Xbox 360 kit keeps a guest buffer's hash while no page of it was written (XB_HASH_KEPT: runtime/xb_mem.c xb_mem_write_gen, the 4 GB
reserve's write watch, and xb_d3d_tex.h data_hash_kept), so a draw does not hash an unchanged vertex buffer or texture again.

- A range hashed and not written since gives the kept answer; one byte written anywhere in it, in its last page, gives a fresh hash.
- The write is seen even when another range sharing that page collected it first (the collection resets Windows' note for the page,
  so a per-range reset without the kit's own page numbers would lose it: the planted case).
- Pages decommitted and committed again count as written (their bytes become zero).
- XB_HASH_KEPT=0 keeps nothing; XB_HASH_KEPT_CHECK=1 finds no kept answer that differs from a fresh hash.

No game data: the functions are cut from the kit's sources and compiled with a 4 GB reserve as the kit makes it.
Nearest existing: tests/test_x360_mem_readable.py - the same cut of xb_mem.c for the page map of committed memory, not write generations."""
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
    const u32 A = 0x20000000u, B = 0x2001f000u, N = 0x20000u;
    unsigned long long h1, h2, h3, hb, h4;
    const char *e = getenv("XB_HASH_KEPT");
    tc_base = (u8 *)VirtualAlloc(NULL, 0x100000000ull, (e && e[0] == '0') ? MEM_RESERVE : MEM_RESERVE | MEM_WRITE_WATCH, PAGE_NOACCESS);
    s_ww = !(e && e[0] == '0');
    if (!tc_base || !commit(A, 0x40000u)) { printf("no reserve\n"); return 2; }
    memset(tc_base + A, 7, 0x40000u);
    h1 = data_hash(A, N);
    h2 = data_hash(A, N);
    printf("A %d %lld\n", h1 == h2, (long long)s_hk_kept);
    tc_base[A + N - 16u] = 9;   /* the last page of A, which B shares */
    hb = data_hash(B, N);
    h3 = data_hash(A, N);
    printf("B %d %d\n", h3 != h1, h3 == (data_hash)(A, N));
    xb_mem_uncommit(A, 0x40000u);
    commit(A, 0x40000u);
    h4 = data_hash(A, N);
    printf("C %d %d\n", h4 != h3, h4 == (data_hash)(A, N));
    printf("E %lld %lld %d\n", (long long)s_hk_kept, (long long)s_hk_bad, hb != 0);
    return 0;
}
'''


def between(path, start, end):
    src = (RT / path).read_text(encoding="utf-8")
    i = src.index(start)
    return src[i:src.index(end, i)]


def source():
    mem = between("xb_mem.c", "/* Which guest pages are committed", "int xb_mem_init(")
    tex = between("xb_d3d_tex.h", "/* A hash of n bytes of guest memory at a", "/* Every Resolve's destination")
    return COMMON + mem + tex + MAIN


def test_sources_cut():
    """The cut holds the write generations, their only resetter and the kept hash's switch (any machine)"""
    s = source()
    assert "WRITE_WATCH_FLAG_RESET" in s and "#define data_hash(a, n) data_hash_kept((a), (n))" in s
    assert s.count("WRITE_WATCH_FLAG_RESET") == 1   # only xb_mem_write_gen resets notes, under its lock
    mem = (RT / "xb_mem.c").read_text(encoding="utf-8")
    assert "MEM_RESERVE | MEM_WRITE_WATCH" in mem
    for p in RT.glob("*.[ch]"):
        if p.name != "xb_mem.c":
            assert "ResetWriteWatch" not in p.read_text(encoding="utf-8", errors="replace"), p.name


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Kept(unittest.TestCase):
    def run_c(self, kept):
        d = tmpdir("x360hk_")
        try:
            (d / "t.c").write_text(source(), encoding="utf-8")
            rc, out = cl(d, "/Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            env = {k: v for k, v in os.environ.items() if not k.startswith("XB_")}
            env["XB_HASH_KEPT_CHECK"] = "1"
            env["XB_HASH_KEPT"] = kept
            return subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=env).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_kept_until_written(self):
        got = self.run_c("4096")
        self.assertEqual(got[:4], ["A 1 1", "B 1 1", "C 1 1", "E 1 0 1"])   # one kept answer (the second ask), none differs

    def test_off_keeps_nothing(self):
        got = self.run_c("0")
        self.assertEqual(got[:4], ["A 1 0", "B 1 1", "C 1 1", "E 0 0 1"])


if __name__ == "__main__":
    unittest.main()

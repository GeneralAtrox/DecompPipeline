"""The Xbox 360 kit keeps a draw that reads a bone palette (an indexed vertex fetch) by the palette rows it read (runtime/xb_d3d_vconv.h vrows_key and
vrows_note, XB_VTX_REUSE), every byte of them, not by every row its index could reach.

- A draw's first conversion records the rows it read and is not kept; the second, with the same bytes, is kept under the vertex key and the rows' hash.
- Planted: a write to a palette row the draw never read (row 100 of 256 reachable) leaves the key as it was: the mesh is not converted again. With
  XB_VTX_REUSE unset or 0 (the old key, every reachable row hashed; the default since 2026-11-14, measured no gain) the same write changes
  the key: the check fails before.
- A write to a row the draw read changes the key (it is converted again); a conversion that reads a row outside the recorded ranges is not kept.

No game data: the key functions are cut from the kit's headers and compiled with a stand-in guest memory (as tests/test_x360_kit_pool.py does).
Nearest existing: tests/test_x360_mem_readable.py - checks which guest memory reads as committed, not what a vertex key covers."""
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
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
#define MEM 0x100000u
static u8 *tc_base;
static unsigned s_frame;
static u32 rd32(u32 at) { u32 v; memcpy(&v, tc_base + at, 4); return v; }
static void wr32(u32 at, u32 v) { memcpy(tc_base + at, &v, 4); }
static u32 rd16(u32 at) { unsigned short v; memcpy(&v, tc_base + at, 2); return v; }
static u32 rd8(u32 at) { return tc_base[at]; }
static int guest_readable(u32 a, u32 n) { return (u64)a + n <= MEM || (a >= 0x20000000u && (u64)a + n <= 0x20000000u + MEM); }   /* two windows, as the kit's 0x20000000 mirror is read */
static int vtx_reuse(void) { const char *e = getenv("XB_VTX_REUSE"); return e && e[0] == '1'; }   /* off unless 1, as the kit's */
typedef struct { u32 guest, n; struct { u16 stream, offset; u32 type; u8 usage, index; } el[32]; } XbDecl;
static XbDecl s_from_fetch;
static u32 s_from_w1[32], s_from_w2[32], s_from_stream[32], s_from_index[32], s_from_icomp[32];
static const u32 *s_vindex;
static u32 s_vfirst, s_vs_container = 0x1234, s_fetch_dev;
static u32 s_ix_lo[32], s_ix_hi[32], s_ix_absent, s_ix_track;
'''

MAIN = r'''
#define PAL 0x10000u
static unsigned long long draw(int rows_lo, int rows_hi, int *keep)   /* key, find (the rows), a conversion that reads rows lo..hi, note */
{
    unsigned long long k = vcache_key_fast(&s_from_fetch, 3, 2, 0x1000u, 16u), key = k;
    if (k && s_vr_key) key = vrows_key(s_vr_key);
    if (s_vr_key) {
        vrows_begin(2);
        s_ix_lo[1] = (u32)rows_lo, s_ix_hi[1] = (u32)rows_hi;
        if (!vrows_note(2)) key = 0;
    }
    *keep = key != 0;
    return key;
}
int main(void)
{
    unsigned long long a, b, c, d;
    int ka, kb, kc, kd;
    u32 i;
    tc_base = (u8 *)VirtualAlloc(NULL, 0x20000000u + MEM, MEM_RESERVE, PAGE_NOACCESS);
    VirtualAlloc(tc_base, MEM, MEM_COMMIT, PAGE_READWRITE), VirtualAlloc(tc_base + 0x20000000u, MEM, MEM_COMMIT, PAGE_READWRITE);
    for (i = 0; i < 0x30; i += 4) wr32(0x1000u + i, 0x03020100u + i);          /* three vertices: fetch 0's bytes are the bone indices */
    for (i = 0; i < 256u * 48u; i += 4) wr32(0x20000000u + PAL + i, i * 2654435761u);           /* the palette: 256 rows of 48 bytes */
    s_fetch_dev = 0x8000u;
    wr32(s_fetch_dev + 8u * (0xefu - 15u), PAL | 3u);                              /* stream 15's fetch constant: the palette */
    s_from_fetch.n = 2;
    s_from_fetch.el[0].type = 6, s_from_fetch.el[1].type = 38;
    s_from_w1[0] = 6u << 16 | 0x688u, s_from_w2[0] = 4u;                          /* fetch 0: stream 0, four bytes (format 6: an 8-bit index) */
    s_from_w1[1] = 38u << 16 | 0x688u, s_from_w2[1] = 12u, s_from_stream[1] = 15u; /* fetch 1: four floats of a 48-byte row of stream 15 */
    s_from_index[1] = 1u, s_from_icomp[1] = 0u;                                    /* its index: fetch 0's x */
    a = draw(2, 5, &ka);
    b = draw(2, 5, &kb);
    printf("first kept %d, second kept %d\n", ka, kb);
    wr32(0x20000000u + PAL + 100u * 48u, 0xdeadbeefu);                                           /* a row the draw never read */
    c = draw(2, 5, &kc);
    printf("unread row written: same key %d kept %d\n", c == b, kc);
    wr32(0x20000000u + PAL + 3u * 48u + 4u, 0xfeedfaceu);                                        /* a row it read */
    d = draw(2, 5, &kd);
    printf("read row written: same key %d\n", d == c);
    d = draw(2, 9, &kd);                                                           /* reads past the recorded rows: not kept, rows widened */
    printf("wider rows kept %d\n", kd);
    d = draw(2, 9, &kd);
    printf("wider rows again kept %d\n", kd);
    (void)a;
    return 0;
}
'''


def between(path, start, end):
    src = (RT / path).read_text(encoding="utf-8")
    i = src.index(start)
    return src[i:src.index(end, i)]


def body(path, sig):
    src = (RT / path).read_text(encoding="utf-8")
    start = src.index(sig)
    return src[start:src.index("\n}\n", start) + 3]


def source():
    cut = body("xb_d3d_tex.h", "static unsigned long long data_hash(u32 a, size_t n)")
    cut += between("xb_d3d_vtx.h", "#define VC_SLOTS", "static void lazy_gather(void)")
    cut += between("xb_d3d_vconv.h", "/* Every byte of n at a, whatever n", "/* XB_VTX_CHECK=1: every draw's rows")
    return COMMON + cut + MAIN


def test_sources_cut():
    """The cut holds the key, the rows and the wiring in draw_vertices (any machine)"""
    s = source()
    assert "static unsigned long long vrows_key(" in s and "static int vrows_note(" in s and "if (rows) s_vr_key = h | 1u;" in s
    d3d = (RT / "xb_d3d.c").read_text(encoding="utf-8")
    assert "key = vrows_key(s_vr_key);" in d3d and "if (s_vr_key && !vrows_note(nat)) key = 0;" in d3d
    assert "typedef struct { u32 guest, n; struct { u16 stream, offset; u32 type; u8 usage, index; } el[32]; } XbDecl;" in d3d


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Rows(unittest.TestCase):
    def run_c(self, env_extra):
        d = tmpdir("x360vr_")
        try:
            (d / "t.c").write_text(source(), encoding="utf-8")
            rc, out = cl(d, "/Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            env = {k: v for k, v in os.environ.items() if not k.startswith("XB_")}
            env.update(env_extra)
            return subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=env).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rows_key(self):
        got = self.run_c({"XB_VTX_REUSE": "1"})
        self.assertEqual(got[0], "first kept 0, second kept 1")
        self.assertEqual(got[1], "unread row written: same key 1 kept 1")
        self.assertEqual(got[2], "read row written: same key 0")
        self.assertEqual(got[3], "wider rows kept 0")
        self.assertEqual(got[4], "wider rows again kept 1")

    def test_old_key_changes_on_unread_row(self):
        got = self.run_c({"XB_VTX_REUSE": "0"})
        self.assertEqual(got[0], "first kept 1, second kept 1")
        self.assertEqual(got[1], "unread row written: same key 0 kept 1")   # the fails-before: the mesh is converted again


if __name__ == "__main__":
    unittest.main()

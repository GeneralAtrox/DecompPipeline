"""The Xbox 360 kit converts a draw's planned vertex inputs one input over every vertex (runtime/xb_vconv.c xb_vconv_run, built /O2, called from
xb_d3d_vconv.h vconv_planned) to the same bytes as vtx_convert_fast's loop, vertex by vertex (XB_VTX_CONV=old, the answer key).

- Every format the plan covers: 32-bit floats 1 to 4 (36 37 38 57), halves (31 32), 16-bit integers (25 26) and bytes (6), each signed or not,
  integer or fraction; random selects 0 to 7 on every lane (4 6 7 give 0, 5 gives 1, a component past the format's count 0 or the fourth 1).
- Where they are read: stream 0 in order, another stream by the draw's indices and by its first vertex, stream 0 at the gather's source.
- Indexed fetches: a palette row by an earlier input's byte, rounded and floored, a constant index, an input not converted yet (0), an index past
  the reach asked about once (stream_element), and indices that read as absent (negative, NaN, 2^24 and over).
- The new path's buffer starts filled with 0xcd bytes and the old one's zeroed: equal output means every byte was written.
Before this change the kit has no xb_vconv.c and no vconv_planned: the cut fails.

No game data: the plan and the loops are cut from the kit's headers and compiled with a stand-in guest memory (as tests/test_x360_vtx_rows.py does).
Nearest existing: tests/test_x360_vtx_rows.py - which draws are kept, not the bytes a conversion writes."""
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

TYPES = r'''
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
extern u8 *tc_base;
'''

COMMON = r'''
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "xb.h"
u8 *tc_base;
#define MEM 0x100000u
static unsigned s_frame, s_draw_n, s_draw_prim;
static u32 rd32(u32 a) { return _byteswap_ulong(*(const u32 *)(tc_base + a)); }
static void wr32(u32 a, u32 v) { *(u32 *)(tc_base + a) = _byteswap_ulong(v); }
static void port_log(const char *f, ...) { (void)f; }
static void xb_stack_line(char *b, unsigned n) { if (n) b[0] = 0; }
static void xb_stop(const char *a, const char *b, u32 c) { printf("STOP %s %s %08x\n", a, b, c); exit(3); }
static int vtx_old(void) { return 0; }
static int guest_readable(u32 a, u32 n) { return (u64)a + n <= MEM || (a >= 0x20000000u && (u64)a + n <= 0x20000000u + MEM); }
typedef struct { u32 guest, n; struct { u16 stream, offset; u32 type; u8 usage, index; } el[32]; } XbDecl;
static XbDecl s_from_fetch;
static u32 s_from_w1[32], s_from_w2[32], s_from_stream[32], s_from_index[32], s_from_icomp[32];
static const u32 *s_vindex;
static u32 s_vfirst, s_vs_container = 0x1234, s_fetch_dev;
static int s_vchk_live;
static __declspec(thread) u32 s_ix_lo[32], s_ix_hi[32], s_ix_absent, s_ix_track;
static __declspec(thread) u32 s_ix_reach[32], s_ix_base[32];
static void vfetch_row_check(unsigned k, u32 idx, u32 at, unsigned i) { (void)k, (void)idx, (void)at, (void)i; }
'''

MAIN = r'''
static u32 rnd_s = 12345u;
static u32 rnd(void) { rnd_s = rnd_s * 1103515245u + 12345u; return rnd_s >> 8; }
static u32 s_ix[64];
static void fetch(unsigned k, u32 fmt, u32 t, u32 stream, u32 off, u32 dw, u32 index, u32 icomp, u32 rd)
{
    u32 m, sel = 0;
    for (m = 0; m < 4; m++) sel |= (rnd() & 7u) << (3 * m);
    s_from_w1[k] = fmt << 16 | (t & 1u) << 12 | (t >> 1) << 13 | rd << 15 | sel;
    s_from_w2[k] = dw, s_from_stream[k] = stream, s_from_index[k] = index, s_from_icomp[k] = icomp;
    s_from_fetch.el[k].offset = (u16)off, s_from_fetch.el[k].type = fmt;
}
static void stream_at(u32 s, u32 base) { wr32(s_fetch_dev + 8u * (0xefu - s), base | 3u); }
static void run(const char *name, unsigned n, unsigned nat, u32 p, u32 stride)
{
    size_t bytes = (size_t)n * nat * 16u, i, k;
    float *v;
    const char *e = getenv("XB_VTX_CONV");
    int old = e && !strcmp(e, "old");
    v = old ? (float *)calloc(bytes, 1) : (float *)malloc(bytes);
    if (!old) memset(v, 0xcd, bytes);
    s_from_fetch.n = nat, s_vu.nu = 0, s_vu.at = 0;
    if (!vtx_convert_fast(n, nat, p, stride, v)) { printf("%s: not planned\n", name); free(v); return; }
    printf("%s rows %u %u:", name, s_vu.nu, (unsigned)vtx_conv_new());
    for (i = 0; i < n; i++)   /* each entry's row: its own, or (XB_VTX_UNIQUE) its distinct row by the remap */
        for (k = 0; k < (size_t)nat * 16u; k++) printf("%02x", ((const u8 *)v)[(s_vu.nu ? (size_t)s_vu.at[i] : i) * nat * 16u + k]);
    printf("\n");
    free(v);
}
int main(void)
{
    u32 i, k;
    float f;
    tc_base = (u8 *)VirtualAlloc(NULL, 0x20000000u + MEM, MEM_RESERVE, PAGE_NOACCESS);
    VirtualAlloc(tc_base, MEM, MEM_COMMIT, PAGE_READWRITE), VirtualAlloc(tc_base + 0x20000000u, MEM, MEM_COMMIT, PAGE_READWRITE);
    for (i = 0; i < MEM; i += 4) wr32(i, rnd() * 2654435761u), wr32(0x20000000u + i, rnd() * 2246822519u);
    for (i = 0; i < 64; i++) wr32(0x20000000u + 0x30000u + 4u * i, 0x7fc00000u + (i & 1u) * 0x80000000u);   /* NaNs both signs in a float stream */
    s_fetch_dev = 0x8000u;
    /* 1: stream 0 in order, the 32-bit float formats */
    fetch(0, 38, 0, 0, 0, 0, 0, 0, 0), fetch(1, 57, 0, 0, 16, 0, 0, 0, 0), fetch(2, 37, 0, 0, 28, 0, 0, 0, 0), fetch(3, 36, 0, 0, 36, 0, 0, 0, 0);
    run("floats", 37, 4, 0x1000u, 40u);
    /* 2: halves, 16-bit integers and bytes from stream 0 and stream 3 by the draw's indices, each sign and kind */
    for (i = 0; i < 64; i++) s_ix[i] = rnd() % 200u;
    s_vindex = s_ix, stream_at(3, 0x10000u);
    fetch(0, 31, 0, 0, 0, 0, 0, 0, 0), fetch(1, 32, 0, 0, 4, 0, 0, 0, 0), fetch(2, 25, 0, 0, 12, 0, 0, 0, 0), fetch(3, 26, 1, 0, 16, 0, 0, 0, 0);
    fetch(4, 25, 2, 3, 0, 6, 0, 0, 0), fetch(5, 26, 3, 3, 4, 6, 0, 0, 0), fetch(6, 6, 0, 3, 12, 6, 0, 0, 0), fetch(7, 6, 1, 3, 16, 6, 0, 0, 0);
    fetch(8, 6, 2, 3, 20, 6, 0, 0, 0), fetch(9, 6, 3, 0, 20, 0, 0, 0, 0), fetch(10, 38, 0, 3, 8, 6, 0, 0, 0);
    run("narrow by index", 64, 11, 0x2000u, 24u);
    /* 3: the same by the first vertex (no indices) */
    s_vindex = 0, s_vfirst = 9;
    run("narrow by first", 50, 11, 0x2000u, 24u);
    /* 4: stream 0 read at the gather's source (an indexed draw's vertices, not gathered) */
    s_vindex = s_ix, s_lazy.n = 64, s_lazy.stride = 24, s_lazy.vb = 0x9000u, s_lazy.to = 0x2000u;
    run("gather source", 64, 11, 0x2000u, 24u);
    s_lazy.n = 0;
    /* 5: indexed fetches: a 256-row palette (stream 15) by fetch 0's bytes (integers), rounded and floored; a constant index; an input not
     *    converted yet; fetch 6's floats as an index (no reach: stream_element), with negative, NaN, 2^24 and a row past committed memory */
    stream_at(15, 0x20000u), stream_at(4, 0x30000u);
    s_vindex = 0, s_vfirst = 0;
    fetch(0, 6, 2, 0, 0, 0, 0, 0, 0), fetch(1, 38, 0, 15, 0, 12, 1, 0, 0), fetch(2, 38, 0, 15, 16, 12, 1, 1, 1), fetch(3, 57, 0, 15, 32, 12, 0x101u, 0, 0);
    fetch(4, 38, 0, 15, 0, 12, 6, 2, 0), fetch(5, 31, 0, 15, 4, 12, 1, 3, 1), fetch(6, 36, 0, 4, 0, 1, 0, 0, 0), s_from_w1[6] &= ~0xfffu, fetch(7, 38, 0, 15, 0, 12, 7, 0, 0);
    fetch(8, 38, 0, 15, 0, 12, 7, 0, 1);
    for (i = 0; i < 64; i++) {   /* fetch 6's index values, one float a vertex at 0x30000 */
        static const float vals[8] = {3.0f, 2.5f, -1.0f, -0.25f, 16777216.0f, 300.7f, 30000.0f, 255.49f};
        f = vals[i & 7u];
        if (i == 13) { wr32(0x20000000u + 0x30000u + 4u * i, 0x7fc00000u); continue; }
        memcpy(&k, &f, 4), wr32(0x20000000u + 0x30000u + 4u * i, k);
    }
    run("indexed", 64, 9, 0x4000u, 4u);
    /* 6 (XB_VTX_UNIQUE): an indexed triangle list whose 96 entries name 20 vertices: stream 0 at the gather's source, stream 3 by the indices, a palette
     *    row by stream 0's byte of the same vertex, a constant index; converted once a vertex and each entry drawn by its row */
    for (i = 0; i < 96; i++) s_ix[i % 64] = 40u + rnd() % 20u;
    s_vindex = s_ix, s_vfirst = 0, s_draw_prim = 4;
    s_lazy.n = 64, s_lazy.stride = 24, s_lazy.vb = 0x9000u, s_lazy.to = 0x2000u, s_lazy.vok = 1, s_lazy.vlo = ~0u, s_lazy.vhi = 0;
    for (i = 0; i < 64; i++) { if (s_ix[i] < s_lazy.vlo) s_lazy.vlo = s_ix[i]; if (s_ix[i] > s_lazy.vhi) s_lazy.vhi = s_ix[i]; }
    fetch(0, 6, 2, 0, 0, 0, 0, 0, 0), fetch(1, 38, 0, 15, 0, 12, 1, 0, 1), fetch(2, 32, 0, 0, 4, 0, 0, 0, 0), fetch(3, 26, 1, 3, 4, 6, 0, 0, 0);
    fetch(4, 57, 0, 15, 32, 12, 0x101u, 0, 0), fetch(5, 31, 0, 15, 4, 12, 1, 3, 1), fetch(6, 38, 0, 3, 8, 6, 0, 0, 0);
    run("unique", 64, 7, 0x2000u, 24u);
    s_lazy.n = 0, s_lazy.vok = 0, s_draw_prim = 0;
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


def xb_h():
    return TYPES + between("xb.h", "typedef struct {\n    u32 kind, nc, t;", "void xb_mem_uncommit(")


def source():
    cut = body("xb_d3d_th.h", "static float half_value(u32 h)")
    cut += between("xb_d3d_vtx.h", "static struct { u32 vb, stride, n, to;", "static unsigned long long vc_span(")
    cut += body("xb_d3d_tex.h", "static u32 stream_element(u32 s, u32 v, u32 offset, u32 w2)")
    cut += body("xb_d3d_vtx.h", "static u32 stream_indexed(unsigned k, const float *row, unsigned i)")
    cut += between("xb_d3d_vconv.h", '#pragma optimize("t", on)', '#pragma optimize("", on)')
    return COMMON + cut + MAIN


def test_sources_cut():
    """The cut holds the plan, both loops and the wiring (any machine)"""
    s = source()
    assert "static void vconv_planned(" in s and "xb_vconv_run(el, nat, n, s_vindex, v," in s and "static int vtx_conv_new(void)" in s
    assert "void xb_vconv_run(const XbVcEl *el" in (RT / "xb_vconv.c").read_text(encoding="utf-8")
    assert '"xb_vconv.c"' in (X360 / "port" / "tools" / "gen_xbox.py").read_text(encoding="utf-8")


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Conv(unittest.TestCase):
    def test_same_bytes_as_the_old_loop(self):
        d = tmpdir("x360vc_")
        try:
            (d / "xb.h").write_text(xb_h(), encoding="utf-8")
            (d / "t.c").write_text(source(), encoding="utf-8")
            shutil.copy2(RT / "xb_vconv.c", d / "xb_vconv.c")
            rc, out = cl(d, "/c /O2 xb_vconv.c", timeout=300)   # as gen_xbox.py O2_FILES: /O2 with /fp:strict
            self.assertEqual(rc, 0, out[-3000:])
            rc, out = cl(d, "/Fet.exe t.c /link xb_vconv.obj", timeout=300)   # an object after /TC is read as C: it goes to the linker
            self.assertEqual(rc, 0, out[-3000:])
            got = {}
            for conv in ("old", ""):
                env = {k: v for k, v in os.environ.items() if not k.startswith("XB_")}
                if conv:
                    env["XB_VTX_CONV"] = conv
                r = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=env)
                self.assertEqual(r.returncode, 0, r.stdout[-2000:])
                got[conv] = [ln for ln in r.stdout.split("\n") if ln]
            old, new = got["old"], got[""]
            self.assertEqual(len(old), 6, old)
            for a, b in zip(old, new):
                name_a, _, bytes_a = a.partition(":")
                name_b, _, bytes_b = b.partition(":")
                self.assertTrue(name_a.endswith(" 0") and name_b.endswith(" 1"), (name_a, name_b))   # the old loop, then the new one
                rows = int(name_b.split()[-2])
                if name_b.startswith("unique"):   # XB_VTX_UNIQUE took it: fewer rows than its 64 entries, each entry's row the old loop's bytes
                    self.assertTrue(0 < rows < 64, name_b)
                else:
                    self.assertEqual(rows, 0, name_b)
                self.assertEqual(len(bytes_a), len(bytes_b))
                diff = [i // 2 for i in range(0, len(bytes_a), 2) if bytes_a[i:i + 2] != bytes_b[i:i + 2]]
                self.assertEqual(diff, [], f"{name_b}: {len(diff)} bytes differ, first at byte {diff[:1]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

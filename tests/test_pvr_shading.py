"""Gouraud or flat (port/pvr/ta_parse.c with ta_batch.c): the real parser and batcher, compiled at /W4 /WX and run on
hand-made TA lists in host memory. The TA takes a polygon's texture, offset, Gouraud and 16-bit UV bits (ISP word bits
25..22) from the parameter control word; Kamui leaves them clear in the header word it writes (kamui1_state.c's
ISP_KEEP), so a polygon with its PCW Gouraud bit set must still blend its vertex colours. Each case is one triangle
strip with a red, a green and a blue vertex:
1. PCW Gouraud set, header ISP word without bit 23 (what Kamui writes): the three colours stay distinct;
2. PCW Gouraud clear: flat, all three take the last vertex's colour;
3. PCW Gouraud clear but ISP bit 23 set: the PCW wins, still flat;
4. the batch's ISP word carries the PCW's texture, offset, Gouraud and UV16 bits (bits 25..22), and keeps the
   header's other bits (here the depth mode and a culling bit).
5. a textured batch's uv_out flags the axes on which a texture coordinate leaves 0..1 (the sampler clamps the others
   when the frame is drawn finer than 640 wide, pvr_states.c): in range, u past 1, v below 0, and u at 1.00005
   (a sprite's derived corner, inside the slop).
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_pvr_shading.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#include <stdio.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "work_count.h"
u32 g_pvr_debug;
int g_work_on;
u64 g_work_now[WC_N];
int pvr_yuv_option(const char *value) { (void)value; return 0; }
int pvr_blend_option(const char *arg, const char *value) { (void)arg; (void)value; return 2; }
void pvr_vram_dump(u32 scene) { (void)scene; }
u32 pvr_parts_vertex(u32 list, u32 addr) { (void)list, (void)addr; return 0; }
u32 pvr_parts_seq(void) { return 0; }
void pvr_parts_from(PvrScene *s) { (void)s; }
int g_parts_blend;
u32 pvr_pack_rim(u32 tcw, u32 tsp) { (void)tcw; (void)tsp; return 0; }
int g_prof_on;
u64 prof_enter(int id) { (void)id; return 0; }
void prof_leave(u64 token) { (void)token; }
void prof_scene(u32 headers, u32 triangles) { (void)headers; (void)triangles; }
void pvr_todo(const char *fmt, ...) { (void)fmt; }
void pvr_seen(const char *fmt, ...) { (void)fmt; }
void port_log(const char *fmt, ...) { (void)fmt; }
void port_exit(const char *why, int code) { printf("exit %s %d\n", why, code), exit(code); }
f32 port_widescreen(void) { return 1.0f; }
f32 port_tallscreen(void) { return 1.0f; }
u32 port_vblank_count(void) { return 0; }
static u32 g_list[8 + 3 * 8];
static u32 bits(f32 f) { union { f32 f; u32 u; } b; b.f = f; return b.u; }
/* a polygon header (list type opaque, packed colours, no texture unless pcw says so) and three vertices */
static void scene(const char *name, u32 pcw_bits, u32 isp)
{
    static PvrScene s;
    PvrPass p;
    static const u32 col[3] = {0xffff0000u, 0xff00ff00u, 0xff0000ffu};
    u32 i, k;
    memset(g_list, 0, sizeof g_list);
    g_list[0] = 4u << 29 | pcw_bits;
    g_list[1] = isp;
    for (i = 0; i < 3u; i++) {
        u32 *v = g_list + 8 + i * 8u;
        v[0] = 7u << 29 | (i == 2u ? 1u << 28 : 0u);
        v[1] = bits(100.0f + 50.0f * (f32)i);
        v[2] = bits(100.0f + 40.0f * (f32)(i & 1u));
        v[3] = bits(0.5f);
        v[6] = col[i];
    }
    memset(&p, 0, sizeof p);
    p.start[0] = (u32)(uintptr_t)g_list;
    p.end[0] = (u32)(uintptr_t)(g_list + 8 + 3 * 8);
    memset(&s, 0, sizeof s);
    ta_parse(&s, &p, 1);
    printf("%s %u %u isp %08x", name, s.nbatch, s.nvtx, s.nbatch ? s.batch[0].isp : 0u);
    for (k = 0; k < s.nvtx; k++) printf(" %08x", s.vtx[k].base);
    printf("\n");
}
/* a textured polygon (packed colour, 32-bit UVs): three vertices with the given u and v each */
static void textured(const char *name, const f32 u[3], const f32 v[3])
{
    static PvrScene s;
    PvrPass p;
    u32 i;
    memset(g_list, 0, sizeof g_list);
    g_list[0] = 4u << 29 | 0x08u;
    for (i = 0; i < 3u; i++) {
        u32 *w = g_list + 8 + i * 8u;
        w[0] = 7u << 29 | (i == 2u ? 1u << 28 : 0u);
        w[1] = bits(100.0f + 50.0f * (f32)i);
        w[2] = bits(100.0f + 40.0f * (f32)(i & 1u));
        w[3] = bits(0.5f);
        w[4] = bits(u[i]);
        w[5] = bits(v[i]);
        w[6] = 0xffffffffu;
    }
    memset(&p, 0, sizeof p);
    p.start[0] = (u32)(uintptr_t)g_list;
    p.end[0] = (u32)(uintptr_t)(g_list + 8 + 3 * 8);
    memset(&s, 0, sizeof s);
    ta_parse(&s, &p, 1);
    printf("uvout %s %u %u\n", name, s.nbatch, s.nbatch ? s.batch[0].uv_out : 99u);
}
int main(void)
{
    static const f32 in[3] = {0.0f, 1.0f, 0.5f}, over[3] = {0.0f, 1.5f, 0.5f}, under[3] = {0.0f, 0.5f, -0.25f};
    static const f32 edge[3] = {0.0f, 1.00005f, 0.5f};
    /* pcw bit 1: Gouraud; bit 3: texture; bit 2: offset; bit 0: UV16 */
    scene("gouraud", 0x02u, 0x80000000u);
    scene("flat", 0x00u, 0x80000000u);
    scene("pcwwins", 0x00u, 0x80800000u);
    scene("bits", 0x0fu, 0x88000000u);
    textured("inside", in, in);
    textured("uover", over, in);
    textured("vunder", in, under);
    textured("slop", edge, edge);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class Shading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.dir = d
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "shade_main.c").write_text("#include <stdlib.h>\n" + DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        srcs = " ".join(f'"{p}"' for p in [KIT / "pvr" / "ta_parse.c", KIT / "pvr" / "ta_batch.c", d / "shade_main.c"])
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" '
              f'/I"{KIT / "base"}" /I"{rd}" {srcs} /Fe:shade.exe')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode or "warning" in r.stdout.lower():
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
        run = subprocess.run([str(d / "shade.exe")], capture_output=True, text=True, timeout=60)
        if run.returncode:
            raise RuntimeError(run.stdout + run.stderr)
        cls.out = {ln.split()[0] if ln.split()[0] != "uvout" else "uvout " + ln.split()[1]: ln.split()
                   for ln in run.stdout.splitlines()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_pcw_gouraud_blends_the_vertex_colours(self):
        # one batch, one triangle; red, green, blue stay as written although the header's ISP word has no bit 23
        self.assertEqual(self.out["gouraud"][1:3], ["1", "3"])
        self.assertEqual(self.out["gouraud"][-3:], ["ffff0000", "ff00ff00", "ff0000ff"])

    def test_pcw_flat_takes_the_last_vertex(self):
        self.assertEqual(self.out["flat"][-3:], ["ff0000ff"] * 3)

    def test_the_pcw_wins_over_the_header_isp_bit(self):
        self.assertEqual(self.out["pcwwins"][-3:], ["ff0000ff"] * 3)

    def test_batch_isp_word_carries_the_pcw_bits(self):
        # texture, offset, Gouraud, UV16 from the PCW (0x03c00000); the header's 0x88000000 (depth mode, culling) otherwise untouched
        self.assertEqual(self.out["bits"][4], "8bc00000")
        self.assertEqual(self.out["gouraud"][4], "80800000")
        self.assertEqual(self.out["flat"][4], "80000000")

    def test_batch_flags_the_axes_whose_coordinates_leave_the_texture(self):
        uv = {k: self.out["uvout " + k][2:] for k in ("inside", "uover", "vunder", "slop")}
        self.assertEqual(uv, {"inside": ["1", "0"], "uover": ["1", "1"], "vunder": ["1", "2"], "slop": ["1", "0"]})


if __name__ == "__main__":
    unittest.main()

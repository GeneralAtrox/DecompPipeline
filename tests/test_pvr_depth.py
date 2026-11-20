"""The depth buffer's scale (port/pvr/pvr_int.h pvr_depth_scale): a scene with a sprite at a huge 1/w keeps every
normal depth above zero, and a scene below the cap scales exactly as 1/max. Spawn's attract rules screen draws sprites
at 1/w = 1.5e37, which scaled the battle behind them to 0 (black world).
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_pvr_depth.py
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
#include <float.h>
#include "ta_batch.c"
u32 g_pvr_debug;
f32 port_widescreen(void) { return 1.0f; }
f32 port_tallscreen(void) { return 1.0f; }
void port_exit(const char *why, int code) { (void)why; exit(code); }
void pvr_todo(const char *fmt, ...) { (void)fmt; }
int main(void)
{
    static PvrScene s;
    static const u16 clip[4] = {0, 0, 640, 480};
    PvrVtx v[3];
    memset(v, 0, sizeof v);
    v[0].x = 10.0f; v[1].x = 20.0f; v[2].y = 20.0f;
    v[0].z = v[1].z = v[2].z = 1.512e37f;
    tb_reset(&s);
    tb_poly(&s, 2u, 0u, 0u, 0u, 0u, clip);
    tb_tri(&s, v);
    printf("sprite %d\n", s.nvtx == 3u && s.vtx[0].z == PVR_DEPTH_TOP && s.max_z == PVR_DEPTH_TOP);
    v[0].z = v[1].z = v[2].z = 0.5f;
    tb_poly(&s, 2u, 0u, 0u, 0u, 1u, clip);
    tb_tri(&s, v);
    printf("normal %d\n", s.nvtx == 6u && s.vtx[3].z == 0.5f);
    volatile f32 z = 0.01f; /* a far polygon */
    f32 huge = pvr_depth_scale(1.512e37f, 0.0f), bg = pvr_depth_scale(0.0f, 1e30f);
    printf("huge %d\n", z * huge >= FLT_MIN);
    printf("bg %d\n", z * bg >= FLT_MIN);
    printf("plain %d\n", pvr_depth_scale(4.0f, 2.0f) == 0.25f);
    printf("bgwins %d\n", pvr_depth_scale(2.0f, 8.0f) == 0.125f);
    printf("cap %d\n", pvr_depth_scale(9e17f, 0.0f) == 1.0f / 9e17f);
    printf("empty %d\n", pvr_depth_scale(0.0f, 0.0f) == 1.0f);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class DepthScale(unittest.TestCase):
    def test_scale(self):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "depth_main.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" '
              f'/I"{KIT / "base"}" /I"{rd}" "{d / "depth_main.c"}" /Fe:depth.exe')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = subprocess.run([str(d / "depth.exe")], capture_output=True, text=True, timeout=30).stdout.split("\n")
        for line in filter(None, out):
            name, ok = line.split()
            self.assertEqual(ok, "1", name)


if __name__ == "__main__":
    unittest.main()

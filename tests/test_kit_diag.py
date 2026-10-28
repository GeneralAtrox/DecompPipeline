"""The kit's bring-up diagnostics (port/runtime/diag.c, port/tools/bu_sweep.py) in behaviour: diag.c compiled at
/W4 /WX /O1 with a driver that stands in for the runtime's log and vertical blank, then run.
1. --diag-skip: a listed function returns 0 (a float one 0.0) and logs its first call; the rest run as before.
2. A skip from vertical blank N leaves the function alone until the blank task at N patches it in.
3. --diag-cover: each listed function logs COVER once, at its first call, and still returns its own result.
And bu_sweep's reading of the linker map's object names (translator, readable, stub). Only where Visual Studio's
32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_kit_diag.py
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

import bu_sweep  # noqa: E402
import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <windows.h>
#include "port.h"
#include "platform.h"

static u32 g_vblank;
static PortVblankFn g_task;
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}
u32 port_vblank_count(void) { return g_vblank; }
void port_add_vblank_task(PortVblankFn fn, void *arg) { (void)arg; g_task = fn; }
void port_exit(const char *why, int code) { printf("EXIT %s\n", why); exit(code); }

__declspec(noinline) int __cdecl victim(int x) { return x * 3 + 7; }
__declspec(noinline) float __cdecl fvictim(float x) { return x * 2.5f + 1.0f; }
__declspec(noinline) int __cdecl later(int x) { return x * 5 + 11; }
__declspec(noinline) int __cdecl covered(int x) { return x * 7 + 13; }
static int (__cdecl *volatile p_victim)(int) = victim;
static float (__cdecl *volatile p_fvictim)(float) = fvictim;
static int (__cdecl *volatile p_later)(int) = later;
static int (__cdecl *volatile p_covered)(int) = covered;

#define rva(f) ((u32)((uintptr_t)(f) - (uintptr_t)GetModuleHandleW(NULL)))

int main(void)
{
    FILE *f = NULL;
    fopen_s(&f, "skip.txt", "w");
    fprintf(f, "%08x 8c000100 i victim\n%08x 8c000200 f fvictim 0\n%08x 8c000300 i later 5\n", rva(victim),
            rva(fvictim), rva(later));
    fclose(f);
    fopen_s(&f, "cover.txt", "w");
    fprintf(f, "%08x 8c000400 covered\n", rva(covered));
    fclose(f);
    diag_option("--diag-skip", "skip.txt");
    diag_option("--diag-cover", "cover.txt");
    diag_install();
    printf("victim %d\n", p_victim(5));
    printf("fvictim %.2f\n", p_fvictim(2.0f));
    printf("later_before %d\n", p_later(3));
    for (; g_vblank < 5; g_task(NULL)) g_vblank++;
    printf("later_after %d\n", p_later(3));
    printf("covered %d %d\n", p_covered(1), p_covered(2));
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class DiagSkipCover(unittest.TestCase):
    def test_skip_and_cover(self):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "diag_main.c").write_text(DRIVER_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "runtime" / "diag.c", d / "diag_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "pvr"}" /I"{KIT / "sdk"}" /I"{rd}" {srcs} /Fe:diag.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "diag.exe")], cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            out = dict((ln.split(" ", 1) + [""])[:2] for ln in run.stdout.splitlines())
            self.assertEqual(out["victim"], "0")            # skipped from the start: a stub's 0
            self.assertEqual(out["fvictim"], "0.00")        # a float stub: 0.0 on the x87 stack
            self.assertEqual(out["later_before"], "26")     # its skip starts at vertical blank 5: runs as before
            self.assertEqual(out["later_after"], "0")
            self.assertEqual(out["covered"], "20 27")       # coverage keeps the function's own result
            self.assertEqual(run.stdout.count("COVER 8c000400 covered at vblank 5"), 1, run.stdout)
            self.assertEqual(run.stdout.count("DIAG SKIP 8c000100 victim call 1 "), 1, run.stdout)
            self.assertIn("diag: skip 8c000300 later", run.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class SweepMap(unittest.TestCase):
    def test_kind_of(self):
        self.assertEqual(bu_sweep.kind_of("txc:tx_8c05fec2.c.obj"), "translator")
        self.assertEqual(bu_sweep.kind_of("overlay_fe_tx:tx_8c4a68aa.c.obj"), "translator")
        self.assertEqual(bu_sweep.kind_of("stubs_00.c.obj"), "stub")
        self.assertEqual(bu_sweep.kind_of("readable:8c010080.c.obj"), "readable")

    def test_table_names_skips(self):
        t = bu_sweep.table([{"step": 0, "skips_before": 2, "class": "fault", "function": "8c000100 F", "vblank": 9,
                             "chain": ["8c000100 F (readable)"], "last_stub": None, "detail": "FAULT"}])
        self.assertIn("| 0 | 2 | fault | 8c000100 F | 9 |", t)


if __name__ == "__main__":
    unittest.main()

"""The kit's guest RAM (port/runtime/guest_mem.c) in behaviour: area 3 repeats the console's 16 MB four times, so a
pointer a game stores in the 0x0e copy (Spawn's hook at 0x8e070d3e, the same code as 0x8c070d3e) must read, write and
fold like the 0x8c one. Compiled at /W4 /WX with a recording port_log stand-in, then run:
1. a word written at 0x8c000100 reads back through 0x8d/0x8e/0x8f (P1), 0x0d/0x0e/0x0f (P0) and 0xad/0xae/0xaf (P2).
2. guest_fold maps 0x8e070d3e to 0x8c070d3e and guest_is_ram says yes (dispatch.c then sends a call to its function).
3. the original views (0x0c.., 0xac.. and the store-queue window) still work.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_kit_guest_mirrors.py
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

DRIVER_C = r"""#include <stdarg.h>
#include <stdio.h>
#include "port.h"
void port_log(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); }

int main(void)
{
    static const u32 bases[] = {0x8d000000u, 0x8e000000u, 0x8f000000u, 0x0c000000u, 0x0d000000u, 0x0e000000u,
                                0x0f000000u, 0xac000000u, 0xad000000u, 0xae000000u, 0xaf000000u, 0xe0000000u};
    if (!guest_mem_init()) { printf("init failed\n"); return 1; }
    *(volatile u32 *)(uintptr_t)0x8c000100u = 0x12345678u;
    for (unsigned i = 0; i < sizeof bases / sizeof bases[0]; i++)
        printf("view %08x %s\n", bases[i], *(volatile u32 *)(uintptr_t)(bases[i] + 0x100u) == 0x12345678u ? "same" : "DIFF");
    *(volatile u32 *)(uintptr_t)0x8e070d3eu = 0xabcd0001u;
    printf("write %s\n", *(volatile u32 *)(uintptr_t)0x8c070d3eu == 0xabcd0001u ? "same" : "DIFF");
    printf("fold %08x %d %d\n", guest_fold(0x8e070d3eu), guest_is_ram(0x8e070d3eu), guest_is_ram(0x90000000u));
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class GuestMirrors(unittest.TestCase):
    def test_area3_copies(self):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "gm_main.c").write_text(DRIVER_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "runtime" / "guest_mem.c", d / "gm_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "base"}" /I"{rd}" {srcs} '
                  f'/Fe:gm.exe /link /LARGEADDRESSAWARE')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "gm.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            views = [ln for ln in run.stdout.splitlines() if ln.startswith("view ")]
            self.assertEqual(len(views), 12, run.stdout)
            for ln in views:
                self.assertTrue(ln.endswith(" same"), ln)
            self.assertIn("write same", run.stdout)
            self.assertIn("fold 8c070d3e 1 0", run.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

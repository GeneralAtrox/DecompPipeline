"""--fault-at's test fault (port/base/fault_plant.c): the real file, compiled at /W4 /WX and run under a vectored
handler that swallows every access violation, as the console kits' memory models do (the Dreamcast kit's boot ROM
model took the old plant, a write to address 0x40, for a ROM write, so no fault was ever raised):
1. under that handler a store to memory that is not there goes on (the old plant's failure, shown on purpose);
2. port_plant_fault still faults: the handler sees an integer divide by zero, which the crash path takes and no
   memory model does, and the process ends with the driver's crash code (3), not by returning.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_fault_plant.py
Nearest existing: tests/test_pvr_blend.py - the same compile-and-run pattern for another module; nothing ran the
plant before, which is how it went unnoticed.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "port" / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#include <stdio.h>
#include <stdlib.h>
#include <windows.h>
int port_plant_fault(void);

static LONG CALLBACK handler(EXCEPTION_POINTERS *ep)
{
    const EXCEPTION_RECORD *er = ep->ExceptionRecord;
    if (er->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && er->NumberParameters >= 2) {
        /* a memory model: the address is made to exist and the access goes on */
        void *page = (void *)(er->ExceptionInformation[1] & ~(ULONG_PTR)0xfff);
        if (VirtualAlloc(page, 0x1000, MEM_COMMIT, PAGE_READWRITE)) return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (er->ExceptionCode == EXCEPTION_INT_DIVIDE_BY_ZERO) {
        printf("fault divide\n");
        fflush(stdout);
        ExitProcess(3);
    }
    printf("fault other %08lx\n", (unsigned long)er->ExceptionCode);
    fflush(stdout);
    ExitProcess(4);
}

int main(void)
{
    volatile unsigned *missing = VirtualAlloc(NULL, 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    AddVectoredExceptionHandler(1, handler);
    *missing = 1u;
    printf("store went on\n");
    fflush(stdout);
    printf("returned %d\n", port_plant_fault());
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class FaultPlant(unittest.TestCase):
    def test_the_plant_faults_where_a_store_does_not(self):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            (d / "plant_main.c").write_text(DRIVER_C, encoding="utf-8")
            srcs = f'"{TOOL / "port" / "base" / "fault_plant.c"}" "{d / "plant_main.c"}"'
            cl = f"cl /nologo /TC /std:c11 /W4 /WX /O2 {srcs} /Fe:plant.exe"
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertFalse(r.returncode or "warning" in r.stdout.lower(), r.stdout[-3000:] + r.stderr[-1000:])
            run = subprocess.run([str(d / "plant.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.stdout.split(), ["store", "went", "on", "fault", "divide"])
            self.assertEqual(run.returncode, 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

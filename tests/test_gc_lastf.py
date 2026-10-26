"""GameCube: both results of one call. The mocked callee sets r3 and f1 (the oracle's scripted call results), but
tc_call returned only r3 and tc_callf only f1, so raw C for an original that reads both after one bl had to make a
second call, which the judge rejects (Skies of Arcadia 800c24cc, found by the GameCube translator session).
tc_lastr()/tc_lastf() give the last call's other result without a new call. The test builds the real runtime
(tc_rt.c, tc_ppc.c) with a small driver and checks:
  - before any call both are 0; after call n they are the scripted r3 and f1 of call n, after tc_call or tc_callf;
  - they never count as a call (the recorded call count stays as the calls made);
  - past the 16 scripted results they are 0, as tc_call and tc_callf are.
Skipped without config.local.json's vcvars (MSVC).
usage: python tests/test_gc_lastf.py
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_fmac import vcvars  # noqa: E402

RT = TOOL / "targets" / "gamecube_ppc" / "runtime"
DRIVER = r"""
#include <stdio.h>
#include <string.h>
#include "tc_rt.h"
#include "tc_rt_internal.h"

static void show(const char *what)
{
    f64 d = tc_lastf();
    u64 b;
    memcpy(&b, &d, 8);
    printf("%s %08x %016llx\n", what, tc_lastr(), (unsigned long long)b);
}

int main(void)
{
    u32 rets[16];
    u64 frets[16];
    for (int i = 0; i < 16; i++) {
        rets[i] = 0x100u + (u32)i;
        frets[i] = 0x4000000000000000ull + (u64)i;
    }
    tcrt_set_call_returns(rets, frets);  /* no tcrt_init: no RAM image is needed, and the call state starts at 0 */
    show("none");
    TcArgs a;
    memset(&a, 0, sizeof a);
    printf("call %08x\n", tc_call(0x80001000u, &a));
    show("after_call");
    show("again");
    f64 f = tc_callf(0x80002000u, &a);
    u64 fb;
    memcpy(&fb, &f, 8);
    printf("callf %016llx\n", (unsigned long long)fb);
    show("after_callf");
    for (int i = 2; i < 17; i++) tc_call(0x80003000u, &a);
    show("past16");
    return 0;
}
"""
WANT = ["none 00000000 0000000000000000",
        "call 00000100", "after_call 00000100 4000000000000000", "again 00000100 4000000000000000",
        "callf 4000000000000001", "after_callf 00000101 4000000000000001",
        "past16 00000000 0000000000000000"]


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class LastResults(unittest.TestCase):
    def test_both_results_of_one_call(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gclastf_", dir=work) as d:
            pathlib.Path(d, "drv.c").write_text(DRIVER, encoding="utf-8")
            vc = str(vcvars(64)).replace("/", "\\")
            src = " ".join(f'"{RT / n}"' for n in ("tc_rt.c", "tc_ppc.c"))
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /O2 /I"{RT}" drv.c {src}"',
                               cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            out = subprocess.run([str(pathlib.Path(d, "drv.exe"))], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.split("\n")[:-1], WANT)

    def test_wiring(self):
        h = (RT / "tc_rt.h").read_text(encoding="utf-8")
        self.assertIn("u32 tc_lastr(void);", h)
        self.assertIn("f64 tc_lastf(void);", h)
        self.assertIn("tc_lastr() (r3) or tc_lastf() (f1)",
                      (TOOL / "targets" / "gamecube_ppc" / "ppc_agent.py").read_text(encoding="utf-8"))
        self.assertIn("tc_last[rf]", (TOOL / "core" / "draft_run.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

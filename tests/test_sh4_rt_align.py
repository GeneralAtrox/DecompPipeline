"""The Dreamcast judge's runtime faults a misaligned access, as the SH-4 does (an address error): a 2-byte rd16/wr16
at an odd address, a 4-byte rd32/wr32 (and rdf32/wrf32, through them) off a multiple of 4. The oracle drops every
vector whose original run makes one (TcFold), so a C that makes one is wrong whatever bytes it reads: before, such a
read returned the bytes there and could match by luck (TC 8c01a50c's mutant `rd32(r12 + 0x89u)` survived).
1. wiring: each sized accessor checks alignment first (before a P4 store and the RAM check), rd8/wr8 do not;
2. aligned() itself, compiled with a stub fault(): every size and low-bit combination, and the fault text.
Skipped (2) without config.local.json's vcvars (MSVC).
usage: python tests/test_sh4_rt_align.py
"""
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_fmac import vcvars  # noqa: E402

RT = TOOL / "targets" / "dreamcast_sh4" / "runtime" / "tc_rt.c"


def text():
    return RT.read_text(encoding="utf-8").replace("\r\n", "\n")


def body(src, head):
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


MAIN = r"""
#include <stdio.h>
typedef unsigned int u32;
static void fault(const char *kind, u32 addr) { printf("FAULT %%s %%08x\n", kind, addr); }
%s
int main(void)
{
    static const u32 addr[] = {0x8cc00500u, 0x8cc00501u, 0x8cc00502u, 0x8cc00503u};
    for (u32 size = 2; size <= 4; size += 2)
        for (int w = 0; w < 2; w++)
            for (int i = 0; i < 4; i++) printf("%%u %%d %%08x %%d\n", size, w, addr[i], aligned(addr[i], size, w));
    return 0;
}
"""


class Wiring(unittest.TestCase):
    def test_accessors(self):
        src = text()
        self.assertIn("if (!aligned(a, 2, 0) || !check(a, 2, 0)) return 0;", body(src, "u16 rd16(u32 a)"))
        self.assertIn("if (!aligned(a, 4, 0) || !check(a, 4, 0)) return 0;", body(src, "u32 rd32(u32 a)"))
        self.assertIn("if (!aligned(a, 2, 1) || tcrt_p4_store(a, v, 2) || !check(a, 2, 1)) return;",
                      body(src, "void wr16(u32 a, u16 v)"))
        self.assertIn("if (!aligned(a, 4, 1) || tcrt_p4_store(a, v, 4) || !check(a, 4, 1)) return;",
                      body(src, "void wr32(u32 a, u32 v)"))
        for head in ("u8 rd8(u32 a)", "void wr8(u32 a, u8 v)"):
            self.assertNotIn("aligned(", body(src, head))
        self.assertIn("f32 rdf32(u32 a) { return u2f(rd32(a)); }", src)
        self.assertIn("void wrf32(u32 a, f32 v) { wr32(a, f2u(v)); }", src)


@unittest.skipUnless(vcvars(64), "no Visual Studio tools (config.local.json vcvars)")
class Aligned(unittest.TestCase):
    def test_cases(self):
        fn = body(text(), "static int aligned(u32 addr, u32 size, int write)")
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "al.c").write_text(MAIN % fn, encoding="utf-8")
            vc = str(vcvars(64)).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /W4 /WX al.c"', cwd=d,
                               capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = subprocess.run([str(d / "al.exe")], capture_output=True, text=True, timeout=60).stdout.split("\n")
        want = []
        for size in (2, 4):
            for w in (0, 1):
                for low in range(4):
                    a = 0x8cc00500 + low
                    ok = not (a & (size - 1))
                    if not ok:
                        want.append(f"FAULT {'write' if w else 'read'}-misaligned {a:08x}")
                    want.append(f"{size} {w} {a:08x} {int(ok)}")
        self.assertEqual([ln.strip() for ln in out if ln.strip()], want)


if __name__ == "__main__":
    unittest.main()

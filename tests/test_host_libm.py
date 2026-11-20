"""Host C++ beside the game's float maths (port/base/host_libm.h): the 32-bit CRT defines sinf, cosf, ... inline in
<cmath>, so a host C++ file that calls one without inlining it (the level editor, Dear ImGui) emits a COMDAT that
clashes with runtime/port_libm.c's definitions at link (LNK2005). host_libm.h, forced into every host C++ file by
CMakeLists.txt, gives the CRT's copies host names.

1. A C++ file using all eleven at /Od (nothing inlined) links beside port_libm.c with the header forced, and the game's
   C still gets port_libm.c's sinf; without the header the same link fails with LNK2005, the clash this guards.
2. CMakeLists.txt forces the header on the port's C++ sources.

Needs Visual Studio's 32-bit tools (skipped without). Run: python -W error tests/test_host_libm.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
KIT = ROOT / "port"
RD = ROOT / "targets" / "dreamcast_sh4" / "runtime_rd"  # rd_rt.h, which platform.h includes
sys.path.insert(0, str(KIT / "tools"))
import game_config  # noqa: E402

HOST_CPP = r"""#include <cmath>
#include <cstdio>
extern "C" float game_sinf(float x);
int main()
{
    volatile float x = 0.5f;
    float s = std::sin(x) + std::cos(x) + std::tan(x) + std::acos(x) + std::atan2(x, 1.0f) + std::log10(x + 1.0f) +
              std::pow(x, 2.0f) + std::floor(x) + std::ceil(x) + std::sqrt(x) + std::ldexp(x, 2);
    std::printf("%d %d\n", s > 0.0f, game_sinf(x) == (float)std::sin((double)x));
    return 0;
}
"""
GAME_C = "float sinf(float x);\nfloat game_sinf(float x) { return sinf(x); } /* the game's C: port_libm.c's */\n" \
         "void port_extra_state(void *p, unsigned size) { (void)p; (void)size; }\n"


class HostLibm(unittest.TestCase):
    def link(self, forced):
        vc = game_config.vcvars32()
        if not vc:
            self.skipTest("no 32-bit Visual Studio environment")
        d = pathlib.Path(tempfile.mkdtemp(prefix="hostlibm_"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "host.cpp").write_text(HOST_CPP, encoding="ascii")
        (d / "game.c").write_text(GAME_C, encoding="ascii")
        (d / "port_config.h").write_text("/* no game.json keys: port_libm.c defines every one */\n", encoding="ascii")
        fi = f'/FI"{KIT / "base" / "host_libm.h"}"' if forced else ""
        cmd = (f'call "{vc}" >nul && cd /d "{d}" && cl /nologo /c /Od /W4 /WX /EHsc /std:c++20 {fi} host.cpp && '
               f'cl /nologo /c /Od /W4 /WX /TC /fp:strict /arch:SSE2 /I"{KIT / "runtime"}" /I"{KIT / "base"}" /I"{RD}" /I. game.c '
               f'"{KIT / "runtime" / "port_libm.c"}" && link /nologo /out:t.exe host.obj game.obj port_libm.obj && "{d / "t.exe"}"')
        bat = d / "t.bat"
        bat.write_text("@echo off\r\n" + cmd + "\r\n", encoding="ascii")
        return subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)

    def test_links_with_the_header(self):
        r = self.link(True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.split()[-2:], ["1", "1"], r.stdout)

    def test_clashes_without_it(self):
        r = self.link(False)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("LNK2005", r.stdout)

    def test_cmake_forces_it(self):
        cm = (KIT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("$<$<COMPILE_LANGUAGE:CXX>:${PORT_CXX_FLAGS};/FI${CMAKE_CURRENT_SOURCE_DIR}/base/host_libm.h>", cm)


if __name__ == "__main__":
    unittest.main()

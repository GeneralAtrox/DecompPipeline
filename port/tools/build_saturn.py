"""Builds a Saturn port: the kit (port/saturn/*.c) and the generator's output for one game into one 32-bit exe.

Nearest existing: build.py - it drives CMake + vcpkg for the Dreamcast kit (SDL3, D3D11, the PVR layers); the Saturn
kit so far is plain C against the Windows API, so this calls cl directly with the same flags (/O1 /fp:strict /W4 /WX,
32-bit). It joins build.py when the Saturn video and audio need SDL3.

  build_saturn.py --gen OUTDIR_OF_gen_saturn --exe PATH [--check] [--asan]   (--check: compile the kit files only, no link)

--asan, or DP_ASAN=1 in the environment (a game's own builder calls this unchanged): AddressSanitizer, as build.py --asan
(MSVC /fsanitize=address), with its runtime dll copied beside the exe.
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parent / "saturn"
PIPELINE = HERE.parent.parent
TC_RT = PIPELINE / "targets" / "saturn_sh2" / "runtime"
sys.path.insert(0, str(HERE))
import game_config  # noqa: E402

FLAGS = ["/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/MT", "/GS-", "/D_CRT_SECURE_NO_WARNINGS"]
KITLIBS = ["user32.lib", "gdi32.lib", "winmm.lib", "kernel32.lib", "shell32.lib", "advapi32.lib", "ole32.lib", "d3d11.lib", "dxgi.lib", "dxguid.lib", "dbghelp.lib",
           "comctl32.lib", "bcrypt.lib"]
BASE = HERE.parent / "base"
CXX_FLAGS = ["/std:c++20", "/EHsc", "/permissive-", "/utf-8", "/DNOMINMAX", "/DWIN32_LEAN_AND_MEAN"]


def sdl3(exe, triplet="x86-windows"):
    """The SDL3 for the base (headers, import library, dll), installed by vcpkg beside the exe; None if vcpkg is missing."""
    root = exe.parent / "vcpkg_installed" / triplet
    if not (root / "lib" / "SDL3.lib").is_file():
        vcpkg = game_config.vcpkg_root()
        if not vcpkg:
            sys.exit("no vcpkg for SDL3 (the port base needs it): set VCPKG_ROOT, or \"vcpkg\" in DecompPipeline/config.local.json")
        r = subprocess.run([str(vcpkg / "vcpkg.exe"), "install", "--triplet", triplet, "--x-manifest-root=" + str(HERE.parent),
                            "--x-install-root=" + str(exe.parent / "vcpkg_installed")], timeout=1500)
        if r.returncode:
            sys.exit("vcpkg could not install SDL3")
    return root


def run(gen, exe, check, jobs, asan=False):
    vc = game_config.vcvars32()
    if not vc:
        sys.exit("no 32-bit Visual Studio environment (set VCVARS32 or config.local.json vcvars)")
    gen = pathlib.Path(gen).resolve()
    exe = pathlib.Path(exe).resolve()
    exe.parent.mkdir(parents=True, exist_ok=True)
    sdl = sdl3(exe)
    kit = sorted(str(p) for p in KIT.glob("*.c")) + sorted(str(p) for p in BASE.glob("*.c")) + [str(gen / "scene_map.c")]
    cxx = sorted(str(p) for p in BASE.glob("*.cpp")) + sorted(str(p) for p in BASE.glob("third_party/*.cpp"))  # imgui_build.cpp
    chunks = [] if check else [str(gen / n) for n in (gen / "sat_chunks.txt").read_text().split()] + [str(gen / "sat_table.c"), str(gen / "txextra.c")]
    inc = ["/I" + str(KIT), "/I" + str(BASE), "/I" + str(TC_RT), "/I" + str(gen), "/I" + str(sdl / "include")]
    obj = exe.parent / "obj"
    obj.mkdir(exist_ok=True)
    asan = asan or os.environ.get("DP_ASAN") == "1"
    # the DLL C runtime under ASan: the static one (/MT) needs LIBVCASAN.lib, which the x86 Build Tools on build box A lack
    cl = ["cl"] + ([f if f != "/MT" else "/MD" for f in FLAGS] + ["/fsanitize=address", "/Z7"] if asan else FLAGS) + [f"/MP{jobs}"] + inc + ["/Fo" + str(obj) + "\\"]
    cl += CXX_FLAGS  # for the base's C++ files (the C files ignore them)
    if check:
        cl += ["/c"] + kit + cxx
    else:
        cl += kit + cxx + chunks + ["/Fe" + str(exe), "/link", "/LIBPATH:" + str(sdl / "lib"), "SDL3.lib"] + KITLIBS + (["/DEBUG", "/INCREMENTAL:NO"] if asan else [])
    bat = exe.parent / "build_saturn.bat"
    cfg = game_config.toolchain()
    from core import toolcfg  # noqa: E402  (on the path through toolchain)
    pin = "".join(f" {a}" for a in toolcfg.vcvars_args(cfg))  # the judge's toolset: the default 14.38 miscompiles translated C
    bat.write_text(f'@echo off\r\ncall "{vc}"{pin}\r\nif errorlevel 1 exit /b 1\r\n' + subprocess.list2cmdline(cl) + "\r\n"
                   + ("if errorlevel 1 exit /b 1\r\nfor %%f in (clang_rt.asan_dynamic-i386.dll) do copy /y \"%%~$PATH:f\" "
                      f'"{exe.parent}" >nul\r\n' if asan and not check else ""), encoding="ascii")
    r = subprocess.run(["cmd", "/c", str(bat)], timeout=1500)
    if r.returncode == 0 and not check:
        shutil.copy2(sdl / "bin" / "SDL3.dll", exe.parent / "SDL3.dll")
    return r.returncode


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--asan", action="store_true", help="AddressSanitizer (also DP_ASAN=1)")
    a = ap.parse_args()
    sys.exit(run(a.gen, a.exe, a.check, a.jobs, a.asan))

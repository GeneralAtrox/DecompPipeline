"""Builds a native game on the port base's game-owned renderer mode (port/base/native_game.h): the base, the no-machine kit
(port/native) and the game's own sources into one 64-bit exe, with SDL3 beside it.

Nearest existing: build_saturn.py - the same plain cl build of the base with its flags, libraries and SDL3 from vcpkg, but
32-bit and for a translated console game; targets/xbox360_ppc/port/tools/gen_xbox.py builds the base 64-bit the same way
inside the Xbox 360 kit's generator. A native game has neither a generator nor a console, so this reuses their pieces
(build_saturn's flags, libraries and sdl3(), the configured vcvars64.bat with its pinned toolset) and adds only the file
list.

  build_native.py --game DIR --exe PATH [--jobs N]
DIR holds the game's .c and .cpp files and its port_config.h (PG_NATIVE_* identity, PORT_GAME_MENU). The base compiles
at /W3 as in the Xbox 360 kit's 64-bit build; the kit and the game at build_saturn's /W4 /WX.
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
PIPELINE = HERE.parent.parent
BASE = HERE.parent / "base"
KIT = HERE.parent / "native"
sys.path[:0] = [str(HERE), str(PIPELINE)]
import build_saturn  # noqa: E402
from core import toolcfg  # noqa: E402


def vcvars64():
    """$VCVARS64, else vcvars64.bat beside config.local.json's "vcvars"; with the pinned toolset's arguments."""
    cfg = toolcfg.load(PIPELINE)
    given = os.environ.get("VCVARS64") or (str(pathlib.Path(cfg["vcvars"]).with_name("vcvars64.bat")) if cfg.get("vcvars") else "")
    if not given or not pathlib.Path(given).is_file():
        sys.exit("no 64-bit Visual Studio environment (set VCVARS64 or config.local.json vcvars)")
    return given, "".join(f" {a}" for a in toolcfg.vcvars_args(cfg))


def bounded(bat, timeout):
    """Runs a .bat with a hard timeout; on expiry its whole process tree is killed by its PID. (rc or None, output)."""
    p = subprocess.Popen(["cmd", "/d", "/c", str(bat)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         errors="replace", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=60)
        return None, "timed out"


def cl_step(vc, name, args, out_dir, timeout, link=()):
    rsp = out_dir / (name + ".rsp")
    rsp.write_text("\n".join(f'"{a}"' if " " in a else a for a in args), encoding="utf-8")
    bat = out_dir / (name + ".bat")
    bat.write_text(f'@echo off\r\ncall "{vc[0]}"{vc[1]} >nul\r\nif errorlevel 1 exit /b 1\r\ncl @"{rsp}"' + "".join(f' "{a}"' for a in link)
                   + "\r\n", encoding="ascii")  # /link and what follows it only work on the command line
    rc, log = bounded(bat, timeout)
    bad = [ln for ln in log.splitlines() if " error " in ln or " warning " in ln or "fatal error" in ln]
    return rc == 0, bad or log.splitlines()[-10:]


def run(game, exe, jobs=4, timeout=1500):
    game = pathlib.Path(game).resolve()
    exe = pathlib.Path(exe).resolve()
    exe.parent.mkdir(parents=True, exist_ok=True)
    vc = vcvars64()
    sdl = build_saturn.sdl3(exe, "x64-windows")
    inc = ["/I" + str(game), "/I" + str(KIT), "/I" + str(BASE), "/I" + str(sdl / "include")]
    strict = build_saturn.FLAGS + build_saturn.CXX_FLAGS + inc
    loose = [f for f in strict if f not in ("/W4", "/WX")] + ["/W3"]
    base = sorted(BASE.glob("*.c")) + sorted(BASE.glob("*.cpp")) + sorted(BASE.glob("third_party/*.cpp"))
    own = sorted(KIT.glob("*.c")) + sorted(game.glob("*.c")) + sorted(game.glob("*.cpp"))
    objs = []
    for name, flags, srcs in (("obj_base", loose, base), ("obj_game", strict, own)):
        obj = exe.parent / name
        obj.mkdir(exist_ok=True)
        ok, lines = cl_step(vc, name, flags + [f"/MP{jobs}", "/c", "/Fo" + str(obj) + "\\"] + [str(s) for s in srcs],
                            exe.parent, timeout)
        if not ok:
            print(f"build_native: {name} failed:\n" + "\n".join(lines[:30]))
            return 1
        objs += [obj / (s.stem + ".obj") for s in srcs]
    ok, lines = cl_step(vc, "link", ["/nologo", "/Fe" + str(exe)] + [str(o) for o in objs], exe.parent, 600,
                        ["/link", "/LIBPATH:" + str(sdl / "lib"), "SDL3.lib"] + build_saturn.KITLIBS)
    if not ok:
        print("build_native: link failed:\n" + "\n".join(lines[:20]))
        return 1
    shutil.copy2(sdl / "bin" / "SDL3.dll", exe.parent / "SDL3.dll")
    print(f"build_native: {exe}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    sys.exit(run(a.game, a.exe, a.jobs))

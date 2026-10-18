"""Generate, configure and build a game's port (32-bit x86, MSVC, Ninja); optionally run it. Every step is
time-bounded and a step that times out is killed with its whole process tree.

    python port/tools/build.py [--game DIR] [--engine | --no-gen] [--jobs N] [--checked] [--run [--seconds N]]
                               [--regress] [--build-dir NAME]
                               [-- PORT ARGS...]   (passed to port.exe, e.g. -- --fixed-clock 1)

--game: the folder with game.json (default: the current folder). Writes into <game>/build: gen (gen_port.py),
x86 or x86chk (CMake + Ninja, the kit's CMakeLists.txt) and, with --run, run/port.log and run/port_sym.log.
--run starts port.exe headless (no window) unless the port arguments ask for one (-- --window 1280x960).
--regress then runs the game's regression scripts (tools/regress.py).
--build-dir NAME: a build of its own, so an agent's builds and runs never touch the user's (build/x86 belongs to
the game's Start Game.bat and the launcher): build/NAME (CMake), build/NAME_gen, build/NAME.log, build/NAME_run.
Two builds in one folder at once break Ninja's log ("Permission denied").
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
KIT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import game_config  # noqa: E402

# The 32-bit MSVC environment: $VCVARS32, else vcvars32.bat beside config.local.json's "vcvars" (game_config).
# NINJA: else the ninja on PATH (e.g. pip install ninja).
NINJA = os.environ.get("NINJA") or shutil.which("ninja") or "ninja"


def run(cmd, timeout, env=None, cwd=None, log=None):
    """Runs cmd; returns (exit code, output). On timeout the process tree is killed and the code is -1."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         errors="replace")
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=60)
        out, _ = p.communicate(timeout=60)
        out += f"\nTIMEOUT after {timeout} s: process tree killed\n"
        p.returncode = -1
    if log:
        pathlib.Path(log).write_text(out, encoding="utf-8")
    return p.returncode, out


def msvc_env(cache):
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    found = game_config.vcvars32()
    if not found:
        raise SystemExit("no 32-bit Visual Studio environment: set VCVARS32, or \"vcvars\" in DecompPipeline/"
                         "config.local.json (vcvars32.bat is looked for beside it)")
    vc = str(found).replace("/", "\\")
    rc, out = run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && set"', 180)
    if rc != 0:
        raise SystemExit("could not load the 32-bit Visual Studio environment (vcvars32.bat)")
    env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(env), encoding="utf-8")
    return env


def configured_from_kit(bdir):
    """True when bdir is a configured build of this kit's CMakeLists.txt with the vcpkg toolchain; a build folder
    configured otherwise (an older layout) loses its cache so that it is configured afresh."""
    cache = bdir / "CMakeCache.txt"
    if not cache.exists() or not (bdir / "build.ninja").exists():
        return False
    text = cache.read_text(encoding="utf-8", errors="replace")
    home = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$", text, re.M)
    if home and pathlib.Path(home.group(1)).resolve() == KIT.resolve() and "vcpkg.cmake" in text:
        return True
    cache.unlink()
    shutil.rmtree(bdir / "CMakeFiles", ignore_errors=True)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=None, help="the folder with game.json (default: the current folder)")
    ap.add_argument("--no-gen", action="store_true")
    ap.add_argument("--engine", action="store_true", help="compile the engine's readable files too (gen_port.py --engine)")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--checked", action="store_true", help="unoptimised /RTC1 build in build/x86chk")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--disc", default=None, help="default: game.json disc")
    ap.add_argument("--must-work", default=None, help="stop at the first listed stub ('' off; default: game.json)")
    ap.add_argument("--regress", action="store_true", help="then run the game's tests/*.script (tools/regress.py)")
    ap.add_argument("--build-dir", default=None, help="a build of its own in build/NAME (agents: not build/x86)")
    ap.add_argument("port_args", nargs=argparse.REMAINDER, help="after --: arguments for port.exe")
    a = ap.parse_args()
    cfg = game_config.load(game_config.find_game(a.game))
    game, build = cfg["dir"], cfg["dir"] / "build"
    own = a.build_dir
    if own and (own in ("x86", "x86chk", "gen", "run", "regress") or not re.fullmatch(r"[\w.-]+", own)):
        raise SystemExit(f"--build-dir {own}: give a plain name of its own (x86 is the user's build)")
    gen = build / (f"{own}_gen" if own else "gen")
    blog = build / (f"{own}.log" if own else "build.log")
    if not a.no_gen:
        rc, out = run([sys.executable, str(TOOLS / "gen_port.py"), "--game", str(game), "--out", str(gen)]
                      + (["--engine"] if a.engine else []), 600)
        print(out, end="")
        if rc:
            raise SystemExit(f"gen_port.py failed ({rc})")
    env = msvc_env(build / "msvc_env_x86.json")
    bdir = build / (own or ("x86chk" if a.checked else "x86"))
    vcpkg = game_config.vcpkg_root()
    if not vcpkg:
        raise SystemExit("no vcpkg for the kit's host libraries (vcpkg.json): set VCPKG_ROOT, or \"vcpkg\" in "
                         "DecompPipeline/config.local.json (a vcpkg checkout)")
    if not configured_from_kit(bdir):
        # vcpkg installs vcpkg.json's libraries for x86-windows into the build folder at the first configure
        rc, out = run(["cmake", "-G", "Ninja",
                       f"-DCMAKE_TOOLCHAIN_FILE={(vcpkg / 'scripts' / 'buildsystems' / 'vcpkg.cmake').as_posix()}",
                       "-DVCPKG_TARGET_TRIPLET=x86-windows", f"-DVCPKG_MANIFEST_DIR={KIT.as_posix()}", f"-DCMAKE_MAKE_PROGRAM={NINJA}", "-DCMAKE_BUILD_TYPE=Release",
                       "-DCMAKE_C_FLAGS_RELEASE=/DNDEBUG",
                       f"-DPORT_CHECKED={'ON' if a.checked else 'OFF'}", f"-DPORT_GEN={gen.as_posix()}",
                       "-S", str(KIT), "-B", str(bdir)],
                      2400, env=env)
        print(out[-3000:], end="")
        if rc:
            raise SystemExit(f"cmake configure failed ({rc})")
    rc, out = run(["cmake", "--build", str(bdir), "--", "-k", "0", f"-j{a.jobs}"], 3000, env=env,
                  log=blog)
    lines = out.splitlines()
    diags = [ln for ln in lines if " error " in ln or " warning " in ln or ": error" in ln or ": warning" in ln]
    print(f"build: exit {rc}; {len(diags)} diagnostic line(s); full log {blog}")
    for ln in diags[:60]:
        print("  " + ln[:300])
    if rc:
        print("\n".join(lines[-15:]))
        raise SystemExit(1)
    if a.run:
        disc = a.disc or (str(cfg["disc"]) if cfg["disc"] else None)
        if not disc:
            raise SystemExit("--run needs a disc image: --disc, or game.json disc")
        must = a.must_work if a.must_work is not None else str(cfg["must_work"] or "")
        rdir = build / (f"{own}_run" if own else "run")
        rdir.mkdir(parents=True, exist_ok=True)
        exe = bdir / "port.exe"
        cmd = [str(exe), "--disc", disc, "--seconds", str(a.seconds), "--log", str(rdir / "port.log"),
               "--dump-boot", str(rdir / "boot.bin")]
        if cfg["fsca_table"]:
            cmd += ["--check-fsca", str(cfg["fsca_table"])]
        if must:
            cmd += ["--must-work", must]
        extra = [x for x in a.port_args if x != "--"]
        if "--window" not in extra:  # --run is for automation: off-screen unless a window is asked for
            cmd += ["--headless"]
        cmd += extra
        rc, _ = run(cmd, a.seconds + 60, cwd=rdir)
        shutil.copyfile(bdir / "port.map", rdir / "port.map")
        _, sym = run([sys.executable, str(TOOLS / "symbolize.py"), str(rdir / "port.map"),
                      str(rdir / "port.log")], 120, log=rdir / "port_sym.log")
        print(sym[-6000:])
        print(f"run: exit {rc}; log {rdir / 'port_sym.log'}")
    if a.regress:
        import regress
        raise SystemExit(regress.main(["--game", str(game)] + (["--build-dir", own] if own else [])))


if __name__ == "__main__":
    main()

"""Generate, configure and build a game's port (32-bit x86, MSVC, Ninja); optionally run it. Every step is
time-bounded and a step that times out is killed with its whole process tree.

    python port/tools/build.py [--game DIR] [--engine | --no-gen] [--jobs N] [--checked] [--run [--seconds N]]

--game: the folder with game.json (default: the current folder). Writes into <game>/build: gen (gen_port.py),
x86 or x86chk (CMake + Ninja, the kit's CMakeLists.txt) and, with --run, run/port.log and run/port_sym.log.
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
    """True when bdir is a configured build of this kit's CMakeLists.txt; a build folder configured from another
    source (an older layout) loses its cache so that it is configured afresh."""
    cache = bdir / "CMakeCache.txt"
    if not cache.exists() or not (bdir / "build.ninja").exists():
        return False
    home = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$", cache.read_text(encoding="utf-8", errors="replace"), re.M)
    if home and pathlib.Path(home.group(1)).resolve() == KIT.resolve():
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
    a = ap.parse_args()
    cfg = game_config.load(game_config.find_game(a.game))
    game, build = cfg["dir"], cfg["dir"] / "build"
    if not a.no_gen:
        rc, out = run([sys.executable, str(TOOLS / "gen_port.py"), "--game", str(game), "--out", str(build / "gen")]
                      + (["--engine"] if a.engine else []), 600)
        print(out, end="")
        if rc:
            raise SystemExit(f"gen_port.py failed ({rc})")
    env = msvc_env(build / "msvc_env_x86.json")
    bdir = build / ("x86chk" if a.checked else "x86")
    if not configured_from_kit(bdir):
        rc, out = run(["cmake", "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={NINJA}", "-DCMAKE_BUILD_TYPE=Release",
                       "-DCMAKE_C_FLAGS_RELEASE=/DNDEBUG",
                       f"-DPORT_CHECKED={'ON' if a.checked else 'OFF'}", f"-DPORT_GEN={(build / 'gen').as_posix()}",
                       "-S", str(KIT), "-B", str(bdir)],
                      600, env=env)
        print(out[-3000:], end="")
        if rc:
            raise SystemExit(f"cmake configure failed ({rc})")
    rc, out = run(["cmake", "--build", str(bdir), "--", "-k", "0", f"-j{a.jobs}"], 3000, env=env,
                  log=build / "build.log")
    lines = out.splitlines()
    diags = [ln for ln in lines if " error " in ln or " warning " in ln or ": error" in ln or ": warning" in ln]
    print(f"build: exit {rc}; {len(diags)} diagnostic line(s); full log {build / 'build.log'}")
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
        rdir = build / "run"
        rdir.mkdir(parents=True, exist_ok=True)
        exe = bdir / "port.exe"
        cmd = [str(exe), "--disc", disc, "--seconds", str(a.seconds), "--log", str(rdir / "port.log"),
               "--dump-boot", str(rdir / "boot.bin")]
        if cfg["fsca_table"]:
            cmd += ["--check-fsca", str(cfg["fsca_table"])]
        if must:
            cmd += ["--must-work", must]
        rc, _ = run(cmd, a.seconds + 60, cwd=rdir)
        shutil.copyfile(bdir / "port.map", rdir / "port.map")
        _, sym = run([sys.executable, str(TOOLS / "symbolize.py"), str(rdir / "port.map"),
                      str(rdir / "port.log")], 120, log=rdir / "port_sym.log")
        print(sym[-6000:])
        print(f"run: exit {rc}; log {rdir / 'port_sym.log'}")


if __name__ == "__main__":
    main()

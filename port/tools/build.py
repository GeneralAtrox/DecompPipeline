"""Generate, configure and build a game's port (32-bit x86, MSVC, Ninja); optionally run it. Every step is
time-bounded and a step that times out is killed with its whole process tree.

    python port/tools/build.py [--game DIR] [--engine | --no-gen] [--jobs N] [--checked] [--run [--seconds N]]
                               [--regress] [--build-dir NAME] [--progress] [--tx DIR [--tx-mode mix|only]]
                               [--tx-ovl ROOT]
                               [-- PORT ARGS...]   (passed to port.exe, e.g. -- --fixed-clock 1)

--game: the folder with game.json (default: the current folder). Writes into <game>/build: gen (gen_port.py),
x86 or x86chk (CMake + Ninja, the kit's CMakeLists.txt) and, with --run, run/port.log and run/port_sym.log.
--run starts port.exe headless (no window) unless the port arguments ask for one (-- --window 1280x960).
--regress then runs the game's regression scripts (tools/regress.py).
--build-dir NAME: a build of its own, so an agent's builds and runs never touch the user's (build/x86 belongs to
the game's Start Game.bat and the launcher): build/NAME (CMake), build/NAME_gen, build/NAME.log, build/NAME_run.
Two builds in one folder at once break Ninja's log ("Permission denied").
--jobs: compile jobs (default: the machine's logical processors).
--tx DIR, --tx-mode: translator C for this build (gen_port.py --tx; default: game.json "translator_c"); --tx-ovl
ROOT: the overlays' (gen_overlays.py --tx; default: overlays.json "translator_c"), --tx-mode for them too.
--progress: the launcher's view (a game's Start Game.bat): one line counting the compiles as they finish, rewritten
in place, warnings and errors in full as they come, "Game is up to date." when nothing changed; the generator's and
the compiler's full output go to the build log only.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time

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


def run_live(cmd, timeout, env=None, on_line=print):
    """run, with each output line handed to on_line as it comes."""
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    expired = []

    def kill():
        expired.append(1)
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=60)

    timer = threading.Timer(timeout, kill)
    timer.start()
    out = []
    try:
        for line in p.stdout:
            out.append(line)
            on_line(line)
        p.wait()
    finally:
        timer.cancel()
    if expired:
        out.append(f"\nTIMEOUT after {timeout} s: process tree killed\n")
        p.returncode = -1
    return p.returncode, "".join(out)


class Progress:
    """--progress: Ninja's [finished/total] compile and link lines become one status line rewritten in place; the
    compiler's, linker's and CMake's diagnostics are printed whole as they come; everything else (CMake re-running
    after a file was added or removed, vcpkg, the failed command lines) is left to the log."""
    STEP = re.compile(r"^\[(\d+)/(\d+)\] (Building|Linking)")
    SHOWN = re.compile(r"error|warning|: note:|fatal|ninja: build stopped|CMake Error", re.I)

    def __init__(self):
        self.total, self.width, self.t0 = 0, 0, time.monotonic()

    def status(self, text):
        sys.stdout.write("\r" + text.ljust(self.width))
        sys.stdout.flush()
        self.width = len(text)

    def clear(self):
        if self.width:
            sys.stdout.write("\r" + " " * self.width + "\r")
            self.width = 0

    def line(self, ln):
        m = self.STEP.match(ln)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            if not self.total:
                self.clear()
                print(f"Updating the game: {total} files to build...")
            self.total = total
            self.status(f"  {done}/{total} built, {time.monotonic() - self.t0:.0f} s")
        elif re.match(r"\[\d+/\d+\] Re-running CMake", ln):
            self.status("Updating the build's file list (files were added or removed)...")
        elif self.SHOWN.search(ln) and not ln.startswith("FAILED: "):
            self.clear()
            print(ln.rstrip("\n"))

    def finish(self, rc):
        self.clear()
        if rc == 0:
            print(f"  built in {time.monotonic() - self.t0:.0f} s" if self.total else "Game is up to date.")


# What the cache keeps, as core/check.CAPTURED (tests/test_ghidra_broker.py keeps the two the same): the compiler's,
# linker's, Visual Studio's, the Windows SDK's and the Windows variables a process needs; nothing else is written.
CAPTURED = ("PATH", "INCLUDE", "LIB", "LIBPATH", "EXTERNAL_INCLUDE", "CL", "_CL_", "LINK", "_LINK_",
            "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "OS", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
            "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
            "COMMONPROGRAMW6432", "PROGRAMDATA", "ALLUSERSPROFILE", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
            "PLATFORM", "COMMANDPROMPTTYPE", "DEVENVDIR", "EXTENSIONSDKDIR", "HTMLHELPDIR", "NETFXSDKDIR",
            "UCRTVERSION", "UNIVERSALCRTSDKDIR", "VCIDEINSTALLDIR", "VCINSTALLDIR", "VCTOOLSINSTALLDIR",
            "VCTOOLSREDISTDIR", "VCTOOLSVERSION", "VSINSTALLDIR", "VISUALSTUDIOVERSION", "WINDOWSLIBPATH",
            "VCPKG_ROOT")  # the port build's vcpkg toolchain
CAPTURED_RE = re.compile(r"VS\d+COMNTOOLS|VSCMD_\w+|__VSCMD_\w+|__DOTNET_\w+|FRAMEWORK\w*|WINDOWSSDK\w*", re.I)


def kept(name):
    return name.upper() in CAPTURED or bool(CAPTURED_RE.fullmatch(name))


def msvc_env(cache):
    cfg = game_config.toolchain()
    from core import toolcfg  # noqa: E402  (game_config put the pipeline on sys.path)
    found = game_config.vcvars32()
    vc = str(found).replace("/", "\\") if found else None
    if cache.exists():
        env = json.loads(cache.read_text(encoding="utf-8"))
        if not all(kept(k) for k in env):  # a cache written before that rule: cleaned in place
            env = {k: v for k, v in env.items() if kept(k)}
            cache.write_text(json.dumps(env), encoding="utf-8")
        if vc and toolcfg.cache_current(cache, env, vc, cfg):  # another vcvars, toolset or cl: loaded again
            return env
    if not found:
        raise SystemExit("no 32-bit Visual Studio environment: set VCVARS32, or \"vcvars\" in DecompPipeline/"
                         "config.local.json (vcvars32.bat is looked for beside it)")
    rc, out = run(toolcfg.vcvars_command(vc, cfg), 180)
    env = {k: v for k, v in (line.split("=", 1) for line in out.splitlines() if "=" in line) if kept(k)}
    try:
        toolcfg.loaded(rc, env, vc, cfg)  # its exit code and the pin decide, not how the variables look
    except RuntimeError as e:
        raise SystemExit(str(e)) from None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(env), encoding="utf-8")
    toolcfg.write_key(cache, env, vc, cfg)
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
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--checked", action="store_true", help="unoptimised /RTC1 build in build/x86chk")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--disc", default=None, help="default: game.json disc")
    ap.add_argument("--must-work", default=None, help="stop at the first listed stub ('' off; default: game.json)")
    ap.add_argument("--regress", action="store_true", help="then run the game's tests/*.script (tools/regress.py)")
    ap.add_argument("--build-dir", default=None, help="a build of its own in build/NAME (agents: not build/x86)")
    ap.add_argument("--progress", action="store_true", help="the launcher's view: a live count")
    ap.add_argument("--tx", default=None, help="translator C: tools/dp_judge.py --sp-input's --out folder")
    ap.add_argument("--tx-mode", default=None, choices=("mix", "only"))
    ap.add_argument("--tx-ovl", default=None, help="the overlays' translator C: a folder of judged folders by "
                    "workspace name (gen_overlays.py --tx; default: overlays.json translator_c)")
    ap.add_argument("port_args", nargs=argparse.REMAINDER, help="after --: arguments for port.exe")
    a = ap.parse_args()
    cfg = game_config.load(game_config.find_game(a.game))
    game_config.oplog().begin("port build.py", [cfg["dir"]])
    game, build = cfg["dir"], cfg["dir"] / "build"
    own = a.build_dir
    if own and (own in ("x86", "x86chk", "gen", "run", "regress") or not re.fullmatch(r"[\w.-]+", own)):
        raise SystemExit(f"--build-dir {own}: give a plain name of its own (x86 is the user's build)")
    gen = build / (f"{own}_gen" if own else "gen")
    blog = build / (f"{own}.log" if own else "build.log")
    gen_out, view = "", Progress() if a.progress else None
    if not a.no_gen:
        if view:
            view.status("Checking the game's files...")
        rc, gen_out = run([sys.executable, str(TOOLS / "gen_port.py"), "--game", str(game), "--out", str(gen)]
                          + (["--engine"] if a.engine else []) + (["--tx", a.tx] if a.tx else [])
                          + (["--tx-mode", a.tx_mode] if a.tx_mode else []), 600)
        if view:
            view.clear()
        if not view or rc:
            print(gen_out, end="")
        if rc:
            raise SystemExit(f"gen_port.py failed ({rc})")
        if (game / "overlays.json").is_file():  # the overlays, compiled side by side (gen_overlays.py)
            rc, out = run([sys.executable, str(TOOLS / "gen_overlays.py"), "--game", str(game), "--out",
                           str(gen / "overlays")] + (["--tx", a.tx_ovl] if a.tx_ovl else [])
                          + (["--tx-mode", a.tx_mode] if a.tx_mode else []), 1200)
            if not view or rc:
                print(out, end="")
            if rc:
                raise SystemExit(f"gen_overlays.py failed ({rc})")
        else:
            (gen / "overlays" / "overlays.cmake").unlink(missing_ok=True)
    env = msvc_env(build / "msvc_env_x86.json")
    bdir = build / (own or ("x86chk" if a.checked else "x86"))
    vcpkg = game_config.vcpkg_root()
    if not vcpkg:
        raise SystemExit("no vcpkg for the kit's host libraries (vcpkg.json): set VCPKG_ROOT, or \"vcpkg\" in "
                         "DecompPipeline/config.local.json (a vcpkg checkout)")
    if not configured_from_kit(bdir):
        if a.progress:
            print("Setting up the build folder (the first time takes a few minutes)...", flush=True)
        # vcpkg installs vcpkg.json's libraries for x86-windows into the build folder at the first configure
        rc, out = run(["cmake", "-G", "Ninja",
                       f"-DCMAKE_TOOLCHAIN_FILE={(vcpkg / 'scripts' / 'buildsystems' / 'vcpkg.cmake').as_posix()}",
                       "-DVCPKG_TARGET_TRIPLET=x86-windows", f"-DVCPKG_MANIFEST_DIR={KIT.as_posix()}", f"-DCMAKE_MAKE_PROGRAM={NINJA}", "-DCMAKE_BUILD_TYPE=Release",
                       "-DCMAKE_C_FLAGS_RELEASE=/DNDEBUG",
                       f"-DPORT_CHECKED={'ON' if a.checked else 'OFF'}", f"-DPORT_GEN={gen.as_posix()}",
                       "-S", str(KIT), "-B", str(bdir)],
                      2400, env=env)
        if not a.progress or rc:
            print(out[-3000:], end="")
        if rc:
            raise SystemExit(f"cmake configure failed ({rc})")
    cmd = ["cmake", "--build", str(bdir), "--", "-k", "0", f"-j{a.jobs}"]
    if view:
        view.t0 = time.monotonic()
        rc, out = run_live(cmd, 3000, env=env, on_line=view.line)
        view.finish(rc)
    else:
        rc, out = run(cmd, 3000, env=env)
    blog.write_text(gen_out + out, encoding="utf-8")
    lines = out.splitlines()
    diags = [ln for ln in lines if " error " in ln or " warning " in ln or ": error" in ln or ": warning" in ln]
    if a.progress:
        if rc:
            print(f"The build failed (exit {rc}): the errors are above; the full log is {blog}")
            raise SystemExit(1)
    else:
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
    game_config.oplog().main(main)

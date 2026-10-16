"""Locations, config and small shared helpers.

Two roots: TOOL is this reusable pipeline (code, UI, per-CPU targets); ROOT is one game's workspace (a folder
with project.json, chosen by decomp.py --project or $DECOMP_PROJECT), which holds every input and result.
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import threading
import time

from . import filelock
from . import scope as scope_mod
from . import toolcfg

TOOL =pathlib.Path(__file__).resolve().parents[1]  # DecompPipeline/
TOOLS = TOOL / "tools"
UI = TOOL / "ui"
TARGETS = TOOL / "targets"


def _project():
    for c in (os.environ.get("DECOMP_PROJECT"), os.getcwd()):
        if c and (pathlib.Path(c) / "project.json").exists():
            return pathlib.Path(c).resolve()
    raise RuntimeError("no project: pass --project <folder with project.json>, or set DECOMP_PROJECT")


ROOT = _project()  # the game workspace
os.environ["DECOMP_PROJECT"] = str(ROOT)  # inherited by every child process (agents' check.py, wtool.py, fakes)
PROJECT = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
TARGET = TARGETS / PROJECT["target"]  # the CPU/platform: locked oracle scripts, Ghidra language, C runtime
TARGET_SCRIPTS = TARGET / "ghidra_scripts"
RUNTIME = TARGET / "runtime"

STATE = ROOT / "state"
# Ghidra refuses project paths with some characters (e.g. an apostrophe): "ghidra_dir" moves both elsewhere.
_GHIDRA_BASE = pathlib.Path(PROJECT["ghidra_dir"]) if PROJECT.get("ghidra_dir") else ROOT
GHIDRA_PROJECT = _GHIDRA_BASE / "ghidra"
GHIDRA_NAME = PROJECT["ghidra_name"]
INPUTS = ROOT / "oracle" / "TcInputs.java"  # this game's input generator (evolved only through the watcher gate)
HINTS = ROOT / "oracle" / "hints"  # per-function input hints (written only through the watcher gate)
SCRIPTS = STATE / "ghidra_scripts"  # what Ghidra compiles: the target's scripts plus INPUTS (sync_scripts)
GHIDRA_SLOTS = _GHIDRA_BASE / "ghidra_slots"  # read-only copies of the project for parallel preparation
PACKETS = ROOT / "packets"
HARNESS = ROOT / "harness"
WORK = ROOT / "work"
MATCHED = ROOT / "matched"
RUNS = ROOT / "runs"
SANDBOX = ROOT / "sandbox"
BUILD = ROOT / "build"
REFERENCE = ROOT / "reference"
IMPORTS = ROOT / "import"  # relocated C from an earlier project (xproject.py), judged before any agent run

CATALOG = STATE / "catalog.json"
CODE_RANGES = STATE / "code_ranges.txt"
FUNCTION_BODIES = STATE / "function_bodies.txt"  # "entry lo hi" per body range (TcCatalog; tools/completeness.py)
FSCA_TABLE = STATE / "fsca_table.bin"
LEDGER = STATE / "ledger.json"
MARKS = STATE / "ghidra_marks.tsv"
MSVC_ENV = STATE / "msvc_env.json"

def game_ranges(cfg):
    """[(lo, hi)] of the game's code: "game_ranges" ([["8c558000", "8c5f9dd0"], ...], for a combined image of
    overlays), else the main binary from its load address to "game_end". The functions to decompile are scope():
    these ranges plus project.json "extra_functions". One definition: core/scope."""
    return scope_mod.game_ranges(cfg)


def scope():
    """The project's decompile scope (core/scope.Scope): `addr in scope()`, .in_ranges(addr), .extras."""
    return scope_mod.Scope(PROJECT, ROOT)


def config():
    """Tool defaults (config.json, with this machine's config.local.json over it: core/toolcfg), then the target's
    facts (target.json), then the project's own settings."""
    cfg = toolcfg.load(TOOL)
    cfg.update(json.loads((TARGET / "target.json").read_text(encoding="utf-8")))
    cfg.update(PROJECT)
    cfg["binary_path"] = (ROOT / cfg["binary"]).resolve()
    return cfg

def show(p):
    """A path for logs and briefs: relative to the folder holding the workspace (decomp/work/...), else absolute."""
    p = pathlib.Path(p)
    try:
        return p.resolve().relative_to(ROOT.parent).as_posix()
    except ValueError:
        return p.as_posix()

def copy_if_changed(src, dst):
    if not dst.exists() or dst.read_bytes() != src.read_bytes():
        shutil.copyfile(src, dst)  # a fresh mtime, so Ghidra recompiles the bundle

_SYNC = threading.Lock()

def sync_scripts():
    """Assemble SCRIPTS from the target's locked scripts and this project's TcInputs.java; returns the folder."""
    with _SYNC:
        SCRIPTS.mkdir(parents=True, exist_ok=True)
        want = {p.name: p for p in TARGET_SCRIPTS.glob("*.java")}
        want["TcInputs.java"] = INPUTS
        for name, src in want.items():
            copy_if_changed(src, SCRIPTS / name)
        for p in SCRIPTS.glob("*.java"):
            if p.name not in want:
                p.unlink()
    return SCRIPTS

def norm_addr(addr):
    a = addr.lower().removeprefix("0x")
    int(a, 16)
    return a.rjust(8, "0")

def sha256_file(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()

def write_json(path, data):
    """Atomic (core/filelock.write_atomic). A read-modify-write of a file other processes also write holds
    filelock.locked(path) around the read and this write, or uses filelock.update_json."""
    filelock.write_json(path, data)

def read_json(path, default=None):
    path = pathlib.Path(path)
    if not path.exists():
        return default
    return json.loads(filelock.read_text(path))  # an open refused during another process's replace is retried

def kill_tree(proc):
    """Kill a process and all of its children."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
    else:
        os.killpg(os.getpgid(proc.pid), 9)
    try:  # reap it (batch 24: an unreaped killed runner raised ResourceWarning under -W error)
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass

def resolve_exe(name):
    """Full path of a command on PATH. On Windows `claude` is a .cmd shim, which CreateProcess only runs by full path."""
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name!r} not found on PATH (DecompPipeline/config.json 'claude')")
    return found

def run_bounded(cmd, timeout, cwd=None, env=None, stdin_text=None, on_line=None, stop_flag=None):
    """Run a command with a hard timeout; the whole process tree is killed when it expires.

    Returns (returncode or None when killed, combined output, reason).
    """
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        creationflags=flags, start_new_session=os.name != "nt")
    if stdin_text is not None:
        proc.stdin.write(stdin_text)
        proc.stdin.close()
    lines = []
    reason = None
    deadline = time.monotonic() + timeout
    def pump():
        for line in proc.stdout:
            lines.append(line)
            if on_line:
                on_line(line)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    while proc.poll() is None:
        if time.monotonic() > deadline:
            reason = "timeout"
            kill_tree(proc)
            break
        if stop_flag is not None and stop_flag.is_set():
            reason = "stopped"
            kill_tree(proc)
            break
        time.sleep(0.2)
    t.join(timeout=10)
    if not t.is_alive():  # the pipe is drained: close it (a descendant still holding it keeps it open instead)
        proc.stdout.close()
    rc = proc.poll() if reason is None else None
    return rc, "".join(lines), reason

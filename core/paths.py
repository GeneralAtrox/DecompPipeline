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

from . import filelock, fsca
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
SHARED_SCRIPTS = TOOL / "core" / "ghidra_shared"  # oracle scripts several targets use (target.json "shared_scripts")
RUNTIME = TARGET / "runtime"

STATE = ROOT / "state"
# Ghidra refuses project paths with some characters (e.g. an apostrophe): "ghidra_dir" moves both elsewhere.
_GHIDRA_BASE = pathlib.Path(PROJECT["ghidra_dir"]) if PROJECT.get("ghidra_dir") else ROOT
GHIDRA_PROJECT = _GHIDRA_BASE / "ghidra"
GHIDRA_NAME = PROJECT["ghidra_name"]
INPUTS = ROOT / "oracle" / "TcInputs.java"  # this game's input generator (evolved only through the watcher gate)
HINTS = ROOT / "oracle" / "hints"  # per-function input hints (written only through the watcher gate)
AGENT_NOTES = ROOT / "agent_notes"  # ADDR.md: a reviewer's note on a known fault, added to every agent's TASK.md
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
FSCA_TABLE = STATE / "fsca" / f"{fsca.FILE_SHA256[:16]}.bin"  # content-named, written once: fsca_table()
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
UNUSED_S = 7 * 86400  # a content folder or file no checkout used for this long is removed

def content_name(parts):
    """16 hex digits naming content: sha256 over (name, bytes) pairs in name order."""
    h = hashlib.sha256()
    for name, data in sorted(parts.items()):
        h.update(f"{name} {len(data)}\n".encode() + data)
    return h.hexdigest()[:16]

def prune_unused(folder, keep):
    """Remove the content entries (16-hex names, and .tmp- leftovers) in folder that nobody used for UNUSED_S,
    except keep."""
    cut = time.time() - UNUSED_S
    for p in folder.iterdir() if folder.is_dir() else ():
        ours = p.name.startswith(".tmp-") or (len(p.stem) == 16 and all(c in "0123456789abcdef" for c in p.stem))
        if ours and p.name != keep and p.stat().st_mtime < cut:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)

def target_scripts():
    """{file name: path} of the target's oracle scripts: its ghidra_scripts/*.java and the *.java of each
    SHARED_SCRIPTS folder its target.json "shared_scripts" names (one implementation of a rule that holds on more
    than one CPU: ppc/TcSingle.java for the PowerPC targets). A missing folder or a name in two places is an error."""
    out = {p.name: p for p in TARGET_SCRIPTS.glob("*.java")}
    for d in json.loads((TARGET / "target.json").read_text(encoding="utf-8")).get("shared_scripts") or []:
        if not (SHARED_SCRIPTS / d).is_dir():
            raise RuntimeError(f"target.json shared_scripts: no folder {SHARED_SCRIPTS / d}")
        for p in (SHARED_SCRIPTS / d).glob("*.java"):
            if p.name in out:
                raise RuntimeError(f"oracle script {p.name} is in both {out[p.name].parent} and {p.parent}")
            out[p.name] = p
    return out

def sync_scripts():
    """Assemble the target's scripts (target_scripts) and this project's TcInputs.java into SCRIPTS/<content_name>/;
    returns that folder. Written once, never changed: a checkout with other scripts gets another folder, so no
    checkout rewrites the scripts another checkout's Ghidra compiles and runs (one shared folder did: any checkout's
    sync changed every other's prepares). Each use refreshes the folder's mtime; prune_unused drops old ones."""
    want = target_scripts()
    want["TcInputs.java"] = INPUTS
    data = {name: src.read_bytes() for name, src in want.items()}
    name = content_name(data)
    out = SCRIPTS / name
    with _SYNC:
        if not out.is_dir():
            tmp = SCRIPTS / f".tmp-{name}-{os.getpid()}-{threading.get_ident()}"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            for n, b in data.items():
                (tmp / n).write_bytes(b)
            try:
                os.replace(tmp, out)
            except OSError:  # another process made it meanwhile: the same content
                shutil.rmtree(tmp, ignore_errors=True)
        os.utime(out)
        prune_unused(SCRIPTS, name)
    return out

def fsca_table():
    """FSCA_TABLE made the current table (core/fsca.ensure) and returned. Named by its content (state/fsca/<the first
    16 hex of its SHA-256>.bin) and written once, so no checkout rewrites the table another checkout's prepares and
    judges read: one state/fsca_table.bin did (a checkout of another table format rewrote it and every other
    checkout's readers refused it). An older checkout's state/fsca_table.bin is left as it is. Each use refreshes
    the mtime; prune_unused drops tables of other contents nobody used for UNUSED_S."""
    fsca.ensure(FSCA_TABLE)
    try:
        os.utime(FSCA_TABLE)
    except OSError:
        pass  # a reader holds it: the next use refreshes it
    prune_unused(FSCA_TABLE.parent, FSCA_TABLE.name)
    return FSCA_TABLE

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
        try:  # the same 0.2 s checks, but back as soon as the process ends (a sleep cost 0.1 s per command)
            proc.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
    t.join(timeout=10)
    if not t.is_alive():  # the pipe is drained: close it (a descendant still holding it keeps it open instead)
        proc.stdout.close()
    rc = proc.poll() if reason is None else None
    return rc, "".join(lines), reason


def agent_note(addr):
    """agent_notes/ADDR.md as a TASK.md section ("" when there is none): what a reviewer found wrong in an earlier
    version that the judge cannot see (e.g. a host local read past its end), for the raw and the readable agents."""
    f = AGENT_NOTES / f"{addr}.md"
    if not f.is_file():
        return ""
    return "\n## Reviewer's note (a fault the check cannot see; follow it)\n\n" + f.read_text(encoding="utf-8").strip() + "\n"

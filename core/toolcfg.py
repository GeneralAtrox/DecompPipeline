"""The tool's own settings: config.json (tracked, portable defaults) with config.local.json (git-ignored, this
machine's paths) merged over it, key by key (batch 26).

The machine paths (ghidra_home, java_home, vcvars, sevenzip, vcpkg: a vcpkg checkout for the port kit, flycast: the
workbench emulator port/tools/emu_capture.py drives; mwcc_root, objdiff_cli, dtk: the gamecube_ppc
byte-match check's CodeWarrior compiler pack, objdiff-cli and decomp-toolkit) are null in config.json; config.local.json gives them.
config.local.json may also pin the C compiler, "vcvars_args": vcvars' own arguments, one string (e.g. "10.0.28000.0
-vcvars_ver=14.44": the Windows SDK and the toolset). Every Visual Studio environment is then loaded with them after
the quoted script (vcvars_command), so machines with other Visual Studio or SDK installs build and judge with the same
compiler and headers. Each cached environment has a key beside it (cache_current, write_key): the vcvars script,
"vcvars_args", cl's full version and the content hash of the Windows SDK's headers and libraries (sdk_hash). A cache
whose key differs is loaded again, so a copied workspace or a changed setting never keeps the old compiler.
Code that needs one calls need(cfg, key), which stops with an error naming the key and config.local.json.
No imports from the rest of core, so tools/ scripts can use it alone.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading

LOCAL = "config.local.json"
MACHINE_KEYS = ("ghidra_home", "java_home", "vcvars", "sevenzip", "vcpkg", "flycast", "mwcc_root", "objdiff_cli",
                "dtk", "vcvars_args")
# The vcvars arguments "vcvars_args" may hold: a Windows SDK version, the toolset (-vcvars_ver=14.44 or 14.44.35207),
# the Spectre libraries. Nothing else, so the string cannot carry anything for cmd but these.
SDK_ARG = re.compile(r"10\.0\.\d+\.\d+")
TOOLSET_ARG = re.compile(r"-vcvars_ver=(\d+\.\d+(?:\.\d+)?)")
VCVARS_ARG = re.compile(rf"{SDK_ARG.pattern}|{TOOLSET_ARG.pattern}|-vcvars_spectre_libs=spectre")
REGRESS = "regress"  # a workspace's own regression fixtures folder
# Visual Studio's own environment scripts. A wrapper elsewhere may pass its own pins, which "vcvars_args" would
# then pass a second time (vcvars_command warns).
STOCK_VCVARS = re.compile(r"(?:^|[\\/])VC[\\/]Auxiliary[\\/]Build[\\/]vcvars(?:64|32)\.bat", re.I)


def load(tool):
    """config.json of the pipeline folder `tool`, with config.local.json (when present) merged over it."""
    tool = pathlib.Path(tool)
    cfg = json.loads((tool / "config.json").read_text(encoding="utf-8"))
    local = tool / LOCAL
    if local.is_file():
        over = json.loads(local.read_text(encoding="utf-8"))
        if not isinstance(over, dict):
            raise ValueError(f"{local}: must hold a JSON object of config keys")
        cfg.update(over)
    return cfg


def need(cfg, key, which=None):
    """cfg[key], a machine path; else (`which`) that program on PATH; else a clear error naming the key and file."""
    value = cfg.get(key)
    if value:
        return str(value)
    found = shutil.which(which) if which else None
    if found:
        return found
    raise RuntimeError(f"config key {key!r} is not set: give this machine's path in DecompPipeline/{LOCAL} "
                       f"(git-ignored, merged over config.json), e.g. {{\"{key}\": \"...\"}}")


def vcvars_command(bat, cfg):
    """The command that loads the Visual Studio environment script bat (vcvars64.bat, vcvars32.bat) and prints it
    (`set`). With config "vcvars_args", those arguments after the quoted path; without it, the same command as
    before. With arguments and a script that is not Visual Studio's own (STOCK_VCVARS), a warning on stderr."""
    args = vcvars_args(cfg)
    if args and not STOCK_VCVARS.search(str(bat)):
        print(f'warning: config "vcvars_args" is set, but {bat} is not Visual Studio\'s own '
              f'VC/Auxiliary/Build/{pathlib.Path(str(bat)).name}: a wrapper that passes its own pins gets them '
              f'twice; point "vcvars" at Visual Studio\'s script', file=sys.stderr, flush=True)
    pin = "".join(f" {a}" for a in args)
    return f'cmd /d /s /c ""{bat}"{pin} >nul 2>&1 && set"'


def vcvars_args(cfg):
    """config "vcvars_args" as a list of arguments ([] without it). Each must be one VCVARS_ARG, else ValueError."""
    args = str(cfg.get("vcvars_args") or "").split()
    bad = [a for a in args if not VCVARS_ARG.fullmatch(a)]
    if bad:
        raise ValueError(f'config "vcvars_args": {bad!r} not a vcvars argument; give a Windows SDK version and '
                         f'-vcvars_ver=, e.g. "10.0.28000.0 -vcvars_ver=14.44"')
    return args


def loaded(rc, env, bat, cfg):
    """The environment a vcvars run (vcvars_command) just printed, or RuntimeError: unless the run exited 0, and
    unless it holds what "vcvars_args" pins (pinned). The exit code decides even when the variables look right: a
    pinned SDK that is not fully installed sets plausible variables and fails. Nothing is cached from a failed run."""
    name = pathlib.Path(str(bat)).name
    hint = ' (config "vcvars_args": are that SDK and toolset fully installed?)' if vcvars_args(cfg) else ""
    if rc != 0:
        raise RuntimeError(f"could not load the Visual Studio environment ({name} exited {rc}){hint}")
    if not pinned(env, cfg):
        sdk = _env(env, "WINDOWSSDKVERSION").rstrip("\\") or "-"
        raise RuntimeError(f"{name} loaded toolset {_env(env, 'VCTOOLSVERSION') or '-'} and SDK {sdk}, not config "
                           f"\"vcvars_args\" {' '.join(vcvars_args(cfg))!r}")
    return env


def _env(env, name):
    return next((str(v) for k, v in env.items() if k.upper() == name), "")


def pinned(env, cfg):
    """True when a cached Visual Studio environment env holds what config "vcvars_args" pins: its WindowsSDKVersion
    is the SDK named, its VCToolsVersion the toolset (or starts with it); always True without a pin. A cache made
    before the pin, or with another, is loaded again."""
    for a in vcvars_args(cfg):
        m = TOOLSET_ARG.fullmatch(a)
        if m:
            have = _env(env, "VCTOOLSVERSION")
            if not (have == m.group(1) or have.startswith(f"{m.group(1)}.")):
                return False
        elif SDK_ARG.fullmatch(a) and _env(env, "WINDOWSSDKVERSION").rstrip("\\") != a:
            return False
    return True


_CL, _CL_LOCK = {}, threading.Lock()  # (cl.exe path, size, mtime_ns) -> its version line (cl_version)


def _cl_stat(env):
    """[path, size, mtime_ns] of the cl.exe on env's PATH; None without one."""
    cl = shutil.which("cl", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
    if not cl:
        return None
    st = os.stat(cl)
    return [cl, st.st_size, st.st_mtime_ns]


def cl_version(env, known=None):
    """The version line the cl.exe on env's PATH prints (e.g. "... Compiler Version 19.44.35219 for x64"): its full
    version, which two installs of one toolset folder can differ in (14.44.35207 gives 19.44.35219 on one machine,
    19.44.35228 on another). '-' without a cl.exe. known: a cache key (env_key) whose version is taken while cl.exe's
    path, size and mtime are the ones it records; otherwise cl runs, once per process per compiler."""
    st = _cl_stat(env or {})
    if not st:
        return "-"
    if known and known.get("cl_file") == st and known.get("cl"):
        return known["cl"]
    k = tuple(st)
    with _CL_LOCK:
        if k in _CL:
            return _CL[k]
    try:
        p = subprocess.run([st[0]], env=env, capture_output=True, text=True, errors="replace", timeout=60)
        line = next((x.strip() for x in (p.stderr + p.stdout).splitlines() if "Version" in x), "-")
    except (OSError, subprocess.SubprocessError):
        line = "-"
    with _CL_LOCK:
        _CL[k] = line
    return line


_KIT = re.compile(r".*[\\/]windows kits[\\/]10[\\/]((?:include[\\/][^\\/]+[\\/](?:ucrt|um|shared))|"
                  r"(?:lib[\\/][^\\/]+[\\/](?:ucrt|um)[\\/][^\\/]+))", re.I)
_SDK, _SDK_LOCK = {}, threading.Lock()  # (folder, its sdk_scan entry) -> the folder's sha256 (sdk_hash)


def sdk_folders(env):
    """The Windows SDK folders the compiler reads in env, from its INCLUDE and LIB: Include/<version>/ucrt, um and
    shared, and Lib/<version>/ucrt and um for the target's architecture. Each as (name below Windows Kits/10, e.g.
    "include/10.0.28000.0/um", folder), so two machines that install the SDK elsewhere name it the same."""
    seen = {}
    for var in ("INCLUDE", "LIB"):
        for p in _env(env, var).split(";"):
            p = os.path.normpath(p) if p else ""
            m = _KIT.fullmatch(p)
            if m:
                seen.setdefault(m.group(1).replace("\\", "/").lower(), p)
    return sorted(seen.items())


def _files(folder):
    for e in os.scandir(folder):
        if e.is_dir(follow_symlinks=False):
            yield from _files(e.path)
        else:
            yield e


def sdk_scan(env):
    """[[name, files, bytes, newest mtime_ns], ...] of env's sdk_folders: a directory listing, a few milliseconds.
    A cache key records it, so a Windows SDK updated in place (10.0.28000.1839 to .2114 keeps WindowsSDKVersion
    10.0.28000.0) makes the cache load again; None for a folder that is gone."""
    out = []
    for name, folder in sdk_folders(env):
        try:
            sts = [e.stat() for e in _files(folder)]
            out.append([name, len(sts), sum(s.st_size for s in sts), max((s.st_mtime_ns for s in sts), default=0)])
        except OSError:
            out.append([name, None, None, None])
    return out


def sdk_hash(env, known=None):
    """sha256 of the contents of env's Windows SDK folders (sdk_folders): every file's path below its folder and its
    bytes. WindowsSDKVersion cannot tell two builds of one SDK apart (10.0.28000.1839 and .2114 both say
    10.0.28000.0, with different um and shared headers and libraries); this can. About 500 MB are read, so it is
    taken from known (a cache key, env_key) while its sdk_scan still matches, and otherwise computed once per
    folder per process. '-' when env names no SDK folder."""
    scan = sdk_scan(env)
    if not scan:
        return "-"
    if known and known.get("sdk_scan") == scan and known.get("sdk"):
        return known["sdk"]
    total = hashlib.sha256()
    for (name, folder), entry in zip(sdk_folders(env), scan):
        k = (folder, tuple(entry))
        with _SDK_LOCK:
            have = _SDK.get(k)
        if have is None:
            h = hashlib.sha256()
            try:
                for e in sorted(_files(folder), key=lambda e: e.path.lower()):
                    h.update(os.path.relpath(e.path, folder).replace("\\", "/").lower().encode() + b"\0")
                    with open(e.path, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    h.update(b"\0")
                have = h.hexdigest()
            except OSError:
                have = "missing"
            with _SDK_LOCK:
                _SDK[k] = have
        total.update(f"{name} {have}\n".encode())
    return total.hexdigest()


def key_path(cache):
    """The key file beside a Visual Studio environment cache: state/msvc_env.json -> state/msvc_env.key.json."""
    cache = pathlib.Path(cache)
    return cache.with_name(cache.stem + ".key.json")


def env_key(bat, cfg, env, known=None):
    """What a cached environment was loaded with: the vcvars script, config "vcvars_args", and the full version of the
    cl.exe it runs (with that file's path, size and mtime, so a later check runs cl only when the file changed), and
    the Windows SDK's content hash with the listing it was taken at (sdk_hash, sdk_scan)."""
    return {"vcvars": str(bat), "vcvars_args": " ".join(vcvars_args(cfg)), "cl_file": _cl_stat(env),
            "cl": cl_version(env, known), "sdk_scan": sdk_scan(env), "sdk": sdk_hash(env, known)}


def compiler_identity(env, known=None):
    """What decides the compiler's output, for the judge stamp: cl's full version line, the WindowsSDKVersion and
    VCToolsVersion of the environment vcvars produced, and the content hash of that SDK's headers and libraries
    (sdk_hash: two builds of one SDK version differ there). Two machines with the same cl can differ in the SDK, as
    vcvars takes the newest SDK unless "vcvars_args" names one."""
    sdk = _env(env, "WINDOWSSDKVERSION").rstrip("\\") or "-"
    return (f"{cl_version(env, known)} sdk {sdk} files {sdk_hash(env, known)} "
            f"tools {_env(env, 'VCTOOLSVERSION') or '-'}")


def cache_current(cache, env, bat, cfg):
    """True when the cached environment env (read from cache) may be used: loaded from this vcvars script with this
    "vcvars_args", and its cl.exe still reports the full version recorded then. A cache without its key (written before
    the key existed, or copied without it), a changed setting or another compiler (a workspace copied to another
    machine, a Visual Studio update) is loaded again (write_key then records the new one)."""
    try:
        key = json.loads(key_path(cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not env or not isinstance(key, dict) or not pinned(env, cfg):
        return False
    if key.get("vcvars") != str(bat) or key.get("vcvars_args") != " ".join(vcvars_args(cfg)):
        return False
    if not key.get("sdk") or key.get("sdk_scan") != sdk_scan(env):  # the SDK's files changed (or a key before them)
        return False
    return key.get("cl") not in (None, "-") and cl_version(env, key) == key["cl"]


def write_key(cache, env, bat, cfg):
    """Record what the environment just written to cache was loaded with (cache_current reads it)."""
    key_path(cache).write_text(json.dumps(env_key(bat, cfg, env)), encoding="utf-8")


def patterns(tests):
    """tests/patterns.json with tests/patterns.local.json's "projects" (this machine's workspace paths, git-ignored)
    merged over its "projects"; a project without a path stays null (regress stops on it when selected).
    Fixtures are game-derived C, so they live with each game, not here: every workspace's own
    regress/fixtures.json ({"fixtures": [...]}, pass/fail named relative to that folder) is added to "fixtures"
    with its project key and absolute paths."""
    tests = pathlib.Path(tests)
    spec = json.loads((tests / "patterns.json").read_text(encoding="utf-8"))
    local = tests / "patterns.local.json"
    if local.is_file():
        over = json.loads(local.read_text(encoding="utf-8"))
        spec["projects"] = dict(spec.get("projects") or {}, **(over.get("projects") or {}))
    fixtures = list(spec.get("fixtures") or [])
    for key, path in spec["projects"].items():
        own = pathlib.Path(path) / REGRESS / "fixtures.json" if path else None
        if own and own.is_file():
            for f in json.loads(own.read_text(encoding="utf-8"))["fixtures"]:
                f = dict(f, project=key)
                for side in ("pass", "fail"):
                    if f.get(side):
                        f[side] = str(own.parent / f[side])
                fixtures.append(f)
    spec["fixtures"] = fixtures
    return spec

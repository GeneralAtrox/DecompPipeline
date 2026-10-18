"""The game's port configuration, game.json, in the game's port folder (see port/README.md for every key).

Paths in game.json are relative to the folder that holds it. The kit reads it; nothing game-specific lives in the
kit itself. load() validates and resolves; the write_* functions emit the generated files the kit's runtime and
CMakeLists.txt read: include/port_config.h, port_entry.c and port_files.cmake.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import time

KIT = pathlib.Path(__file__).resolve().parents[1]  # DecompPipeline/port
PIPELINE = KIT.parent
SDK_DIR = KIT / "sdk"
ADDR = re.compile(r"[0-9a-f]{8}")
NAME = re.compile(r"[A-Za-z_]\w*")
REQUIRED = ("name", "decomp", "entry", "image_base")
KNOWN = set(REQUIRED) | {"boot_file", "disc", "fsca_table", "must_work", "sdk", "sources", "hardware", "wrap",
                         "console", "id", "boot_sha256", "arrows"}
CONSOLE = ("country", "language", "broadcast")


class ConfigError(SystemExit):
    pass


def _addr(value, what):
    a = str(value).lower().removeprefix("0x")
    if not ADDR.fullmatch(a):
        raise ConfigError(f"game.json {what}: {value!r} is not an 8-digit hex guest address")
    return a


def _int(value, what):
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"game.json {what}: {value!r} is not a number") from None


def vcvars32():
    """The 32-bit MSVC environment script: $VCVARS32, else vcvars32.bat next to the pipeline's configured vcvars
    (config.local.json "vcvars"); None when neither exists (no Visual Studio on this machine)."""
    if os.environ.get("VCVARS32"):
        given = pathlib.Path(os.environ["VCVARS32"])
        return given if given.is_file() else None
    sys.path.insert(0, str(PIPELINE))
    from core import toolcfg  # noqa: E402  (no imports from the rest of core)
    vc = toolcfg.load(PIPELINE).get("vcvars")
    found = pathlib.Path(vc).with_name("vcvars32.bat") if vc else None
    return found if found and found.is_file() else None


def vcpkg_root():
    """The vcpkg checkout the kit's host libraries (vcpkg.json: SDL3) come from: $VCPKG_ROOT, else config.local.json
    "vcpkg"; None when neither names a folder with scripts/buildsystems/vcpkg.cmake."""
    given = os.environ.get("VCPKG_ROOT")
    if not given:
        sys.path.insert(0, str(PIPELINE))
        from core import toolcfg  # noqa: E402
        given = toolcfg.load(PIPELINE).get("vcpkg")
    root = pathlib.Path(given) if given else None
    return root if root and (root / "scripts" / "buildsystems" / "vcpkg.cmake").is_file() else None


def sdk_modules():
    """The kit's SDK replacement modules: {name: path}."""
    return {p.stem: p for p in sorted(SDK_DIR.glob("*.c"))}


def find_game(start=None):
    """The folder holding game.json: start, else the current folder."""
    d = pathlib.Path(start or ".").resolve()
    if not (d / "game.json").is_file():
        raise ConfigError(f"no game.json in {d} (pass --game DIR)")
    return d


def load(game_dir):
    """game.json, validated, with every path resolved against its folder."""
    game_dir = pathlib.Path(game_dir).resolve()
    raw = json.loads((game_dir / "game.json").read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in raw]
    unknown = sorted(set(raw) - KNOWN)
    if missing or unknown:
        raise ConfigError(f"game.json: missing {missing}, unknown {unknown}")
    entry = raw["entry"]
    if not isinstance(entry, dict) or not NAME.fullmatch(str(entry.get("name", ""))):
        raise ConfigError('game.json "entry": {"name": C name, "addr": hex}')
    mods = sdk_modules()
    sdk = list(raw.get("sdk", []))
    bad = [m for m in sdk if m not in mods]
    if bad:
        raise ConfigError(f"game.json sdk: unknown module(s) {bad}; the kit has {sorted(mods)}")

    def path(key):
        return (game_dir / raw[key]).resolve() if raw.get(key) else None
    sources = [(game_dir / s).resolve() for s in raw.get("sources", [])]
    absent = [str(s) for s in sources if not s.is_file()]
    if absent:
        raise ConfigError(f"game.json sources: not found {absent}")
    hardware = {_addr(a, "hardware"): n for a, n in raw.get("hardware", {}).items()}
    wrap = {_addr(a, "wrap"): n for a, n in raw.get("wrap", {}).items()}
    bad = [n for n in wrap.values() if not NAME.fullmatch(str(n))]
    if bad or set(wrap) & set(hardware):
        raise ConfigError(f"game.json wrap: {{addr: C name}} of readable functions (not hardware ones); bad {bad}")
    console = raw.get("console")
    if console is not None:
        if set(console) != set(CONSOLE):
            raise ConfigError(f'game.json "console" needs exactly {list(CONSOLE)}')
        console = {k: _int(console[k], f"console.{k}") for k in CONSOLE}
    boot = raw.get("boot_file") or ""
    if not re.fullmatch(r"[A-Za-z0-9_.]{0,31}", boot):
        raise ConfigError(f"game.json boot_file: {boot!r} (an ISO 9660 name at the disc's root, up to 31 chars)")
    gid = str(raw.get("id") or game_dir.parent.name)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", gid):
        raise ConfigError(f"game.json id: {gid!r} (letters, digits, '_', '.', '-')")
    arrows = raw.get("arrows", "both")
    if arrows not in ("both", "stick"):
        raise ConfigError(f'game.json arrows: {arrows!r} ("both": the d-pad and the stick; "stick": the stick only)')
    sha = [str(h).lower() for h in raw.get("boot_sha256", [])]
    if not all(re.fullmatch(r"[0-9a-f]{64}", h) for h in sha):
        raise ConfigError("game.json boot_sha256: a list of the accepted boot files' SHA-256 (64 hex digits each)")
    return {"dir": game_dir, "name": str(raw["name"]), "id": gid, "boot_sha256": sha, "decomp": path("decomp"),
            "entry": {"name": entry["name"], "addr": _addr(entry.get("addr"), "entry.addr")},
            "image_base": _addr(raw["image_base"], "image_base"), "boot_file": boot, "disc": path("disc"),
            "fsca_table": path("fsca_table"), "must_work": path("must_work"),
            "sdk": [mods[m] for m in sdk], "sources": sources, "hardware": hardware, "wrap": wrap,
            "console": console, "arrows": arrows}


def c_string(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def port_config_h(cfg):
    lines = ["/* Generated by port/tools/gen_port.py from game.json: do not edit. */", "#ifndef PORT_CONFIG_H",
             "#define PORT_CONFIG_H", "", f"#define PORT_GAME_NAME {c_string(cfg['name'])}",
             f"#define PORT_ENTRY_NAME {c_string(cfg['entry']['name'])}",
             f"#define PORT_ENTRY_ADDR 0x{cfg['entry']['addr']}u",
             f"#define PORT_IMAGE_BASE 0x{cfg['image_base']}u /* where the boot file loads */",
             f"#define PORT_BOOT_FILE {c_string(cfg['boot_file'])} /* empty: IP.BIN's boot file name */",
             f"#define PORT_GAME_ID {c_string(cfg['id'])}",
             "/* the accepted boot files' SHA-256 (game.json boot_sha256), \"\"-terminated; none: any boot file */",
             "#define PORT_BOOT_SHA256 {" + ", ".join(c_string(h) for h in cfg["boot_sha256"] + [""]) + "}"]
    stick = int(cfg.get("arrows") == "stick")
    lines.append(f"#define PORT_ARROWS_STICK {stick} /* 1: the arrow keys move only the stick (game.json arrows) */")
    if cfg["console"]:
        lines += [f"#define PORT_CONSOLE_{k.upper()} 0x{v:x}" for k, v in cfg["console"].items()]
    return "\n".join(lines + ["", "#endif"]) + "\n"


def commit(folder):
    """The folder's git commit (12 digits, "-dirty" with uncommitted changes), or "unknown" outside git."""
    try:
        out = subprocess.run(["git", "-C", str(folder), "describe", "--always", "--dirty", "--abbrev=12"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def port_version(cfg):
    """The build's identity (port.exe --version, installation.json "version"): the game id, the game's and the kit's
    commits and the date of the build."""
    return f"{cfg['id']} {commit(cfg['dir'])} DecompPipeline {commit(KIT)} {time.strftime('%Y-%m-%d')}"


def port_entry_c(cfg):
    name = cfg["entry"]["name"]
    return "\n".join(["/* Generated by port/tools/gen_port.py from game.json: the game's entry function and the",
                      " * build's identity. */", '#include "game.h"', "", "void port_game_entry(void);",
                      "extern const char port_version[];", "", f"void port_game_entry(void) {{ {name}(); }}",
                      f"const char port_version[] = {c_string(port_version(cfg))};", ""])


def cmake_list(var, items):
    return [f"set({var}"] + [f'  "{pathlib.Path(x).as_posix()}"' for x in items] + [")"]


def port_files_cmake(cfg, readable_src, readable_files, readable_include, rd_rt_dir, defines=None):
    """defines: {readable file stem: ["Name=Wrapper", ...]}, the game.json wraps of the functions that file calls."""
    lines = ["# Generated by port/tools/gen_port.py: what the kit's CMakeLists.txt compiles for this game."]
    lines += cmake_list("PORT_READABLE", [readable_src / f"{x}.c" for x in readable_files])
    lines += [f'set(PORT_READABLE_INCLUDE "{readable_include.as_posix()}")', f'set(PORT_RD_RT "{rd_rt_dir.as_posix()}")']
    lines += cmake_list("PORT_SDK_SOURCES", cfg["sdk"]) + cmake_list("PORT_GAME_SOURCES", cfg["sources"])
    for x, defs in sorted((defines or {}).items()):
        lines.append(f'set_source_files_properties("{(readable_src / f"{x}.c").as_posix()}" PROPERTIES '
                     f'COMPILE_DEFINITIONS "{";".join(defs)}")')
    return "\n".join(lines) + "\n"

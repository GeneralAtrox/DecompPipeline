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
                         "console", "id", "boot_sha256", "arrows", "widescreen", "aica_arm", "kamui1", "lockstep_sample_pcs",
                         "lockstep_rtc_pcs", "lockstep_scene_pcs", "translator_c", "tx_first", "not_code", "texture_pack"}
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


def toolchain():
    """The pipeline's config (config.json with config.local.json over it): its "vcvars_args" (the Windows SDK and the
    toolset) go after vcvars32() in the kit's environment load (core/toolcfg.vcvars_command)."""
    sys.path.insert(0, str(PIPELINE))
    from core import toolcfg  # noqa: E402  (no imports from the rest of core)
    return toolcfg.load(PIPELINE)


def vcvars32():
    """The 32-bit MSVC environment script: $VCVARS32, else vcvars32.bat next to the pipeline's configured vcvars
    (config.local.json "vcvars"); None when neither exists (no Visual Studio on this machine). The script alone: its
    "vcvars_args" come from toolchain(), as for the judge's vcvars32.bat (core/readable_check)."""
    if os.environ.get("VCVARS32"):
        given = pathlib.Path(os.environ["VCVARS32"])
        return given if given.is_file() else None
    sys.path.insert(0, str(PIPELINE))
    from core import toolcfg  # noqa: E402  (no imports from the rest of core)
    vc = toolcfg.load(PIPELINE).get("vcvars")
    found = pathlib.Path(vc).with_name("vcvars32.bat") if vc else None
    return found if found and found.is_file() else None


def oplog():
    """core/oplog, the manual operator steps record (docs/DEFINITION_OF_DONE.md gate 2): the port tools' runs write it
    into the game folder's state/."""
    sys.path.insert(0, str(PIPELINE))
    from core import oplog as log  # noqa: E402  (no imports from the rest of core)
    return log


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
    widescreen = raw.get("widescreen", False)
    if not isinstance(widescreen, bool):
        raise ConfigError(f"game.json widescreen: {widescreen!r} "
                          "(true: the game widens its culling by port_widescreen())")
    if raw.get("texture_pack") is not None and not isinstance(raw["texture_pack"], str):
        raise ConfigError(f"game.json texture_pack: {raw['texture_pack']!r} (a folder, relative to game.json, that "
                          "holds a texture pack's index.txt: pvr/pvr_pack.c; it may be built later)")
    aica_arm = raw.get("aica_arm", False)
    if not isinstance(aica_arm, bool):
        raise ConfigError(f"game.json aica_arm: {aica_arm!r} (true: the game's own sound driver runs on the kit's "
                          "AICA ARM7, runtime/aica_arm.c)")
    k1 = raw.get("kamui1", {})
    if not isinstance(k1, dict) or set(k1) - {"list_word"}:
        raise ConfigError('game.json kamui1: {"list_word": the guest address of the Kamui 1 list table word}')
    kamui1 = {"list_word": _addr(k1["list_word"], "kamui1.list_word")} if "list_word" in k1 else {}
    # where the port's vertical blank comes (the SDK's render and vsync-wait entries): tools/lockstep_emu.lua samples
    # the emulator there, not at a vertical blank inside the frame's draw
    pcs = raw.get("lockstep_sample_pcs", [])
    if not isinstance(pcs, list) or len(pcs) > 8:
        raise ConfigError("game.json lockstep_sample_pcs: up to 8 guest addresses (the port's sample points)")
    pcs = [_addr(a, "lockstep_sample_pcs") for a in pcs]
    # where the game reads the RTC (the SDK's date read): a frame that reads it drives the port with what it read
    rtc = raw.get("lockstep_rtc_pcs", [])
    if not isinstance(rtc, list) or len(pcs) + len(rtc) > 8:
        raise ConfigError("game.json lockstep_rtc_pcs: the RTC reads' guest addresses, up to 8 with the sample PCs")
    rtc = [_addr(a, "lockstep_rtc_pcs") for a in rtc]
    # where a scene begins (the SDK's kmBeginScene, the port's lockstep_scene): frames then count as the port's do
    scene = raw.get("lockstep_scene_pcs", [])
    if not isinstance(scene, list) or len(pcs) + len(rtc) + len(scene) > 8:
        raise ConfigError("game.json lockstep_scene_pcs: the scene begins' guest addresses, up to 8 with the other PCs")
    scene = [_addr(a, "lockstep_scene_pcs") for a in scene]
    # translator C (tools/tx_port.py): {"dir": tools/dp_judge.py --sp-input's --out folder, "mode": "mix"|"only"};
    # absent, the port compiles readable C and logging stubs only
    tc = raw.get("translator_c")
    if tc is not None and (not isinstance(tc, dict) or set(tc) - {"dir", "mode"} or not tc.get("dir")
                           or tc.get("mode", "mix") not in ("mix", "only")):
        raise ConfigError('game.json translator_c: {"dir": the judged translator C folder, "mode": "mix" or "only"}')
    tc = {"dir": str((game_dir / tc["dir"]).resolve()), "mode": tc.get("mode", "mix")} if tc else None
    # mix mode: functions whose readable C gives way to translator C, the same function's machine code judged; for a
    # readable file that passes its judge yet loses an argument the raw harness has no slot for (e.g. one passed on
    # in r5 through a table call, which sync-names turns into `(void)cb;` or keeps out of the callers' raw-name calls)
    first = raw.get("tx_first", [])
    if not isinstance(first, list):
        raise ConfigError("game.json tx_first: the functions (guest addresses) whose readable C yields to translator C")
    first = [_addr(a, "tx_first") for a in first]
    # catalog entries that are data, not code (a disassembler's function start inside a table the game reads): no
    # dispatch row, so no guest code thunk is written over the data. {addr: evidence}
    not_code = raw.get("not_code", {})
    if not isinstance(not_code, dict) or not all(isinstance(v, str) and v for v in not_code.values()):
        raise ConfigError("game.json not_code: {guest address: the evidence that it is data, not a function}")
    not_code = {_addr(a, "not_code"): v for a, v in not_code.items()}
    sha =[str(h).lower() for h in raw.get("boot_sha256", [])]
    if not all(re.fullmatch(r"[0-9a-f]{64}", h) for h in sha):
        raise ConfigError("game.json boot_sha256: a list of the accepted boot files' SHA-256 (64 hex digits each)")
    return {"dir": game_dir, "name": str(raw["name"]), "id": gid, "boot_sha256": sha, "decomp": path("decomp"),
            "entry": {"name": entry["name"], "addr": _addr(entry.get("addr"), "entry.addr")},
            "image_base": _addr(raw["image_base"], "image_base"), "boot_file": boot, "disc": path("disc"),
            "fsca_table": path("fsca_table"), "must_work": path("must_work"),
            "sdk": [mods[m] for m in sdk], "sources": sources, "hardware": hardware, "wrap": wrap,
            "console": console, "arrows": arrows, "widescreen": widescreen, "texture_pack": path("texture_pack"), "aica_arm": aica_arm, "kamui1": kamui1,
            "lockstep_sample_pcs": pcs, "lockstep_rtc_pcs": rtc, "lockstep_scene_pcs": scene,
            "translator_c": tc, "tx_first": first, "not_code": not_code}


def c_string(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def port_config_h(cfg, own_crt=(), bu_image=False):
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
    lines.append(f"#define PORT_WIDESCREEN {int(bool(cfg.get('widescreen')))} /* 1: Hor+ in play runs (game.json) */")
    if cfg.get("texture_pack"):  # pvr/pvr_pack.c: the pack folder the renderer reads (game.json texture_pack)
        lines.append(f"#define PORT_TEXTURE_PACK {c_string(str(cfg['texture_pack']).replace(chr(92), '/'))}")
    if cfg.get("aica_arm"):  # runtime/aica_arm.c: the game's sound driver runs on the AICA's ARM7 (off: no ARM)
        lines.append("#define PORT_AICA_ARM 1")
    if cfg["console"]:
        lines += [f"#define PORT_CONSOLE_{k.upper()} 0x{v:x}" for k, v in cfg["console"].items()]
    if cfg.get("kamui1", {}).get("list_word"):  # sdk/kamui1_scene.c: where the game reads Kamui 1's list table word
        lines.append(f"#define PORT_KAMUI1_LIST_WORD 0x{cfg['kamui1']['list_word']}u")
    if own_crt:  # runtime/port_libm.c leaves these out: the game's own judged C defines them (gen_port.py OWN_CRT)
        lines += [f"#define PORT_OWN_CRT_{n} 1" for n in sorted(own_crt)]
    if bu_image:  # sdk/bu_vmu.c's file image functions take the game's BUS_BACKUPFILEHEADER (a game without it has none)
        lines.append("#define PORT_BU_FILE_IMAGE 1")
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


def port_files_cmake(cfg, readable_src, readable_files, readable_include, rd_rt_dir, defines=None, no_pch=(),
                     tx=None):
    """defines: {readable file stem: ["Name=Wrapper", ...]}, the game.json wraps of the functions that file calls.
    no_pch: the readable files compiled without the precompiled game.h (CMakeLists.txt). tx: (the translator C
    functions' tx_ADDR.c files, tc_call's glue files, the judged runtime's folder with tc_rt.h), or None."""
    lines = ["# Generated by port/tools/gen_port.py: what the kit's CMakeLists.txt compiles for this game."]
    lines += cmake_list("PORT_READABLE", [readable_src / f"{x}.c" for x in readable_files])
    lines += cmake_list("PORT_READABLE_NO_PCH", [readable_src / f"{x}.c" for x in no_pch])
    lines += [f'set(PORT_READABLE_INCLUDE "{readable_include.as_posix()}")', f'set(PORT_RD_RT "{rd_rt_dir.as_posix()}")']
    lines += cmake_list("PORT_SDK_SOURCES", cfg["sdk"]) + cmake_list("PORT_GAME_SOURCES", cfg["sources"])
    if tx:
        lines += cmake_list("PORT_TX", tx[0]) + cmake_list("PORT_TX_CALLS", tx[1])
        lines.append(f'set(PORT_TC_RT "{pathlib.Path(tx[2]).as_posix()}")')
    for x, defs in sorted((defines or {}).items()):
        lines.append(f'set_source_files_properties("{(readable_src / f"{x}.c").as_posix()}" PROPERTIES '
                     f'COMPILE_DEFINITIONS "{";".join(defs)}")')
    return "\n".join(lines) + "\n"

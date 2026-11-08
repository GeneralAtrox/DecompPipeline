"""The game's port configuration, game.json, in the game's port folder (see port/CONTRACT.md for every key).

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
import part_draw  # noqa: E402 (beside this file, as every importer of game_config has it on the path)

KIT = pathlib.Path(__file__).resolve().parents[1]  # DecompPipeline/port
PIPELINE = KIT.parent
SDK_DIR = KIT / "sdk"
ADDR = re.compile(r"[0-9a-f]{8}")
NAME = re.compile(r"[A-Za-z_]\w*")
REQUIRED = ("name", "decomp", "entry", "image_base")
KNOWN = set(REQUIRED) | {"boot_file", "disc", "fsca_table", "must_work", "sdk", "sources", "hardware", "wrap",
                         "console", "id", "boot_sha256", "arrows", "left_stick", "widescreen", "wide_cull", "part_draw", "interp_scene", "present_thread", "aica_arm", "aica_arm_fast", "aica_arm_native", "kamui1", "lockstep_sample_pcs",
                         "lockstep_rtc_pcs", "lockstep_scene_pcs", "translator_c", "tx_first", "spin_words", "not_code", "texture_pack", "vram_init", "sq_queue_only", "sq_fast", "game_o2",
                         "contract"}
CONSOLE = ("country", "language", "broadcast")
CONTRACT_DOC = KIT / "CONTRACT.md"  # its "Contract version: X.Y.Z" line is the kit's version, the only copy
_warned_no_contract = []


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


def contract_version(doc=CONTRACT_DOC):
    """The kit's game contract version (port/CONTRACT.md), as (major, minor, patch)."""
    m = re.search(r"^Contract version: (\d+)\.(\d+)\.(\d+)$", doc.read_text(encoding="utf-8"), re.M)
    if not m:
        raise ConfigError(f"{doc}: no 'Contract version: X.Y.Z' line")
    return tuple(int(x) for x in m.groups())


def check_contract(raw, game_dir, kit=None):
    """game.json "contract", the contract version the game was built against: another major version stops, a newer
    minor warns, a missing key warns once a run (port/CONTRACT.md "Versions"). Returns the version, or None."""
    kit = kit or contract_version()
    have = ".".join(map(str, kit))
    given = raw.get("contract")
    if given is None:
        if not _warned_no_contract:
            _warned_no_contract.append(1)
            print(f'warning: {game_dir / "game.json"} has no "contract" key: add "contract": "{have}" '
                  "(port/CONTRACT.md)", file=sys.stderr)
        return None
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(given))
    if not m:
        raise ConfigError(f'game.json contract: {given!r} (the contract version, like "{have}")')
    want = tuple(int(x) for x in m.groups())
    if want[0] != kit[0]:
        raise ConfigError(f"This game was built against contract {given}, but this kit has contract {have}. "
                          f"A different major version means the game needs updating first: read port/CONTRACT.md "
                          f"for what changed, then set game.json \"contract\" to \"{have}\".")
    if want[1] > kit[1]:
        print(f"warning: game.json contract {given} is newer than this kit's {have}: the game may use what this kit "
              "lacks (port/CONTRACT.md)", file=sys.stderr)
    return str(given)


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
    contract = check_contract(raw, game_dir)
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
    # wide_cull: the functions that drop what falls outside the viewport (four floats W, H, X, Y at one guest address);
    # gen_port wraps each so a full-screen viewport is the frame's rectangle for the call (port_wide_cull_begin)
    wide_cull = raw.get("wide_cull")
    if wide_cull is not None:
        if (not isinstance(wide_cull, dict) or set(wide_cull) != {"viewport", "functions"}
                or not isinstance(wide_cull["functions"], list) or not wide_cull["functions"]):
            raise ConfigError('game.json wide_cull: {"viewport": the guest address of its four floats W, H, X, Y, '
                              '"functions": [the guest addresses of the functions that cull against them]}')
        wide_cull = {"viewport": _addr(wide_cull["viewport"], "wide_cull.viewport"),
                     "functions": sorted({_addr(a, "wide_cull.functions") for a in wide_cull["functions"]})}
        both = sorted(set(wide_cull["functions"]) & (set(wrap) | set(hardware)))
        if both:
            raise ConfigError(f"game.json wide_cull: {', '.join(both)} also in wrap or hardware")
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
    left_stick = raw.get("left_stick", "off")
    if left_stick not in ("off", "choice", "on"):
        raise ConfigError(f'game.json left_stick: {left_stick!r} ("off": not offered; "choice": the key screen offers the left stick as movement, off until picked; "on": on until changed)')
    arrows = raw.get("arrows", "both")
    if arrows not in ("both", "stick"):
        raise ConfigError(f'game.json arrows: {arrows!r} ("both": the d-pad and the stick; "stick": the stick only)')
    widescreen = raw.get("widescreen", False)
    if not isinstance(widescreen, bool):
        raise ConfigError(f"game.json widescreen: {widescreen!r} "
                          "(true: the game widens its culling by port_widescreen())")
    present_thread = raw.get("present_thread", False)
    if not isinstance(present_thread, bool):
        raise ConfigError(f"game.json present_thread: {present_thread!r} is not true or false")
    interp_scene = raw.get("interp_scene", False)
    if not isinstance(interp_scene, bool):
        raise ConfigError(f"game.json interp_scene: {interp_scene!r} "
                          "(true: in-between frames blend the scenes, not the game's draw function: runtime/frame_extra.c)")
    if raw.get("texture_pack") is not None and not isinstance(raw["texture_pack"], str):
        raise ConfigError(f"game.json texture_pack: {raw['texture_pack']!r} (a folder, relative to game.json, that "
                          "holds a texture pack's index.txt: pvr/pvr_pack.c; it may be built later)")
    # bytes the game reads from texture memory that nothing it uploads covers (the console holds the frame buffers or
    # leftovers there, which the port keeps elsewhere): {byte offset in texture memory: hex bytes}
    vi = raw.get("vram_init", {})
    if not isinstance(vi, dict):
        raise ConfigError('game.json vram_init: {"byte offset in texture memory": "hex bytes"}')
    vram_init = []
    for a, h in vi.items():
        if not re.fullmatch(r"[0-9a-fA-F]{1,6}", str(a)):
            raise ConfigError(f"game.json vram_init: {a!r} is not a hex byte offset in the 8 MB texture memory")
        at = int(a, 16)
        if not isinstance(h, str) or not re.fullmatch(r"([0-9a-fA-F]{2})+", h) or at + len(h) // 2 > 0x800000:
            raise ConfigError(f"game.json vram_init {a}: hex bytes inside the 8 MB texture memory, got {h!r}")
        vram_init.append((at, h.lower()))
    sq_queue_only = raw.get("sq_queue_only", False)
    if not isinstance(sq_queue_only, bool):
        raise ConfigError(f"game.json sq_queue_only: {sq_queue_only!r} (true: a store into the store-queue window 0xe0000000.. stays in the "
                          "queue the translated code models, it does not also land in RAM)")
    sq_fast = raw.get("sq_fast", False)
    if not isinstance(sq_fast, bool):
        raise ConfigError(f"game.json sq_fast: {sq_fast!r} (true: the translated store-queue helpers a block at a time, tools/tx_sqfast.py)")
    game_o2 = raw.get("game_o2", False)
    if not isinstance(game_o2, bool):
        raise ConfigError(f"game.json game_o2: {game_o2!r} (true: the game's own C at /O2, not the judge's /O1; build.py)")
    aica_arm = raw.get("aica_arm", False)
    if not isinstance(aica_arm, bool):
        raise ConfigError(f"game.json aica_arm: {aica_arm!r} (true: the game's own sound driver runs on the kit's "
                          "AICA ARM7, runtime/aica_arm.c)")
    aica_arm_fast = raw.get("aica_arm_fast", False)
    if not isinstance(aica_arm_fast, bool) or (aica_arm_fast and not aica_arm):
        raise ConfigError(f"game.json aica_arm_fast: {aica_arm_fast!r} (true, with aica_arm: the ARM skips its idle scan "
                          "loops exactly, runtime/arm7_scan.c)")
    native_c = os.environ.get("DP_AICA_ARM_NATIVE") or raw.get("aica_arm_native")  # the env: a proof build without editing game.json
    if native_c is not None:
        native_c = (game_dir / str(native_c)).resolve() if aica_arm else None
        if native_c is None or not native_c.is_file():
            raise ConfigError(f"game.json aica_arm_native: {raw.get('aica_arm_native')!r} (with aica_arm: the C file "
                              "tools/arm7_translate.py made of the sound driver, relative to game.json; it must exist)")
    k1 = raw.get("kamui1", {})
    if not isinstance(k1, dict) or set(k1) - {"list_word", "direct_list"}:
        raise ConfigError('game.json kamui1: {"list_word": the guest address of the Kamui 1 list table word, '
                          '"direct_list": {"at": guest address, "bytes": size} the direct list own room}')
    kamui1 = {"list_word": _addr(k1["list_word"], "kamui1.list_word")} if "list_word" in k1 else {}
    if "direct_list" in k1:
        dl = k1["direct_list"]
        if not isinstance(dl, dict) or set(dl) != {"at", "bytes"}:
            raise ConfigError('game.json kamui1.direct_list: {"at": guest address (RAM the game leaves free), "bytes": room}')
        kamui1["direct_list"] = {"at": _addr(dl["at"], "kamui1.direct_list.at"), "bytes": _int(dl["bytes"], "kamui1.direct_list.bytes")}
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
    # tx_first, mix mode: functions whose readable C gives way to translator C (a readable file that passes its judge
    # yet loses an argument the raw harness has no slot for, e.g. one passed on in r5 through a table call). spin_words:
    # words the game spins on until an interrupt changes them, read through tx/tx_spin.c's check (CONTRACT.md)
    first, spin = raw.get("tx_first", []), raw.get("spin_words", [])
    if not (isinstance(first, list) and isinstance(spin, list) and len(spin) <= 4 and (tc or not spin)):
        raise ConfigError("game.json tx_first, spin_words (up to 4, needs translator_c): lists of guest addresses")
    first, spin = [_addr(a, "tx_first") for a in first], [_addr(a, "spin_words") for a in spin]
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
            "sdk": [mods[m] for m in sdk], "sources": sources, "hardware": hardware, "wrap": wrap, "wide_cull": wide_cull,
            "part_draw": part_draw.config(raw.get("part_draw"), wrap, hardware, wide_cull, ConfigError),
            "sq_fast": sq_fast, "game_o2": game_o2, "console": console, "arrows": arrows, "left_stick": left_stick, "widescreen": widescreen, "interp_scene": interp_scene, "present_thread": present_thread, "texture_pack": path("texture_pack"), "aica_arm": aica_arm, "aica_arm_fast": aica_arm_fast, "aica_arm_native": native_c, "kamui1": kamui1, "vram_init": vram_init, "sq_queue_only": sq_queue_only,
            "lockstep_sample_pcs": pcs, "lockstep_rtc_pcs": rtc, "lockstep_scene_pcs": scene,
            "translator_c": tc, "tx_first": first, "spin_words": spin, "not_code": not_code, "contract": contract}


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
    left_stick = ("off", "choice", "on").index(cfg.get("left_stick", "off"))
    lines.append(f"#define PORT_LEFT_STICK {left_stick} /* the left stick as movement: 0 not offered, 1 a choice, 2 on until changed (game.json left_stick) */")
    lines.append(f"#define PORT_WIDESCREEN {int(bool(cfg.get('widescreen')))} /* 1: Hor+ in play runs (game.json) */")
    lines += [f"#define PORT_INTERP_SCENE {int(bool(cfg.get('interp_scene')))} /* 1: in-between frames by scene blending (game.json) */", f"#define PORT_PART_DRAW {int(bool(cfg.get('part_draw')))} /* 1: those frames from object parts (game.json part_draw) */"]
    lines.append(f"#define PORT_PRESENT_THREAD {int(bool(cfg.get('present_thread')))} /* 1: those frames on a thread of their own (game.json) */")
    if cfg.get("vram_init"):  # pvr/pvr_state.c: texture memory starts with these bytes (game.json vram_init)
        lines.append("#define PORT_VRAM_INIT " + ", ".join(
            "{0x%xu, %s}" % (at, c_string(h)) for at, h in cfg["vram_init"]) + ", {0, 0}")
    if cfg.get("sq_queue_only"):  # runtime/tx/tx_rt.c: translated stores into the store-queue window do not land in RAM
        lines.append("#define PORT_SQ_QUEUE_ONLY 1")
    if cfg.get("texture_pack"):  # pvr/pvr_pack.c: the pack folder the renderer reads (game.json texture_pack)
        lines.append(f"#define PORT_TEXTURE_PACK {c_string(str(cfg['texture_pack']).replace(chr(92), '/'))}")
    if cfg.get("aica_arm"):  # runtime/aica_arm.c: the game's sound driver runs on the AICA's ARM7 (off: no ARM)
        lines.append("#define PORT_AICA_ARM 1")
        if cfg.get("aica_arm_native"):  # runtime/aica_arm.c runs the driver as the translated blocks (the interpreter stays selectable)
            lines.append("#define PORT_AICA_ARM_NATIVE 1")
        if cfg.get("aica_arm_fast"):  # runtime/arm7_scan.c on by default (the environment can still switch it)
            lines.append("#define PORT_AICA_ARM_FAST 1")
    if cfg["console"]:
        lines += [f"#define PORT_CONSOLE_{k.upper()} 0x{v:x}" for k, v in cfg["console"].items()]
    if cfg.get("kamui1", {}).get("list_word"):  # sdk/kamui1_scene.c: where the game reads Kamui 1's list table word
        lines.append(f"#define PORT_KAMUI1_LIST_WORD 0x{cfg['kamui1']['list_word']}u")
    if cfg.get("kamui1", {}).get("direct_list"):  # sdk/kamui1_scene.c: the direct list's own room (the game's vertex buffer is not it)
        dl = cfg["kamui1"]["direct_list"]
        lines += [f"#define PORT_KAMUI1_DIRECT_AT 0x{dl['at']}u", f"#define PORT_KAMUI1_DIRECT_BYTES {dl['bytes']}u"]
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
    lines += cmake_list("PORT_SDK_SOURCES", cfg["sdk"]) + cmake_list("PORT_GAME_SOURCES", cfg["sources"] + ([cfg["aica_arm_native"]] if cfg.get("aica_arm_native") else []))
    if tx:
        lines += cmake_list("PORT_TX", tx[0]) + cmake_list("PORT_TX_CALLS", tx[1])
        lines.append(f'set(PORT_TC_RT "{pathlib.Path(tx[2]).as_posix()}")')
    for x, defs in sorted((defines or {}).items()):
        lines.append(f'set_source_files_properties("{(readable_src / f"{x}.c").as_posix()}" PROPERTIES '
                     f'COMPILE_DEFINITIONS "{";".join(defs)}")')
    return "\n".join(lines) + "\n"

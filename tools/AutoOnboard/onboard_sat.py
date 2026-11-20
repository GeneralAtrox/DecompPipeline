"""The Saturn adapters of tools/AutoOnboard/autoonboard.py's stages (onboard_stages.STAGES): disc, extract, census, symbols and boot,
each calling the existing tools: targets/saturn_sh2/tools/satdisc.py (IP.BIN, file list, code scan, extraction),
tools/game_setup.py (the work RAM image through overlay_image.py, project.json, then decomp.py setup),
targets/saturn_sh2/tools/sgl_flirt.py (SGL/SBL library names; Robo Pit's matcher) and, for the boot try, the route Robo
Pit's own port/build.py takes: the translator over the whole image (targets/saturn_sh2/translator/tools/overlay.py),
port/tools/gen_saturn.py, port/tools/build_saturn.py, then the exe headless and bounded.

What a person chose by hand for each earlier Saturn game is chosen here from the disc:
- which code files go in the image and where (load_base: the address at which most of the file's own pointers land on
  function starts; the first-read file sits where IP.BIN says). The largest code file goes in first, then every other
  that overlaps nothing so far; one that overlaps is an overlay loaded at another time (its own workspace);
- code an imaged file carries for work RAM low (segments: Dragon Force's /0 holds, at file offset 0xaffa4, code that
  runs at 00210000, copied there at boot; its first call was a STUB). The boot try translates each such block at its
  run address, and every function of code loaded at run time is guarded (gen_saturn.py --guard): it runs only while
  its own bytes are in memory, never over another overlay's code;
- code files loaded at run time over other code (overlays: Dragon Force's battle DF.BIN and its FIELD0..7.BIN over /0 at
  06010000, READY0..7.BIN in work RAM low): the boot try translates each at its own address, one translation per file
  (gen_saturn.py --overlay), and the kit runs the one whose bytes are in memory. Without them the battle ran only in
  the SH-2 interpreter;
- the boot try's game.json "saturn" (entry: the first-read address, or the start of the image's main file when the
  first-read file is a loader it replaces; stacks from IP.BIN, else the kit's default; frame rate from the area code).

Nearest existing: tools/AutoOnboard/onboard_dc.py - the same stages for the Dreamcast, whose disc names one boot file at a fixed
address; a Saturn game loads overlays at addresses no header names, and its kit builds through gen_saturn/build_saturn
(port/tools/build.py is the Dreamcast kit's). Its census adapter is reused here.
"""
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile

import onboard_dc as DC
import onboard_stages as S

T = "saturn_sh2"
LOAD, RAM_END = 0x06000000, 0x06100000
STEP = 0x100  # load addresses on the discs seen are 256-byte aligned (0x06004000, 0x06006000, 0x06010000, 0x0602d000)
PTR_SAMPLE = 3000  # pointers voted per file: a clear winner, and a 768 KB file in about a second
MIN_VOTES, MARGIN = 30, 2.0  # a base needs this many pointers on function starts and this lead over the next base
HELD = 8  # the main program's pointers into another folder's file that make it a library the game calls (Saturn
#          Bomberman: 0KRNL.BIN holds 26 into /XBAND/H2HLIBUS.BIN's jump table at 06002f00 and calls 06002f04 at boot)
KIT_SP = 0x060FFC00  # the kit's stack when the game sets none (port/saturn/sat_call.c; Robo Pit's entry_sp)
PLAN = "saturn_plan.json"
LOW, LOW_END = 0x00200000, 0x00300000  # work RAM low
SEG_SAMPLE, SEG_TOP = 300, 5  # low-RAM pointers sampled to find a block's run address; candidates then counted in full
OVERLAY_JOBS = 3  # overlay translations at once in boot (the build box keeps 4 GB free)


def find_image(ctx):
    """--disc, else the one .cue in the project root (its track files are .bin too), else one .iso/.bin."""
    if ctx.disc:
        return S.find_image(ctx, S.CONSOLES["saturn"][2])
    cues = sorted(p for p in ctx.project.iterdir() if p.is_file() and p.suffix.lower() == ".cue")
    if len(cues) == 1:
        return cues[0]
    if len(cues) > 1:
        raise S.StageError(f"{len(cues)} cue sheets in {ctx.project}: {', '.join(p.name for p in cues)}",
                           ["one disc image, not several"], "pass --disc PATH to say which")
    return S.find_image(ctx, (".iso", ".bin"))


def starts(data):
    """Offsets of likely function starts: sts.l pr,@-r15 (0x4f22) or mov.l rN,@-r15 (0x2fN6)."""
    n = len(data) // 2
    w = struct.unpack(f">{n}H", data[:2 * n])
    return {2 * i for i, x in enumerate(w) if x == 0x4F22 or (x & 0xFF0F) == 0x2F06}


def load_base(data):
    """(base, votes, runner-up votes) of a code file in work RAM high: the 256-byte aligned address at which the most
    of its own 32-bit pointers land on a function start (SH-2 pools hold call targets). None without such pointers."""
    if not data or len(data) > RAM_END - LOAD:
        return None
    st = starts(data)
    ptrs = [x for x in struct.unpack(f">{len(data) // 4}I", data[:len(data) // 4 * 4]) if LOAD <= x < RAM_END]
    if not ptrs or not st:
        return None
    if len(ptrs) > PTR_SAMPLE:
        k = len(ptrs) / PTR_SAMPLE
        ptrs = [ptrs[int(i * k)] for i in range(PTR_SAMPLE)]
    votes = sorted(((sum(1 for p in ptrs if b <= p < b + len(data) and (p - b) in st), b)
                    for b in range(LOAD, RAM_END - len(data) + 1, STEP)), reverse=True)
    return votes[0][1], votes[0][0], votes[1][0] if len(votes) > 1 else 0


def _longs(data, lo, hi):
    """The file's aligned 32-bit words in [lo, hi), the cache-through mirror (bit 29) folded onto the RAM."""
    return [x for x in (y & ~0x20000000 for y in struct.unpack(f">{len(data) // 4}I", data[:len(data) // 4 * 4]))
            if lo <= x < hi]


def segment(data, lo=LOW, hi=LOW_END):
    """(delta, votes, runner-up votes) for code inside a file that runs at another address (file offset + delta), found
    as load_base finds a whole file's base: the delta at which most of the file's pointers into [lo, hi) land on function
    starts in the file. None without such pointers. Dragon Force's /0: 0x0016005c (680 of 2217 low pointers)."""
    st = starts(data)
    ptrs = _longs(data, lo, hi)
    if not ptrs or not st:
        return None
    sample, sts = sorted(set(ptrs[::max(1, len(ptrs) // SEG_SAMPLE)])), sorted(st)
    votes = {}
    for p in sample:
        for s in sts:
            votes[p - s] = votes.get(p - s, 0) + 1
    cand = sorted(votes, key=lambda d: -votes[d])[:SEG_TOP]
    full = sorted(((sum(1 for p in ptrs if (p - d) in st), d) for d in cand), reverse=True)
    return full[0][1], full[0][0], full[1][0] if len(full) > 1 else 0


def segments(image, datas):
    """The work RAM low blocks the image's files carry: [{path, offset, size, base, votes, runner, entries, calls}].
    The block runs from its first pointed-at function to the first function after its last that the file's own code
    (at the file's base) is pointed at, or the file's end. entries: its functions the image's files point at (the
    translator's list; it follows their calls and tables). calls: functions of the image's files the block points at
    (catalog_overrides.entry: a census of the image alone does not see that code called from the block)."""
    out, gst = [], {g["path"]: starts(datas.get(g["path"], b"")) for g in image}
    for f in image:
        data = datas.get(f["path"])
        got = segment(data) if data else None
        if not got or not accepted(got[1], got[2]):
            continue
        d, st = got[0], gst[f["path"]]
        hits = sorted({p - d for p in _longs(data, LOW, LOW_END) if (p - d) in st})
        own = sorted({p - f["base"] for p in _longs(data, f["base"], f["base"] + len(data)) if (p - f["base"]) in st})
        off = hits[0]
        end = next((o for o in own if o > hits[-1]), len(data))
        lo, hi = off + d, end + d
        entries = {p for g in image for p in _longs(datas.get(g["path"], b""), lo, hi) if (p - d) in st}
        inside = lambda x, g: g["base"] <= x < g["base"] + g["size"]  # noqa: E731
        calls = {p for p in _longs(data[off:end], LOAD, RAM_END) for g in image if inside(p, g)
                 and (p - g["base"]) in gst[g["path"]]}
        out.append({"path": f["path"], "offset": off, "size": end - off, "base": lo, "votes": got[1], "runner": got[2],
                    "entries": [f"{a:08x}" for a in sorted(entries)], "calls": [f"{a:08x}" for a in sorted(calls)]})
    return out


def build_suffix(pointers, overlays):
    """Boot's build folder suffix: --pointers off and --overlays off each get their own C and exe (onboard_np_novl), so
    the default build is kept beside them."""
    return ("" if pointers else "_np") + ("" if overlays else "_novl")


def boot_overlay_files(plan, datas, on=True):
    """overlay_files() when boot translates overlays; none with --overlays off. Dragon Force (2026-11-17): every build
    with overlay translations drew the strategy map as a flat teal plane, so the launcher build leaves the overlays to
    the SH-2 interpreter (slow, but the map is right) until the wrong translation is found."""
    return overlay_files(plan, datas) if on else []


def overlay_files(plan, datas):
    """The code files loaded at run time over other code, each to be translated on its own (Boot, gen_saturn.py
    --overlay): [{path, tag, base, size, entries}]. A left-out file in work RAM high that overlaps an image file (Dragon
    Force: DF.BIN, the battle, and FIELD0..7.BIN over /0 at 06010000), and a work RAM low file (plan "low_ram") whose
    own pointers place it, the whole file at one address (READY0..7.BIN). entries: the file's start and every pointer
    of its own that lands on a function start in it (overlay.py follows their calls and tables). tag: the file's stem,
    letters and digits only, made unique."""
    out, tags = [], set()

    def add(path, base, data):
        st = starts(data)
        ents = {base} | {x for x in _longs(data, base, base + len(data)) if (x - base) in st}
        tag = re.sub(r"[^A-Za-z0-9]", "", path.rsplit("/", 1)[-1].rsplit(".", 1)[0]) or "OVL"
        while tag in tags:
            tag += "x"
        tags.add(tag)
        out.append({"path": path, "tag": tag, "base": base, "size": len(data), "entries": [f"{a:08x}" for a in sorted(ents)]})

    for f in plan.get("left_out", ()):
        data = datas.get(f["path"])
        if data and LOAD <= f["base"] < RAM_END and any(
                g["base"] < f["base"] + f["size"] and f["base"] < g["base"] + g["size"] for g in plan["image"]):
            add(f["path"], f["base"], data)
    for path in plan.get("low_ram", ()):
        data = datas.get(path)
        got = segment(data) if data else None
        # the best base, without accepted()'s 2x lead: a wrong one costs only a translation, since the guard never lets it
        # run over other bytes (READY0..7 all vote 002a2c60, 990..1125 against runners up to 919)
        if got and got[1] >= MIN_VOTES and got[1] > got[2] and LOW <= got[0] and got[0] + len(data) <= LOW_END:
            add(path, got[0], data)
    return sorted(out, key=lambda o: (o["base"], o["path"]))


def overlay_paths(plan):
    """The files overlay_files may take, to read from game-data."""
    return [f["path"] for f in plan.get("left_out", ())] + list(plan.get("low_ram", ()))


def accepted(votes, runner):
    return votes >= MIN_VOTES and votes >= MARGIN * max(runner, 1)


def identity(disc):
    """Name tokens the disc gives itself: the ISO volume id and the stems of its copyright, abstract and bibliographic
    files (sector 16). The game's own folder carries one of them (Saturn Bomberman: S_BOMBERMAN, BOMSS_CP.TXT -> BOMSS)."""
    try:
        pvd = disc.sector(16)
    except (OSError, ValueError, IndexError):
        return set()
    if pvd[1:6] != b"CD001":
        return set()
    raw = b" ".join((pvd[40:72], pvd[702:739], pvd[739:776], pvd[776:813])).decode("latin-1").upper()
    return {t for t in re.split(r"[^A-Z0-9]+", raw.split(";")[0]) if len(t) >= 3 and t not in ("TXT", "BIN")}


def owned(path, ident):
    """True when the file's folder (or, at the root, its stem) is named like the disc itself."""
    parts = [x.upper() for x in path.strip("/").split("/")]
    names = parts[:-1] or [re.split(r"[^A-Z0-9]", parts[-1])[0]]
    return any(n.startswith(t) or t.startswith(n) for n in names if len(n) >= 3 for t in ident)


def compose(placed, ident=(), held=None):
    """(in the image, left out with why). Files that share load addresses are loaded at different times; the main
    program is taken first: the one in the disc's own folder (identity), then the one naming most other disc files,
    then the largest. Each other file goes in only if it overlaps none taken so far and, once the main program is in
    the disc's own folder, sits in that folder or the root (another folder is an add-on's, e.g. /XBAND), unless the
    main program holds HELD or more pointers into it (held(main, file)): a library it calls, such as Saturn
    Bomberman's /XBAND/H2HLIBUS.BIN, whose table at 06002f04 0KRNL.BIN calls at boot (a call to 0 without it)."""
    held = held or (lambda _main, _f: 0)
    image, out = [], []
    home = lambda f: f["path"].rsplit("/", 1)[0]
    rank = lambda f: (not owned(f["path"], ident), -f.get("names", 0), -f["size"], f["path"])
    for f in sorted(placed, key=rank):
        lo, hi = f["base"], f["base"] + f["size"]
        clash = next((g for g in image if lo < g["base"] + g["size"] and g["base"] < hi), None)
        main = image[0] if image else None
        n = held(main, f) if main else 0
        if (main and owned(main["path"], ident) and home(f) not in ("", home(main)) and not owned(f["path"], ident)
                and n < HELD):
            out.append(dict(f, why=f"in {home(f)}, not the game's folder {home(main) or '/'}: another program's file"))
        elif clash:
            out.append(dict(f, why=f"overlaps {clash['path']} ({clash['base']:08x}-{clash['base'] + clash['size']:08x})"
                                   ": loaded at another time, a workspace of its own"))
        else:
            image.append(dict(f, held=n) if n >= HELD else f)
    return sorted(image, key=lambda f: f["base"]), out


def entry_of(plan):
    """(entry address, why): where the BIOS starts the image, or the main file's start that the loader jumps to."""
    first = next((f for f in plan["image"] if f["first"]), None)
    if first:
        return first["base"], "the first-read address (IP.BIN): where the BIOS starts the game"
    main = max(plan["image"], key=lambda f: f["size"])
    held = plan.get("first_holds", {}).get(f"{main['base']:08x}")
    return main["base"], (f"the start of {main['path']}, which replaces the first-read file "
                          f"{plan['ip']['first_read_file']} in memory; the first-read file "
                          + ("holds this address (its jump target)" if held else "does not hold this address: check"))


def boot_dir(plan):
    """The folder of the program the image starts at when it replaces the first-read loader ("/BOMSS"), else None. The
    loader left the game's file system in that folder (the boot ROM's hand-over, port/saturn/sat_cd.c): Saturn Bomberman's
    0KRNL.BIN looks for PROGRAM.BIN, which is in /BOMSS, and took its error exit when its file system started at the root."""
    if any(f["first"] for f in plan["image"]):
        return None
    return max(plan["image"], key=lambda f: f["size"])["path"].rsplit("/", 1)[0] or None


def starts_of(plan):
    """Addresses that must be functions whatever analysis finds: the entry and each image file's start. With KRNL
    alone the image began at the entry and Ghidra took it as one; with H2HLIBUS below it (06002f00) nothing marked
    06006000, whose first words are a jump stub (mov.l @(1,pc),r3; jmp @r3), so the boot's first call had no function."""
    out = {entry_of(plan)[0]} | {f["base"] for f in plan["image"]}
    out |= {int(a, 16) for s in plan.get("segments", ()) for a in s["calls"]}  # functions only a low-RAM block calls
    return sorted(f"{a:08x}" for a in out)


def _a(x):
    return x.lower().removeprefix("0x").rjust(8, "0")


def mark_starts(ws, plan):
    """Adds starts_of(plan) to project.json catalog_overrides.entry (TcOverride makes each a function). Returns the
    ones added."""
    return add_entries(ws, starts_of(plan))


def add_entries(ws, addrs):
    """Adds addresses to project.json catalog_overrides.entry (the plan's starts, the boot's fallback seeds). Returns the
    ones added."""
    pj = ws / "project.json"
    cfg = json.loads(pj.read_text(encoding="utf-8"))
    over = cfg.get("catalog_overrides") or {}
    have = {_a(a) for a in over.get("entry", [])}
    add = [_a(a) for a in addrs if _a(a) not in have]
    if add:
        over["entry"] = sorted(have | set(add))
        cfg["catalog_overrides"] = over
        pj.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8", newline="\n")
    return add


def missing_starts(ws):
    """catalog_overrides.entry addresses state/catalog.json lacks: the catalog predates them. [] without a catalog."""
    try:
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        cat = {_a(f["addr"]) for f in json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]}
    except (OSError, ValueError, KeyError):
        return []
    return [a for a in (cfg.get("catalog_overrides") or {}).get("entry", []) if _a(a) not in cat]


SDL3_TRIPLET = "x86-windows"


def sdl3_ready(project, exe_dir, triplet=SDL3_TRIPLET):
    """Puts an installed SDL3 beside the exe so build_saturn never runs vcpkg install there: that waits on the vcpkg
    root's lock, which another card's build may hold (20 minutes on build box A, 2026-11-16). Takes the first found: the
    exe's own vcpkg_installed, this project's cache (.work/onboard/sdl3), an earlier build under port/build or under a
    moved-aside */port/build. Returns where it came from, or None (build_saturn installs it, once)."""
    lib = pathlib.Path(triplet) / "lib" / "SDL3.lib"
    if (exe_dir / "vcpkg_installed" / lib).is_file():
        return exe_dir / "vcpkg_installed"
    cands = [project / S.STATE_DIR / "sdl3" / "vcpkg_installed", *sorted(project.glob("port/build/*/vcpkg_installed")),
             *sorted(project.glob("*/port/build/*/vcpkg_installed"))]
    src = next((c for c in cands if (c / lib).is_file()), None)
    if src:
        exe_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, exe_dir / "vcpkg_installed", dirs_exist_ok=True)
    return src


def sdl3_keep(project, exe_dir, triplet=SDL3_TRIPLET):
    """After a build: the exe's SDL3 into the project's cache, so a fresh build folder reuses it (sdl3_ready)."""
    src, dst = exe_dir / "vcpkg_installed", project / S.STATE_DIR / "sdl3" / "vcpkg_installed"
    if (src / triplet / "lib" / "SDL3.lib").is_file() and not (dst / triplet / "lib" / "SDL3.lib").is_file():
        shutil.copytree(src, dst, dirs_exist_ok=True)


EXE_REL = "port/build/onboard/game.exe"  # Boot's build; image.bin is copied beside it
START_BAT = r"""@echo off
rem {name}: written by AutoOnboard (tools/AutoOnboard/autoonboard.py) (project stage). Core shape (Too Human's core-move Start Game.bat): the data
rem folder data\ holds logs, settings.txt and saves; no --log and no per-game save variable. Saturn has no --game-dir:
rem the game's files come from the disc image (the one .cue beside this file). The build folder and the arguments are the
rem ones the boot stage proved (its verdict's "launch"); arguments given to this file come after them (a check adds --headless).
cd /d "%~dp0"
if not exist "%~dp0{exe}" (
  echo {name} is not built yet: run tools/AutoOnboard/autoonboard.py through its boot stage first.
  pause
  exit /b 1
)
set "CUE="
for %%c in ("%~dp0*.cue") do if not defined CUE set "CUE=%%~fc"
{disc}if not defined CUE (
  echo No .cue disc image beside Start Game.bat.
  pause
  exit /b 1
)
start "" "%~dp0{exe}" --image "%~dp0{image}" --disc "%CUE%" --data-directory "%~dp0data"{args} %*
timeout /t 6 >nul
"""
ROOT_BAT = r"""@echo off
rem {name}: written by AutoOnboard (tools/AutoOnboard/autoonboard.py). Starts the build the tool made in {rel}\ (game.exe, image.bin and the
rem disc are there); that folder's Start Game.bat does the rest.
if not exist "%~dp0{rel}\Start Game.bat" (
  echo {name} is not built yet: run tools/AutoOnboard/autoonboard.py through its boot stage first.
  pause
  exit /b 1
)
call "%~dp0{rel}\Start Game.bat" %*
"""
CATALOGUE_ENTRY = "catalogue_entry.json"


# The boot's 20 s headless run on a fixed clock makes 1,500 to 9,000 vertical blanks on a build box, even a busy one. A run
# that stalls early passed while only 0 failed: Saturn Bomberman at b6130d3af stopped at 6 (its task longjmp ran in the
# interpreter, 2026-11-17) and the boot stage still called the headless run good.
HEADLESS_MIN_VB = 600


def ran_enough(why, vb):
    """The headless boot run counts only when it ended with an exit line and reached HEADLESS_MIN_VB vertical blanks."""
    return bool(why) and vb >= HEADLESS_MIN_VB


def game_root(project):
    """The game's own folder: the nearest folder above the project holding LOCATIONS.md and GameFiles (Saturn Bomberman's
    project is <game>\\.work\\auto). None when the project is that folder, or nothing above it is (a remote copy)."""
    for up in list(project.parents)[:4]:
        if (up / "LOCATIONS.md").is_file() and (up / "GameFiles").is_dir():
            return up
    return None


GAME_SRC = "game_src"  # the project's copy of the game folder's port/game/*.c and *.h: the game's own native code for the kit
GAME_SRC_FILES = ("*.c", "*.h")  # a game's own header goes too: build_saturn.py puts the folder on the include path


def game_src_files(folder):
    """The game's own code in a folder: its .c and .h files, and the fonts its code loads with their licences (port/game/fonts,
    kept flat in game_src: S.GAME_FONTS), sorted by name."""
    return sorted([f for g in GAME_SRC_FILES for f in folder.glob(g)] + [f for g in S.GAME_FONTS for f in (folder / "fonts").glob(g)],
                  key=lambda f: f.name)


def sync_game_src(project):
    """Replaces <project>/game_src with the *.c and *.h of the game folder's port/game and its fonts/ (a deleted file goes). Nothing when no game
    folder is above the project (a remote copy: onboard_remote.sync sent them) or it has no port/game. Returns the names."""
    root = game_root(project)
    src = root / "port" / "game" if root else None
    if not src or not src.is_dir():
        return []
    dst = project / GAME_SRC
    shutil.rmtree(dst, ignore_errors=True)
    files = game_src_files(src)
    if files:
        dst.mkdir(parents=True)
        for f in files:
            shutil.copyfile(f, dst / f.name)
    return [f.name for f in files]


def crlf(text):
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii", "replace")


def write_bat(bat, text):
    bat.write_bytes(crlf(text))


def slug(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def camel(name):
    """The game's name as the port repositories spell it: Saturn Bomberman -> SaturnBomberman, Robo Pit -> RoboPit
    (GeneralAtrox/SaturnBomberman_Saturn_NativePort, DragonForce_Saturn_NativePort, RoboPit_Saturn_NativePort)."""
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", name))


def catalogue_entry(project, name):
    """The game's entry for Phangrade's Base/data/catalogue.json, named from the game (not the workspace folder: Dragon
    Force's first one said folder "auto", repository "auto_Saturn_NativePort"). Base starts sourceRoot/localExecutable
    itself with no arguments (Base src/platform.cpp play, content_storage.cpp launch_arguments), and the Saturn exe needs
    --image, --disc and the proven flags: so localExecutable is the game folder's Start Game.bat, which passes them. No
    `port` object (only once a published release passes scripts\\check-port.ps1) and no `profile` (scripts/game.ps1 has
    no Saturn build recipe; dcpipeline builds the Dreamcast kit)."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    return {"id": slug(name), "title": name, "platform": "Saturn",
            "short": ("".join(w[0] for w in words)[:3] or name[:2]).upper(), "folder": camel(name),
            "repository": f"{camel(name)}_Saturn_NativePort", "executable": "game.exe",
            "localExecutable": "Start Game.bat", "logs": "logs"}


DISC_NOTE = "disc.txt"  # the disc image this project was onboarded from on this machine (--disc), for Start Game.bat


def local_disc(project, disc=None):
    """The disc image Start Game.bat falls back on when no .cue sits beside it: --disc when given (kept in
    .work/onboard/disc.txt), else the one kept. None when neither (or it is gone)."""
    note = project / S.STATE_DIR / DISC_NOTE
    if disc and pathlib.Path(disc).suffix.lower() == ".cue" and pathlib.Path(disc).is_file():
        note.parent.mkdir(parents=True, exist_ok=True)
        if not note.is_file() or note.read_text(encoding="utf-8").strip() != str(pathlib.Path(disc).resolve()):
            note.write_text(str(pathlib.Path(disc).resolve()) + "\n", encoding="utf-8")
    try:
        got = pathlib.Path(note.read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return got if got.is_file() else None


# The proven launch is kept beside the build it names: autoonboard.py drops boot.json on any later run that starts at or
# before boot (drop_from, --only translate included), and the bats were then rewritten with no arguments. Saturn
# Bomberman, 2026-11-17: a translate run at 00:36 rewrote Start Game.bat without --fallback on; the user's first battle
# then hit two STUBs and had no enemies.
LAUNCH_FILE = "launch.json"


def launch_of(project):
    """The build folder and arguments the last ok boot proved (boot.json "launch": dir, files, args), else Boot's default
    build with no arguments. Dragon Force, 2026-11-16: the bat named port\\build\\onboard and no flags while the build was
    proven on build box A with --fallback on and copied here by hand; the windowed launch nobody had run crashed."""
    try:
        v = json.loads((project / S.STATE_DIR / "boot.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        v = {}
    got = v.get("launch") if v.get("status") == "ok" else None
    if isinstance(got, dict) and got.get("dir"):
        return got
    if v.get("status") == "stale" and v.get("was") == "ok" and isinstance(v.get("launch"), dict) and v["launch"].get("dir") \
            and (project / v["launch"]["dir"] / "game.exe").is_file():
        return v["launch"]  # an earlier stage ran again: the build boot proved still runs until a new boot proves another
    # no ok boot verdict: the launch kept beside its build (keep_launch), newest first, while its game.exe is there
    kept = sorted((project / "port" / "build").glob(f"*/{LAUNCH_FILE}"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in kept:
        try:
            got = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(got, dict) and got.get("dir") and (project / got["dir"] / "game.exe").is_file():
            return got
    return {"dir": EXE_REL.rsplit("/", 1)[0], "args": []}


def keep_launch(project, launch):
    """Writes the proven launch beside its build (LAUNCH_FILE) when the build folder is here and the file differs."""
    d = project / launch["dir"]
    if not d.is_dir():
        return
    text = json.dumps({k: launch[k] for k in ("dir", "args", "files") if k in launch}, indent=1) + "\n"
    f = d / LAUNCH_FILE
    if not f.is_file() or f.read_text(encoding="utf-8") != text:
        f.write_text(text, encoding="utf-8", newline="\n")


# a root Start Game.bat is ours when it names the tool: the old entry point (bats written before the rename, and
# stand-ins) or AutoOnboard's
LAUNCHER_MARKERS = (b"tools/onboard.py", b"tools/AutoOnboard/autoonboard.py")


def root_target(text):
    """The project folder (relative, backslashes) a tool-made root Start Game.bat calls, or None."""
    m = re.search(rb'call "%~dp0(.+?)\\Start Game\.bat"', text or b"")
    return m.group(1).decode("ascii", "replace") if m else None


def launcher_files(project, name, disc=None):
    """Start Game.bat (Core shape, Saturn's exe and arguments: the build and flags boot proved, launch_of) and the
    launcher's catalogue entry. A bat is written when missing or when the one there is tool-made (it names
    tools/AutoOnboard/autoonboard.py) and differs, so a person's edits stay. The game folder's root bat is left alone
    when it calls another project (the live one: a second onboarding of the same game must not repoint the user's
    launcher). Returns what was made (and a "kept ..." note for a root bat left alone)."""
    made = []
    bat = project / "Start Game.bat"
    launch = launch_of(project)
    if launch.get("args"):
        keep_launch(project, launch)
    exe = (launch["dir"].strip("/") + "/game.exe").replace("/", "\\")
    cue = local_disc(project, disc)
    text = START_BAT.format(name=name, exe=exe, image=exe.rsplit("\\", 1)[0] + "\\image.bin",
                            args="".join(" " + str(x) for x in launch.get("args", ())),
                            disc=f'if not defined CUE if exist "{cue}" set "CUE={cue}"\n' if cue else "")
    old = bat.read_bytes() if bat.exists() else None
    if old is None or (any(m in old for m in LAUNCHER_MARKERS) and old != crlf(text)):
        write_bat(bat, text)
        made.append("Start Game.bat")
    # the user, 2026-11-16: "Make sure Start Game.bat is put at the root SaturnBomberman folder, it shouldnt be hidden
    # away". Written when missing, or when the one there is tool-made or a stand-in for it (it names tools/AutoOnboard/autoonboard.py)
    root = game_root(project)
    if root:
        top = root / "Start Game.bat"
        rel = str(project.relative_to(root)).replace("/", "\\")
        text = ROOT_BAT.format(name=name, rel=rel)
        old = top.read_bytes() if top.exists() else None
        other = root_target(old)
        if old is not None and other and other.lower() != rel.lower():
            made.append(f"kept {top}: it starts {other}, another project (the live one stays until a person points it here)")
        elif old is None or (any(m in old for m in LAUNCHER_MARKERS) and old != crlf(text)):
            write_bat(top, text)
            made.append(str(top))
    ent = project / S.STATE_DIR / CATALOGUE_ENTRY
    want = json.dumps(catalogue_entry(project, name), indent=1) + "\n"
    if not ent.is_file() or ent.read_text(encoding="utf-8") != want:  # a fix to the naming reaches an old record too
        ent.parent.mkdir(parents=True, exist_ok=True)
        ent.write_text(want, encoding="utf-8", newline="\n")
        made.append(f"{S.STATE_DIR}/{CATALOGUE_ENTRY}")
    return made


class Project(S.Scaffold):
    def local(self, ctx):
        """What autoonboard.py --host writes here, in the local project, on every run: the launcher files. A remote run's
        project and boot stages write them only in the remote copy, where no game folder is above it (2026-11-16, Dragon
        Force: no Start Game.bat at the game's root because every run after fb9794f94 ran on build box A)."""
        return launcher_files(ctx.project, ctx.name, getattr(ctx, "disc", None)) + [f"{GAME_SRC}/{n}" for n in sync_game_src(ctx.project)]

    def calls(self, ctx):
        return S.Scaffold.calls(self, ctx) + ["write Start Game.bat (Core shape) and the launcher's catalogue entry"]

    def run(self, ctx):
        got = S.Scaffold.run(self, ctx)
        made = launcher_files(ctx.project, ctx.name, getattr(ctx, "disc", None))
        got["paths"].update({"start": str(ctx.project / "Start Game.bat"),
                             "catalogue entry": str(ctx.project / S.STATE_DIR / CATALOGUE_ENTRY)})
        if made:
            got["numbers"]["made"] += len(made)
            got["message"] = "made " + ", ".join(made) + ("" if got["message"] == "all there already"
                                                          else "; " + got["message"])
        got["next"] = [x.replace(f"<Game>_{S.CONSOLES['saturn'][1]}_NativePort", f"{camel(ctx.name)}_Saturn_NativePort")
                       for x in got.get("next", [])]
        return got


# ------------------------------------------------------------------------------------------------ the machine check
SIG_ZIP = "saturnsigs100.zip"
SIG_URL = "https://web.archive.org/web/20070107192409id_/http://www.cyberwarriorx.com:80/files/saturnsigs100.zip"
SIG_SHA256 = "b0e7cf6da8838d60d191b455136ab6eb1b6be941810bf12bfac7b9ef332e5af2"  # targets/saturn_sh2/tools/sgl_flirt.py
SATURN_REF = pathlib.Path("Integrations") / "SegaSaturn" / "reference"


def _ghidra_ok(p):
    return bool(p) and (pathlib.Path(p) / "support" / "analyzeHeadless.bat").is_file()


def _java_ok(p):
    return bool(p) and (pathlib.Path(p) / "bin" / "java.exe").is_file()


def _vcvars_ok(p):
    return bool(p) and pathlib.Path(p).is_file() and pathlib.Path(p).with_name("vcvars32.bat").is_file()


def _vcpkg_ok(p):
    return bool(p) and (pathlib.Path(p) / "scripts" / "buildsystems" / "vcpkg.cmake").is_file()


def _known(globs):
    """The paths the patterns name; a wildcard may sit in any part (Visual Studio's edition folder)."""
    import glob
    out = []
    for g in globs:
        out += [pathlib.Path(x) for x in sorted(glob.glob(str(g)), reverse=True)] if any(c in str(g) for c in "*?")             else [pathlib.Path(g)]
    return out


VS = "C:/Program Files/Microsoft Visual Studio/2022/*"
MACHINE_KEYS = (  # key, check, known places, what it is, what supplies it
    ("ghidra_home", _ghidra_ok, ("C:/ghidra*", "C:/Tools/ghidra*", "D:/ghidra*"), "Ghidra (the census)",
     "Ghidra 11 (support/analyzeHeadless.bat inside)"),
    # Build box A, 2026-11-19: a new checkout got ghidra_home but not java_home, and the census stopped on it
    ("java_home", _java_ok, (os.environ.get("JAVA_HOME") or "C:/no-java-home", "C:/Program Files/Eclipse Adoptium/jdk-*",
                             "C:/Program Files/Java/jdk-*", "C:/Program Files/Microsoft/jdk-*", "C:/Tools/jdk-*"),
     "a Java 17+ JDK (Ghidra runs on it)", "a JDK 17 or later (bin/java.exe inside)"),
    ("vcvars", _vcvars_ok, (f"{VS}/VC/Auxiliary/Build/vcvars64.bat",), "Visual Studio 2022 C++ (the 32-bit kit build)",
     "Visual Studio 2022 with the C++ workload (vcvars64.bat beside vcvars32.bat)"),
    ("vcpkg", _vcpkg_ok, ("C:/vcpkg", "C:/src/vcpkg", "C:/tools/vcpkg", "D:/vcpkg", f"{VS}/VC/vcpkg"),
     "vcpkg (SDL3 for the kit)", "a vcpkg checkout (scripts/buildsystems/vcpkg.cmake inside)"),
)
ENV_OVER = {"vcvars": "VCVARS32", "vcpkg": "VCPKG_ROOT"}  # port/tools/game_config.py takes these before config.local.json


def machine_settings(tool, project=None):
    """(keys to set {key: value}, [(key, what, supply)] still missing, notes) for this machine's Saturn build settings: a
    key whose config value works stays; else another checkout's value (onboard_stages.sibling_configs) or a known place.
    vcpkg is not needed while the project holds SDL3 from an earlier build (sdl3_ready)."""
    import os
    sys.path.insert(0, str(tool))
    from core import toolcfg
    try:
        cfg = toolcfg.load(tool)
    except Exception:  # an unreadable config.json is the census's own error to give; here: nothing set
        cfg = {}
    sets, gone, notes = {}, [], []
    others = []
    for f in S.sibling_configs(tool):
        try:
            others.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    for key, ok, known, what, supply in MACHINE_KEYS:
        env = ENV_OVER.get(key)
        if (env and ok(os.environ.get(env))) or ok(cfg.get(key)):
            continue
        if key == "vcpkg" and project is not None and any((c / SDL3_TRIPLET / "lib" / "SDL3.lib").is_file() for c in
                                                          [project / S.STATE_DIR / "sdl3" / "vcpkg_installed",
                                                           *project.glob("port/build/*/vcpkg_installed")]):
            notes.append("vcpkg: not set, not needed (SDL3 is in this project from an earlier build)")
            continue
        got = next(((str(v), f"from {f}") for f, c in others for v in [c.get(key)] if ok(v)), None)
        got = got or next(((str(k), "a known place") for k in _known(known) if ok(k)), None)
        if got:
            sets[key] = got[0]
            notes.append(f"{key} = {got[0]} ({got[1]})")
        else:
            gone.append((key, what, supply))
    return sets, gone, notes


def sig_target(ctx):
    """Where Saturn FLIRT signatures go when none are found: the shared Integrations\\SegaSaturn\\reference\\symbols above
    the project, else the project's own reference/ (a build box's remote copy, which onboard_symbols reads first)."""
    for up in ctx.project.resolve().parents:
        if (up / SATURN_REF).is_dir():
            return up / SATURN_REF / "symbols"
    return ctx.project / "reference"


def sig_zips(ctx):
    """saturnsigs100.zip copies on this machine (by name, any of the places signatures are kept), checked by sha256."""
    places = [sig_target(ctx), sig_target(ctx).parent, ctx.project, ctx.project / "reference", ctx.tool / ".work"]
    out = []
    for d in places:
        for z in (d.glob(SIG_ZIP) if d.is_dir() else ()):
            if hashlib.sha256(z.read_bytes()).hexdigest() == SIG_SHA256:
                out.append(z)
    return out


class Machine(S.Built):
    """The first check of a Saturn onboarding, on the machine that builds: this checkout's config.local.json has working
    paths for Ghidra, 32-bit MSVC and vcpkg (Saturn Bomberman's build box A clone needed "vcpkg" set by hand, from Toy
    Commander's copy), and the FLIRT signature library the symbols stage reads is there (Integrations\\SegaSaturn\\
    reference\\symbols was copied by hand from Robo Pit's). A missing key is taken from another checkout or a known
    place and written; a missing library is unpacked from saturnsigs100.zip when a checked copy is here; else the
    verdict says exactly what to set."""
    tools = ()

    def calls(self, ctx):
        return [f"config.local.json: {', '.join(k for k, *_ in MACHINE_KEYS)} (missing ones from another checkout's "
                "config.local.json or a known place)", "the .sig files of the symbol folders (onboard_symbols.folders); "
                f"else {SIG_ZIP} (sha256 {SIG_SHA256[:12]}...) unpacked into {SATURN_REF / 'symbols'}"]

    def run(self, ctx):
        import onboard_symbols as SYM
        sets, gone, notes = machine_settings(ctx.tool, ctx.project)
        if sets:
            S.set_config_local(ctx.tool, sets)
        try:
            dirs = [d for d, _ in SYM.folders(ctx)]
        except S.StageError:
            dirs = []
        sigs = sorted({q for d in dirs for q in d.rglob("*.sig")})
        made_sigs = []
        if not sigs:
            zips = sig_zips(ctx)
            if zips:
                dest = sig_target(ctx)
                dest.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zips[0]) as z:
                    for n in z.namelist():
                        if n.lower().endswith(".sig") and "/" in n and n.split("/")[-2].upper() == "SH3":
                            (dest / n.split("/")[-1].lower()).write_bytes(z.read(n))
                            made_sigs.append(n.split("/")[-1].lower())
                notes.append(f"{len(made_sigs)} signature files unpacked from {zips[0]} into {dest}")
            else:
                gone.append(("signatures", "the Saturn FLIRT signature library (SGL/SBL names, the symbols stage)",
                             f"{SIG_ZIP} from {SIG_URL} (sha256 {SIG_SHA256}): put it in {sig_target(ctx)}, or its "
                             f"SH3/*.sig files there"))
        nums = {"settings written": len(sets), "signature files": len(sigs) or len(made_sigs),
                "missing": len(gone)}
        if gone:
            cfgf = ctx.tool / "config.local.json"
            raise S.StageError("this machine lacks " + "; ".join(f"{w} ({k})" for k, w, _ in gone)
                               + ("; " + "; ".join(notes) if notes else ""),
                               [f'"{k}" in {cfgf}' if k != "signatures" else w for k, w, _ in gone],
                               " | ".join(f'set "{k}": "<{s}>" in {cfgf}' if k != "signatures" else s for k, _, s in gone),
                               numbers=nums)
        return {"numbers": nums, "paths": {"config": str(ctx.tool / "config.local.json"),
                                           "signatures": str(dirs[0] if dirs else sig_target(ctx))},
                "message": "build settings and signatures in place" + ("; " + "; ".join(notes) if notes else ""),
                "next": []}


# ---------------------------------------------------------------------------------------- the launcher's catalogue
ENTRY_LINE = re.compile(r'^([ \t]*)(\{"id":.*\})(,?)[ \t]*\r?$', re.M)  # Base's catalogue.json is CRLF


def put_in_catalogue(path, ent):
    """Adds `ent` to Phangrade's catalogue (one compact line per game, as the file keeps them) or, when an entry of that
    id is there, adds the keys it lacks; a value already there is never changed (a person's). Every other byte stays.
    Returns (what was done, [keys whose values differ from the tool's])."""
    raw = path.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")
    data = json.loads(text)
    have = next((g for g in data.get("games", ()) if g.get("id") == ent["id"]), None)
    lines = list(ENTRY_LINE.finditer(text))
    if len(lines) != len(data.get("games", ())):
        raise S.StageError(f"{path}: not one entry a line ({len(lines)} lines, {len(data.get('games', ()))} games)",
                           ["the catalogue's layout"], f"add {ent} to {path} by hand")
    if have is None:
        last = lines[-1]
        new = (text[:last.end(2)] + "," + nl + last.group(1) + json.dumps(ent, separators=(",", ":"), ensure_ascii=False)
               + text[last.end(2) + len(last.group(3)):])
        done, differ = "added", []
    else:
        m = next(m for m in lines if json.loads(m.group(2)).get("id") == ent["id"])
        old = json.loads(m.group(2))
        add = {k: v for k, v in ent.items() if k not in old}
        differ = [k for k, v in ent.items() if k in old and old[k] != v]
        if not add:
            return "there already", differ
        new = text[:m.start(2)] + json.dumps({**old, **add}, separators=(",", ":"), ensure_ascii=False) + text[m.end(2):]
        done = "completed with " + ", ".join(add)
    got = json.loads(new).get("games", [])
    if len(got) != len(data["games"]) + (have is None) or not any(g.get("id") == ent["id"] for g in got):
        raise S.StageError(f"{path}: the edit did not come out as one more entry", ["a catalogue edit"],
                           f"add {ent} to {path} by hand")
    path.write_bytes(new.encode("utf-8"))
    return done, differ


class Register(S.Built):
    """The game in Phangrade's launcher: its entry in Base/data/catalogue.json (on the user's laptop; Base builds the file
    into Phangrade.exe, so a rebuild of Base shows it), named from the game and starting the game folder's Start Game.bat.
    Never a dist folder, never a commit or a release (publish-release.ps1 is the only way out)."""
    tools = ()

    def calls(self, ctx):
        return ["Base/data/catalogue.json: the game's entry added, or its missing keys filled (onboard_stages.phangrade_catalogue)",
                f"{S.STATE_DIR}/{CATALOGUE_ENTRY}: the same entry, kept with the project"]

    def run(self, ctx):
        ent = catalogue_entry(ctx.project, ctx.name)
        cat = S.phangrade_catalogue(ctx)
        done, differ = put_in_catalogue(cat, ent)
        launcher_files(ctx.project, ctx.name, getattr(ctx, "disc", None))
        root = game_root(ctx.project)
        top = (root / "Start Game.bat") if root else None
        target = root_target(top.read_bytes()) if top and top.is_file() else None
        rel = str(ctx.project.relative_to(root)).replace("/", "\\") if root else None
        nxt = [f"Rebuild Phangrade's launcher (Base) to show {ctx.name}: the catalogue is built into Phangrade.exe. "
               "In the launcher, Locate source folder points it at the game's folder"]
        if not top or not top.is_file():
            nxt.append(f"No game folder (LOCATIONS.md and GameFiles) above {ctx.project}: the entry's Start Game.bat is "
                       f"this project's own ({ctx.project / 'Start Game.bat'})")
        elif target and rel and target.lower() != rel.lower():
            nxt.append(f"{top} starts {target}, not this project ({rel}): the launcher keeps that build")
        if differ:
            nxt.append(f"The catalogue's {', '.join(differ)} differ from the tool's and were kept")
        return {"numbers": {"catalogue": done, "kept values": len(differ)},
                "paths": {"catalogue": str(cat), "entry": str(ctx.project / S.STATE_DIR / CATALOGUE_ENTRY),
                          "start": str(top if top and top.is_file() else ctx.project / "Start Game.bat")},
                "message": f"{ent['id']} in {cat}: {done}" + (f"; kept the catalogue's {', '.join(differ)}" if differ else ""),
                "next": nxt}


class Disc(S.Built):
    tools = ("targets/saturn_sh2/tools/satdisc.py", "tools/game_setup.py")

    def calls(self, ctx):
        return ["find the .cue (or one .iso/.bin) in the project root", "tools/game_setup.detect", "satdisc.py info + list",
                "satdisc.py scan -> .work/onboard/disc_scan.tsv", "load_base per code file -> .work/onboard/" + PLAN]

    def run(self, ctx):
        image = find_image(ctx)
        S.import_tools(ctx, T)
        import game_setup
        import satdisc
        try:
            target = game_setup.detect(image)
        except SystemExit as e:
            raise S.StageError(str(e), [f"a Saturn boot header in {image}"], "the right disc image")
        if target != T:
            raise S.StageError(f"{image} is a {target} disc; this project is saturn", ["a Saturn disc"],
                               "the right disc image, or onboard it as its own console's project")
        state = ctx.project / S.STATE_DIR
        tsv = state / "disc_scan.tsv"
        try:
            disc = satdisc.Disc(str(image))
            try:
                ip, files = disc.ip(), disc.walk()
                ip = {k: v.split("\0")[0].strip() for k, v in ip.items()}  # IP.BIN fields: space or NUL padded
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    satdisc.scan(disc, tsv)
                rows = satdisc.scan_rows(disc)
                first, placed, low, refused = ip["first_read_file"], [], [], []
                bodies, ptrs, datas = {}, {}, {}
                ident = identity(disc)
                basenames = {p.rsplit("/", 1)[-1].encode() for p, _l, _s in files}
                for path, lba, size, score, hi, lo, _libs in rows:
                    if not (satdisc.is_code((path, lba, size, score, hi, lo)) or path == first):
                        continue
                    if lo > hi and path != first:
                        low.append(path)
                        continue
                    data = disc.read(lba, size)
                    datas[path] = data
                    got = load_base(data)
                    ptrs[path] = [x for x in (y & ~0x20000000 for y in struct.unpack(f">{size // 4}I", data[:size // 4 * 4]))
                                  if LOAD <= x < RAM_END]  # the cache-through mirror (0x26...) is the same RAM
                    rec = {"path": path, "size": size, "first": path == first, "code_per_kb": round(score, 2),
                           "owned": owned(path, ident), "names": sum(1 for b in basenames if len(b) > 4 and b in data)}
                    if path == first:
                        base = int(ip["first_read_addr"], 16)
                        rec.update(base=base, votes=got[1] if got else 0, agrees=bool(got and got[0] == base))
                        bodies[path] = data
                        placed.append(rec)
                    elif got and accepted(got[1], got[2]):
                        rec.update(base=got[0], votes=got[1], runner=got[2])
                        placed.append(rec)
                    else:
                        refused.append(dict(rec, votes=got[1] if got else 0, runner=got[2] if got else 0))
            finally:
                disc.f.close()
        except (OSError, ValueError, IndexError, SystemExit) as e:
            raise S.StageError(f"{image}: unreadable ({type(e).__name__}: {e})",
                               [f"a complete image ({image.name} and every track file it names)"], "the disc image again")
        S.log_path(ctx, "disc").write_text(out.getvalue(), encoding="utf-8")
        if not placed:
            raise S.StageError(f"{image}: no code file in work RAM high could be placed", ["a load address"],
                               "satdisc.py scan, then game_setup.py setup --file NAME@ADDR by hand")
        inside = lambda x, f: f["base"] <= x < f["base"] + f["size"]  # noqa: E731
        img, left = compose(placed, ident, lambda m, f: sum(1 for x in ptrs.get(m["path"], ()) if inside(x, f) and not inside(x, m)))
        fdata = next(iter(bodies.values()), b"")
        longs = set(struct.unpack(f">{len(fdata) // 4}I", fdata[:len(fdata) // 4 * 4])) if fdata else set()
        plan = {"image_file": str(image), "ip": ip, "identity": sorted(ident), "image": img, "left_out": left, "low_ram": low,
                "not_placed": [{"path": r["path"], "votes": r["votes"], "runner": r["runner"]} for r in refused],
                "first_holds": {f"{f['base']:08x}": (f["base"] in longs or (f["base"] | 0x20000000) in longs)
                                for f in img},
                "segments": segments(img, datas)}
        (state / PLAN).write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8", newline="\n")
        entry, why = entry_of(plan)
        shown = ", ".join(f"{f['path']}@{f['base']:08x}" for f in img)
        return {"numbers": {"title": ip["title"], "product": ip["product"], "version": ip["version"],
                            "area": ip["area"], "files": len(files), "MB": round(sum(s for _, _, s in files) / 2**20, 1),
                            "code files": len(placed) + len(low) + len(refused), "in the image": len(img),
                            "left out (overlap)": len(left), "not placed": len(refused), "low RAM": len(low),
                            "low RAM blocks": len(plan["segments"]),
                            "entry": f"{entry:08x}"},
                "paths": {"image": str(image), "scan": str(tsv), "plan": str(state / PLAN)},
                "message": f"{ip['title']} ({ip['product']} {ip['version']}): image {shown}; entry {entry:08x}, {why}"
                + "".join(f"; {s['path']}+{s['offset']:x} runs at {s['base']:08x} ({s['size']} bytes, "
                          f"{len(s['entries'])} entries)" for s in plan["segments"]),
                "next": [f"{f['path']} at {f['base']:08x} is left out ({f['why']})" for f in left]
                + ([f"{len(low)} code file(s) for work RAM low (e.g. {low[0]}) are not imaged"] if low else [])}


class Extract(S.Built):
    tools = ("tools/game_setup.py", "targets/saturn_sh2/tools/satdisc.py", "targets/saturn_sh2/tools/overlay_image.py",
             "tools/bounded.py")

    def setup_args(self, ctx, plan):
        a = ["tools/game_setup.py", "setup", plan["image_file"], S.ws_of(ctx), "--target", T, "--no-ghidra",
             "--name", ctx.name]
        if not any(f["first"] for f in plan["image"]):
            a.append("--no-first")
        for f in plan["image"]:
            if not f["first"]:
                a += ["--file", f"{f['path'].lstrip('/')}@{f['base']:08x}"]
        return a

    def calls(self, ctx):
        return ["tools/game_setup.py setup IMAGE <project>/decomp --target saturn_sh2 --no-ghidra --name NAME "
                "[--no-first] --file NAME@ADDR ... (from the disc stage's plan)",
                "targets/saturn_sh2/tools/satdisc.py extract IMAGE <project>/game-data --all"]

    def run(self, ctx):
        state = ctx.project / S.STATE_DIR
        plan = json.loads((state / PLAN).read_text(encoding="utf-8"))
        image = pathlib.Path(plan["image_file"])
        S.import_tools(ctx, T)
        import game_setup
        import overlay_image
        import satdisc
        disc = satdisc.Disc(str(image))
        try:
            files = disc.walk()
            where = {p: (lba, size) for p, lba, size in files}
            parts = [(f["base"], disc.read(*where[f["path"]]), f["path"], False) for f in plan["image"]]
        finally:
            disc.f.close()
        with contextlib.redirect_stderr(io.StringIO()):
            want_sha = game_setup.sha256(overlay_image.build(parts)[0])
        ws, reused = S.ws_of(ctx), []
        if (ws / "project.json").exists():
            have = json.loads((ws / "project.json").read_text(encoding="utf-8")).get("binary_sha256")
            if have != want_sha:
                raise S.StageError(f"{ws / 'project.json'} is of another image ({have}, the plan's is {want_sha})",
                                   [f"a workspace of this plan at {ws}"], f"move {ws} away, then run again")
            reused.append("workspace")
        elif (ws / "binary").exists():
            raise S.StageError(f"{ws / 'binary'} exists without project.json: an earlier extract stopped part way",
                               [f"{ws / 'binary'} removed"], f"delete {ws / 'binary'} (that extract only), then run again")
        else:
            rc, tail = S.run_tool(ctx, "extract", self.setup_args(ctx, plan), 900)
            if rc:
                raise S.StageError(f"game_setup.py setup exited {rc}: {' | '.join(tail[-3:])}",
                                   ["a workspace from game_setup.py"], f"read {S.log_path(ctx, 'extract')}")
        mark_starts(ws, plan)
        total = sum(s for _, _, s in files)
        if total > S.ASSET_LIMIT:
            raise S.StageError(f"the disc's files are {total / 2**30:.1f} GB, over the 5 GB output limit",
                               ["a smaller extraction"], "the user decides what to extract (CLAUDE.md, 5 GB rule)")
        data = ctx.project / "game-data"
        skip = data / ".skipped.txt"
        have = sum(1 for q in data.rglob("*") if q.is_file() and q != skip) if data.exists() else 0
        skipped = len(skip.read_text(encoding="utf-8").split()) if skip.is_file() else 0
        if data.exists() and skip.is_file() and have + skipped == len(files):
            reused.append("game-data")
        elif data.exists():
            raise S.StageError(f"{data} holds {have} of the disc's {len(files)} files: an earlier extract stopped "
                               "part way", [f"{data} removed"], f"delete {data} (disc data only), then run again")
        else:
            rc, tail = S.run_tool(ctx, "extract", [f"targets/{T}/tools/satdisc.py", "extract", image, data, "--all"],
                                  1800)
            if rc:
                raise S.StageError(f"satdisc.py extract exited {rc}: {' | '.join(tail[-3:])}", ["the disc's files"],
                                   f"read {S.log_path(ctx, 'extract')}")
            have = sum(1 for q in data.rglob("*") if q.is_file() and q != skip)
            skipped = len(skip.read_text(encoding="utf-8").split()) if skip.is_file() else 0
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        return {"numbers": {"image bytes": sum(len(p[1]) for p in parts), "game_ranges": len(cfg.get("game_ranges", [])),
                            "image_gaps": len(cfg.get("image_gaps", [])), "asset files": have,
                            "XA streams skipped": skipped, "asset MB": round(total / 2**20, 1)},
                "paths": {"workspace": str(ws), "binary": str(ws / cfg["binary"]), "game-data": str(data)},
                "message": ("reused " + ", ".join(reused) if reused else "extracted")
                + f"; image sha256 {want_sha[:12]}, game ranges "
                + ", ".join("-".join(r) for r in cfg.get("game_ranges", [])),
                "next": []}


class Census(DC.Census):
    """The Dreamcast census (tools/game_setup.py ghidra, then the decode check) with the Saturn's own wording."""

    def run(self, ctx):
        ws = S.ws_of(ctx)
        pf = ctx.project / S.STATE_DIR / PLAN
        if pf.is_file() and (ws / "project.json").is_file():  # a workspace extracted before the starts were marked
            mark_starts(ws, json.loads(pf.read_text(encoding="utf-8")))
        seeds = read_seeds(ws)  # code the boot's SH-2 interpreter ran: functions the catalog must have (fallback seeds)
        if seeds:
            add_entries(ws, seeds)
        miss = missing_starts(ws)
        if miss:  # the catalog predates catalog_overrides.entry: build the Ghidra project again (every stamp changes)
            rc, tail = S.run_tool(ctx, "census", ["decomp.py", "--project", ws, "setup", "--force"], 5400,
                                  why=f"onboard census of {ctx.name}: the catalog again with start(s) {' '.join(miss)}",
                                  produces=ws / "state" / "catalog.json")
            if rc or missing_starts(ws):
                raise S.StageError(f"decomp.py setup --force exited {rc}; still not functions: "
                                   f"{' '.join(missing_starts(ws))}: {' | '.join(tail[-3:])}",
                                   ["a catalog with the marked starts"], f"read {S.log_path(ctx, 'census')}")
            tf = ws / "state" / "game_setup.json"
            rec = json.loads(tf.read_text(encoding="utf-8")) if tf.is_file() else {}
            rec.pop("decode_check", None)  # the decode check runs again on the new catalog
            tf.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8", newline="\n")
        got = DC.Census.run(self, ctx)
        n = got["numbers"]
        got["message"] = (f"found by analysis: {n['functions']} functions, {n['game range']} in the image's game ranges. "
                          "A floor: code reached only through tables of pointers is not found (Saturn Bomberman's "
                          "hand-made workspace added entries in project.json catalog_overrides)")
        got["next"] = ["The symbols stage names the SGL/SBL library functions (targets/saturn_sh2/tools/sgl_flirt.py)",
                       "tools/data_funcs.py on the workspace for code addresses held in data tables"]
        return got


SIG_SOURCE = ("CyberWarriorX's saturnsigs100.zip (SH3/*.sig: SGL 2.0a, 2.1, 3.00, 3.02j, SBL 6.0, 6.01); the Wayback "
              "URL and sha256 are in targets/saturn_sh2/tools/sgl_flirt.py. Put the .sig files in the console's shared "
              "folder Integrations\\SegaSaturn\\reference\\symbols")
FLIRT_LINE = re.compile(r"^(\w+): .* matched at (\d+) listed starts, (\d+) addresses in all", re.M)


class Symbols(S.Built):
    """SGL/SBL library names by FLIRT signatures over the image (no Ghidra), at the census catalog's starts and at
    every 2-byte address; unique names kept in .work/onboard/symbols/names.json (git-ignored)."""
    tools = (f"targets/{T}/tools/sgl_flirt.py", "tools/AutoOnboard/onboard_symbols.py", "tools/jobreg.py", "tools/bounded.py")

    def calls(self, ctx):
        return ["the .sig files of the symbol folders (onboard_symbols.folders: reference/, config.local.json "
                "\"symbol_sets\", Integrations\\SegaSaturn\\reference\\symbols)",
                f"tools/jobreg.py run -- tools/bounded.py 1800 -- targets/{T}/tools/sgl_flirt.py IMAGE BASE FUNCS "
                "state/function_bodies.txt .work/onboard/symbols/tc_fid.tsv SIG...",
                "unique names -> .work/onboard/symbols/names.json"]

    def run(self, ctx):
        import onboard_symbols as SYM
        if ctx.verdicts.get("census", {}).get("status") != "ok":
            raise S.StageError("symbols needs the census (its catalog's function starts)", ["an ok census verdict"],
                               "autoonboard.py --from census")
        dirs = SYM.folders(ctx)
        sigs = sorted({q for d, _ in dirs for q in d.rglob("*.sig")})
        if not sigs:
            raise S.StageError(f"no .sig files in {', '.join(str(d) for d, _ in dirs)}",
                               [f"Saturn FLIRT signatures in {dirs[0][0]}"], SIG_SOURCE)
        ws = S.ws_of(ctx)
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        out = ctx.project / S.STATE_DIR / "symbols"
        out.mkdir(parents=True, exist_ok=True)
        funcs = out / "funcs.txt"
        cat = json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]
        funcs.write_text("".join(f["addr"] + "\n" for f in cat), encoding="utf-8")
        tsv = out / "tc_fid.tsv"
        tsv.unlink(missing_ok=True)
        rc, tail = S.run_tool(ctx, "symbols", [f"targets/{T}/tools/sgl_flirt.py", ws / cfg["binary"],
                                               str(cfg.get("load_address") or "06000000"), funcs,
                                               ws / "state" / "function_bodies.txt", tsv, *sigs], 1800,
                              why=f"onboard symbols of {ctx.name}: FLIRT {len(sigs)} Saturn signature files",
                              produces=tsv)
        if rc or not tsv.is_file():
            raise S.StageError(f"sgl_flirt.py exited {rc}: {' | '.join(tail[-3:])}", ["the FLIRT result"],
                               f"read {S.log_path(ctx, 'symbols')}")
        lines = tsv.read_text(encoding="utf-8").splitlines()
        rows = [dict(zip(lines[0].split("\t"), ln.split("\t"))) for ln in lines[1:]] if lines else []
        per = {m.group(1): (int(m.group(2)), int(m.group(3)))
               for m in FLIRT_LINE.finditer(S.log_path(ctx, "symbols").read_text(encoding="utf-8", errors="replace"))}
        best = max(per, key=lambda k: per[k][0]) if per else None
        names = {r["addr"]: {"name": r["fid_names"], "library": r["library_object"], "kind": r["kind"],
                             "sets": r["release"]} for r in rows if r["unique"] == "yes" and r["kind"] != "data"}
        (out / "names.json").write_text(json.dumps(names, indent=0) + "\n", encoding="utf-8")
        kinds = {}
        for r in rows:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        return {"numbers": {"signature files": len(sigs), "named addresses": len(rows),
                            "catalog functions named": kinds.get("function", 0),
                            "inside a catalog body": kinds.get("inside_function", 0),
                            "outside the catalog": kinds.get("outside_catalogue", 0), "data": kinds.get("data", 0),
                            "unique": len(names), "ambiguous": sum(1 for r in rows if r["unique"] != "yes"),
                            "best set": f"{best} ({per[best][0]} starts)" if best else "none"},
                "paths": {"names": str(out / "names.json"), "tc_fid": str(tsv)},
                "message": (f"{len(rows)} addresses named by {len(sigs)} FLIRT sets, {kinds.get('function', 0)} of them "
                            f"catalog functions; closest library build {best}" if rows else
                            f"no SGL/SBL library function matched ({len(sigs)} sets): the game links neither, or another "
                            "release of them"),
                "next": ([f"Starts the catalog lacks ({kinds['outside_catalogue']}) can go in project.json "
                          "catalog_overrides.entry" if kinds.get("outside_catalogue") else ""]
                         + [f"Apply the names where the readable step takes them ({tsv.name} is tools/sdk_classify.py's "
                            "tc_fid.tsv form)"]) if rows else []}


# ---------------------------------------------------------------------------------------------------------- the boot try
BOOT_TX_S, BOOT_BUILD_S, BOOT_RUN_S = 2700, 1800, 20
# The launch run: the exe as Start Game.bat starts it (a window, the real clock, the arguments the bat will pass) for
# LAUNCH_RUN_S. The headless fixed-clock runs never make a window, and a window makes the thread a fiber
# (port/base/host_modal.cpp): Dragon Force's launch crashed at vertical blank 8 in SwitchToFiber(NULL) when its slave CPU
# started (2026-11-16) while every headless run, real clock too, passed. A crash, or fewer than LAUNCH_MIN_VB blanks,
# fails the stage, so no unproven build reaches the launcher. The window opens on the build box only (boot is heavy).
LAUNCH_RUN_S, LAUNCH_MIN_VB = 15, 60
SIMPLIFY = "shift,const,copy,params,struct"  # Robo Pit's port/build.py: no dead-code removal (live-ins after calls)
# --pointers: the census's list plus the code it lacks that the listed code reaches (call targets, code addresses held as
# data, such as handler tables). Ghidra's census misses code reached only through a table: Saturn Bomberman's KRNL calls
# 060342f8, 06034322, 0603412e, 06034194 and 0603415a through its table at 060b5364, and each was a STUB at run time.
# --call-sites: each call's return address for the kit (gen_saturn.py turns it into the PR the callee is handed, as the
# SH-2 sets it): the frames a save state resumes (port/saturn/sat_call.c). Comments in the C, so the code is the same.
TX_FLAGS = ("--simplify", SIMPLIFY, "--pointers", "--call-sites")
TX_FOUND = ("call", "pointer", "table")
# --fallback on (the default here): the kit runs code with no translated function in its SH-2 interpreter and counts it
# (port/saturn/sat_interp.c) instead of a STUB that returns 0 and stalls the game. Each address is a seed: the next boot
# translates it (an entry-only line added to the list overlay.py reads) and a census run takes it as catalog_overrides.entry,
# so the count goes to zero. Five of Saturn Bomberman's eight onboarding rounds (2026-11-16) were such a stall.
FALLBACK_ROW = re.compile(r"^fallback: ([0-9a-f]{8}) entries (\d+) instructions (\d+)( nested)? from (.+)$", re.M)
FALLBACKS = re.compile(r"^fallbacks: (\d+) address", re.M)
SEEDS = "fallback_seeds.txt"
SEEDS_HEAD = ("# fallback seeds: code the Saturn kit's SH-2 interpreter ran in a boot run (port/saturn/sat_interp.c), one "
              "address a line: entries, the translated call chain at its first entry\n")


def fallback_facts(text):
    """(count, or None when the kit did not report one; rows (addr, entries, instructions, nested, chain)) from port.log."""
    m = FALLBACKS.search(text)
    rows = [(r[0], int(r[1]), int(r[2]), bool(r[3]), "<".join(x.strip() for x in r[4].split(" < ") if x.strip() != "00000000"))
            for r in FALLBACK_ROW.findall(text)]
    return (int(m.group(1)) if m else None), rows


def seeds_path(ws):
    return ws / "state" / SEEDS


def read_seeds(ws):
    """The fallback addresses earlier boot runs logged (decomp/state/fallback_seeds.txt), as 8-digit hex."""
    f = seeds_path(ws)
    if not f.is_file():
        return []
    return [_a(ln.split()[0]) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def add_seeds(ws, rows):
    """Adds a run's fallback rows to the seeds file (kept across runs, one line per address). Returns the new ones."""
    have = set(read_seeds(ws))
    new = [r for r in rows if _a(r[0]) not in have]
    if new:
        f = seeds_path(ws)
        f.parent.mkdir(parents=True, exist_ok=True)
        old = f.read_text(encoding="utf-8") if f.is_file() else SEEDS_HEAD
        f.write_text(old + "".join(f"{_a(r[0])} {r[1]} {r[4] or '-'}\n" for r in new), encoding="utf-8", newline="\n")
    return [_a(r[0]) for r in new]


# Calls to untranslated code the game made while it was played here (Start Game.bat runs, scene dumps): STUB lines
# (--fallback off) and fallback rows. Saturn Bomberman, 2026-11-17: the user's first battle called 0606b78e and 0606b734
# (KRNL code the census missed; RAM held the image's own bytes there) and got no enemies, but boot's seeds came only from
# its own 15 s headless run, which never reaches a battle. These go to the boot as seeds (autoonboard.py --seeds).
STUB_CALL = re.compile(r"STUB #\d+ \((?:master|slave)\): call to ([0-9a-f]{8}) has no translated function")


def play_seeds(project):
    """Addresses of untranslated code in work RAM high that play logs here name: data/logs/*.log, the scene dumps' log
    warnings and their replay logs. Sorted 8-digit hex."""
    data, found, texts = project / "data", set(), []
    for f in [*data.glob("logs/*.log"), *data.glob("scene_dumps/*_replay/port.log")]:
        try:
            texts.append(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    for f in data.glob("scene_dumps/*.json"):
        try:
            texts.append("\n".join(json.loads(f.read_text(encoding="utf-8")).get("host", {}).get("log_warnings", ())))
        except (OSError, ValueError, AttributeError):
            continue
    for t in texts:
        found.update(STUB_CALL.findall(t))
        found.update(r[0] for r in FALLBACK_ROW.findall(t))
    return sorted(a for a in found if LOAD <= int(a, 16) < RAM_END)


def take_seeds(ws, addrs):
    """Adds addresses reached in play (play_seeds, autoonboard.py --seeds) to the seeds file. Returns the new ones."""
    return add_seeds(ws, [(a, 0, 0, False, "play") for a in sorted(set(_a(x) for x in addrs if x))])


def seeded_bodies(ws, bodies):
    """The function list plus an entry-only line per seed it lacks (overlay.py takes an entry alone and finds its extent),
    at decomp/state/boot_bodies.txt; the list itself when no seed is missing from it. Only seeds in the image's range
    (work RAM high): a work RAM low block takes its own (segment_entries)."""
    seeds = [a for a in read_seeds(ws) if LOAD <= int(a, 16) < RAM_END]
    text = bodies.read_text(encoding="utf-8")
    have = {int(ln.split()[0], 16) for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}
    add = [a for a in seeds if int(a, 16) not in have]
    if not add:
        return bodies
    out = ws / "state" / "boot_bodies.txt"
    out.write_text(text.rstrip("\n") + "\n# fallback seeds (state/fallback_seeds.txt)\n" + "".join(f"{a}\n" for a in add),
                   encoding="utf-8", newline="\n")
    return out


def segment_entries(ws, seg):
    """A work RAM low block's entries (the disc plan's) plus the seeds that fall inside it (Dragon Force's 00213ba0 ran in
    the interpreter on every boot: a seed nothing translated, since seeds went only to the image's list)."""
    lo, hi = seg["base"], seg["base"] + seg["size"]
    have = {_a(a) for a in seg["entries"]}
    return list(seg["entries"]) + sorted(a for a in read_seeds(ws) if lo <= int(a, 16) < hi and a not in have)


def translatable(addr, plan):
    """True when a boot can translate code at addr: in the image (work RAM high) or in a work RAM low block it carries.
    A call to 00000000 (Dragon Force's one stub) or into memory no file of the image covers is not."""
    a = int(addr, 16)
    return a != 0 and (LOAD <= a < RAM_END or any(s["base"] <= a < s["base"] + s["size"] for s in plan.get("segments", ())))


def run_seeds(ws, plan, text):
    """The seeds a boot run's log gives (its fallback rows and its STUB calls), added to the seeds file. Returns the new
    ones a boot can translate: the seed loop goes round again while there are any."""
    _, rows = fallback_facts(text)
    rows = list({r[0]: r for r in rows}.values())  # one row an address (several logs joined)
    rows = rows + [(a, 0, 0, False, "stub") for a in sorted(set(STUB_CALL.findall(text)) - {r[0] for r in rows})]
    return add_seeds(ws, [r for r in rows if translatable(_a(r[0]), plan)])


# The seed loop (gap closed 2026-11-19): a boot run's interpreter fallbacks and STUB calls become seeds, the image is
# translated again with them and booted again, until a run gives no new translatable address or SEED_ROUNDS rounds are
# done. Before, each seed waited for the next boot run, and Saturn Bomberman's 0606b78e/0606b734 and Dragon Force's 16
# entries 0601651a-0601653a were fed back by hand.
SEED_ROUNDS = 4


def seed_rounds(round_, limit=SEED_ROUNDS, deadline=None, clock=time.time):
    """round_(n) builds and runs round n and returns (its result, the new seeds it found). Rounds go on while a round finds
    seeds, up to `limit`; a round that would not end before `deadline` (by the last round's length) stops with a partial
    StageError (the seeds are kept: a run again goes on from them). Returns (the last result, rounds, every new seed)."""
    found, n = [], 0
    while True:
        n += 1
        t0 = clock()
        got, new = round_(n)
        found += new
        if not new or n >= limit:
            return got, n, found
        if deadline is not None and clock() + (clock() - t0) > deadline:
            raise S.StageError(f"boot: seed round {n + 1} of up to {limit} would pass the run's time bound; "
                               f"{len(found)} new seed(s) kept ({', '.join(found[:8])}): run again to go on",
                               ["time for another seed round"], "the same command again (boot resumes from the seeds)",
                               status="partial", numbers={"seed rounds": n, "seeds added": len(found)})


# The run flags (gap closed 2026-11-19): with none given, boot tries these in order and keeps the first that reaches the
# title (a headless run of HEADLESS_MIN_VB, Start changes the picture, the windowed launch run passes); the choice goes
# into port/game.json "onboard" and the next boot tries it first. Overlays off comes first: their translation drew Dragon
# Force's strategy map as a flat teal plane (2026-11-17, the overlay translations' own code, not found yet), well past the
# title, where boot cannot see; Dragon Force's launcher builds were made with --overlays off by hand. Pointers on comes
# first among those: both games reach the title with it, and less is left to the interpreter.
FLAG_NAMES = ("pointers", "overlays", "fallback")
VARIANTS = (("on", "off", "on"), ("off", "off", "on"), ("on", "on", "on"), ("on", "on", "off"))
BOOT_CHECKS_S = 900  # the start check, the menu probe and the launch run after the seed rounds


def flag_text(v):
    return " ".join(f"--{k} {x}" for k, x in zip(FLAG_NAMES, v))


def proven_flags(gj):
    try:
        got = json.loads(gj.read_text(encoding="utf-8"))["onboard"]
        return tuple(got[k] for k in FLAG_NAMES) if all(got.get(k) in ("on", "off") for k in FLAG_NAMES) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def variants(ctx, gj):
    """The flag sets boot tries, in order: the given flags alone (the rest from game.json "onboard", else VARIANTS[0]);
    with none given, game.json's proven set first, then VARIANTS."""
    given = {k: getattr(ctx, k, None) for k in FLAG_NAMES}
    proven = proven_flags(gj)
    if any(given.values()):
        base = dict(zip(FLAG_NAMES, proven or VARIANTS[0]))
        base.update({k: x for k, x in given.items() if x})
        return [tuple(base[k] for k in FLAG_NAMES)]
    return list(dict.fromkeys(([proven] if proven else []) + list(VARIANTS)))


def play_keys(gj, plan, image, fallback):
    """game.json "saturn" keys a launcher's play run needs (port/tools/gen_saturn_image.py): image_files and image_sha256,
    so the kit makes the code image from the player's disc (no image.bin ships in a release), and fallback, the --fallback
    this boot runs with, as the build's default (the launcher passes no flags). Returns the keys changed."""
    cfg = json.loads(gj.read_text(encoding="utf-8"))
    sat = cfg.setdefault("saturn", {})
    want = {"image_files": [[f["path"], f"0x{f['base']:08x}"] for f in sorted(plan["image"], key=lambda f: f["base"])],
            "image_sha256": hashlib.sha256(image).hexdigest(), "fallback": "on" if fallback else "off"}
    changed = [k for k in want if sat.get(k) != want[k]]
    if changed:
        sat.update(want)
        gj.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8", newline="\n")
    return changed


def remember_flags(gj, v):
    """game.json "onboard": the flags the boot proved (read by variants; gen_saturn reads only "saturn")."""
    cfg = json.loads(gj.read_text(encoding="utf-8"))
    want = dict(zip(FLAG_NAMES, v))
    if cfg.get("onboard") != want:
        cfg["onboard"] = want
        gj.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8", newline="\n")
        return True
    return False


def boot_flags(pointers=True):
    """overlay.py's flags for the boot's translation; --pointers off leaves the fallback to find table-reached code."""
    return TX_FLAGS if pointers else tuple(f for f in TX_FLAGS if f != "--pointers")  # overlay.json "found" of a function the translator added to the list
EXIT = re.compile(r"exit: (.+?) \((-?\d+)\) after (\d+) vertical blanks")


def starter(ctx):
    plan = json.loads((ctx.project / S.STATE_DIR / PLAN).read_text(encoding="utf-8"))
    ip, (entry, _why) = plan["ip"], entry_of(plan)
    sp, slave = int(ip["stack_master"], 16), int(ip["stack_slave"], 16)
    area = re.sub(r"[^A-Z]", "", ip.get("area", ""))
    g = {"name": ctx.name, "image": "image.bin", "image_base": f"0x{LOAD:08x}", "entry": f"0x{entry:08x}",
         "entry_sp": f"0x{sp or KIT_SP:08x}", "frame_hz": 50 if area and set(area) <= set("E") else 60,
         "product": ip["product"],
         # The 68000 runs the driver the game loads into sound RAM (sat_scsp.c). Without it the kit has no driver, so a
         # command the game leaves in the driver's mailbox is never taken: Saturn Bomberman's SBL sound call (0609e434)
         # wrote 0x0800 to its slot in sound RAM and polled it for ever, one-colour frames from vertical blank 87 on.
         # sound_clear (the hand-made stand-in) needs the mailbox's addresses, which only a person reading the game knows.
         "sound_cpu": True,
         # SBL's SMPC handler takes the pad only from the hardware's INTBACK report (SR bits, CONTINUE by an inverted IREG0
         # bit 7: sat_smpc.c). A game without this key keeps the older report, which Robo Pit's recordings were made with.
         "smpc_intback": "hardware",
         # The Saturn pad on the host's: A B X Y, RB C, LB Z, LT L, RT R, Start, the d-pad; the stick clicks, Back, Guide and
         # the right stick unassigned (RUNTIME_TARGET.md "Modern controllers"); the log and --pad-style say saturn, not
         # dreamcast. The kit gives the layout (sat_input.c) and the name (base/pad_style.c); the keyboard is KEYS below.
         "controller": "saturn"}
    if slave:
        g["slave_sp"] = f"0x{slave:08x}"
    if boot_dir(plan):
        g["boot_dir"] = boot_dir(plan)
    return {"saturn": g, "keys": dict(KEYS)}


# The keyboard of a Saturn game (game.json "keys", base/input_kbm.c): the kit's layout, with Q W E R and Space on the Saturn's
# L R Z C: the shoulder actions now sit on other host buttons (the "controller" key), so the keys follow them and each
# keeps the action it always had. Enter is player 1's Start.
KEYS = {"Z": "A", "X": "B", "A": "X", "S": "Y", "RETURN": "START", "KP_ENTER": "START", "UP": "UP+LSUP", "DOWN": "DOWN+LSDOWN",
        "LEFT": "LEFT+LSLEFT", "RIGHT": "RIGHT+LSRIGHT", "I": "UP", "K": "DOWN", "J": "LEFT", "L": "RIGHT",
        "Q": "LB", "LSHIFT": "LB", "W": "RB", "SPACE": "RB", "E": "LT", "R": "RT"}


def add_missing_keys(gj, start):
    """Keys a newer starter has that an existing port/game.json's "saturn" object lacks (boot_dir, d1cc1b311), added
    without touching any key already there, so a project made by an older tool gets them with no hand edit. Returns the
    keys added."""
    cfg = json.loads(gj.read_text(encoding="utf-8"))
    have = cfg.setdefault("saturn", {})
    added = [k for k in start["saturn"] if k not in have]
    keys = [k for k in start if k != "saturn" and k not in cfg]  # a top-level key the game has not set (the keyboard)
    if keys:
        cfg.update({k: start[k] for k in keys})
    if added or keys:
        have.update({k: start["saturn"][k] for k in added})
        gj.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8", newline="\n")
    return added + keys


def log_facts(text):
    """(exit why, vertical blanks, stubs hit, the first STUB line, functions in the table) from a kit run's log."""
    m = EXIT.search(text)
    stubs = re.search(r"stubs hit: (\d+)", text)
    first = next((ln.strip() for ln in text.splitlines() if ln.startswith("STUB #1 ") or "cannot open" in ln), None)
    k = re.search(r"port: image .* (\d+) functions", text)
    return (m.group(1) if m else None, int(m.group(3)) if m else 0, int(stubs.group(1)) if stubs else 0, first,
            int(k.group(1)) if k else None)


def exe_key(exe):
    """'exe <sha256 prefix>' of the build a run starts (jobreg's key sees only the command line)."""
    try:
        return "exe " + hashlib.sha256(pathlib.Path(exe).read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def run_exe(ctx, exe, args, seconds, why):
    """The exe under tools/bounded.py, inside tools/jobreg.py; output appended to the boot stage's log. The run's key cannot
    see the exe's contents, so --changed (ctx.changed, as onboard_stages.run_tool passes it) reaches jobreg here too: without
    it a rebuild's runs were refused or reused as the old exe's (2026-11-17, Dragon Force: game_src changed, boot ran 0 vb)."""
    # and the exe's own hash, always: the seed rounds rebuild the same path, and a 20 s run ends on its watchdog (exit
    # 2), which jobreg counts as a failure, so round 2's run was refused as "failed before" (Dragon Force, 2026-11-19)
    changed = "; ".join(x for x in (getattr(ctx, "changed", None), exe_key(exe)) if x)
    cmd = [sys.executable, str(ctx.tool / "tools" / "jobreg.py"), "run", "--why", why,
           *(["--changed", changed] if changed else []), "--", sys.executable,
           str(ctx.tool / "tools" / "bounded.py"), str(seconds), "--", str(exe), *map(str, args)]
    log = S.log_path(ctx, "boot")
    with open(log, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n$ {subprocess.list2cmdline(cmd)}\n")
        f.flush()
        return subprocess.run(cmd, cwd=exe.parent, stdout=f, stderr=subprocess.STDOUT, timeout=seconds + 300).returncode


TX_STAMP = "bodies.sha256"


def tx_args(img, bodies, out, flags=TX_FLAGS):
    """overlay.py's arguments for the whole image (work RAM high) from the census's function list."""
    return [f"targets/{T}/translator/tools/overlay.py", "--image", img, "--load", f"0x{LOAD:08x}", "--range",
            f"0x{LOAD:08x}", f"0x{RAM_END:08x}", "--bodies", bodies, *flags, "--out", out]


def seg_args(seg_bin, base, size, entries, out):
    """overlay.py's arguments for a work RAM low block (an image of its own at its run address)."""
    return [f"targets/{T}/translator/tools/overlay.py", "--image", seg_bin, "--load", f"0x{base:08x}", "--range",
            f"0x{base:08x}", f"0x{base + size:08x}", "--bodies", entries, *TX_FLAGS, "--out", out]


def overlaid(plan):
    """True when a left-out file loads over an image file: the image's code is then not always what is in memory."""
    return any(g["base"] < f["base"] + f["size"] and f["base"] < g["base"] + g["size"]
               for f in plan.get("left_out", ()) for g in plan["image"])


def tx_key(bodies, translator=None, flags=TX_FLAGS):
    """What a translation depends on: function_bodies.txt, overlay.py's flags and, when given, the translator's sources
    (every .py under it). A translator fix changes every C file; boot then reused the old C until its stamp held only
    the bodies."""
    h = hashlib.sha256(bodies.read_bytes())
    h.update("\0".join(flags).encode() + b"\0")
    if translator:
        for f in sorted(pathlib.Path(translator).rglob("*.py")):
            if "__pycache__" not in f.parts:
                h.update(f.relative_to(translator).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def tx_current(tx, bodies, translator=None, flags=TX_FLAGS):
    """True when tx/c holds the translation of this function_bodies.txt by this translator (tx/bodies.sha256 matches).
    A census that adds a function (06006000, the entry, after its start was marked) changes the bodies; boot then reused
    the old C and the entry call was a stub (0 vertical blanks, 2026-11-16)."""
    stamp = tx / TX_STAMP
    return (any((tx / "c").glob("*.c")) and stamp.is_file()
            and stamp.read_text(encoding="utf-8").strip() == tx_key(bodies, translator, flags))


def tx_stamp(tx, bodies, translator=None, flags=TX_FLAGS):
    (tx / TX_STAMP).write_text(tx_key(bodies, translator, flags) + "\n", encoding="utf-8")


START_PRESSES = tuple(range(2000, 12001, 600))  # vertical blanks of the Start presses of the "start responds" check
START_HOLD, START_LOOK = 10, 200  # held for 10 vertical blanks; the frame compared is START_LOOK after the press
START_STOP, START_RUN_S = START_PRESSES[-1] + START_LOOK + 10, 300


def png_is_black(data):
    """True when a PNG's pixels are all zero (the run went black). No image library: inflate the IDAT chunks and
    ignore the filter byte of each row (a filtered row of zeros is zeros)."""
    import zlib
    try:
        pos, h, idat = 8, 0, b""
        while pos + 8 <= len(data):
            n, kind = struct.unpack_from(">I4s", data, pos)
            if kind == b"IHDR":
                h = struct.unpack_from(">I", data, pos + 12)[0]
            elif kind == b"IDAT":
                idat += data[pos + 8:pos + 8 + n]
            pos += 12 + n
        raw_ = zlib.decompress(idat)
        stride = len(raw_) // h
        return not any(any(raw_[r * stride + 1:(r + 1) * stride]) for r in range(h))
    except (struct.error, zlib.error, ZeroDivisionError):
        return False


def start_responds(idle, pressed, is_black=png_is_black):
    """Compare two fixed-clock runs of one game: idle (no input) and pressed (Start pressed and released at a set of
    vertical blanks). Both map a press's vertical blank to the bytes of the frame START_LOOK blanks after it, or None
    when the run never made it. Start responds when the picture differs after a press; the first such press is
    returned. A missing frame (a crash) and a black one (the run went black) do not count. It tells a game that
    ignores the pad from one that reacts, not a menu from any other reaction (any scene change the press causes)."""
    for vb in sorted(pressed):
        a, b = idle.get(vb), pressed[vb]
        if a is None or not b or is_black(b):
            continue
        if a != b:
            return True, vb
    return False, None


def start_check(ctx, exe, args):
    """Run the exe headless twice on the same fixed clock, idle and with Start pressed at START_PRESSES, a frame
    START_LOOK blanks after each press, and compare them (start_responds). Returns (yes/no, first press, the paths)."""
    d = exe.parent / "start_check"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    shots = {}
    for tag, extra in (("idle", []), ("pressed", ["--input", ",".join(f"START@{v}+{START_HOLD}" for v in START_PRESSES)])):
        run_exe(ctx, exe, ["--headless", "--fixed-clock", "1", "--seconds", START_RUN_S, "--stop-at", START_STOP,
                           "--log", d / f"{tag}.log", "--shot", START_PRESSES[0] + START_LOOK, d / tag,
                           "--shot-every", START_PRESSES[1] - START_PRESSES[0], "--shot-max", len(START_PRESSES),
                           *args, *extra], START_RUN_S + 60, f"onboard boot of {ctx.name}: the {tag} start check run")
        shots[tag] = {int(f.stem.rsplit("_", 1)[1]) - START_LOOK: f.read_bytes() for f in d.glob(f"{tag}_*.png")}
    yes, first = start_responds(shots["idle"], shots["pressed"])
    keep = {f"idle_{first + START_LOOK:05d}.png", f"pressed_{first + START_LOOK:05d}.png"} if yes else set()
    for f in d.glob("*.png"):
        if f.name not in keep:
            f.unlink()
    return yes, first, ({"start frame idle": str(d / f"idle_{first + START_LOOK:05d}.png"),
                         "start frame pressed": str(d / f"pressed_{first + START_LOOK:05d}.png")} if yes else {})


# The menu probe: which of the game's own buttons moves on in its menus and which backs out (game.json "saturn" "confirm"
# and "cancel"; the kit puts them on the host's A and B, sat_input.c). From the press of Start that opened the menu
# (start_check's first), each button is pressed in its own fixed-clock run PROBE_WAIT blanks later, and the frames after
# it are compared with an idle run's. Level 1 is the menu: when no button moves on there (a "press Start" title), Start
# is pressed there and the buttons are tried again PROBE_WAIT later, up to PROBE_STARTS times. Level 2 is the screen the
# confirm found opens, where a button whose picture returns to the menu's backs out. A button whose picture changes to
# something new moves on. Saturn Bomberman (2026-11-17): Start at 2000 skips the intro to the title, where no button but
# Start does anything; Start at 2700 opens GAME MODE / OPTION, where A and C move on and B returns to the title.
PAD_BUTTONS = ("A", "B", "C", "X", "Y", "Z", "L", "R")
CONFIRM_ORDER = ("C", "A", "X", "Y", "Z", "R", "L", "B")  # when more than one moves on (Saturn games mostly take C and A)
CANCEL_ORDER = ("B", "A", "C", "X", "Y", "Z", "R", "L")
PROBE_WAIT, PROBE_HOLD, PROBE_STEP, PROBE_SHOTS = 700, 10, 100, 5  # shots at P-10, P+90 .. P+390 around the press P
PROBE_RUN_S, PROBE_JOBS, PROBE_STARTS = 300, 3, 3
HOST_KEYS = {"confirm": "Z", "cancel": "X"}  # the keyboard keys of the host's A and B (KEYS): what the build check presses


def probe_input(starts, presses):
    """--input for a probe run: the Start presses up to the one that opened the menu, then (button, vertical blank)s."""
    return ",".join([f"START@{v}+{START_HOLD}" for v in starts] + [f"{b}@{v}+{PROBE_HOLD}" for b, v in presses])


def menu_buttons(idle, runs, press, back, is_black=png_is_black):
    """idle and runs[button] map a vertical blank to the bytes of the frame there (runs: the button pressed at press).
    back: the frames of the screen before this one. Returns (moved, backed): the buttons whose picture after the press
    differs from idle's and is new, and those whose picture returns to a frame in back. A missing or black frame does not
    count."""
    moved, backed = [], []
    for b in PAD_BUTTONS:
        after = [(vb, f) for vb, f in sorted(runs.get(b, {}).items()) if vb > press and f and not is_black(f)]
        changed = [f for vb, f in after if idle.get(vb) not in (None, f)]
        if any(f in back for f in changed):
            backed.append(b)
        elif changed:
            moved.append(b)
    return moved, backed


def pick(found, order, not_=None):
    return next((b for b in order if b in found and b != not_), None)


def same_after(a, b, press):
    """True when two runs' frames after the press are there and identical (a host key against the probe's button)."""
    va = {vb: f for vb, f in a.items() if vb > press}
    return bool(va) and va == {vb: f for vb, f in b.items() if vb > press}


def sha8(f):
    return hashlib.sha256(f).hexdigest()[:8] if f else "none"


class Prober:
    """The probe's fixed-clock runs of one exe, each in exe/../menu_probe: --input for the game's buttons and, for the
    build check, --kbm-inject for the host's keys."""

    def __init__(self, ctx, exe, args, first_start):
        self.ctx, self.exe, self.args = ctx, exe, list(args)
        self.starts = [v for v in START_PRESSES if v <= first_start]
        self.d = exe.parent / "menu_probe"

    def shoot(self, tag, presses, at, keys=()):
        first = at - 10
        extra = ["--kbm-inject", ",".join(f"{k}@{v}+{PROBE_HOLD}" for k, v in keys)] if keys else []
        run_exe(self.ctx, self.exe, ["--headless", "--fixed-clock", "1", "--seconds", PROBE_RUN_S, "--stop-at",
                                     first + PROBE_STEP * PROBE_SHOTS, "--log", self.d / f"{tag}.log", "--shot", first,
                                     self.d / tag, "--shot-every", PROBE_STEP, "--shot-max", PROBE_SHOTS, *self.args,
                                     "--input", probe_input(self.starts, presses), *extra], PROBE_RUN_S + 60,
                f"onboard boot of {self.ctx.name}: menu probe run {tag}")
        return tag, {int(f.stem.rsplit("_", 1)[1]): f.read_bytes() for f in self.d.glob(f"{tag}_*.png")}

    def level(self, name, lead, press):
        jobs = [(f"{name}_idle", lead, press)] + [(f"{name}_{b}", lead + [(b, press)], press) for b in PAD_BUTTONS]
        with concurrent.futures.ThreadPoolExecutor(PROBE_JOBS) as pool:
            got = dict(pool.map(lambda j: self.shoot(*j), jobs))
        return got.pop(f"{name}_idle"), {t.rsplit("_", 1)[1]: s for t, s in got.items()}

    def probe(self):
        """{"confirm", "cancel" (None: not found), "evidence": [lines], "proof": {key: (lead, press, frames)}}: the
        frames each button made, which the build check compares the host's keys with."""
        shutil.rmtree(self.d, ignore_errors=True)
        self.d.mkdir()
        lead, p1, ev = [], self.starts[-1] + PROBE_WAIT, []
        for step in range(PROBE_STARTS):
            idle1, runs1 = self.level(f"menu{step}", lead, p1)
            moved1, _ = menu_buttons(idle1, runs1, p1, set())
            ev.append(f"screen at {p1 - 10}: {sha8(idle1.get(p1 - 10))}; press at {p1}; moved on: {' '.join(moved1) or 'none'}")
            if moved1:
                break
            lead, p1 = lead + [("START", p1)], p1 + PROBE_WAIT  # a "press Start" screen: Start, then try again
        menu = {f for vb, f in idle1.items() if vb < p1}  # the menu's picture before the press
        confirm = pick(moved1, CONFIRM_ORDER)
        out = {"confirm": confirm, "cancel": None, "evidence": ev, "proof": {}}
        if not confirm:
            return out
        out["proof"]["confirm"] = (lead, p1, runs1[confirm])
        p2 = p1 + PROBE_WAIT
        idle2, runs2 = self.level("deeper", lead + [(confirm, p1)], p2)
        moved2, backed2 = menu_buttons(idle2, runs2, p2, menu)
        cancel = pick(backed2, CANCEL_ORDER, confirm)
        ev.append(f"after {confirm} at {p1}: {sha8(idle2.get(p2 - 10))} at {p2 - 10}; press at {p2}; moved on: "
                  f"{' '.join(moved2) or 'none'}; back to the menu: {' '.join(backed2) or 'none'}"
                  + (f" ({cancel}: {sha8(runs2[cancel].get(p2 + 90))} at {p2 + 90})" if cancel else ""))
        out["cancel"] = cancel
        if cancel:
            out["proof"]["cancel"] = (lead + [(confirm, p1)], p2, runs2[cancel])
        keep = {f"menu{step}_idle_{p1 - 10:05d}.png", f"menu{step}_{confirm}_{p1 + 90:05d}.png",
                f"deeper_idle_{p2 - 10:05d}.png"}
        keep |= {f"deeper_{cancel}_{p2 + 90:05d}.png"} if cancel else set()
        for f in self.d.glob("*.png"):
            if f.name not in keep:
                f.unlink()
        return out

    def host_check(self, proof):
        """The build's host keys (HOST_KEYS) pressed where the probe pressed the game's buttons: {key: True when the
        frames after the press are the probe's}."""
        got = {}
        for key, (lead, press, frames) in proof.items():
            _, shots = self.shoot(f"host_{key}", lead, press, keys=[(HOST_KEYS[key], press)])
            got[key] = same_after(shots, frames, press)
            for f in self.d.glob(f"host_{key}_*.png"):
                if f.name != f"host_{key}_{press + 90:05d}.png":
                    f.unlink()
        return got


def set_buttons(gj, found):
    """game.json "saturn" "confirm" and "cancel" from the probe, where it found them and the game sets none. Returns the
    keys written (a value the game already has stays: a person's choice wins)."""
    cfg = json.loads(gj.read_text(encoding="utf-8"))
    have = cfg.setdefault("saturn", {})
    put = {k: found[k] for k in ("confirm", "cancel") if found.get(k) and k not in have}
    if put:
        have.update(put)
        gj.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8", newline="\n")
    return sorted(put)


def launch_check(ctx, exe, args):
    """The launch run (LAUNCH_RUN_S): a window, the real clock, `args` as Start Game.bat passes them. Returns (vertical
    blanks, exit why, the CRASH line or None, the log)."""
    d = exe.parent / "launch_check"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    log = d / "port.log"
    run_exe(ctx, exe, ["--image", exe.parent / "image.bin", "--data-directory", d, "--log", log, "--seconds", LAUNCH_RUN_S,
                       *args], LAUNCH_RUN_S + 60, f"onboard boot of {ctx.name}: the launch run (a window, the real clock)")
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    why, vb = log_facts(text)[:2]
    crash = next((ln.strip() for ln in text.splitlines() if ln.startswith("CRASH")), None)
    return vb, why, crash, log


def menu_step(ctx, exe, gj, make, args, first_start):
    """The menu probe in the boot stage: find confirm and cancel (Prober.probe) when game.json "saturn" has neither, write
    them (set_buttons), rebuild with them (make), and press the host's A and B keys (Z, X) on the new build where the probe
    pressed the game's buttons (Prober.host_check). Returns (numbers, a message tail, paths)."""
    have = json.loads(gj.read_text(encoding="utf-8")).get("saturn", {})
    if first_start is None:
        return {"menu probe": "not run: Start does not respond"}, "; menu probe not run (Start does not respond)", {}
    if have.get("confirm") and have.get("cancel"):
        nums = {"confirm": have["confirm"], "cancel": have["cancel"], "menu probe": "not run: game.json sets both"}
        return nums, f"; confirm {have['confirm']}, cancel {have['cancel']} (game.json)", {}
    pr = Prober(ctx, exe, args, first_start)
    found = pr.probe()
    put = set_buttons(gj, found)
    nums = {"confirm": found["confirm"] or "not found", "cancel": found["cancel"] or "not found",
            "menu probe": " | ".join(found["evidence"]), "game.json gained": ", ".join(put) or "nothing"}
    if put:
        make()
    if found["proof"]:
        got = pr.host_check(found["proof"])
        nums["host keys"] = ", ".join(f"{HOST_KEYS[k]} ({'host A' if k == 'confirm' else 'host B'}) "
                                      f"{'does' if ok else 'does NOT do'} the game's {found[k]}" for k, ok in got.items())
        if not all(got.values()):
            raise S.StageError(f"menu probe: {nums['host keys']} on the rebuilt exe", ["host A confirms, host B cancels"],
                               f"read {pr.d} and port/saturn/sat_input.c (sat_layout)", numbers=nums)
    msg = (f"; menu probe: confirm {found['confirm'] or 'not found'}, cancel {found['cancel'] or 'not found'}"
           + (f", {nums['host keys']}" if "host keys" in nums else ""))
    return nums, msg, {"menu probe": str(pr.d)}


class Boot(S.Built):
    def local_seeds(self, ctx):
        """What autoonboard.py --host sends a remote boot (--seeds): the play logs here, which the build box has not got."""
        return play_seeds(ctx.project)

    tools = (f"targets/{T}/translator/tools/overlay.py", "port/tools/gen_saturn.py", "port/tools/build_saturn.py",
             "tools/jobreg.py", "tools/bounded.py")

    def calls(self, ctx):
        return ["port/game.json: a starter \"saturn\" object from the disc plan when there is none",
                f"the flag sets in order ({' | '.join(flag_text(v) for v in variants(ctx, ctx.project / 'port' / 'game.json'))}"
                "), keeping the first that reaches the title; each in seed rounds (up to "
                f"{SEED_ROUNDS}): the interpreter's fallbacks and STUB calls translated and booted again",
                f"tools/jobreg.py run -- tools/bounded.py {BOOT_TX_S} -- targets/{T}/translator/tools/overlay.py "
                f"--image IMG --load 0x06000000 --range 0x06000000 0x06100000 --bodies state/function_bodies.txt "
                f"{' '.join(TX_FLAGS)} --out port/build/onboard_tx (the list plus the code it reaches that it lacks)",
                "port/tools/gen_saturn.py --game port/game.json --tx port/build/onboard_tx/c --out port/build/onboard_gen",
                f"tools/jobreg.py run -- tools/bounded.py {BOOT_BUILD_S} -- port/tools/build_saturn.py --gen ... --exe "
                "port/build/onboard/game.exe",
                f"the exe --headless --fixed-clock 1 --seconds {BOOT_RUN_S} --image IMG --disc CUE --log port.log "
                "--fallback on: vertical blanks, stubs hit, the first missing function, the fallbacks (the SH-2 "
                "interpreter's addresses, kept in decomp/state/fallback_seeds.txt as seeds for the next run)",
                f"the exe as Start Game.bat starts it (a window, the real clock, --fallback) for {LAUNCH_RUN_S} s: no crash, "
                f"at least {LAUNCH_MIN_VB} vertical blanks; the verdict's \"launch\" names the build and its arguments"]

    def run(self, ctx):
        port, made = ctx.project / "port", []
        # also here, not only in the project stage: a project resumed past it (--from census, --only boot) got none
        made += [m for m in launcher_files(ctx.project, ctx.name, getattr(ctx, "disc", None)) if not m.startswith("kept ")]
        made += [f"{GAME_SRC}/{n}" for n in sync_game_src(ctx.project)]
        gj = port / "game.json"
        if not gj.exists():
            port.mkdir(parents=True, exist_ok=True)
            gj.write_text(json.dumps(starter(ctx), indent=1) + "\n", encoding="utf-8", newline="\n")
            made.append("a starter port/game.json")
        else:
            added = add_missing_keys(gj, starter(ctx))
            if added:
                made.append("port/game.json gained " + ", ".join(added))
        ws = S.ws_of(ctx)
        played = take_seeds(ws, list(getattr(ctx, "seeds", None) or ()) + play_seeds(ctx.project))
        if played:
            made.append(f"{len(played)} seed(s) from play: {', '.join(played)}")
        deadline = getattr(ctx, "t0", time.time()) + getattr(ctx, "seconds", 2900) - BOOT_CHECKS_S
        tried, soft, vs = [], None, variants(ctx, gj)
        for i, v in enumerate(vs):
            if i and time.time() > deadline:  # no time for another flag set: stop as partial, not killed by the bound
                raise S.StageError(f"boot: no time left for the flag set {flag_text(v)}; tried: " + " || ".join(tried),
                                   ["time for the next flag set"], "the same command again (seeds and translations "
                                   "are kept)", status="partial", numbers={"flag sets tried": len(tried)})
            try:
                got = self.attempt(ctx, gj, ws, v, deadline, made)
            except S.StageError as e:
                if e.status == "partial":
                    raise
                tried.append(f"{flag_text(v)}: {str(e)[:240]}")
                if i == len(vs) - 1 and soft is None:
                    raise S.StageError("boot: no flag set reached the title. " + " || ".join(tried), e.missing,
                                       e.supply, numbers={**e.numbers, "flag sets tried": len(tried)})
                continue
            if got["numbers"]["start responds"] != "yes" and len(vs) > 1:
                soft = soft or v  # it runs, but Start changes nothing: the next set may reach the title
                tried.append(f"{flag_text(v)}: ran, but Start changed no frame")
                if i < len(vs) - 1:
                    continue
                got = self.attempt(ctx, gj, ws, soft, deadline, made) if soft != v else got
                v = soft
            return self.chosen(gj, v, got, tried)
        got = self.attempt(ctx, gj, ws, soft, deadline, made)  # every later set failed: the one that ran
        return self.chosen(gj, soft, got, tried)

    @staticmethod
    def chosen(gj, v, got, tried):
        wrote = remember_flags(gj, v)
        got["numbers"]["flags"] = flag_text(v)
        got["numbers"]["flag sets tried before"] = " || ".join(tried) or "none"
        got["message"] += (f"; flags {flag_text(v)}" + (" (written to port/game.json \"onboard\")" if wrote else "")
                           + (f" after {len(tried)} other set(s)" if tried else ""))
        return got

    def attempt(self, ctx, gj, ws, v, deadline, made):
        """One flag set: translate, build and run in seed rounds (seed_rounds), then the start check, the menu probe and
        the launch run. A StageError says why this set does not reach the title."""
        pointers, overlays, fallback = (x != "off" for x in v)
        port = ctx.project / "port"
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        img = ws / cfg["binary"]
        build = port / "build"
        flags, sfx = boot_flags(pointers), build_suffix(pointers, overlays)  # its own C and exe, the default kept
        tx = build / f"onboard_tx{build_suffix(pointers, True)}"  # the image's C does not depend on the overlays
        gen, exe = build / f"onboard_gen{sfx}", build / f"onboard{sfx}" / "game.exe"
        nfun = sum(1 for ln in (ws / "state" / "function_bodies.txt").read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.startswith("#"))
        trans = ctx.tool / "targets" / T / "translator"
        plan = json.loads((ctx.project / S.STATE_DIR / PLAN).read_text(encoding="utf-8"))
        image_bytes = img.read_bytes()
        play_keys(gj, plan, image_bytes, fallback)  # a launcher's run: the image from the disc, this --fallback as the default
        disc = ctx.verdicts.get("disc", {}).get("paths", {}).get("image")
        plog = exe.parent / "port.log"
        st = {}
        run_args = (["--image", exe.parent / "image.bin"] + (["--disc", disc] if disc else [])
                    + ["--fallback", "on" if fallback else "off"])

        def translations():
            """The image's C (with the seeds), each work RAM low block's (with its seeds) and each overlay's."""
            bodies = seeded_bodies(ws, ws / "state" / "function_bodies.txt")
            if not tx_current(tx, bodies, trans, flags):
                if tx.exists():
                    shutil.rmtree(tx)  # another catalog's translation
                rc, tail = S.run_tool(ctx, "boot", tx_args(img, bodies, tx, flags), BOOT_TX_S,
                                      why=f"onboard boot of {ctx.name}: translate the whole image for the kit",
                                      produces=tx / "c")
                if not any((tx / "c").glob("*.c")):
                    raise S.StageError(f"overlay.py exited {rc}, no C: {' | '.join(tail[-3:])}", ["the translator's C"],
                                       f"read {S.log_path(ctx, 'boot')}")
                tx_stamp(tx, bodies, trans, flags)
            txs, guards, nseg = [tx / "c"], [], 0
            if overlaid(plan):
                guards.append(f"{img}@{LOAD:08x}")
            for s in plan.get("segments", ()):
                f = next(g for g in plan["image"] if g["path"] == s["path"])
                name = f"onboard_seg_{s['base']:08x}"
                sbin, ents, stx = build / f"{name}.bin", build / f"{name}.txt", build / f"{name}_tx"
                at = f["base"] - LOAD + s["offset"]
                sbin.parent.mkdir(parents=True, exist_ok=True)
                sbin.write_bytes(image_bytes[at:at + s["size"]])
                ents.write_text("".join(a + "\n" for a in segment_entries(ws, s)), encoding="utf-8")
                if not tx_current(stx, ents, trans):
                    if stx.exists():
                        shutil.rmtree(stx)
                    S.run_tool(ctx, "boot", seg_args(sbin, s["base"], s["size"], ents, stx), BOOT_TX_S,
                               why=f"onboard boot of {ctx.name}: translate the work RAM low block at {s['base']:08x}",
                               produces=stx / "c")
                    if not any((stx / "c").glob("*.c")):
                        raise S.StageError(f"overlay.py made no C for the block at {s['base']:08x}",
                                           ["the translator's C"], f"read {S.log_path(ctx, 'boot')}")
                    tx_stamp(stx, ents, trans)
                txs.append(stx / "c")
                guards.append(f"{sbin}@{s['base']:08x}")
                nseg += sum(1 for _ in (stx / "c").glob("*.c"))
            # each overlay (a code file loaded at run time over other code) translated on its own, at its own address
            gd, ovl_args, novl, ovls, ovl_failed = ctx.project / "game-data", [], 0, [], []
            datas = {q: (gd / q.lstrip("/")).read_bytes() for q in overlay_paths(plan) if (gd / q.lstrip("/")).is_file()}
            jobs = []
            for o in boot_overlay_files(plan, datas, overlays):
                name = f"onboard_ovl_{o['tag']}"
                obin, ents, otx = build / f"{name}.bin", build / f"{name}.txt", build / f"{name}_tx"
                obin.write_bytes(datas[o["path"]])
                ents.write_text("".join(a + "\n" for a in o["entries"]), encoding="utf-8")
                jobs.append((o, obin, ents, otx, not tx_current(otx, ents, trans)))

            def translate(job):
                o, obin, ents, otx, _ = job
                if otx.exists():
                    shutil.rmtree(otx)
                S.run_tool(ctx, "boot", seg_args(obin, o["base"], o["size"], ents, otx), BOOT_TX_S,
                           why=f"onboard boot of {ctx.name}: translate the overlay {o['path']} at {o['base']:08x}",
                           produces=otx / "c")
                if any((otx / "c").glob("*.c")):
                    tx_stamp(otx, ents, trans)

            # Dragon Force has 17 overlays: OVERLAY_JOBS translations at once, each its own overlay.py process
            with concurrent.futures.ThreadPoolExecutor(OVERLAY_JOBS) as pool:
                list(pool.map(translate, [j for j in jobs if j[4]]))
            for o, obin, ents, otx, _ in jobs:
                if not any((otx / "c").glob("*.c")):
                    ovl_failed.append(o["path"])  # the interpreter still runs it: not a stop
                    continue
                n = sum(1 for _ in (otx / "c").glob("*.c"))
                ovl_args += ["--overlay", f"{o['tag']}={otx / 'c'}@{obin}@{o['base']:08x}"]
                novl += n
                ovls.append(f"{o['path']}@{o['base']:08x} ({n})")
            st.update(txs=txs, guards=guards, ovl_args=ovl_args, nseg=nseg, novl=novl, ovls=ovls, ovl_failed=ovl_failed,
                      ntx=sum(1 for _ in (tx / "c").glob("*.c")) + nseg + novl)

        def make():
            """gen_saturn.py then build_saturn.py: the exe from game.json and the translations (again after the menu probe
            wrote confirm and cancel)."""
            if gen.exists():
                shutil.rmtree(gen)
            rc, tail = S.run_tool(ctx, "boot", ["port/tools/gen_saturn.py", "--game", gj, "--out", gen]
                                  + [x for t_ in st["txs"] for x in ("--tx", t_)]
                                  + [x for g in st["guards"] for x in ("--guard", g)] + st["ovl_args"], 1800)
            if rc:
                raise S.StageError(f"gen_saturn.py exited {rc}: {' | '.join(tail[-3:])}", ["the kit's generated files"],
                                   f"read {S.log_path(ctx, 'boot')}; port/game.json \"saturn\" (port/CONTRACT.md)")
            exe.parent.mkdir(parents=True, exist_ok=True)
            sdl3_ready(ctx.project, exe.parent)
            own = sorted((ctx.project / GAME_SRC).glob("*.c"))  # the game's own code: always built, gen is made anew above
            rc, tail = S.run_tool(ctx, "boot", ["port/tools/build_saturn.py", "--gen", gen, "--exe", exe]
                                  + (["--game-src", ctx.project / GAME_SRC] if own else []), BOOT_BUILD_S,
                                  why=f"onboard boot of {ctx.name}: build the Saturn kit with the translated game")
            sdl3_keep(ctx.project, exe.parent)
            if rc or not exe.is_file():
                raise S.StageError(f"not built: build_saturn.py exit {rc}: {' | '.join(tail[-3:])}",
                                   ["a port that builds"], f"read {S.log_path(ctx, 'boot')} (MSVC 32-bit, vcpkg SDL3)")
            shutil.copyfile(img, exe.parent / "image.bin")
            S.place_fonts(ctx.project / GAME_SRC, exe.parent)  # the fonts the game's code loads (Dragon Force's clearer text)

        def round_(n):
            translations()
            make()
            plog.unlink(missing_ok=True)
            rc = run_exe(ctx, exe, ["--headless", "--fixed-clock", "1", "--seconds", BOOT_RUN_S, "--image",
                                    exe.parent / "image.bin", "--log", plog, "--fallback", "on" if fallback else "off"]
                         + (["--disc", disc] if disc else []),
                         BOOT_RUN_S + 60, f"onboard boot of {ctx.name}: a {BOOT_RUN_S} s headless run ({flag_text(v)}, "
                                          f"seed round {n})")
            text = plog.read_text(encoding="utf-8", errors="replace") if plog.is_file() else ""
            # the start check's runs press Start into the menus, where the idle run never goes: their fallbacks and
            # STUB calls are seeds too (Saturn Bomberman's 0606b78e and 0606b734 run only after Start: found by hand)
            st["start"] = start_check(ctx, exe, run_args)
            seen = "".join((exe.parent / "start_check" / f"{t_}.log").read_text(encoding="utf-8", errors="replace")
                           for t_ in ("idle", "pressed") if (exe.parent / "start_check" / f"{t_}.log").is_file())
            return (rc, text), run_seeds(ws, plan, text + "\n" + seen)

        (rc, text), rounds, seeded = seed_rounds(round_, deadline=deadline)
        why, vb, stubs, first, table = log_facts(text)
        nfb, fb_rows = fallback_facts(text)
        try:
            rows = json.loads((tx / "overlay.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = {}
        added = sum(1 for r in rows.values() if r.get("found") in TX_FOUND)
        listed = ", ".join(f"{r[0]} ({r[1]}x)" for r in fb_rows[:8]) + (" ..." if len(fb_rows) > 8 else "")
        fb_msg = (f"; {nfb} fallback(s) ran in the SH-2 interpreter{': ' + listed if listed else ''}" if nfb is not None
                  else "; fallbacks not reported (--fallback off, or a kit without sat_interp.c)")
        nums = {"functions listed": nfun, "added by the translator": added, "translated": st["ntx"],
                "of them in low RAM blocks": st["nseg"], "of them in overlays": st["novl"],
                "overlays translated": len(st["ovls"]), "overlays": "; ".join(st["ovls"]) or "none",
                "overlays not translated": "; ".join(st["ovl_failed"]) or "none", "guarded files": len(st["guards"]),
                "in the kit's table": table, "built": True, "seed rounds": rounds, "seeds added": len(seeded),
                "vertical blanks": vb, "stubs hit": stubs, "fallbacks": nfb if nfb is not None else "not reported",
                "pointers": "on" if pointers else "off", "overlay translation": "on" if overlays else "off",
                "ended by": why or f"no exit line (rc {rc})"}
        if not ran_enough(why, vb):
            raise S.StageError(f"built; ran {vb} vertical blanks, ended by {why or 'no exit line, rc ' + str(rc)}"
                               f"; first: {first or 'no STUB line'}{fb_msg}",
                               [f"a headless run that reaches {HEADLESS_MIN_VB} vertical blanks"],
                               f"read {plog} and {S.log_path(ctx, 'boot')}", numbers=nums)
        ok_start, first_start, start_paths = st["start"]  # the last seed round's
        probe, probe_msg, probe_paths = menu_step(ctx, exe, gj, make, run_args, first_start if ok_start else None)
        launch_args = ["--fallback", "on" if fallback else "off"]
        lvb, lwhy, lcrash, llog = launch_check(ctx, exe, (["--disc", disc] if disc else []) + launch_args)
        nums.update({"start responds": "yes" if ok_start else "no",
                     "first press that changed the picture (vertical blank)": first_start if ok_start else "none",
                     "launch run (windowed, real clock)": f"{lvb} vertical blanks, " + (lcrash or f"ended by {lwhy}"),
                     "launch arguments": " ".join(launch_args), **probe})
        if lcrash or lvb < LAUNCH_MIN_VB:
            raise S.StageError(f"the launch as Start Game.bat makes it (a window, the real clock, {' '.join(launch_args)}, "
                               f"{LAUNCH_RUN_S} s) ran {lvb} vertical blanks: {lcrash or 'ended by ' + str(lwhy)}; the "
                               "headless runs passed. The build is not handed to the launcher",
                               ["a build that runs as the launcher starts it"], f"read {llog} (and the crash dump beside it)",
                               numbers=nums)
        nxt = ["Each STUB line in port.log is a function the translator did not make or the catalog lacks: "
               "add it (catalog_overrides, a hand .c) and run the boot stage again (--only boot)"] if stubs else []
        if not ok_start:
            nxt.append("Start pressed at vertical blanks 2000..12000 changed no frame: the game ignores the pad here "
                       "(see port/saturn/sat_smpc.c, sat_input.c and start_check/*.log)")
        if fb_rows:
            nxt.append(f"{len(fb_rows)} address(es) ran in the SH-2 interpreter (FALLBACK lines in port.log) after "
                       f"{rounds} seed round(s); they are seeds ({seeds_path(ws)}). The count must reach 0: the fallback "
                       "is a bring-up aid, not the play path")
        return {"numbers": nums, "paths": {"game.json": str(gj), "exe": str(exe), "port.log": str(plog),
                                           "fallback seeds": str(seeds_path(ws)), "launch log": str(llog), **start_paths,
                                           **probe_paths},
                # the proven build and its flags: autoonboard.py --host copies these files here and the bats start them
                "launch": {"dir": exe.parent.relative_to(ctx.project).as_posix(), "args": launch_args,
                           "config": gj.relative_to(ctx.project).as_posix(),
                           "files": sorted(f.name for f in exe.parent.iterdir() if f.is_file() and (
                               f.suffix.lower() in (".exe", ".dll") or f.name == "image.bin"))},
                "message": (("; ".join(made) + "; ") if made else "") + f"built and ran {BOOT_RUN_S} s headless: "
                f"{vb} vertical blanks, ended by {why}; {rounds} seed round(s), {len(seeded)} seed(s) added; {stubs} "
                f"missing function(s) called, first: {first or 'none'}{fb_msg}; start "
                f"{'responds (Start pressed at vertical blank ' + str(first_start) + ' changed the picture)' if ok_start else 'does not respond (no press changed the picture)'}" + probe_msg,
                "next": nxt}


# ------------------------------------------------------------------------------------------------- the gaps' kit part
BLOCKS = ((0x05C00000, 0x05D00000, "VDP1", "sat_vdp1.c"), (0x05E00000, 0x05F80000, "VDP2 VRAM and colour RAM", "sat_vdp2.c"),
          (0x05F80000, 0x05FC0000, "VDP2 registers", "sat_vdp2.c"), (0x05FE0000, 0x05FF0000, "SCU", "sat_scu.c"),
          (0x00100000, 0x00100100, "SMPC", "sat_smpc.c"), (0x00180000, 0x00200000, "backup RAM", "sat_bup.c"),
          (0x05A00000, 0x05B00000, "SCSP sound RAM", "sat_scsp.c"), (0x05B00000, 0x05C00000, "SCSP registers", "sat_scsp.c"),
          (0x05890000, 0x058A0000, "CD block", "sat_cd.c"), (0x02000000, 0x05800000, "A-bus cartridge and CS2", None),
          (0x1FFFFE00, 0x20000000, "SH-2 on-chip (FRT, DMA, divider)", "sat_mem.c"))


def kit_section(ws, sc):
    """The hardware blocks the scope's literal pools name, against the kit file that models each (port/saturn)."""
    cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    data = (ws / cfg["binary"]).read_bytes()
    base = int(str(cfg.get("load_address") or "06000000"), 16)
    bodies = []
    for ln in (ws / "state" / "function_bodies.txt").read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) >= 3 and not ln.startswith("#") and p[0] in sc:
            bodies.append((int(p[-2], 16) - base, int(p[-1], 16) - base))
    regs = {}
    for lo, hi in bodies:  # a function's pool follows its code: scan its block and the 256 bytes after it
        for o in range(lo & ~3, min(hi + 256, len(data) - 3), 4):
            v = struct.unpack_from(">I", data, o)[0]
            a = v & 0x1FFFFFFF if v < 0xFFFFFE00 else 0x1FFFFE00 | (v & 0x1FF)
            for blo, bhi, name, _f in BLOCKS:
                if blo <= a < bhi and (v >> 29) in (0, 1, 7):
                    regs.setdefault(name, set()).add(v)
    kit = pathlib.Path(__file__).resolve().parents[2] / "port" / "saturn"
    out, lacks = [f"Hardware the scope's literal pools address (a static scan of each function body and the 256 bytes "
                  f"after it), by block, against the Saturn kit's file for it ({kit}):", ""], 0
    for _lo, _hi, name, f in BLOCKS:
        if name not in regs:
            continue
        ok = bool(f and (kit / f).is_file())
        lacks += not ok
        out.append(f"- {name}: {len(regs[name])} address(es); kit: {f if ok else 'no file models it'}")
    if not regs:
        out.append("- none found")
    seeds = read_seeds(ws)
    out += ["", "Functions the game calls that have no translated C run at boot in the kit's SH-2 interpreter (--fallback "
            "on, port/saturn/sat_interp.c) and are counted; with --fallback off they are STUB lines.",
            f"- fallback addresses from earlier boot runs (seeds for the next translation and census, "
            f"{seeds_path(ws)}): {len(seeds)}"]
    if seeds:
        rows = [ln.split() for ln in seeds_path(ws).read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")]
        out += [f"  - `{r[0]}`: {r[1] if len(r) > 1 else '?'} entries, from {r[2] if len(r) > 2 else '-'}" for r in rows[:60]]
        out += ["  - ..."] if len(rows) > 60 else []
    return out, {"kit gaps": lacks, "hardware blocks": len(regs), "fallback seeds": len(seeds)}

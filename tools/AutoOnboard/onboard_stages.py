"""The stages of tools/AutoOnboard/autoonboard.py: per stage its contract (what it needs, the existing tool it calls, where it runs, the
verdict it writes) and one adapter per console. An adapter only calls an existing tool and reads what it wrote.

Each adapter is one of: built (calls the tool now), NotBuilt (a hard error naming what it will call), NotApplicable
(declared for that console, with the reason). A stage with no entry for a console is a hard error, never a skip.
Consoles DP has no translator target for (original Xbox, arcade) are refused at the first stage.

Nearest existing: tools/game_setup.py - one console's setup steps with no state, no verdicts and no overview; the
adapters here call it (and gdrom.py, decomp.py through it) rather than repeat it.
"""
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parents[2]
CONSOLES = {  # the four consoles with a translator target: target, Integrations folder, disc image suffixes
    "dreamcast": ("dreamcast_sh4", "DreamCast", (".gdi", ".cue")),
    "saturn": ("saturn_sh2", "SegaSaturn", (".cue", ".iso", ".bin")),
    "gamecube": ("gamecube_ppc", "GameCube", (".iso", ".gcm", ".rvz", ".wia", ".ciso")),  # gcdisc.Disc reads all
    "xbox360": ("xbox360_ppc", "Xbox360", (".iso", ".xex")),
}
REFUSED = {
    "xbox": "original Xbox: DP has no translator target for it (docs/proposals/xbox_original_target.md, not started); "
            "its games are hand-built ports. A target must exist first",
    "arcade": "arcade: DP has no translator target for it; the arcade ports are hand-built C++ with MAME as the "
              "reference. Only a NAOMI (SH-4) program could ride on dreamcast_sh4, and nothing is built for that",
}
ASSET_LIMIT = 5 << 30  # the user's rule: cut a job down when its output is over 5 GB
STATE_DIR = pathlib.Path(".work") / "onboard"


class StageError(Exception):
    """A hard stop: what is missing, and what would supply it. status "partial" (a time-bounded stage that kept its
    progress and resumes: the stage's numbers go in the verdict) still stops the run, as "error" does."""

    def __init__(self, message, missing=(), supply="", status="error", numbers=None):
        super().__init__(message)
        self.missing, self.supply, self.status, self.numbers = list(missing), supply, status, dict(numbers or {})


class NotBuilt:
    kind = "not-built"

    def __init__(self, calls):
        self.what = calls

    def calls(self, ctx):
        return [self.what]


class NotApplicable:
    kind = "not-applicable"

    def __init__(self, reason):
        self.reason = reason

    def calls(self, ctx):
        return []


class Built:
    kind = "built"
    tools = ()  # DP-relative paths it calls: a missing one is a hard error before it runs

    def calls(self, ctx):
        raise NotImplementedError


class Stage:
    def __init__(self, name, does, needs, writes, heavy, adapters):
        self.name, self.does, self.needs, self.writes, self.heavy, self.adapters = (name, does, needs, writes, heavy,
                                                                                    adapters)

    @property
    def remote(self):
        """Runs on the build box in --host mode: everything but the scaffold, the launcher's catalogue (Base is on the
        user's laptop) and the overview."""
        return self.name not in LOCAL_STAGES


LOCAL_STAGES = ("project", "register", "overview")


# ------------------------------------------------------------------------------------------------------------ helpers
def need_tools(ctx, rels):
    gone = [r for r in rels if not (ctx.tool / r).exists()]
    if gone:
        raise StageError(f"missing tool(s) in {ctx.tool}: {', '.join(gone)}", gone,
                         "a DecompPipeline checkout that has them (git pull the private remote's master)")


def ws_of(ctx):
    return ctx.project / "decomp"


# ------------------------------------------------------------------------------------- the machine's paths (config)
def config_local(tool):
    """DecompPipeline's config.local.json (this machine's paths), {} when absent or unreadable."""
    try:
        got = json.loads((pathlib.Path(tool) / "config.local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def set_config_local(tool, updates):
    """Adds keys to config.local.json (made when absent); keys already there keep their values."""
    cfg = config_local(tool)
    cfg.update({k: v for k, v in updates.items() if k not in cfg or not cfg[k]})
    (pathlib.Path(tool) / "config.local.json").write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8",
                                                          newline="\n")


CONFIG_PATTERNS = ("config.local.json", "*/config.local.json", "*/dp/config.local.json", "*/*/dp/config.local.json",
                   "*/*/config.local.json")


def sibling_configs(tool, levels=3):
    """Other DecompPipeline checkouts' config.local.json on this machine, newest first: up to `levels` folders above this
    checkout (never a drive's root), each searched by CONFIG_PATTERNS. Build box A's C:/dpx/<Game>/dp clones and a worktree's
    main checkout are found this way (onboard_remote's UPDATE takes ghidra_home from the same places)."""
    own = (pathlib.Path(tool) / "config.local.json").resolve()
    found = {}
    for up in list(pathlib.Path(tool).resolve().parents)[:levels]:
        if up.parent == up:
            continue
        for pat in CONFIG_PATTERNS:
            for f in up.glob(pat):
                try:
                    if f.resolve() != own and f.is_file():
                        found[f.resolve()] = f.stat().st_mtime
                except OSError:
                    continue
    return sorted(found, key=found.get, reverse=True)


def phangrade_catalogue(ctx):
    """Phangrade's launcher catalogue (Base/data/catalogue.json, which Base builds into Phangrade.exe): found above the
    project or this DecompPipeline checkout, else machines.local.json "phangrade_base". Never a file under a dist folder
    (the installed launcher); a StageError when there is none here (a build box)."""
    cands = []
    try:
        named = json.loads((ctx.tool / "machines.local.json").read_text(encoding="utf-8")).get("phangrade_base")
    except (OSError, ValueError):
        named = None
    if named:
        cands.append(pathlib.Path(named) / "data" / "catalogue.json")
    for start in (ctx.project, ctx.tool):
        cands += [up / "Base" / "data" / "catalogue.json" for up in pathlib.Path(start).resolve().parents]
    for c in cands:
        if c.is_file() and not any(p.lower() == "dist" for p in c.parts):
            return c
    raise StageError(f"no Phangrade launcher catalogue (Base/data/catalogue.json) above {ctx.project} or {ctx.tool}",
                     ["Phangrade's Base checkout on this machine"],
                     "run the register stage on the user's laptop (autoonboard.py --host runs it there), or set "
                     "\"phangrade_base\" in machines.local.json")


GAME_FONTS = ("*.ttf", "*.otf", "OFL*.txt")  # a game's fonts and their licences: port/game/fonts, flat in game_src


def place_fonts(game_src, exe_dir):
    """The fonts a game's own code loads (game_src's GAME_FONTS) into <exe_dir>/fonts, which the game reads beside its exe
    (Dragon Force's clearer text, port/game/df_text.c); an old folder goes. Returns the names."""
    names = sorted({f.name for g in GAME_FONTS for f in pathlib.Path(game_src).glob(g)})
    shutil.rmtree(pathlib.Path(exe_dir) / "fonts", ignore_errors=True)
    if names:
        (pathlib.Path(exe_dir) / "fonts").mkdir(parents=True)
        for n in names:
            shutil.copyfile(pathlib.Path(game_src) / n, pathlib.Path(exe_dir) / "fonts" / n)
    return names


def log_path(ctx, stage):
    return ctx.project / STATE_DIR / f"{stage}.log"


REUSED = "jobreg: NOT RUN"  # tools/jobreg.py: the same inputs finished before, nothing ran


def run_tool(ctx, stage, argv, seconds, why=None, produces=None, changed=None):
    """argv (DP-relative script first) under tools/bounded.py; a heavy job (why given) also under tools/jobreg.py on
    this machine. Output goes to the stage's log. Returns (exit code, last lines).
    produces: a path the job writes. When jobreg points to an earlier finished job instead of running (its key cannot
    see that this project was made again, e.g. a scratch project deleted and rebuilt) and the path is not there, the job
    runs once more with --changed naming that: the earlier result is not in this project.
    changed: an input the key cannot see, named for jobreg --changed from the start (a resumable job's own progress)."""
    base = [sys.executable, str(ctx.tool / "tools" / "bounded.py"), str(seconds), "--", sys.executable,
            str(ctx.tool / argv[0]), *map(str, argv[1:])]
    log = log_path(ctx, stage)
    log.parent.mkdir(parents=True, exist_ok=True)
    changed = "; ".join(x for x in (changed, getattr(ctx, "changed", None)) if x) or None
    while True:
        cmd = base
        if why:
            cmd = [sys.executable, str(ctx.tool / "tools" / "jobreg.py"), "run", "--why", why,
                   *(["--changed", changed] if changed else []), "--", *base]
        start = log.stat().st_size if log.exists() else 0
        with open(log, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n$ {subprocess.list2cmdline(cmd)}\n")
            f.flush()
            r = subprocess.run(cmd, cwd=ctx.tool, stdout=f, stderr=subprocess.STDOUT, timeout=seconds + 300)
        with open(log, encoding="utf-8", errors="replace") as f:
            f.seek(start)
            new = f.read()
        if (why and produces is not None and "is not in this project" not in (changed or "") and r.returncode == 0
                and REUSED in new
                and not pathlib.Path(produces).exists()):
            changed = "; ".join(x for x in (changed, f"{produces} is not in this project: the earlier job's result is "
                                                     "gone (project made again)") if x)
            continue
        return r.returncode, new.splitlines()[-8:] or log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]


def ghidra_project(ws, cfg):
    """The workspace's Ghidra project file (.gpr); a StageError when Ghidra would refuse its folder (a path element
    starting with '.', as under .work: "Path element starting with '.' is not permitted")."""
    gdir = pathlib.Path(cfg["ghidra_dir"]) if cfg.get("ghidra_dir") else ws
    dotted = [part for part in gdir.parts if part.startswith(".")]
    if dotted:
        raise StageError(f"Ghidra refuses its project under {gdir}: the path element {dotted[0]} starts with '.'",
                         [f"\"ghidra_dir\" in {ws / 'project.json'}: a folder with no element starting with '.'"],
                         "set ghidra_dir (core/paths) to such a folder, then run again. A game folder under "
                         "Integrations has none by default; a project under .work needs one outside it (8.3 "
                         "short names are off on build box B's D:)")
    return gdir / "ghidra" / f"{cfg['ghidra_name']}.gpr"


def import_tools(ctx, target):
    for p in (ctx.tool, ctx.tool / "tools", ctx.tool / "targets" / target / "tools"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def find_image(ctx, suffixes):
    """The disc image: --disc, else the one file in the project root with one of the console's suffixes."""
    if ctx.disc:
        image = pathlib.Path(ctx.disc).resolve()
        if not image.is_file():
            raise StageError(f"--disc {image}: no such file", [str(image)], "the path of the disc image")
        return image
    found = sorted(p for p in ctx.project.iterdir() if p.is_file() and p.suffix.lower() in suffixes)
    if not found:
        raise StageError(f"no disc image in {ctx.project} (looked for {' '.join(suffixes)} in the folder itself)",
                         [f"a {ctx.console} disc image ({' or '.join(suffixes)}) in {ctx.project}"],
                         "the user puts the disc image in the project root (or pass --disc PATH)")
    if len(found) > 1:
        raise StageError(f"{len(found)} disc images in {ctx.project}: {', '.join(p.name for p in found)}",
                         ["one disc image, not several"], "pass --disc PATH to say which")
    return found[0]


# ----------------------------------------------------------------------------------------------- project (scaffold)
GITIGNORE = """# {game}: an allow-list written by DecompPipeline tools/AutoOnboard/autoonboard.py. Everything is ignored unless listed.
# Never in git, even in a private repository: the disc image, extracted game data, SDK material, Ghidra projects,
# harnesses, test vectors, and any decompiled or translated C (the user's rule). The translator stays pushed in
# DecompPipeline instead, so that C is regenerated from the disc. Hand-written native code lives in src/, port/src/.
/*
!/.gitignore
!/README.md
!/LOCATIONS.md
!/docs/
!/scripts/
!/src/
!/port/
/port/build/
!/decomp/
/decomp/*
!/decomp/*.md
!/decomp/project.json
!/decomp/oracle/
# Decompiled, translated or generated C and headers: never tracked, wherever they land ...
*.c
*.h
*.cc
*.cpp
*.inc
# ... except hand-written native sources.
!/src/**/*.c
!/src/**/*.h
!/src/**/*.cpp
!/port/src/**/*.c
!/port/src/**/*.h
!/port/src/**/*.cpp
# Backups and locks anywhere.
*.bak*
*.lock
*.orig
__pycache__/
*.pyc
"""
MUST_IGNORE = ("decomp/matched/8c010000.c", "decomp/readable/src/s01/f.c", "decomp/work/8c010000.c",
               "decomp/reference/8c010000.c", "decomp/readable/include/game.h", "decomp/oracle/gen.c",
               "port/translated/8c010000.c", "port/tx/raw.c", "docs/raw.c", "8c010000.c", "game-data/1ST_READ.BIN",
               "decomp/disc/1ST_READ.BIN", "decomp/ghidra/x.gpr", "decomp/harness/8c010000/meta.json",
               "game.gdi", "game.cue", "track01.bin", "game.iso", "port/build/port.exe")
MUST_TRACK = (".gitignore", "LOCATIONS.md", "decomp/project.json", "decomp/oracle/TcInputs.java",
              "port/src/hardware.c", "src/main.cpp", "port/game.json", "docs/NOTES.md")


def git(project, *args, timeout=60):
    return subprocess.run(["git", "-c", "gc.auto=0", "-C", str(project), *args], capture_output=True, text=True,
                          timeout=timeout)


def check_ignore(project, extra=()):
    """(tracked paths that should be ignored, ignored paths that should be tracked) under the project's .gitignore."""
    probes = list(MUST_IGNORE) + list(extra) + list(MUST_TRACK)
    r = git(project, "check-ignore", "--no-index", *probes)
    if r.returncode not in (0, 1):
        raise StageError(f"git check-ignore failed: {r.stderr.strip()[:300]}", ["a working git"], "install git")
    ignored = set(r.stdout.split("\n"))
    return ([p for p in list(MUST_IGNORE) + list(extra) if p not in ignored], [p for p in MUST_TRACK if p in ignored])


class Scaffold(Built):
    tools = ("core/locations.py",)

    def calls(self, ctx):
        return ["git init", "write .gitignore (allow-list) and check it with git check-ignore",
                "core/locations.write_skeleton (LOCATIONS.md)", "mkdir .work/onboard"]

    def run(self, ctx):
        p, made = ctx.project, []
        (p / STATE_DIR).mkdir(parents=True, exist_ok=True)
        if not (p / ".git").exists():
            r = git(p, "init", "-q", "-b", "main")
            if r.returncode:
                raise StageError(f"git init failed: {r.stderr.strip()[:300]}", ["git"], "install git")
            made.append("git repository (local only, no remote)")
        gi = p / ".gitignore"
        if not gi.exists():
            gi.write_text(GITIGNORE.format(game=ctx.name), encoding="utf-8", newline="\n")
            made.append(".gitignore")
        images = [q.name for q in p.iterdir() if q.is_file() and q.suffix.lower() in (".gdi", ".cue", ".bin", ".iso",
                                                                                         ".gcm", ".rvz", ".wia", ".ciso",
                                                                                         ".xex")]
        leaks, hidden = check_ignore(p, images)
        if leaks or hidden:
            raise StageError(f"{gi} would let git track {leaks} or hides {hidden}",
                             [f"{gi} ignoring: {', '.join(leaks)}" if leaks else f"{gi} tracking: {', '.join(hidden)}"],
                             "fix the .gitignore (or delete it: this stage writes the allow-list), then run again")
        sys.path.insert(0, str(ctx.tool))
        from core import locations
        loc = locations.write_skeleton(ws_of(ctx), ctx.name)
        if loc:
            made.append("LOCATIONS.md (skeleton)")
        home = ctx.project.parent.parent
        notes = []
        if not (home.name == "Integrations" and ctx.project.parent.name == CONSOLES[ctx.console][1]):
            notes.append(f"not under Integrations\\{CONSOLES[ctx.console][1]}\\<Game> (a scratch or test project)")
        return {"numbers": {"made": len(made), "gitignore probes": len(MUST_IGNORE) + len(images) + len(MUST_TRACK)},
                "paths": {"project": str(p), "locations": str(locations.file_for(ws_of(ctx)))},
                "message": ("made " + ", ".join(made) if made else "all there already")
                           + "".join("; " + n for n in notes),
                "next": ["Fill in LOCATIONS.md per machine (AGENTS.md); a private GitHub repo "
                         f"<Game>_{CONSOLES[ctx.console][1]}_NativePort is made by a person"]}


# ---------------------------------------------------------------------------------------------------- the stage table
# Built on first use (module __getattr__), not at import: every adapter module imports this one, so a table built here
# at import time made "import onboard_symbols" (or onboard_x360, onboard_dc, onboard_gc) before this module a circular
# import that failed. Now any adapter module imports on its own.
NO_SYMBOLS = ("{c}: no symbol-matching route. DP's symbol matching is the Xbox 360's (pdb_names.py), the GameCube's "
              "(gc_names.py) and the Saturn's FLIRT sets (sgl_flirt.py); {why}")
PLAIN = {"project": "set up the game folder", "disc": "read the disc", "extract": "copy the game's files off the disc",
         "census": "find every function in the game's code", "symbols": "look for debug symbols",
         "estimate": "estimate time and disk from a sample", "translate": "translate every function (no AI agents)",
         "gaps": "list what is not translated, and why", "boot": "build the translated game and start it",
         "machine": "check this machine's build settings and the console's shared files",
         "register": "put the game in the Phangrade launcher's catalogue"}


def _table():
    import onboard_dc as DC
    import onboard_estimate as EST
    import onboard_gaps as GAPS
    import onboard_gc as GC
    import onboard_sat as SAT
    import onboard_symbols as SYM
    import onboard_x360 as X360

    def per(dc, x360, gc, saturn):
        return {"dreamcast": dc, "xbox360": x360, "gamecube": gc, "saturn": saturn}
    stages = [
        Stage("project", "game folder, local git repo, allow-list .gitignore (no disc data, no decompiled C), "
              "LOCATIONS.md skeleton, .work/onboard", (), ".gitignore, LOCATIONS.md, .work/onboard/", False,
              {**{c: Scaffold() for c in CONSOLES}, "saturn": SAT.Project()}),
        Stage("machine", "this machine's build settings (config.local.json: Ghidra, 32-bit MSVC, vcpkg) and the "
              "console's shared files (Saturn: the FLIRT signature library): taken from another checkout or a known "
              "place when missing, else what to set, exactly", ("project",), "config.local.json (missing keys only)",
              False, {**{c: NotApplicable(f"{c}: its boot and census stages name their own missing settings")
                         for c in CONSOLES}, "saturn": SAT.Machine()}),
        Stage("disc", "find the disc image in the project root, identify the console, list and scan its files",
              ("project",), ".work/onboard/disc_scan.tsv", False,
              per(DC.Disc(), X360.Disc(), GC.Disc(), SAT.Disc())),
        Stage("extract", "the main binary into a workspace (project.json, TcInputs.java) and every disc file into "
              "game-data/", ("disc",), "decomp/, game-data/", False,
              per(DC.Extract(), X360.Extract(), GC.Extract(), SAT.Extract())),
        Stage("census", "Ghidra catalog of every function, the decode check, counts by range, size and hardware use",
              ("extract",), "decomp/state/catalog.json, decode_check.tsv", True,
              per(DC.Census(), X360.Census(), GC.Census(), SAT.Census())),
        # After the census: the GameCube's names are matched to the census catalog (no catalog before Ghidra there).
        Stage("symbols", "find, match and verify symbol sets (PDB, MAP, ELF) from the write-ups", ("disc", "extract"),
              ".work/onboard/symbols/names.json (git-ignored)", True,
              per(NotApplicable(NO_SYMBOLS.format(c="Dreamcast", why="the 7 sets in Integrations\\DreamCast\\PDBs "
                                                  "are other games' MAP/DWARF files with no matcher; SDK names come "
                                                  "later from tools/sdk_fid.py with a Sega SDK")),
                  SYM.Symbols(SYM.X360), SYM.Symbols(SYM.GAMECUBE), SAT.Symbols())),
        Stage("boot", "a bounded headless boot of the translator's C on the console's kit: built or not, frames "
              "reached, the first error", ("census",), ".work/onboard/boot.log, port/game.json (a starter)", True,
              per(DC.Boot(), X360.Boot(), GC.Boot(), SAT.Boot())),
        Stage("register", "the game's entry in Phangrade's launcher catalogue (Base/data/catalogue.json), starting the "
              "game folder's Start Game.bat: added when missing, missing keys filled, a person's values kept; never "
              "dist, no commit, no release", ("boot",), "Base/data/catalogue.json, .work/onboard/catalogue_entry.json",
              False, {**{c: NotApplicable(f"{c}: its launcher entry is written with its first release (Base "
                                           "docs/port-contract.md); the kit's start path differs") for c in CONSOLES},
                      "saturn": SAT.Register()}),
        Stage("estimate", "translate a seeded sample, extrapolate hours and pass rate to the whole scope", ("census",),
              ".work/onboard/estimate_detail.json", True,
              per(EST.Estimate(), EST.Estimate(), EST.Estimate(), EST.Estimate())),
        Stage("translate", "the translator's first pass over the scope in bounded chunks, no agents", ("census",),
              "decomp/state/ledger.json (source translated)", True,
              per(EST.Translate(), EST.Translate(), EST.Translate(), EST.Translate())),
        Stage("gaps", "GAPS.md: untranslated functions by cause, what the kit lacks for this game; partial while the "
              "translator pass is not finished", ("census",), "GAPS.md", False,
              {c: GAPS.Gaps() for c in CONSOLES}),
    ]
    names = [s.name for s in stages] + ["overview"]
    return {"STAGES": stages, "NAMES": names, "BY_NAME": {s.name: s for s in stages}}


_BUILT = {}


def __getattr__(name):
    """STAGES, NAMES and BY_NAME, built once on first use (see the note above _table)."""
    if name in ("STAGES", "NAMES", "BY_NAME"):
        if not _BUILT:
            _BUILT.update(_table())
        return _BUILT[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def machine():
    return os.environ.get("COMPUTERNAME") or platform.node()

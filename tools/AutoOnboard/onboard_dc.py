"""The Dreamcast adapters of tools/AutoOnboard/autoonboard.py's stages (onboard_stages.STAGES): disc, extract and census, each calling
the existing tools (tools/game_setup.py, targets/dreamcast_sh4/tools/gdrom.py, decomp.py setup through game_setup).

Nearest existing: tools/game_setup.py - the Dreamcast setup steps themselves, which these call; it keeps no verdicts,
stops a re-run on its own folders and does not extract the disc's other files. One file per console keeps each under
the 400-line cap as the other consoles' adapters arrive.
"""
import contextlib
import io
import json
import pathlib
import re
import shutil
import sys

import onboard_stages as S


class Disc(S.Built):
    tools = ("targets/dreamcast_sh4/tools/gdrom.py", "tools/game_setup.py")

    def calls(self, ctx):
        return ["find the .gdi/.cue in the project root", "tools/game_setup.detect", "gdrom.py info + list",
                "gdrom.py scan -> .work/onboard/disc_scan.tsv"]

    def run(self, ctx):
        image = S.find_image(ctx, S.CONSOLES["dreamcast"][2])
        S.import_tools(ctx, "dreamcast_sh4")
        import game_setup
        import gdrom
        try:
            target = game_setup.detect(image)
        except SystemExit as e:
            raise S.StageError(str(e), [f"a Dreamcast boot header in {image}"], "the right disc image")
        if target != "dreamcast_sh4":
            raise S.StageError(f"{image} is a {target} disc; this project is {ctx.console}", ["a Dreamcast disc"],
                             "the right disc image, or onboard it as its own console's project")
        tsv = ctx.project / S.STATE_DIR / "disc_scan.tsv"
        try:
            disc = gdrom.Disc(str(image))
            ip, files = disc.ip(), disc.walk()
            ip = {k: v.split("\0")[0].strip() for k, v in ip.items()}  # IP.BIN fields: space or NUL padded
            with contextlib.redirect_stdout(io.StringIO()) as out:
                gdrom.scan(disc, tsv)
        except (OSError, ValueError, IndexError, SystemExit) as e:
            raise S.StageError(f"{image}: unreadable ({type(e).__name__}: {e})", [f"a complete image ({image.name} "
                             "and every track file it names)"], "the disc image again")
        S.log_path(ctx, "disc").write_text(out.getvalue(), encoding="utf-8")
        rows = [ln.split("\t") for ln in tsv.read_text(encoding="utf-8").splitlines()[1:]]
        code = [r for r in rows if float(r[2]) >= gdrom.CODE_PER_KB or r[0] == ip["boot_file"]]
        boot = next((r for r in rows if r[0] == ip["boot_file"] or r[0].split("/")[-1] == ip["boot_file"]), None)
        if boot is None:
            raise S.StageError(f"{image}: the boot file {ip['boot_file']} that IP.BIN names is not on the disc",
                             [ip["boot_file"]], "a good dump of the disc")
        p1, p0 = int(boot[3]), int(boot[4])
        return {"numbers": {"title": ip["title"], "product": ip["product"], "version": ip["version"],
                            "boot file": ip["boot_file"], "files": len(files), "MB": round(sum(s for _, _, s in files)
                                                                                          / 2**20, 1),
                            "code files": len(code), "link": "P0 (seed_functions)" if p0 > 4 * max(p1, 1) else "P1"},
                "paths": {"image": str(image), "scan": str(tsv)},
                "message": f"{ip['title']} ({ip['product']} {ip['version']}), {len(code)} file(s) look like SH-4 code",
                "next": [f"Look at the code files in {tsv.name} other than the boot file: an overlay of the game's "
                         "own needs a workspace of its own (docs/NEW_GAME.md step 1)"] if len(code) > 1 else []}


class Extract(S.Built):
    tools = ("tools/game_setup.py", "targets/dreamcast_sh4/tools/gdrom.py", "tools/bounded.py")

    def calls(self, ctx):
        return ["tools/game_setup.py setup IMAGE <project>/decomp --no-ghidra --name NAME",
                "targets/dreamcast_sh4/tools/gdrom.py extract IMAGE <project>/game-data --all"]

    def run(self, ctx):
        image = pathlib.Path(ctx.verdicts["disc"]["paths"]["image"])
        S.import_tools(ctx, "dreamcast_sh4")
        import game_setup
        import gdrom
        disc = gdrom.Disc(str(image))
        boot, files = disc.ip()["boot_file"], disc.walk()
        lba, size = next((lba, s) for p, lba, s in files if p == boot or p.split("/")[-1] == boot)
        want_sha = game_setup.sha256(disc.read(lba, size))
        ws, reused = S.ws_of(ctx), []
        if (ws / "project.json").exists():
            have = json.loads((ws / "project.json").read_text(encoding="utf-8")).get("binary_sha256")
            if have != want_sha:
                raise S.StageError(f"{ws / 'project.json'} is of another boot file ({have}, the disc's is {want_sha})",
                                 [f"a workspace of this disc at {ws}"], f"move {ws} away, then run again")
            reused.append("workspace")
        elif (ws / "disc").exists():
            raise S.StageError(f"{ws / 'disc'} exists without project.json: an earlier extract stopped part way",
                             [f"{ws / 'disc'} removed"], f"delete {ws / 'disc'} (it holds only that extract), then run "
                             "again")
        else:
            rc, tail = S.run_tool(ctx, "extract", ["tools/game_setup.py", "setup", image, ws, "--no-ghidra", "--name",
                                                 ctx.name], 900)
            if rc:
                raise S.StageError(f"game_setup.py setup exited {rc}: {' | '.join(tail[-3:])}",
                                 ["a workspace from game_setup.py"], f"read {S.log_path(ctx, 'extract')}")
        total = sum(s for _, _, s in files)
        if total > S.ASSET_LIMIT:
            raise S.StageError(f"the disc's files are {total / 2**30:.1f} GB, over the 5 GB output limit",
                             ["a smaller extraction"], "the user decides what to extract (CLAUDE.md, 5 GB rule)")
        data = ctx.project / "game-data"
        have = sum(1 for q in data.rglob("*") if q.is_file()) if data.exists() else 0
        if data.exists() and have == len(files):
            reused.append("game-data")
        elif data.exists():
            raise S.StageError(f"{data} holds {have} of the disc's {len(files)} files: an earlier extract stopped "
                               "part way", [f"{data} removed"], f"delete {data} (disc data only), then run again")
        else:
            rc, tail = S.run_tool(ctx, "extract", ["targets/dreamcast_sh4/tools/gdrom.py", "extract", image, data,
                                                 "--all"], 1800)
            if rc:
                raise S.StageError(f"gdrom.py extract exited {rc}: {' | '.join(tail[-3:])}", ["the disc's files"],
                                 f"read {S.log_path(ctx, 'extract')}")
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        return {"numbers": {"boot bytes": size, "game_end": cfg.get("game_end"),
                            "seed_functions": bool(cfg.get("seed_functions")), "asset files": len(files),
                            "asset MB": round(total / 2**20, 1)},
                "paths": {"workspace": str(ws), "binary": str(ws / cfg["binary"]), "game-data": str(data)},
                "message": ("reused " + ", ".join(reused) if reused else "extracted")
                           + f"; boot file sha256 {want_sha[:12]}",
                "next": []}


# The one finished Dreamcast project, Toy Commander: what this census finds on its disc (build box B, 2026-11-15) and what
# its finished catalog holds. Of the 146 it lacks, 24 were added by hand (project.json catalog_overrides: 8 entry, 13
# from the entry table at 8c0a5598; extra_functions: 3) and 122 by catalog rules added while the project ran (batch 2b
# 81, batch 12 TcRuns 37, batch 13 4), which a fresh run of those rules does not reproduce here. A floor, not the game.
# Measured on that disc (build box B, 2026-11-15, onboarding phase 3): giving a scratch census the 24 hand entries finds 28 of
# the 146 (21 hand, 7 of the 122 rule-made); the rule-made ones follow from them only in small part. The automatic source
# that exists, tools/data_funcs.py (code addresses in the image's data tables), named 13 candidates, all real and all
# among the 146, but feeding them in found 15 of 146 with 1 false extra and lost 2 the plain census had: not wired in.
# The 8c0a5598-style table (305 entries) is not one data_funcs finds. So a new game should expect about this shortfall.
REFERENCE = {"game": "Toy Commander", "found": 4040, "finished": 4183, "by hand": 24, "by later rules": 122,
             "data tables": "15 of 146, 1 false extra"}
ENTRY_TABLES_NEXT = ("Entry tables are not scanned: run tools/data_funcs.py on the workspace and add the code addresses it "
                     "names to project.json catalog_overrides.entry (Toy Commander: 13 named, all real), and look for "
                     "tables of code pointers like Toy Commander's 8c0a5598 (catalog_overrides.entry_tables)")


def denominator(n):
    r = REFERENCE
    more = (r["finished"] - r["found"]) / r["found"] * 100
    return (f"found by analysis: {n} functions. Not the whole game: {r['game']}, the finished Dreamcast project, "
            f"needed {more:.1f}% more than analysis of its disc finds ({r['found']} -> {r['finished']}), added as they "
            f"were discovered: {r['by hand']} by hand (catalog_overrides, extra_functions), {r['by later rules']} by "
            "catalog rules written during that project. Expected shortfall for a new game: about the same, since entry "
            f"tables are not scanned (on {r['game']} the existing data-table scan found {r['data tables']})")


class Census(S.Built):
    tools = ("tools/game_setup.py", "decomp.py", "tools/jobreg.py", "tools/bounded.py", "core/scope.py")

    def calls(self, ctx):
        return ["tools/jobreg.py run -- tools/bounded.py 5400 -- tools/game_setup.py ghidra <project>/decomp "
                "(decomp.py setup: Ghidra import, analysis, catalog; then the decode check)",
                "read state/catalog.json, state/game_setup.json: counts"]

    def run(self, ctx):
        ws = S.ws_of(ctx)
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        gpr = S.ghidra_project(ws, cfg)
        gdir = gpr.parent.parent
        cat_f, timing_f = ws / "state" / "catalog.json", ws / "state" / "game_setup.json"
        timing = json.loads(timing_f.read_text(encoding="utf-8")) if timing_f.exists() else {}
        if gpr.exists() and not cat_f.exists():
            raise S.StageError(f"{gpr} exists without state/catalog.json: an earlier Ghidra setup stopped part way",
                             [f"{gdir / 'ghidra'} rebuilt"], f"py -3.12 decomp.py --project {ws} setup --force (under "
                             "tools/jobreg.py and tools/bounded.py), then run again")
        if not (cat_f.exists() and timing.get("decode_check", {}).get("passes")):
            rc, tail = S.run_tool(ctx, "census", ["tools/game_setup.py", "ghidra" if not gpr.exists() else "check", ws],
                                5400, why=f"onboard census of {ctx.name}: Ghidra catalog and decode check",
                                produces=cat_f)
            timing = json.loads(timing_f.read_text(encoding="utf-8")) if timing_f.exists() else {}
            if rc or not cat_f.exists():
                raise S.StageError(f"game_setup.py exited {rc}: {' | '.join(tail[-3:])}",
                                 ["a Ghidra catalog with a passing decode check"],
                                 f"read {S.log_path(ctx, 'census')} and {ws / 'state'}")
        sys.path.insert(0, str(ctx.tool))
        from core import scope
        funcs = json.loads(cat_f.read_text(encoding="utf-8"))["functions"]
        ranges, extras = scope.game_ranges(cfg), set(scope.listed(cfg, ws))
        game = [f for f in funcs if any(lo <= int(f["addr"], 16) < hi for lo, hi in ranges)]
        bands = {"<64 B": 0, "64-511 B": 0, "512-4095 B": 0, ">=4 KB": 0}
        for f in game:
            b = f.get("bytes", 0)
            bands["<64 B" if b < 64 else "64-511 B" if b < 512 else "512-4095 B" if b < 4096 else ">=4 KB"] += 1
        dc = timing.get("decode_check", {})
        return {"numbers": {"functions": len(funcs), "found by": "analysis only (a floor, see message)",
                            "game range": len(game),
                            "outside (library)": len(funcs) - len(game) - len(extras & {f['addr'] for f in funcs}),
                            "extra_functions": len(extras), "hw_refs": sum(1 for f in game if f.get("hw_refs")),
                            "sys_ops": sum(1 for f in game if f.get("sys_ops")), **bands,
                            "decode check": "passes" if dc.get("passes") else "FAILS",
                            "ghidra s": timing.get("ghidra_setup", {}).get("seconds")},
                "paths": {"catalog": str(cat_f), "decode_check": str(ws / "state" / "decode_check.tsv")},
                "message": f"{denominator(len(funcs))}; {len(game)} in the game range (game_end {cfg.get('game_end')})",
                "next": ["Narrow game_end to where the SDK starts (docs/NEW_GAME.md step 3: tools/sdk_fid.py, "
                         "tools/sdk_classify.py) and pick the references and broken control (step 2)", ENTRY_TABLES_NEXT]}


BOOT_S, BOOT_RUN_S = 1500, 20  # the build and a 20-second fixed-clock headless run (docs/NEW_GAME.md step 8.4)
BOOT_DIR = "onboard"  # port/tools/build.py --build-dir: build/onboard*, never the user's build/x86
DP_JUDGE = "targets/dreamcast_sh4/translator/tools/dp_judge.py"
ERROR_LINE = re.compile(r"error|fault|exception|must_work|stopped|missing|cannot|TIMEOUT", re.I)


def starter(ctx, image):
    """port/game.json from what the project knows (port/CONTRACT.md keys). The entry is left empty: which function the
    port calls first is a person's choice a script cannot make (Toy Commander's is Game_Main, not the boot code)."""
    sys.path.insert(0, str(ctx.tool / "port" / "tools"))
    import game_config
    cfg = json.loads((S.ws_of(ctx) / "project.json").read_text(encoding="utf-8"))
    disc = None
    if image:
        image = pathlib.Path(image).resolve()
        disc = "../" + image.name if image.parent == ctx.project.resolve() else image.as_posix()
    return {"name": ctx.name, "contract": ".".join(map(str, game_config.contract_version())),
            "decomp": "../decomp", "entry": {"name": None, "addr": None},
            "image_base": str(cfg.get("load_address") or "8c010000"), "boot_file": None, "disc": disc,
            "sdk": [], "sources": [], "hardware": {},
            "translator_c": {"dir": "../decomp/translated_port", "mode": "only"},
            "boot_sha256": [cfg["binary_sha256"]] if cfg.get("binary_sha256") else []}


def boot_inputs(ctx, gj):
    """[(what is missing, what supplies it)] before a build can start."""
    sys.path.insert(0, str(ctx.tool / "port" / "tools"))
    import game_config
    g, ws, out = json.loads(gj.read_text(encoding="utf-8")), S.ws_of(ctx), []
    entry = g.get("entry") or {}
    if not (entry.get("name") and entry.get("addr")):
        out.append((f"{gj} \"entry\": the game's first function to call, void name(void), name and address",
                    "a person's choice (Toy Commander: Game_Main 8c02796a, the main loop the boot code calls); "
                    "it must be named in the registry"))
    names = {}
    for f in sorted((ws / "readable" / "names").glob("*.json")):
        try:
            names.update((json.loads(f.read_text(encoding="utf-8")).get("functions") or {}))
        except ValueError:
            pass
    if not names:
        out.append((f"a function registry ({ws / 'readable' / 'names'}\\*.json): game.h and the dispatch table are "
                    "generated from it", "decomp.py systems, then tools/name_pass.py or a design pass (docs/NEW_GAME.md "
                    "step 5)"))
    elif entry.get("addr") and not re.search(r"^\s*void\s+\w+\s*\(\s*(void)?\s*\)",
                                             (names.get(entry["addr"]) or {}).get("proto", "")):
        out.append((f"a registry entry {entry['addr']} with a void(void) prototype", "the registry (readable/names)"))
    tx = (g.get("translator_c") or {}).get("dir")
    txd = (gj.parent / tx).resolve() if tx else None
    if not txd or not any(txd.glob("*.c")):
        out.append((f"the translator's C judged in port form ({txd or 'game.json translator_c dir'})",
                    f"py -3.12 {DP_JUDGE} --ws {ws} --out {txd or 'DIR'} --sp-input --all (a judge run over every "
                    "translated function, heavy: a build box)"))
    if not game_config.vcvars32():
        out.append(("32-bit MSVC (vcvars32.bat)", 'Visual Studio 2022 C++ and config.local.json "vcvars"'))
    if not (shutil.which("ninja") or (__import__("os").environ.get("NINJA"))):
        out.append(("ninja on PATH", "py -3.12 -m pip install ninja"))
    return out


def label(what):
    """A missing input's short name: its text up to ' (' or ': ' (a path's own 'D:\\' is not a cut)."""
    return re.split(r" \(|: ", what, maxsplit=1)[0]


def port_log_facts(log):
    """(vertical blanks reached, scenes drawn, the first error line) from a kit run's port.log."""
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    vbs = [int(x) for x in re.findall(r"vblank (\d+)", text)]
    first = next((ln.strip() for ln in text.splitlines() if ERROR_LINE.search(ln)), None)
    return (max(vbs) if vbs else 0), len(re.findall(r"^ta: scene \d+", text, re.M)), first


class Boot(S.Built):
    tools = ("port/tools/build.py", "port/tools/gen_port.py", "port/tools/game_config.py", "tools/jobreg.py",
             "tools/bounded.py")

    def calls(self, ctx):
        return ["port/game.json: a starter when there is none (the entry left to a person)",
                "check the build's inputs: entry, registry, translator C in port form, MSVC, ninja",
                f"tools/jobreg.py run -- tools/bounded.py {BOOT_S} -- port/tools/build.py --game <project>/port "
                f"--build-dir {BOOT_DIR} --no-build-gc --run --seconds {BOOT_RUN_S} -- --fixed-clock 1",
                f"read port/build/{BOOT_DIR}_run/port.log: vertical blanks, scenes drawn, the first error line"]

    def run(self, ctx):
        port, made = ctx.project / "port", []
        gj = port / "game.json"
        if not gj.exists():
            port.mkdir(parents=True, exist_ok=True)
            gj.write_text(json.dumps(starter(ctx, ctx.verdicts.get("disc", {}).get("paths", {}).get("image")),
                                     indent=1) + "\n", encoding="utf-8", newline="\n")
            made.append(f"wrote a starter {gj}")
        gone = boot_inputs(ctx, gj)
        if gone:
            lab = [label(w) for w, _ in gone]
            raise S.StageError("no build tried: " + "; ".join(made + [f"{len(gone)} input(s) missing: " + ", ".join(lab)]),
                               [w for w, _ in gone], " | ".join(f"{x}: {s}" for x, (_, s) in zip(lab, gone)))
        rc, tail = S.run_tool(ctx, "boot", ["port/tools/build.py", "--game", port, "--build-dir", BOOT_DIR,
                                            "--no-build-gc", "--run", "--seconds", BOOT_RUN_S, "--", "--fixed-clock",
                                            "1"], BOOT_S, why=f"onboard boot try of {ctx.name}: build and a "
                                                              f"{BOOT_RUN_S} s headless run")
        exe = next((port / "build" / BOOT_DIR).glob("**/port.exe"), None) if (port / "build" / BOOT_DIR).exists() \
            else None
        vb, scenes, first = port_log_facts(port / "build" / f"{BOOT_DIR}_run" / "port.log")
        nums = {"built": bool(exe), "vertical blanks": vb, "scenes drawn": scenes, "exit": rc}
        if rc or not exe:
            raise S.StageError(f"boot: {'built' if exe else 'not built'}, build.py exit {rc}, {vb} vertical blanks, "
                               f"{scenes} scenes; first error: {first or ' | '.join(tail[-2:])}",
                               ["a port that builds and runs" if exe else "a port that builds"],
                               f"read {S.log_path(ctx, 'boot')} and port/build/{BOOT_DIR}_run/port.log; stubs the "
                               "start cannot pass go in port/must_work.txt (docs/NEW_GAME.md step 8.4)")
        return {"numbers": nums, "paths": {"game.json": str(gj), "port.log": str(port / "build" / f"{BOOT_DIR}_run" /
                                                                                  "port.log")},
                "message": f"built and ran {BOOT_RUN_S} s headless: {vb} vertical blanks, {scenes} scenes drawn; "
                           f"first error: {first or 'none'}",
                "next": ["Replace the stubs the log shows first (docs/NEW_GAME.md step 8)"]}

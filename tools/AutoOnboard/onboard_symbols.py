"""tools/AutoOnboard/autoonboard.py's symbols stage, one acceptance rule for every console with symbol sets (Xbox 360, GameCube).

Xbox 360: find the symbol sets (a development build's PDB or linker MAP
with its own XEX) in the folders that hold them, read-only; rank them against this game's XEX; match the best few with
targets/xbox360_ppc/tools/pdb_names.py --verify; accept a set ONLY when its matches are verified; merge the accepted
sets' names into <project>/.work/onboard/symbols/names.json (git-ignored: extracted names never go in git).

Folders (each one read, never written): <project>/reference when it exists, then config.local.json "symbol_sets":
{"xbox360": [folder, ...]}, else the console's shared folder Integrations\\Xbox360\\reference\\symbols for a project
under Integrations\\Xbox360. A named folder that is not there is a hard error (the Xbox 360 declares symbol sources:
docs/XBOX360_SYMBOL_SETS.json), and so is no folder at all. Archives (.7z, .zip, .rar) are counted, never unpacked.
Rank: same title ID as the game first, then the nearest SDK build, then not a debug build (its code is shaped
differently). The top MAX_SCORED are matched; a set whose own XEX is retail-encrypted and the key is absent here is
listed as not scored.

Verification (pdb_names.verify): the exact and shape keys mask every call target, so the call targets are evidence the
match did not see. For each pair matched on its exact bytes whose i-th direct calls both reach matched functions, the
callees should be a pair too. Accepted when, on the exact tier: at least MIN_PAIRS pairs and MIN_CALLS calls checked,
agreement at least MIN_RATE, and at least MIN_MARGIN above the control (the same test on a deliberately wrong pairing:
partners rotated within the tier). A set that fails is reported with its numbers and its names are not used.
A verified set is this game's only when at least MIN_OWN of its exact pairs are in the game's own modules (an empty
library in the PDB); then every name it gives is used. Otherwise only its SDK library names are used: measured on Too
Human's release PDB against Mega Man 10's workspace (2026-11-15), the callee check passes (61 exact pairs, 99.1%
agree, control 0.0%) because both link the same XDK and C runtime code, and 853 of its 908 names are library
functions; the other 55 are Too Human class names on trivial bodies (7 exact pairs in its own modules, against 401 on
Too Human itself). "No verified match" is an ok verdict with zero names: a game without symbols still goes on.

GameCube (GAMECUBE, after the census: names are matched to its Ghidra catalog): the sets are folders of CodeWarrior
or SN maps with their .dol, or ELFs with a symbol table (the write-up: docs/GAMECUBE_BUILD_VERSIONS.md, its table
docs/GAMECUBE_SYMBOL_SETS.json), from other games and builds, never this game's own so far. Folders as above, the
shared one being Integrations\\GameCube\\PDBs. Each set folder is looked up in the table by name: one with no function
names or no code to match by bytes is listed, not scored. Rank: the same game ID, then the nearest Dolphin SDK OS build
date to the one in main.dol, then a map naming each function's library. The top MAX_SCORED are matched by
targets/gamecube_ppc/tools/gc_names.py --verify (pdb_names' matcher and verify(), unchanged), and accepted by the same
accept() and keep(): a GameCube "library" is the SDK, the C runtime (MSL), MetroTRK or known middleware (gc_names
LIB rules), so another game's own names count toward MIN_OWN and are never used unless MIN_OWN of them pair exactly.

Nearest existing: targets/xbox360_ppc/tools/pdb_names.py - matches one set a person chose and names everything it
pairs, with no check that the set is this game's; targets/xbox360_ppc/RESEARCH_SYMBOLS.md and
docs/XBOX360_SYMBOL_SETS.json describe the sets (title, XDK) with no paths and no matching.
"""
import json
import os
import pathlib
import re
import time

import onboard_stages as S

T, GC = "targets/xbox360_ppc", "targets/gamecube_ppc"
MAX_SCORED, MATCH_S = 4, 2700
MIN_PAIRS, MIN_CALLS, MIN_RATE, MIN_MARGIN, MIN_OWN = 50, 50, 0.90, 0.50, 50
ARCHIVES = (".7z", ".zip", ".rar")


def folders(ctx, shared=("reference", "symbols")):
    """[(folder, why)] to search; a declared folder that is absent is a StageError. shared: the console's shared folder
    under Integrations\\<Console> (Xbox 360: reference\\symbols; GameCube: PDBs)."""
    out = []
    if (ctx.project / "reference").is_dir():
        out.append((ctx.project / "reference", "the project's reference folder"))
    try:
        cfg = json.loads((ctx.tool / "config.local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    named = (cfg.get("symbol_sets") or {}).get(ctx.console)
    if named:
        out += [(pathlib.Path(p), 'config.local.json "symbol_sets"') for p in named]
    else:
        # the console folder is the ancestor right under Integrations: a game folder (Integrations\<Console>\<Game>)
        # or a project inside one (<Game>\.workuto, Dragon Force 2026-11-16)
        console = next((d for d in ctx.project.resolve().parents if d.parent.name == "Integrations"), None)
        if console is not None:
            out.append((console.joinpath(*shared), "the console's shared symbol sets"))
    gone = [str(p) for p, _ in out if not p.is_dir()]
    if gone or not out:
        raise S.StageError(("symbol folder(s) not there: " + ", ".join(gone)) if gone else
                           f"no symbol folder for {ctx.project}: no reference/ in it, no \"symbol_sets\" in "
                           f"{ctx.tool / 'config.local.json'}, and it is not under Integrations\\{S.CONSOLES[ctx.console][1]}",
                           gone or [f'"symbol_sets": {{"{ctx.console}": [folder]}} in {ctx.tool / "config.local.json"}'],
                           f"the folder that holds the {ctx.console} symbol sets on this machine (LOCATIONS.md), or remove "
                           "the entry; a game with none still names the shared folder")
    return out


def candidates(dirs):
    """([{"symbols", "kind", "xex"}], archives): every .pdb or .map with a .xex (or .exe) of the same stem beside it."""
    sets, archives = [], 0
    for d, _why in dirs:
        for root, _subs, files in os.walk(d):
            low = {f.lower(): f for f in files}
            for f in files:
                p, ext = pathlib.Path(root) / f, pathlib.Path(f).suffix.lower()
                if ext in ARCHIVES:
                    archives += 1
                elif ext in (".pdb", ".map"):
                    stem = pathlib.Path(f).stem.lower()
                    xex = next((low[stem + e] for e in (".xex", ".exe") if stem + e in low), None)
                    sets.append({"symbols": str(p), "kind": ext[1:], "xex": str(pathlib.Path(root) / xex) if xex else None})
    return sets, archives


def rank(ctx, game, sets):
    """The sets in order: same title ID, nearest SDK build, not a debug build. Each gets its XEX's facts."""
    S.import_tools(ctx, "xbox360_ppc")
    import pdb_readers
    import xex
    key = xex.retail_key(ctx.tool) is not None
    for c in sets:
        if not c["xex"]:
            c["skip"] = "no XEX of the same name beside it"
            continue
        try:
            f = pdb_readers.read_xex(pathlib.Path(c["xex"]), {})
            x = xex.Xex(pathlib.Path(c["xex"]).read_bytes())
        except Exception as e:  # a damaged or foreign file: listed, not scored
            c["skip"] = f"its XEX does not read ({type(e).__name__}: {e})"
            continue
        c.update(title_id=f.get("title_id"), sdk_build=f.get("sdk_build"))
        if x.encryption == xex.ENC_NORMAL and not key:  # a development build opens with the development key
            try:
                x.image(keys=[xex.DEVKIT_KEY])
            except xex.XexError:
                c["skip"] = "its XEX is retail-encrypted and this machine has no retail key"
    sdk = game.get("SDK build") or 0

    def order(c):
        return ("skip" in c, c.get("title_id") != game.get("title ID"), abs((c.get("sdk_build") or 0) - sdk),
                "debug" in pathlib.Path(c["symbols"]).name.lower(), c["symbols"])
    return sorted(sets, key=order)


def accept(ver):
    """(accepted, why) from pdb_names.verify's numbers on the exact tier."""
    t = ver.get("tiers", {}).get("exact")
    c = ver.get("control", {}).get("exact", {})
    if not t:
        return False, "no exact pairs with a checkable call"
    margin = t["rate"] - c.get("rate", 0.0)
    ok = t["pairs"] >= MIN_PAIRS and t["calls"] >= MIN_CALLS and t["rate"] >= MIN_RATE and margin >= MIN_MARGIN
    return ok, (f"exact {t['pairs']} pairs, {t['agree']}/{t['calls']} calls agree ({t['rate']:.1%}), control "
                f"{c.get('rate', 0.0):.1%}: " + ("verified" if ok else f"not verified (needs >= {MIN_PAIRS} pairs, "
                                                 f">= {MIN_CALLS} calls, >= {MIN_RATE:.0%}, >= {MIN_MARGIN:.0%} over "
                                                 "the control)"))


def keep(names):
    """("game", names) when the set is this game's (MIN_OWN exact pairs in its own modules), else ("libraries", only
    the names in SDK libraries)."""
    own = sum(1 for v in names.values() if v.get("tier") == "exact" and not v.get("lib"))
    if own >= MIN_OWN:
        return "game", names, own
    return "libraries", {r: v for r, v in names.items() if v.get("lib")}, own


class X360:
    """The Xbox 360's sets: a PDB or MAP with its own XEX, matched by pdb_names.py against catalog_inputs.json."""
    shared = ("reference", "symbols")
    tools = (f"{T}/tools/pdb_names.py", f"{T}/tools/pdb_readers.py", f"{T}/tools/xex.py")
    plan = ["walk the symbol folders read-only: .pdb/.map with their own .xex; rank by title ID, SDK build",
            f"top {MAX_SCORED}: tools/jobreg.py run -- tools/bounded.py {MATCH_S} -- {T}/tools/pdb_names.py "
            "--pdb|--map SET --xex ITS_XEX --ws <project>/decomp --out .work/onboard/symbols/<n> --verify"]
    candidates, rank = staticmethod(candidates), staticmethod(rank)

    @staticmethod
    def command(c, ws, d):
        return [f"{T}/tools/pdb_names.py", "--" + c["kind"], c["symbols"], "--xex", c["xex"], "--ws", ws, "--out", d,
                "--verify"]

    @staticmethod
    def stamp(c):
        return {k: c[k] for k in ("symbols", "xex")} | {"size": pathlib.Path(c["symbols"]).stat().st_size}

    @staticmethod
    def total(ws):
        cat = json.loads((ws / "catalog_inputs.json").read_text(encoding="utf-8"))
        return sum(1 for f in cat["functions"] if f.get("kind") == "function")


GC_TABLE = "docs/GAMECUBE_SYMBOL_SETS.json"
GC_FILES = (".elf", ".map", ".dol")


def gc_candidates(dirs, table):
    """([{"symbols": set folder, ...facts}], archives): each folder directly under a searched one (or the searched one
    itself) holding a .elf or .map, with its row of the table (set name = folder name) when it has one."""
    rows = {r["set"]: r for r in table}
    sets, archives = [], 0
    for d, _why in dirs:
        for f in (d, *sorted(q for q in d.iterdir() if q.is_dir())):
            files = [q for q in (f.rglob("*") if f != d else f.iterdir()) if q.is_file()]
            archives += sum(1 for q in files if q.suffix.lower() in ARCHIVES)
            have = {q.suffix.lower() for q in files}
            if not have & {".elf", ".map"}:
                continue
            r = rows.get(f.name, {})
            c = {"symbols": str(f), "kind": "set", "game_id": r.get("game_id"), "sdk_os_built": r.get("sdk_os_built"),
                 "fn_names": r.get("fn_names"), "bytes_match": r.get("bytes_match"), "lib_obj": r.get("lib_obj"),
                 "in_table": bool(r)}
            if not r:  # a set the write-up does not list (a project's own reference folder): judged by its files
                c.update(fn_names=True, bytes_match="elf" if ".elf" in have else "dol" if ".dol" in have else "no")
            sets.append(c)
    return sets, archives


def sdk_date(text):
    """A Dolphin SDK build line's date ("Sep  5 2002 05:33:05") as a day number, or None."""
    import datetime
    m = re.match(r"(\w{3})\s+(\d+)\s+(\d{4})", " ".join(str(text or "").split()))
    try:
        return datetime.datetime.strptime(" ".join(m.groups()), "%b %d %Y").toordinal() if m else None
    except ValueError:
        return None


def gc_rank(ctx, game, sets):
    """Same game ID (its first three characters: the game, any region) first, then the nearest SDK OS build, then a
    map naming each function's library. A set with no names, or no code that matches by bytes, is listed not scored."""
    gid, day = (game.get("game ID") or "")[:3], sdk_date(game.get("SDK OS build"))
    for c in sets:
        if not c["fn_names"]:
            c["skip"] = "no function names (the write-up's table)"
        elif c["bytes_match"] in (None, "no"):
            c["skip"] = "no code to match by bytes (no .dol or ELF with its sections)"
        elif c["bytes_match"] == "rel":
            c["skip"] = "only REL modules' code matches by bytes; gc_names.py reads a main executable"

    def order(c):
        d = sdk_date(c.get("sdk_os_built"))
        return ("skip" in c, (c.get("game_id") or "")[:3] != gid or not gid,
                abs(d - day) if d is not None and day is not None else 10 ** 6, c.get("lib_obj") != "lib+obj",
                c["symbols"])
    return sorted(sets, key=order)


class GameCube:
    """The GameCube's sets: folders with an ELF or a map and its .dol, matched by gc_names.py against the census
    catalog (state/catalog.json)."""
    shared = ("PDBs",)
    tools = (f"{GC}/tools/gc_names.py", f"{GC}/tools/gc_maps.py", f"{GC}/tools/gc_binary.py", f"{GC}/tools/gcdisc.py",
             f"{T}/tools/pdb_names.py", GC_TABLE)
    plan = ["walk the symbol folders read-only: set folders with .elf or .map+.dol; facts from " + GC_TABLE + "; rank by "
            "game ID, nearest Dolphin SDK OS build, lib+obj map",
            f"top {MAX_SCORED}: tools/jobreg.py run -- tools/bounded.py {MATCH_S} -- {GC}/tools/gc_names.py --set SET "
            "--ws <project>/decomp --out .work/onboard/symbols/<n> --verify"]

    @staticmethod
    def candidates(dirs):
        table = json.loads((S.TOOL / GC_TABLE).read_text(encoding="utf-8"))
        return gc_candidates(dirs, table)

    rank = staticmethod(gc_rank)

    @staticmethod
    def command(c, ws, d):
        return [f"{GC}/tools/gc_names.py", "--set", c["symbols"], "--ws", ws, "--out", d, "--verify"]

    @staticmethod
    def stamp(c):
        files = sorted(p for p in pathlib.Path(c["symbols"]).rglob("*") if p.suffix.lower() in GC_FILES)
        return {"symbols": c["symbols"], "files": [[p.name, p.stat().st_size] for p in files]}

    @staticmethod
    def total(ws):
        cat_f = ws / "state" / "catalog.json"
        if not cat_f.is_file():
            raise S.StageError(f"no {cat_f}: GameCube names are matched to the census catalog", [str(cat_f)],
                               "the census stage first")
        return len(json.loads(cat_f.read_text(encoding="utf-8"))["functions"])


GAMECUBE = GameCube


class Symbols(S.Built):
    def __init__(self, source):
        self.src = source
        self.tools = (*source.tools, "tools/jobreg.py", "tools/bounded.py")

    def calls(self, ctx):
        return [*self.src.plan, f"accept on callee agreement (exact tier: >= {MIN_PAIRS} pairs, >= {MIN_RATE:.0%}, "
                f">= {MIN_MARGIN:.0%} over a wrong pairing); merge into .work/onboard/symbols/names.json"]

    def stamp(self, c):
        """What a kept match was made from: the set's files and the matcher's own source. Without the second, a fixed
        matcher reused its old answer (Skies, 2026-11-15: the library_runs fix showed no change until this)."""
        import hashlib
        h = hashlib.sha256()
        for t in self.src.tools:
            p = S.TOOL / t
            h.update(t.encode() + (p.read_bytes() if p.is_file() else b""))
        return self.src.stamp(c) | {"tools": h.hexdigest()}

    def run(self, ctx):
        src, ws, out = self.src, S.ws_of(ctx), ctx.project / S.STATE_DIR / "symbols"
        total = src.total(ws)
        out.mkdir(parents=True, exist_ok=True)
        dirs = folders(ctx, src.shared)
        sets, archives = src.candidates(dirs)
        sets = src.rank(ctx, ctx.verdicts["disc"]["numbers"], sets)
        t0, scored, merged, disagree = time.time(), [], {}, 0
        for i, c in enumerate([c for c in sets if "skip" not in c][:MAX_SCORED]):
            d = out / str(i)
            stamp = self.stamp(c)
            if not ((d / "verify.json").is_file() and (d / "set.json").is_file()
                    and json.loads((d / "set.json").read_text(encoding="utf-8")) == stamp):
                if time.time() - t0 + MATCH_S > ctx.seconds:
                    raise S.StageError(f"symbols: {i} of {min(MAX_SCORED, len(sets))} sets matched in this run",
                                       ["the other sets' matches"], "autoonboard.py again: it resumes at symbols, the "
                                       "matched sets are kept")
                d.mkdir(parents=True, exist_ok=True)
                cmd = src.command(c, ws, d)
                rc, tail = S.run_tool(ctx, "symbols", cmd, MATCH_S,
                                      why=f"onboard symbols of {ctx.name}: match {pathlib.Path(c['symbols']).name}",
                                      produces=d / "verify.json")
                if rc or not (d / "verify.json").is_file():
                    c["skip"] = f"{pathlib.Path(cmd[0]).name} exited {rc}: {' | '.join(tail[-2:])}"
                    continue
                (d / "set.json").write_text(json.dumps(stamp), encoding="utf-8")
            ok, why = accept(json.loads((d / "verify.json").read_text(encoding="utf-8")))
            level, names, own = keep(json.loads((d / "names.json").read_text(encoding="utf-8")))
            why += (f"; {own} exact pairs in the game's own modules: " + ("this game's set, every name used" if level ==
                    "game" else f"under {MIN_OWN}, so only its {len(names)} SDK library names are used")) if ok else \
                f"; {own} exact pairs in its own (non-library) code"
            c.update(accepted=ok, level=level if ok else None, why=why, named=len(names) if ok else 0, out=str(d),
                     own_exact=own)
            scored.append(c)
            if ok:
                for r, v in names.items():
                    if r in merged:
                        disagree += merged[r]["name"] != v["name"]
                    else:
                        merged[r] = dict(v, set=c["symbols"])
        (out / "names.json").write_text(json.dumps(merged, indent=0), encoding="utf-8")
        (out / "candidates.json").write_text(json.dumps(sets, indent=1), encoding="utf-8")
        acc = [c for c in scored if c["accepted"]]
        tiers = {}
        for v in merged.values():
            tiers[v["tier"]] = tiers.get(v["tier"], 0) + 1
        nm = lambda c: pathlib.Path(c["symbols"]).name  # noqa: E731
        return {"numbers": {"folders": len(dirs), "sets": len(sets), "archives (not read)": archives,
                            "scored": len(scored), "accepted": len(acc),
                            "game's own set": sum(1 for c in acc if c["level"] == "game"), "named": len(merged),
                            "named %": round(100 * len(merged) / max(1, total), 1),
                            **{f"by {k}": n for k, n in sorted(tiers.items())}, "disagree": disagree},
                "paths": {"names": str(out / "names.json"), "candidates": str(out / "candidates.json"),
                          **{f"set {i}": c["symbols"] for i, c in enumerate(acc)}},
                "message": (f"verified: {', '.join(nm(c) for c in acc)}; {len(merged)} of {total} functions named "
                            f"({100 * len(merged) / max(1, total):.1f}%)" if acc else "no verified match: 0 names")
                           + "".join(f"; {nm(c)}: {c['why']}" for c in scored)
                           + (f"; {sum(1 for c in sets if 'skip' in c)} set(s) not scored" if any('skip' in c for c in sets)
                              else ""),
                "next": ([f"The names are in {out / 'names.json'} (git-ignored). Apply them where the readable step "
                          "reads names (readable/names, AGENTS.md) and cut the SDK libraries they name out of the "
                          "translate scope"] if acc else [])}

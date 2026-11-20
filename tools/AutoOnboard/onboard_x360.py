"""The Xbox 360 adapters of tools/AutoOnboard/autoonboard.py's stages (onboard_stages.STAGES): disc, extract, census and boot (its
inputs), each calling the target's own tools: xiso.py (the disc), xex.py and pdb_readers.read_xex (the XEX's header),
x360setup.py (the workspace and the Ghidra step with its decode audit), survey.py. Symbols is onboard_symbols.py.

The retail key: an XEX with "normal" encryption opens only with the console's retail key, which only the user supplies
("xex_retail_key" in DecompPipeline's config.local.json, or DP_XEX_RETAIL_KEY; xex.retail_key). It is never printed or
copied: the disc stage says only whether this machine has one. Without it the stage stops naming it, unless
--inputs WS names a workspace of this title made where the key is (x360setup's copy route: its four catalog inputs,
checked against the disc's XEX header first: title ID, original PE name, load address, image size, entry point).

Nearest existing: targets/xbox360_ppc/tools/x360setup.py - the setup and Ghidra steps themselves, which these call; it
keeps no verdicts, stops a re-run on its own workspace and does not say what a missing key blocks. onboard_dc.py is the
Dreamcast's file (one file per console keeps each under the 400-line cap).
"""
import json
import pathlib
import re

import onboard_stages as S

T = "targets/xbox360_ppc"
SUFFIXES = (".iso", ".xex")
CENSUS_S = 7200 + 1800  # x360setup's whole-catalog Ghidra timeout, then the decode audit
# Too Human, the one Xbox 360 project: its catalogue (the same analysis: pdata, code pointers, calls) against what its
# port runs (2026-11-14 compare of the kit's generated C with the judge catalogue, TooHuman .work/tx1112).
REFERENCE = {"game": "Too Human", "catalog": 88187, "outside": 6549, "holes": 5928, "inside": 507, "leaves": 114}


def facts(ctx, xex_path):
    """The XEX header's facts (pdb_readers.read_xex) with its encryption and compression; never decrypts."""
    S.import_tools(ctx, "xbox360_ppc")
    import pdb_readers
    import xex
    name = pathlib.Path(xex_path).name
    f = pdb_readers.read_xex(pathlib.Path(xex_path), {})
    if f.get("error"):
        raise S.StageError(f"{xex_path}: {f['error']}", ["the game's default.xex (XEX2)"], "a good dump of the disc")
    if f.get("delta_patch"):
        raise S.StageError(f"{name} is a title update (a delta patch), not a game", ["the game's own XEX"],
                           "the disc's default.xex")
    try:
        x = xex.Xex(pathlib.Path(xex_path).read_bytes())
    except xex.XexError as e:
        raise S.StageError(f"{name}: {e}", ["an XEX2 file xex.py reads"], "a good dump of the disc") from e
    if x.compression == xex.COMP_NORMAL:
        raise S.StageError(f"{name} is LZX-compressed, which xex.py does not expand yet",
                           ["an XEX with no or basic compression"], f"a DP change ({T}/tools/xex.py, LZX)")
    ex = x.execution_info() or {}
    f.update(encryption=x.encryption, compression=x.compression, load_address=x.load_address,
             image_size=x.image_size, entry_point=x.entry_point(), title=ex.get("title_id"))
    return f, x


INPUTS = ("image.bin", "image.json", "catalog_inputs.json", "code_ranges.txt")  # x360setup.INPUTS: what it copies
SAME = (("original PE name", "original_pe_name"), ("load address", "load_address"), ("image size", "image_size"),
        ("entry point", "entry_point"), ("title ID", "title"))


def same_title(f, desc):
    """[(field, disc, workspace)] where a workspace's image.json differs from the disc XEX's header facts."""
    got = dict(desc, title=(desc.get("execution") or {}).get("title_id"))
    return [(k, f.get(n), got.get(n)) for k, n in SAME if f.get(n) != got.get(n)]


class Disc(S.Built):
    tools = (f"{T}/tools/xiso.py", f"{T}/tools/xex.py", f"{T}/tools/pdb_readers.py")

    def calls(self, ctx):
        return ["find the .iso/.xex in the project root", f"{T}/tools/xiso.py: every file, default.xex to "
                ".work/onboard/default.xex", "pdb_readers.read_xex: title, SDK build; xex.py: encryption, compression",
                "the retail key: present or not (never printed); --inputs WS: its image.json against the header"]

    def run(self, ctx):
        image = S.find_image(ctx, SUFFIXES)
        S.import_tools(ctx, "xbox360_ppc")
        import xex
        import xiso
        state = ctx.project / S.STATE_DIR
        files, xpath = [], image
        if image.suffix.lower() == ".iso":
            xpath = state / "default.xex"
            try:
                with xiso.Disc(image) as d:
                    files = [(p, n) for p, _s, n, is_dir in d.entries() if not is_dir]
                    d.extract("default.xex", xpath)
            except (xiso.DiscError, OSError, ValueError) as e:
                raise S.StageError(f"{image}: not a disc image xiso.py reads ({e})", [f"an Xbox 360 game disc image "
                                   "(XDVDFS at offset 0, XGD1, XGD2 or XGD3) in the project root"],
                                   "a full-disc .iso dump; Xbox Live Arcade packages (STFS) are not taken yet")
        f, x = facts(ctx, xpath)
        needs, opened = x.encryption == xex.ENC_NORMAL, True
        have = needs and xex.retail_key(ctx.tool) is not None
        if needs:
            try:
                x.image()  # the retail key when this machine has one, then the development key; never printed
            except xex.XexError:
                opened = False
        inputs = getattr(ctx, "inputs", None)
        where = "none" if not needs else "opens with this machine's keys" if opened else "retail: no key here"
        if needs and not opened and have:
            raise S.StageError(f"the retail key on this machine does not open {xpath.name} (wrong key or a damaged "
                               "file)", ["the console's retail key"], "the user checks xex_retail_key")
        if needs and not opened and not inputs:
            raise S.StageError(f"{xpath.name} is encrypted with the console's retail key, and this machine has none",
                               [f'"xex_retail_key" in {ctx.tool / "config.local.json"} (or the environment '
                                "variable DP_XEX_RETAIL_KEY): the console's retail key, which only the user supplies"],
                               "the user sets the key on this machine; or --inputs WS: a workspace of this title made "
                               "where the key is (x360setup copies its catalog inputs: no key, no decrypt)")
        if inputs:
            ws = pathlib.Path(inputs)
            gone = [n for n in INPUTS if not (ws / n).is_file()]
            if gone:
                raise S.StageError(f"--inputs {ws} lacks {', '.join(gone)}", [f"a workspace of "
                                   f"{f.get('original_pe_name')} with x360setup's catalog inputs"],
                                   "the right workspace, or the retail key here")
            bad = same_title(f, json.loads((ws / "image.json").read_text(encoding="utf-8")))
            if bad:
                raise S.StageError(f"--inputs {ws}: not this disc's title ("
                                   + "; ".join(f"{k}: disc {a}, workspace {b}" for k, a, b in bad) + ")",
                                   [f"a workspace of {f.get('original_pe_name')}"], "the right workspace, or the "
                                   "retail key here")
            where += f"; catalog inputs from {ws} (same title: {len(SAME)} header facts agree)"
        return {"numbers": {"title ID": f.get("title_id"), "version": f.get("version"),
                            "PE name": f.get("original_pe_name"), "SDK build": f.get("sdk_build"),
                            "files": len(files), "GB": round(sum(n for _p, n in files) / 2**30, 2),
                            "encryption": where, "compression": {0: "none", 1: "basic"}.get(x.compression, x.compression)},
                "paths": {"image": str(image), "xex": str(xpath), **({"inputs": str(inputs)} if inputs else {})},
                "message": f"{f.get('original_pe_name')} (title {f.get('title_id')}, SDK {f.get('sdk_build')}), "
                           f"{len(files)} files; {where}",
                "next": []}


class Extract(S.Built):
    tools = (f"{T}/tools/x360setup.py", f"{T}/tools/catalog_inputs.py", f"{T}/tools/xiso.py", "tools/bounded.py")

    def calls(self, ctx):
        return [f"{T}/tools/x360setup.py setup SOURCE <project>/decomp --no-ghidra --name NAME (SOURCE: the disc "
                "image, or the disc stage's --inputs workspace)", f"{T}/tools/xiso.py: every disc file into "
                "game-data/ when they are under 5 GB"]

    def run(self, ctx):
        dv = ctx.verdicts["disc"]
        image, src = pathlib.Path(dv["paths"]["image"]), dv["paths"].get("inputs") or dv["paths"]["image"]
        f, _x = facts(ctx, dv["paths"]["xex"])
        ws, reused = S.ws_of(ctx), []
        if (ws / "project.json").exists():
            bad = same_title(f, json.loads((ws / "image.json").read_text(encoding="utf-8")))
            if bad:
                raise S.StageError(f"{ws} is a workspace of another XEX ({bad[0][0]}: {bad[0][2]}, the disc's {bad[0][1]})",
                                   [f"a workspace of this disc at {ws}"], f"move {ws} away, then run again")
            reused.append("workspace")
        elif ws.exists() and any(ws.iterdir()):
            raise S.StageError(f"{ws} holds files but no project.json: an earlier extract stopped part way",
                               [f"{ws} removed"], f"delete {ws} (it holds only that extract), then run again")
        else:
            rc, tail = S.run_tool(ctx, "extract", [f"{T}/tools/x360setup.py", "setup", src, ws, "--no-ghidra", "--name",
                                                 ctx.name], 1800)
            if rc or not (ws / "project.json").exists():
                raise S.StageError(f"x360setup.py setup exited {rc}: {' | '.join(tail[-3:])}",
                                   ["a workspace from x360setup.py"], f"read {S.log_path(ctx, 'extract')}")
        data, n, total = ctx.project / "game-data", dv["numbers"].get("files") or 0, (dv["numbers"].get("GB") or 0) * 2**30
        note = self.game_data(ctx, image, data, n, total, reused)
        cat = json.loads((ws / "catalog_inputs.json").read_text(encoding="utf-8"))
        st = cat.get("stats", {})
        return {"numbers": {"functions": st.get("functions"), **{f"from {k}": v for k, v in st.get("by_source", {}).items()},
                            "refused": sum(st.get("refused", {}).values()), "mid entries": st.get("mid_entries"),
                            "imports": len(cat.get("imports", [])), "game-data": note},
                "paths": {"workspace": str(ws), "catalog_inputs": str(ws / "catalog_inputs.json"),
                          **({"game-data": str(data)} if data.exists() else {})},
                "message": ("reused " + ", ".join(reused) if reused else "extracted")
                           + f"; catalog inputs: {st.get('functions')} functions; game-data: {note}",
                "next": ([] if data.exists() else [f"The disc's files ({total / 2**30:.1f} GB) are not extracted: over the "
                                                   "5 GB limit (CLAUDE.md). The kit's runs read them, so the user decides "
                                                   "where they go before the boot stage"])}

    @staticmethod
    def game_data(ctx, image, data, n, total, reused):
        if image.suffix.lower() != ".iso":
            return "none (a bare XEX)"
        if total > S.ASSET_LIMIT:
            return f"not extracted ({total / 2**30:.1f} GB, over the 5 GB limit)"
        have = sum(1 for q in data.rglob("*") if q.is_file()) if data.exists() else 0
        if data.exists() and have == n:
            reused.append("game-data")
            return f"{n} files"
        if data.exists():
            raise S.StageError(f"{data} holds {have} of the disc's {n} files: an earlier extract stopped part way",
                               [f"{data} removed"], f"delete {data} (disc data only), then run again")
        S.import_tools(ctx, "xbox360_ppc")
        import xiso
        with xiso.Disc(image) as d:
            for p, _s, _n, is_dir in d.entries():
                if not is_dir:
                    d.extract(p, data / p)
        return f"{n} files"


def denominator(n, st):
    r = REFERENCE
    return (f"found by analysis: {n} functions (pdata {st.get('pdata', 0)}, code pointers {st.get('pointer', 0)}, "
            f"calls {st.get('bl', 0)}). Not the whole game: {r['game']}'s catalogue by the same analysis "
            f"({r['catalog']}) lacked {r['outside']} entries its port runs ({r['outside'] / r['catalog']:.1%} more): "
            f"{r['holes']} code between functions, {r['inside']} inside or mid entries, {r['leaves']} call targets. "
            "tools/catfeed.py adds them (branch x360-judge-catfeed, not on master): expect about the same shortfall")


class Census(S.Built):
    tools = (f"{T}/tools/x360setup.py", "decomp.py", "tools/jobreg.py", "tools/bounded.py", "core/scope.py")

    def calls(self, ctx):
        return [f"tools/jobreg.py run -- tools/bounded.py {CENSUS_S} -- {T}/tools/x360setup.py ghidra <project>/decomp "
                "(decomp.py setup over the whole catalog, then TcDecodeAudit: gate G2)",
                "read state/catalog.json, catalog_inputs.json: counts"]

    def run(self, ctx):
        ws = S.ws_of(ctx)
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        gpr = S.ghidra_project(ws, cfg)
        S.import_tools(ctx, "xbox360_ppc")
        from core import scope
        cat_f, audit = ws / "state" / "catalog.json", ws / "state" / "decode_audit.tsv"
        if gpr.exists() and not cat_f.exists():
            raise S.StageError(f"{gpr} exists without state/catalog.json: an earlier Ghidra setup stopped part way",
                               [f"{gpr.parent} rebuilt"], f"delete {gpr.parent}, then run again")
        lp = S.log_path(ctx, "census")
        old = lp.read_text(encoding="utf-8", errors="replace") if lp.is_file() else ""
        rc, tail = 0, []
        if not (cat_f.exists() and audit.exists() and "gate G2 passes" in old):  # else a finished census: kept
            rc, tail = S.run_tool(ctx, "census", [f"{T}/tools/x360setup.py", "ghidra", ws], CENSUS_S,
                                  why=f"onboard census of {ctx.name}: Ghidra catalog and decode audit (G2)",
                                  produces=audit)
        log = lp.read_text(encoding="utf-8", errors="replace")
        g2 = re.findall(r"decode audit: (\d+) words .*?; (\d+) disagreement\(s\) in functions the target prepares, "
                        r"(\d+) in refused functions and (\d+) in import records \(expected\): gate G2 (\w+)", log)
        if rc or not cat_f.exists() or not g2 or g2[-1][4] != "passes":
            raise S.StageError(f"x360setup.py ghidra exited {rc}" + (f": gate G2 {g2[-1][4]}, {g2[-1][1]} disagreement(s)"
                                                                     if g2 else f": {' | '.join(tail[-3:])}"),
                               ["a Ghidra catalog with gate G2 passing"], f"read {S.log_path(ctx, 'census')} and "
                               f"{ws / 'state'}")
        funcs = json.loads(cat_f.read_text(encoding="utf-8"))["functions"]
        ranges = scope.game_ranges(cfg)
        game = [x for x in funcs if any(lo <= int(x["addr"], 16) < hi for lo, hi in ranges)]
        ci = json.loads((ws / "catalog_inputs.json").read_text(encoding="utf-8"))
        st = ci.get("stats", {})
        words, bad, refused, records, _v = g2[-1]
        return {"numbers": {"catalog": len(funcs), "game range": len(game), "catalog inputs": st.get("functions"),
                            "refused before prepare": sum(st.get("refused", {}).values()),
                            **{k.replace("unsupported: ", ""): v for k, v in sorted(st.get("refused", {}).items())},
                            "mid entries": st.get("mid_entries"), "audit words": int(words),
                            "G2": f"passes ({refused} in refused, {records} in import records)"},
                "paths": {"catalog": str(cat_f), "decode_audit": str(audit)},
                "message": f"{denominator(st.get('functions'), st.get('by_source', {}))}; Ghidra catalog {len(funcs)}, "
                           f"{len(game)} in the game range",
                "next": ["Before the agent step: name the SDK libraries (the symbols stage) and cut the Direct3D library "
                         f"({T}/port/tools/boundary.py), so neither is translated for nothing",
                         "Code between catalogue functions is not in it: tools/catfeed.py (branch x360-judge-catfeed)"]}


class Boot(S.Built):
    """Contract only: the inputs a bounded bring-up try needs, each named with what supplies it. The try itself
    (gen_xbox.py bringup) is not wired: it builds the kit (MSVC, SDL3) and runs the translated game."""
    tools = (f"{T}/port/tools/gen_xbox.py", f"{T}/port/boundary.json")

    def calls(self, ctx):
        return ["check the bring-up's inputs: the project's Direct3D cut (port/boundary.json), the disc's files, MSVC, "
                "vcpkg (SDL3)",
                f"not wired: {T}/port/tools/gen_xbox.py bringup --ws WS --out OUT --rounds 1 --seconds 20"]

    def run(self, ctx):
        ws, gone = S.ws_of(ctx), []
        if not (ctx.project / "port" / "boundary.json").is_file():  # the kit's own is Too Human's, read by a person
            gone.append((f"the Direct3D cut for this game ({ctx.project / 'port' / 'boundary.json'}): which library "
                         "code the kit replaces. The kit's port/boundary.json is Too Human's, chosen by a person; "
                         "nothing checks that one fits another image",
                         f"py -3.12 {T}/port/tools/boundary.py --ws {ws} --out boundary_candidates.json, read by a "
                         "person, then port/boundary.json"))
        if not (ctx.project / "game-data").is_dir():
            gone.append(("the disc's files (game-data/): the game reads them", "the extract stage, once the user "
                         "decides where they go (over 5 GB)"))
        cfg = {}
        for name in ("config.json", "config.local.json"):
            try:
                cfg.update(json.loads((ctx.tool / name).read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        for key, what in (("vcvars", "64-bit MSVC (vcvars64.bat)"), ("vcpkg", "vcpkg (the kit's SDL3)")):
            if not cfg.get(key) or not pathlib.Path(cfg[key]).exists():
                gone.append((f'{what}: "{key}" in config.local.json', "Visual Studio 2022 C++ / vcpkg on the build box"))
        raise S.StageError("no bring-up tried: " + (f"{len(gone)} input(s) missing: " + ", ".join(w.split(" (")[0]
                                                                                                 for w, _ in gone)
                                                    if gone else "every input is there, and the try is not wired yet"),
                           [w for w, _ in gone] or ["the bring-up try in tools/AutoOnboard/onboard_x360.py"],
                           " | ".join(s for _, s in gone) or f"run {T}/port/tools/gen_xbox.py bringup --ws {ws} --out "
                           "OUT --rounds 1 --seconds 20 on a build box (heavy: MSVC)")

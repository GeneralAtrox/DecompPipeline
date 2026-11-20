"""The GameCube adapters of tools/AutoOnboard/autoonboard.py's stages (onboard_stages): disc, extract, census and the boot contract,
each calling the existing GameCube tools (targets/gamecube_ppc/tools/gcdisc.py, tools/game_setup.py, decomp.py setup
through game_setup), and kit_section() for tools/AutoOnboard/onboard_gaps.py.

Disc: the image formats gcdisc.Disc reads (a plain .iso/.gcm, CISO, RVZ and WIA; a Wii disc or no GameCube magic is a
hard error), the header (title, game ID, maker, version, region), the file system, main.dol and every REL module
(loose, Yaz0 or inside a RARC: gcdisc.modules), and the Dolphin SDK build lines in main.dol (gc_binary.scan_blob).
Extract: game_setup.py setup --no-ghidra, which runs gcdisc.setup: main.dol, the REL modules, linked.dol (main.dol and
every REL linked at fixed addresses by gcrel.py, a link error stops it) and project.json on that image; then the disc's
files into game-data/.
Census: Ghidra over that image (the Dreamcast census class, which calls game_setup.py ghidra); RELs are catalogued
because they are linked into the image Ghidra reads. A REL the setup did not link (--rels none, or a second module
with the same id) or a linked one with no catalogued function is a hard error naming it.
Boot: the contract and the inputs it needs. There is no GameCube port kit; what one would have to supply for this game
comes from kit_section(), read from the game's code: the hardware register blocks it touches (lis 0xCC00.. bases
with their offsets) and the SDK and OS functions it calls, by the names the symbols stage matched.

Nearest existing: tools/AutoOnboard/onboard_dc.py - the Dreamcast adapters, whose Census this reuses; gcdisc.py and game_setup.py
are the setup itself and keep no verdicts. One file per console keeps each under the 400-line cap.
"""
import collections
import contextlib
import hashlib
import io
import json
import pathlib
import struct
import sys

import onboard_dc as DC
import onboard_stages as S

GC = "targets/gamecube_ppc"
FORMATS = {b"RVZ": "RVZ", b"WIA": "WIA", b"CISO": "CISO"}


def _tools(ctx):
    S.import_tools(ctx, "gamecube_ppc")
    p = str(ctx.tool / "targets" / "xbox360_ppc" / "tools")
    if p not in sys.path:
        sys.path.append(p)


def open_disc(image):
    import gcdisc
    try:
        return gcdisc.Disc(image)
    except SystemExit as e:
        raise S.StageError(str(e), [f"a GameCube disc image ({' or '.join(S.CONSOLES['gamecube'][2])}: plain, CISO, "
                                    "RVZ or WIA)"], "the right disc image")


def sdk_facts(dol):
    import gc_binary
    acc = {"sdk": set(), "os_rev": set(), "sdk_path": set(), "compiler": collections.Counter(),
           "mw_str": collections.defaultdict(set), "mw_ver": collections.defaultdict(set)}
    gc_binary.scan_blob(dol, acc)
    os_line = next((x for x in sorted(acc["sdk"]) if x[0] == "OS"), None)
    return {"SDK OS build": os_line[2] if os_line else None, "SDK code": os_line[3] if os_line else None,
            "SDK libraries": len(acc["sdk"]), "middleware": ", ".join(sorted(acc["mw_str"])) or "none found"}


class Disc(S.Built):
    tools = (f"{GC}/tools/gcdisc.py", f"{GC}/tools/gc_binary.py")

    def calls(self, ctx):
        return ["find the .iso/.gcm/.rvz/.wia/.ciso in the project root", "gcdisc.Disc: header, file system, main.dol",
                "gcdisc.scan -> .work/onboard/disc_scan.tsv (the REL modules)", "gc_binary.scan_blob: SDK build lines"]

    def run(self, ctx):
        image = S.find_image(ctx, S.CONSOLES["gamecube"][2])
        _tools(ctx)
        import gcdisc
        with open(image, "rb") as f:
            magic = f.read(4)
        disc = open_disc(image)
        tsv = ctx.project / S.STATE_DIR / "disc_scan.tsv"
        try:
            hdr, files, dol = disc.header(), disc.walk(), disc.dol()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                gcdisc.scan(disc, tsv)
        except (OSError, ValueError, IndexError, struct.error, SystemExit) as e:
            raise S.StageError(f"{image}: unreadable ({type(e).__name__}: {e})", [f"a complete image ({image.name})"],
                               "the disc image again")
        finally:
            disc.close()
        S.log_path(ctx, "disc").write_text(out.getvalue(), encoding="utf-8")
        rels = [ln.split("\t") for ln in tsv.read_text(encoding="utf-8").splitlines()[1:]]
        fmt = FORMATS.get(magic[:3], FORMATS.get(magic, "plain"))
        sdk = sdk_facts(dol)
        return {"numbers": {"title": hdr["title"], "game ID": hdr["game_id"], "maker": hdr["maker"],
                            "version": hdr["version"], "region": hdr["region"], "disc": hdr["disc"], "format": fmt,
                            "files": len(files), "MB": round(sum(s for _, _, s in files) / 2**20, 1),
                            "main.dol bytes": len(dol), "REL modules": len(rels), **sdk},
                "paths": {"image": str(image), "scan": str(tsv)},
                "message": f"{hdr['title']} ({hdr['game_id']} v{hdr['version']}, {hdr['region']}, {fmt}): main.dol "
                           f"{len(dol)} bytes, {len(rels)} REL module(s); Dolphin SDK OS {sdk['SDK OS build'] or 'not found'}",
                "next": [f"{len(rels)} REL module(s) ({tsv.name}): the extract links them into linked.dol, which the "
                         "census catalogues"] if rels else []}


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def write_files(disc, files, data):
    """Every disc file under data (its path inside the disc), in 8 MB pieces."""
    root = data.resolve()
    for path, off, size in files:
        dst = (data / path.lstrip("/")).resolve()
        if root not in dst.parents:
            raise S.StageError(f"a disc file name leaves {data}: {path}", ["a good dump"], "the disc image again")
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "wb") as f:
            for at in range(0, size, 8 << 20):
                f.write(disc.read(off + at, min(8 << 20, size - at)))


class Extract(S.Built):
    tools = ("tools/game_setup.py", f"{GC}/tools/gcdisc.py", f"{GC}/tools/gcrel.py", "tools/bounded.py")

    def calls(self, ctx):
        return ["tools/game_setup.py setup IMAGE <project>/decomp --no-ghidra --name NAME (gcdisc.setup: main.dol, "
                "the REL modules linked into linked.dol by gcrel.py, disc.json, project.json)",
                "every disc file into <project>/game-data (gcdisc.Disc.walk/read)"]

    def run(self, ctx):
        image = pathlib.Path(ctx.verdicts["disc"]["paths"]["image"])
        _tools(ctx)
        disc = open_disc(image)
        try:
            files, want = disc.walk(), sha256(disc.dol())
            ws, reused = S.ws_of(ctx), []
            if (ws / "project.json").exists():
                rec = json.loads((ws / "disc" / "disc.json").read_text(encoding="utf-8")) \
                    if (ws / "disc" / "disc.json").is_file() else {}
                have = (rec.get("main_dol") or {}).get("sha256")
                if have != want:
                    raise S.StageError(f"{ws} is of another main.dol ({have}, the disc's is {want})",
                                       [f"a workspace of this disc at {ws}"], f"move {ws} away, then run again")
                reused.append("workspace")
            elif (ws / "disc").exists():
                raise S.StageError(f"{ws / 'disc'} exists without project.json: an earlier extract stopped part way",
                                   [f"{ws / 'disc'} removed"], f"delete {ws / 'disc'} (it holds only that extract), "
                                   "then run again")
            else:
                rc, tail = S.run_tool(ctx, "extract", ["tools/game_setup.py", "setup", image, ws, "--no-ghidra",
                                                       "--name", ctx.name], 900)
                if rc:
                    raise S.StageError(f"game_setup.py setup exited {rc}: {' | '.join(tail[-3:])}",
                                       ["a workspace from game_setup.py (gcdisc.setup)"],
                                       f"read {S.log_path(ctx, 'extract')}; a REL link error names the module "
                                       "(game_setup.py --rels ID,ID... or none chooses)")
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
                write_files(disc, files, data)
        finally:
            disc.close()
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        rec = json.loads((ws / "disc" / "disc.json").read_text(encoding="utf-8"))
        linked = rec.get("linked") or {}
        return {"numbers": {"main.dol bytes": rec["main_dol"]["size"], "binary": cfg["binary"],
                            "REL modules": len(rec.get("rels") or []), "linked": len(linked.get("modules") or []),
                            "not linked": len(linked.get("not_linked") or []), "asset files": len(files),
                            "asset MB": round(total / 2**20, 1)},
                "paths": {"workspace": str(ws), "binary": str(ws / cfg["binary"]), "game-data": str(data)},
                "message": ("reused " + ", ".join(reused) if reused else "extracted") + f"; main.dol sha256 {want[:12]}",
                "next": []}


# The one GameCube game carried through translate and judge (targets/gamecube_ppc/SKIES_TRANSLATE_SCORE.md): its census
# is Ghidra over the whole main.dol (no RELs, no catalog_overrides), 6,751 functions, every one judged.
REFERENCE = {"game": "Skies of Arcadia Legends", "catalog": 6751,
             "missing": "4 script opcode handlers reached only through a data table (seeded in a side workspace)"}


def rel_check(ws, funcs):
    """(numbers, problems): the REL modules on the disc against what the setup linked and the catalog holds."""
    rec = json.loads((ws / "disc" / "disc.json").read_text(encoding="utf-8"))
    rels, linked = rec.get("rels") or [], rec.get("linked")
    if not rels:
        return {"REL modules": 0}, []
    if not linked:
        return {"REL modules": len(rels), "linked": 0}, [
            f"{len(rels)} REL module(s) on the disc and none linked, so their code is not in the catalog: "
            + ", ".join(r["path"] for r in rels[:8])]
    link = json.loads((ws / "disc" / "link.json").read_text(encoding="utf-8"))
    addrs = sorted(int(f["addr"], 16) for f in funcs)
    problems, in_rels = [f"not linked: {n}" for n in linked.get("not_linked") or []], 0
    for m in link["modules"]:
        n = 0
        for s in m["sections"]:
            if s["exec"] and s["size"]:
                lo = int(s["addr"], 16)
                n += sum(1 for a in addrs if lo <= a < lo + s["size"])
        in_rels += n
        if not n:
            problems.append(f"REL module {m['module_id']} ({m['name']}): linked, but no catalogued function in its code")
    return {"REL modules": len(rels), "linked": len(link["modules"]), "functions in RELs": in_rels}, problems


class Census(DC.Census):
    def calls(self, ctx):
        return DC.Census.calls(self, ctx) + ["disc/disc.json, link.json: every REL module linked and catalogued"]

    def run(self, ctx):
        v = DC.Census.run(self, ctx)
        ws = S.ws_of(ctx)
        funcs = json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]
        rels, problems = rel_check(ws, funcs)
        if problems:
            raise S.StageError("the census cannot catalogue REL code: " + "; ".join(problems), problems,
                               "game_setup.py setup --rels ID,ID... (gcdisc.setup links them into linked.dol), or "
                               "a person decides the module is not the game's")
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        v["numbers"].update(rels)
        v["numbers"]["found by"] = "Ghidra over " + cfg["binary"]
        r = REFERENCE
        v["message"] = (f"found by analysis: {len(funcs)} functions (Ghidra over {cfg['binary']}"
                        + (f", {rels['linked']} REL module(s) linked into it, {rels['functions in RELs']} functions in "
                           "them" if rels.get("linked") else ", no REL modules") + f"). Reference: {r['game']}'s census "
                        f"is the same route over its whole main.dol, {r['catalog']} functions, all judged; it lacked "
                        f"{r['missing']}; {v['numbers']['game range']} in project.json's game_ranges")
        v["next"] = ["Narrow game_ranges to the game's own code: the symbols stage's library names (SDK, MSL, "
                     "MetroTRK) mark where the libraries sit", "Data tables of code pointers are not scanned (Skies "
                     "lacked 4 opcode handlers): seed them with project.json seed_entries"]
        return v


# --------------------------------------------------------------------------------------------- kit side, from the code
HW_BLOCKS = [(0xCC000000, "CP command processor"), (0xCC001000, "PE pixel engine"), (0xCC002000, "VI video interface"),
             (0xCC003000, "PI processor interface"), (0xCC004000, "MI memory interface"),
             (0xCC005000, "DSP and ARAM interface"), (0xCC006000, "DI disc interface"), (0xCC006400, "SI serial (pads)"),
             (0xCC006800, "EXI (memory card, RTC, IPL)"), (0xCC006C00, "AI audio interface"),
             (0xCC008000, "GX write-gather pipe"), (0xCC010000, "")]
D_FORM = set(range(32, 56)) | {14}
GPR_WRITES = {14, 15, 32, 33, 34, 35, 40, 41, 42, 43, 46}


def hw_refs(img, base, a, size):
    """The hardware register addresses (0xCC000000-0xCC00FFFF) a function builds from lis + a D-form offset."""
    lo, lis, out = a - base, {}, set()
    if lo < 0 or lo + size > len(img):
        return out
    for (w,) in struct.iter_unpack(">I", img[lo:lo + (size & ~3)]):
        op, rd, ra = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        if op == 15 and ra == 0:
            lis[rd] = (w & 0xFFFF) << 16
            continue
        if op in D_FORM and ra in lis:
            ea = (lis[ra] + ((w & 0xFFFF) ^ 0x8000) - 0x8000) & 0xFFFFFFFF
            if 0xCC000000 <= ea < 0xCC010000:
                out.add(ea)
        if op in GPR_WRITES and rd in lis:  # an addi, addis or load writes rd: no longer the lis base
            del lis[rd]
    return out


def calls_of(img, base, a, size):
    lo = a - base
    out = []
    if lo < 0 or lo + size > len(img):
        return out
    for i, (w,) in enumerate(struct.iter_unpack(">I", img[lo:lo + (size & ~3)])):
        if w >> 26 == 18 and w & 1:
            t = (w & 0x03FFFFFC) - (0x04000000 if w & 0x02000000 else 0)
            out.append((t if w & 2 else a + 4 * i + t) & 0xFFFFFFFF)
    return out


def kit_section(ws, sc):
    """(lines, numbers) for GAPS.md section 4 on a GameCube: there is no kit, so the list is what one would have to
    supply, read from the scope's code: hardware register blocks and SDK/OS calls by matched name."""
    ws = pathlib.Path(ws)
    tool = S.TOOL
    for p in (tool / GC / "tools", tool / "targets" / "xbox360_ppc" / "tools"):
        if str(p) not in sys.path:
            sys.path.append(str(p))
    import gc_names
    cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    img, base, _ex = gc_names.dol_image((ws / cfg["binary"]).read_bytes())
    size = {f["addr"]: f.get("bytes", 0) for f in
            json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]}
    names_f = ws.parent / S.STATE_DIR / "symbols" / "names.json"
    names = json.loads(names_f.read_text(encoding="utf-8")) if names_f.is_file() else {}
    lib = {int(a, 16): v["name"] for a, v in names.items() if v.get("lib")}
    blocks, regs, sdk = collections.Counter(), collections.defaultdict(set), collections.Counter()
    callers = collections.defaultdict(set)
    for a in sc:
        n = size.get(a, 0)
        if not n:
            continue
        ai = int(a, 16)
        for ea in hw_refs(img, base, ai, n):
            name = next(nm for (lo, nm), (hi, _) in zip(HW_BLOCKS, HW_BLOCKS[1:]) if lo <= ea < hi)
            blocks[name] += 1
            regs[name].add(ea)
        if ai not in lib:  # the game's own code (or code no set named) calling a library function
            for t in calls_of(img, base, ai, n):
                if t in lib:
                    sdk[lib[t]] += 1
                    callers[lib[t]].add(a)
    out = ["**No GameCube port kit exists** (DP has no targets/gamecube_ppc/port): every item below is something a kit "
           "would have to supply. Generated from this game's code, not written by hand: hardware register blocks from "
           "lis 0xCC00 bases with their offsets in the scope's functions (block names from the hardware map), SDK and "
           "OS calls from the scope's own functions to the library functions the symbols stage matched.", "",
           f"- hardware register blocks touched: {len(blocks)}"]
    out += [f"  - {nm}: {blocks[nm]} function(s), {len(regs[nm])} register(s) {min(regs[nm]):08x}-{max(regs[nm]):08x}"
            for nm in sorted(blocks, key=lambda k: min(regs[k]))]
    if not lib:
        out.append(f"- SDK and OS calls by name: not known - no verified symbol set named the libraries ({names_f})")
    else:
        fams = collections.Counter()
        for nm, k in sdk.items():
            fams[next((p for p in ("OS", "DVD", "GX", "VI", "PAD", "AI", "AR", "ARQ", "DSP", "CARD", "EXI", "SI", "MTX",
                                   "PSMTX", "VEC", "C_MTX", "AX", "THP") if nm.startswith(p)), "other")] += 1
        out += [f"- library functions the game's own code calls, by matched name: {len(sdk)} "
                f"({', '.join(f'{k} {v}' for k, v in fams.most_common())})"]
        out += [f"  - `{nm}` ({k} call(s) from {len(callers[nm])} function(s))" for nm, k in sdk.most_common(60)]
        out += ["  - ..."] if len(sdk) > 60 else []
    return out, {"kit gaps": "no GameCube kit", "hardware blocks": len(blocks),
                 "library calls named": len(sdk) if lib else "not known"}


class Boot(S.Built):
    """The boot stage's contract on a GameCube, and the inputs it needs; no build is tried (there is no kit)."""
    tools = ()

    def calls(self, ctx):
        return ["contract: in port/game.json (port/CONTRACT.md keys), the translator's C in port form, a GameCube port "
                "kit; out: a bounded headless run (20 s, fixed clock) with frames reached and the first error, as on "
                "the Dreamcast (onboard_dc.Boot)", "list the inputs that are missing; no build tried"]

    def run(self, ctx):
        ws = S.ws_of(ctx)
        need = [(f"a GameCube port kit ({ctx.tool / GC / 'port'}): none exists", "a kit project of its own: the "
                 "hardware blocks and SDK calls in GAPS.md section 4, on the shared port base (port/)")]
        if not (ctx.project / "port" / "game.json").is_file():
            need.append((f"{ctx.project / 'port' / 'game.json'} with its \"entry\"", "a person's choice of the "
                         "game's first function (the game loop __start's main reaches), named in the registry"))
        if not any((ws / "readable" / "names").glob("*.json")):
            need.append((f"a function registry ({ws / 'readable' / 'names'})", "decomp.py systems, then a naming pass"))
        need.append((f"the translator's C judged in port form", f"py -3.12 {GC}/translator/tools/dp_judge.py over every "
                     "function (heavy: a build box); its port form is defined by the kit"))
        gaps = ctx.project / S.STATE_DIR / "GAPS.md"
        lab = [DC.label(w) for w, _ in need]
        raise S.StageError("no build tried: no GameCube port kit. " + f"{len(need)} input(s) missing: " + ", ".join(lab)
                           + (f"; what a kit must supply for this game: {gaps} section 4" if gaps.is_file() else ""),
                           [w for w, _ in need], " | ".join(f"{x}: {s}" for x, (_, s) in zip(lab, need)))

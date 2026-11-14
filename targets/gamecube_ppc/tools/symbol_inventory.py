"""Inventory of GameCube symbol sets: what each set's files really hold, read from the file contents (never the file names),
so a project can find the sets that match its SDK build and middleware, and that carry the symbols it needs.

A set is a folder of CodeWarrior or SN Systems link maps (.map), executables (.dol, .elf) and relocatable modules (.rel).
For each set the scan records:
  sdk       every "<< Dolphin SDK - LIB<TAB>release build: Nov 26 2003 05:18:37 (0x2301) >>" string in the .dol/.elf/.rel
            (library, release or debug, date, time, SDK code), the "Dolphin OS $Revision: N $" string older SDKs carry
            instead, and an SDK install path ("DolphinSDK1.0") where one is embedded
  compiler  compiler and linker strings in the executables (MW EABI PPC C-Compiler, GNU C 3.0.3 SN, ...)
  maps      the map kind (CodeWarrior link map, CodeWarrior section layout, SN/ld style, address list, binary SYM1,
            other) and, parsed from the lines: function names and sizes, data symbols, the library and object each
            came from, functions attributed to the Dolphin SDK libraries (os, dvd, vi, pad, gx, ...), the C/C++ runtime
            (MSL, Runtime), MetroTRK, and whether the addresses fall inside a .dol/.elf of the same set (so names can
            be matched to code by bytes)
  elf       the symbol table (function names and sizes, data symbols, source-file entries) and whether DWARF is present
  rel       the number of relocatable modules and how many have a map of their own
  middleware  library and object names, symbol-name prefixes and version strings of MusyX, JSystem, Bink, HVQM/THP,
            RenderWare, CRI ADX/Sofdec, NetImmerse/Gamebryo, Havok, zlib, Lua, FMOD, Miles, Unreal and others
The symbol files themselves, name lists and game code are never copied into the output: only counts, set names, version
strings, and library and object names.

scan writes one JSON line per set to <out>/inventory.jsonl, skipping sets already there (same name and file list), and
works through two sets at a time (--slots), below normal priority. report writes GAMECUBE_BUILD_VERSIONS.md,
GAMECUBE_SYMBOL_SETS.csv and GAMECUBE_SYMBOL_SETS.json (one row per set, stable field names).

Nearest existing: targets/xbox360_ppc/tools/pdb_inventory.py - the same job for Xbox 360 PDB/XEX sets (its scan/resume/report
shape is copied); its readers are for PDB, XEX and PE, and none of GameCube's .dol, .elf, .rel or CodeWarrior map files
are read anywhere in targets/gamecube_ppc/tools (gcdisc, gcrel, ciso, rvz).

usage: py -3.12 -I symbol_inventory.py scan <folder of sets> --out <dir> [--only REGEX] [--slots 2] [--budget-min N]
       py -3.12 -I symbol_inventory.py report --out <dir> --docs <dir>
       py -3.12 -I symbol_inventory.py one <set folder>        (prints the record, for checking a set by eye)
"""
import argparse
import collections
import datetime
import hashlib
import json
import multiprocessing
import os
import pathlib
import re
import sys
import time


HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "tools"))
from gc_binary import MW_LIB, read_dol, read_elf, scan_blob  # noqa: E402
from gc_maps import MAP_SECTIONS_TEXT, SDK_NAME_RE, is_sdk_lib, libbase, read_map  # noqa: E402,F401
from symset_common import below_normal, wait_gate, write_rows, yn  # noqa: E402

# ---------------------------------------------------------------------------------------------------------------- sets
NAME_TAG = re.compile(r"^\[(?P<plat>GC)\]\s*")
ID_RE = re.compile(r"\[([A-Z0-9]{6,7})\]")
REGION_RE = re.compile(r"\((USA|Europe|Japan|JPN|Germany|France|Spain|Italy|Netherlands|Australia|Korea|Latin America|Sweden|PAL|NSTC|NTSC|"
                       r"USA, Canada|Japan, Demo|JPN\)|Brazil)[^)]*\)")
VER_NAME = re.compile(r"\((v\d+\.\d+)\)|\((Rev \d+)\)")
DATE_NAME = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]|\[(\d{1,2}-\d{1,2}-\d{4})\]|\[(\d{4}-\d{2}-\d{2})\]|"
                       r"\(((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}) Prototype\)|"
                       r"\((?:Alpha [\d.]+ )?(\w+ \d{4})\)|\((\d{4})\)|\((October \d{4}|March \d{4}|September \d{4})\)|"
                       r"\((?:Gekkan [^)]*?(\d{4}\.\d+\.\d+))\)")
TAGS = ("Prototype", "Demo", "Unreleased", "Debug", "Beta", "Preview", "Alpha")


def parse_name(name):
    d = {"set": name}
    m = ID_RE.search(name)
    d["game_id"] = m.group(1) if m else None
    m = REGION_RE.search(name)
    d["region"] = m.group(1) if m else None
    m = VER_NAME.search(name)
    d["version"] = (m.group(1) or m.group(2)) if m else None
    dates = [g for mm in DATE_NAME.finditer(name) for g in mm.groups() if g]
    d["date_in_name"] = dates[0] if dates else None
    d["name_tags"] = [t for t in TAGS if t in name]
    d["title"] = re.sub(r"\s*[\[(].*$", "", NAME_TAG.sub("", name)).strip() or name
    return d


def set_dedupe_key(root):
    items = sorted((str(p.relative_to(root)).lower().replace("\\", "/"), p.stat().st_size) for p in root.rglob("*") if p.is_file())
    return hashlib.sha1(json.dumps(items).encode()).hexdigest()[:16], items


def scan_set(args):
    root_s, name = args
    root = pathlib.Path(root_s) / name
    t0 = time.time()
    key, items = set_dedupe_key(root)
    rec = parse_name(name)
    rec.update(files_key=key, size=sum(s for _, s in items))
    kinds = collections.Counter(pathlib.Path(p).suffix.lower().lstrip(".") or "none" for p, _ in items)
    rec["files"] = dict(sorted(kinds.items()))
    acc = {"sdk": set(), "os_rev": set(), "sdk_path": set(), "compiler": collections.Counter(), "mw_str": collections.defaultdict(set),
           "mw_ver": collections.defaultdict(set)}
    errors, binaries, rel_stems, map_stems = [], [], set(), set()
    elf_fn, elf_data, elf_sized, elf_files, dwarf = set(), set(), set(), 0, collections.Counter()
    ranges = []
    elf_ranges = False
    maps = []
    kind_count = collections.Counter()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".dol":
                i = read_dol(p, acc)
                ranges += [(a, b) for a, b, _ in i["ranges"]]
                binaries.append("dol")
            elif ext == ".elf":
                i = read_elf(p, acc)
                ranges += [(a, b) for a, b, _ in i["ranges"]]
                elf_ranges = elf_ranges or bool(i["ranges"] and i["fn"])
                elf_fn |= i["fn"]
                elf_data |= i["data"]
                elf_sized |= i["fn_sized"]
                elf_files += i["files"]
                for k, v in i["dwarf"].items():
                    dwarf[k] = max(dwarf[k], v)
                binaries.append("elf")
                if i.get("error"):
                    errors.append(f"{p.name}: {i['error']}")
            elif ext in (".rel", ".plf"):
                scan_blob(p.read_bytes(), acc)
                if ext == ".rel":
                    rel_stems.add(p.stem.lower())
            elif ext == ".map":
                s = read_map(p)
                maps.append(s)
                map_stems.add(p.stem.lower())
                kind_count[s.kind] += 1
        except Exception as e:  # noqa: BLE001 - one bad file must not lose the set
            errors.append(f"{p.name}: {type(e).__name__}: {e}"[:160])
    # --- sdk
    libs = collections.defaultdict(list)
    for lib, kind, when, code in sorted(acc["sdk"]):
        libs[lib].append({"kind": kind, "built": when, "code": code})
    rec["sdk_libs"] = dict(libs)
    rec["sdk_codes"] = sorted({c for v in libs.values() for x in v for c in [x["code"]]})
    rec["sdk_kinds"] = sorted({x["kind"] for v in libs.values() for x in v})
    dates = []
    for v in libs.values():
        for x in v:
            for fmt in ("%b %d %Y %H:%M:%S", "%b %d %Y"):
                try:
                    dates.append(datetime.datetime.strptime(x["built"], fmt))
                    break
                except ValueError:
                    pass
    rec["sdk_date_min"] = min(dates).strftime("%Y-%m-%d") if dates else None
    rec["sdk_date_max"] = max(dates).strftime("%Y-%m-%d") if dates else None
    rec["sdk_os_built"] = libs["OS"][0]["built"] if "OS" in libs else None
    rec["os_revision"] = sorted(acc["os_rev"])
    rec["sdk_path"] = sorted(acc["sdk_path"])
    rec["sdk_from"] = ("strings in " + "/".join(sorted(set(binaries) or {"?"}))) if libs else \
        ("OS revision string only" if acc["os_rev"] else ("no SDK strings" if binaries else "no executable in the set"))
    allc = [k for k, _ in acc["compiler"].most_common(12)]
    rec["compiler"] = [k for k in allc if not k.startswith(("MetroTRK", "Metrowerks Target"))][:5]
    rec["metrotrk"] = [k for k in allc if k.startswith("MetroTRK for")][:2]
    cfg = set(rec["sdk_kinds"])
    paths = " ".join(p for p, _ in items).lower()
    for w in ("debug", "release", "final", "profile", "master"):
        if re.search(r"(^|[^a-z])" + w + r"([^a-z]|$)", paths):
            cfg.add("path:" + w)
    rec["config"] = sorted(cfg)
    # --- maps and symbols
    fn, sized, data, sdkfn, mslfn, trkfn = set(), set(), set(), set(), set(), set()
    have_lib = have_obj = False
    libcnt, objcnt, vs, rel_n = collections.Counter(), collections.Counter(), [], 0
    for s in maps:
        fn |= s.fn
        sized |= s.fn_sized
        data |= s.data
        sdkfn |= s.sdk_fn
        mslfn |= s.msl_fn
        trkfn |= s.trk_fn
        have_lib |= s.have_lib
        have_obj |= s.have_obj
        libcnt.update(s.libs)
        objcnt.update(s.objs)
        vs += s.vaddr_sample[:60]
        rel_n += s.relative
    rec["map_kinds"] = dict(kind_count)
    rec["maps"] = len(maps)
    nonmap_fn = len(fn)
    rec["fn_names"] = max(nonmap_fn, len(elf_fn))
    rec["fn_names_from"] = ("map" if nonmap_fn >= len(elf_fn) else "elf symtab") if rec["fn_names"] else None
    rec["fn_sizes"] = max(len(sized), len(elf_sized))
    rec["data_syms"] = max(len(data), len(elf_data))
    rec["lib_obj"] = "lib+obj" if have_lib else ("obj" if have_obj or elf_files else "no")
    rec["elf_file_entries"] = elf_files
    # SDK functions: by library when the map names it; by name prefix otherwise (labelled)
    if have_lib:
        rec["sdk_fns"], rec["sdk_fns_basis"] = len(sdkfn), "library"
        rec["msl_fns"], rec["trk_fns"] = len(mslfn), len(trkfn)
    elif fn or elf_fn:
        names = fn | elf_fn
        rec["sdk_fns"], rec["sdk_fns_basis"] = sum(1 for n in names if SDK_NAME_RE.match(n)), "name prefix"
        rec["msl_fns"] = None
        rec["trk_fns"] = sum(1 for n in names if re.match(r"^(TRK|Trk|trk|MetroTRK)", n))
    else:
        rec["sdk_fns"] = rec["msl_fns"] = rec["trk_fns"] = 0
        rec["sdk_fns_basis"] = None
    rec["dwarf"] = dict(dwarf)
    # --- can the names be matched to code by bytes?
    inside = 0
    for a in vs:
        if any(lo <= a < hi for lo, hi in ranges):
            inside += 1
    if vs and ranges and inside >= 0.5 * len(vs):
        rec["bytes_match"] = "dol/elf"
    elif elf_fn and elf_ranges:
        rec["bytes_match"] = "elf"      # the ELF holds its own symbols and the code they name
    elif (rel_n or any(s.relative for s in maps)) and rel_stems:
        rec["bytes_match"] = "rel"
    else:
        rec["bytes_match"] = "no"
    rec["rels"] = len(rel_stems)
    rec["rels_with_map"] = len(rel_stems & map_stems)
    # --- middleware
    names_blob = list(libcnt) + list(objcnt) + sorted({p for s in maps for p in s.paths}) + [pathlib.Path(p).stem for p, _ in items]
    mw = {}
    for (n, rx) in MW_LIB:
        hit_names = sorted({x for x in names_blob if rx.search(x)})
        # an object or library prefix alone is weak: demand 2 distinct names, or a string hit
        strs = sorted(acc["mw_str"].get(n, set()))
        if strs or len(hit_names) >= 2:
            mw[n] = {"strings": strs[:3], "versions": sorted(acc["mw_ver"].get(n, set()))[:4], "names": hit_names[:4]}
    rec["middleware"] = mw
    rec["errors"] = errors[:5]
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def scan(a):
    below_normal()
    if not wait_gate():
        return 4
    src, out = pathlib.Path(a.src), pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    inv = out / "inventory.jsonl"
    done = set()
    if inv.exists():
        for ln in inv.read_text(encoding="utf-8").splitlines():
            done.add(json.loads(ln)["set"])
    items = sorted(p.name for p in src.iterdir() if p.is_dir())
    if a.only:
        items = [n for n in items if re.search(a.only, n, re.I)]
    items = [n for n in items if n not in done]
    if a.limit:
        items = items[:a.limit]
    t0, n = time.time(), 0
    with multiprocessing.Pool(a.slots, initializer=below_normal) as pool, open(inv, "a", encoding="utf-8") as fh:
        for rec in pool.imap_unordered(scan_set, [(str(src), n_) for n_ in items]):
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            n += 1
            if n % 25 == 0 or n == len(items):
                print(f"{n}/{len(items)} sets, {round(time.time() - t0)} s", flush=True)
            if a.budget_min and time.time() - t0 > a.budget_min * 60:
                print("budget reached; run again to continue")
                pool.terminate()
                break
    print(f"{n} sets added; {len(done) + n} in {inv}; {round(time.time() - t0)} s")
    return 0


def one(a):
    rec = scan_set((str(pathlib.Path(a.path).parent), pathlib.Path(a.path).name))
    print(json.dumps(rec, indent=1))
    return 0



# -------------------------------------------------------------------------------------------------------------- report
def sdk_cell(r):
    if r["sdk_codes"]:
        d = r["sdk_date_min"] if r["sdk_date_min"] == r["sdk_date_max"] else f"{r['sdk_date_min']}..{r['sdk_date_max']}"
        return f"{', '.join(r['sdk_codes'])} {'/'.join(r['sdk_kinds'])} {d} ({len(r['sdk_libs'])} libs)"
    if r["os_revision"]:
        return "OS revision " + ", ".join(map(str, r["os_revision"])) + " only"
    return "not known"


def flat(r):
    """One flat row with stable field names, for the CSV and the JSON."""
    mw = r["middleware"]
    row = {
        "set": r["set"], "title": r["title"], "game_id": r["game_id"], "region": r["region"], "version": r["version"],
        "date_in_name": r["date_in_name"], "name_tags": "; ".join(r["name_tags"]), "copies": r["copies"],
        "files": " ".join(f"{k}{'x' + str(v) if v > 1 else ''}" for k, v in r["files"].items()),
        "size_bytes": r["size"],
        "sdk_codes": "; ".join(r["sdk_codes"]) or None, "sdk_kinds": "; ".join(r["sdk_kinds"]) or None,
        "sdk_date_min": r["sdk_date_min"], "sdk_date_max": r["sdk_date_max"], "sdk_os_built": r["sdk_os_built"],
        "sdk_lib_count": len(r["sdk_libs"]), "sdk_libs": "; ".join(f"{k} {v[0]['built']}" for k, v in sorted(r["sdk_libs"].items())) or None,
        "os_revision": "; ".join(map(str, r["os_revision"])) or None, "sdk_from": r["sdk_from"],
        "compiler": "; ".join(r["compiler"][:3]) or None, "metrotrk": "; ".join(r["metrotrk"]) or None, "config": "; ".join(r["config"]) or None,
        "map_kinds": "; ".join(sorted(r["map_kinds"])) or None, "map_files": r["maps"],
        "fn_names": bool(r["fn_names"]), "fn_names_count": r["fn_names"], "fn_names_from": r["fn_names_from"],
        "fn_sizes": bool(r["fn_sizes"]), "fn_sizes_count": r["fn_sizes"],
        "bytes_match": r["bytes_match"], "lib_obj": r["lib_obj"],
        "sdk_fns": bool(r["sdk_fns"]), "sdk_fns_count": r["sdk_fns"], "sdk_fns_basis": r["sdk_fns_basis"],
        "msl_fns": (bool(r["msl_fns"]) if r["msl_fns"] is not None else None), "msl_fns_count": r["msl_fns"],
        "trk_fns": bool(r["trk_fns"]), "trk_fns_count": r["trk_fns"],
        "data_syms": bool(r["data_syms"]), "data_syms_count": r["data_syms"],
        "dwarf": bool(r["dwarf"]), "dwarf_sections": "; ".join(sorted(r["dwarf"])) or None,
        "rel_files": r["rels"], "rel_with_map": r["rels_with_map"],
        "middleware": "; ".join(f"{k}{' ' + '/'.join(v['versions']) if v['versions'] else ''}" for k, v in sorted(mw.items())) or None,
    }
    return row


def report(a):
    out, docs = pathlib.Path(a.out), pathlib.Path(a.docs)
    recs = [json.loads(ln) for ln in (out / "inventory.jsonl").read_text(encoding="utf-8").splitlines()]
    grp = collections.Counter(r["files_key"] for r in recs)
    for r in recs:
        r["copies"] = grp[r["files_key"]]
    recs.sort(key=lambda r: (r["sdk_os_built"] is None, r["sdk_codes"], r["title"].lower(), r["set"]))
    rows = [flat(r) for r in recs]
    docs.mkdir(parents=True, exist_ok=True)
    write_rows(docs, "GAMECUBE_SYMBOL_SETS", rows)   # the CSV; the JSON is rewritten below with the per-library detail
    json_full = [{**flat(r), "sdk_libs_detail": r["sdk_libs"], "middleware_detail": r["middleware"]} for r in recs]
    (docs / "GAMECUBE_SYMBOL_SETS.json").write_text(json.dumps(json_full, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    table = ["| Set | Copies | Has | Game ID | SDK build | Compiler | Cfg | Fn names | Sizes | Bytes | Lib/obj | SDK fns | MSL | TRK | Data | DWARF | .rel maps | Middleware |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r, f in zip(recs, rows):
        has = f["files"].replace("x", "×")
        table.append("| " + " | ".join([
            r["set"].replace("|", "/"), str(r["copies"]), has, r["game_id"] or "not known", sdk_cell(r),
            (r["compiler"][0] if r["compiler"] else "not known").replace("|", "/")[:40], "/".join(c.replace("path:", "") for c in r["config"]) or "not known",
            yn(r["fn_names"], r["fn_names"]), "yes" if r["fn_sizes"] else "no", r["bytes_match"], r["lib_obj"],
            yn(r["sdk_fns"], r["sdk_fns"]) + (" *" if r["sdk_fns_basis"] == "name prefix" and r["sdk_fns"] else ""),
            yn(r["msl_fns"], r["msl_fns"]) if r["msl_fns"] is not None else "not known", yn(r["trk_fns"], r["trk_fns"]),
            yn(r["data_syms"], r["data_syms"]), ("yes" if r["dwarf"] else "no"),
            f"{r['rels_with_map']} of {r['rels']}" if r["rels"] else "—",
            f["middleware"] or "none found"]) + " |")
    # indexes
    def index(title, groups):
        L = ["", f"### {title}", "", "| Key | Sets |", "|---|---|"]
        for k in sorted(groups):
            L.append(f"| {k} | {'; '.join(sorted(groups[k]))} |")
        return L
    by_sdk, by_mw, by_names, by_dwarf = (collections.defaultdict(list) for _ in range(4))
    for r in recs:
        if r["sdk_codes"]:
            by_sdk[f"{', '.join(r['sdk_codes'])}, OS built {r['sdk_os_built'] or 'not known'}"].append(r["set"])
        elif r["os_revision"]:
            by_sdk["OS revision " + ", ".join(map(str, r["os_revision"])) + " (no build string)"].append(r["set"])
        for k, v in r["middleware"].items():
            by_mw[k + (" " + "/".join(v["versions"]) if v["versions"] else "")].append(r["set"])
        if r["fn_names"] and r["bytes_match"] != "no":
            by_names[f"{r['fn_names_from']}, matching code {r['bytes_match']}"].append(r["set"])
        if r["dwarf"]:
            by_dwarf["; ".join(sorted(r["dwarf"]))].append(r["set"])
    (docs / "_tables.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    (docs / "_indexes.md").write_text("\n".join(index("By SDK build", by_sdk) + index("By middleware and version", by_mw) +
                                                index("Sets with function names and matching code", by_names) +
                                                index("Sets with DWARF", by_dwarf)) + "\n", encoding="utf-8")
    c = collections.Counter()
    for r in recs:
        c["sets"] += 1
        c["sdk read"] += bool(r["sdk_codes"])
        c["os revision only"] += (not r["sdk_codes"]) and bool(r["os_revision"])
        c["0x2301"] += "0x2301" in r["sdk_codes"]
        c["fn names"] += bool(r["fn_names"])
        c["bytes"] += r["bytes_match"] != "no"
        c["dwarf"] += bool(r["dwarf"])
        c["maps"] += bool(r["maps"])
    print(json.dumps(c))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("src")
    s.add_argument("--out", required=True)
    s.add_argument("--only")
    s.add_argument("--slots", type=int, default=2)
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--budget-min", type=float, default=0)
    r = sub.add_parser("report")
    r.add_argument("--out", required=True)
    r.add_argument("--docs", required=True)
    o = sub.add_parser("one")
    o.add_argument("path")
    a = ap.parse_args(argv)
    return {"scan": scan, "report": report, "one": one}[a.cmd](a)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())


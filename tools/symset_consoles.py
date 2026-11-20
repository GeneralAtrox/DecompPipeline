"""The scan / report / one commands of `symbol_inventory.py --console xbox|dreamcast|saturn`: lists a console's sets, scans each with
that console's reader (symset_xbox.scan_set, symset_sega.scan_dc, symset_sega.scan_disc), and writes the rows into the build-version
doc between its <!-- sets --> and <!-- indexes --> markers, so the hand-written text around them ("Our ports", reading your own
build) is never overwritten. Also writes <CONSOLE>_SYMBOL_SETS.csv/.json (one flat row per set, stable field names).

A Saturn "set" is one retail disc (.cue); the Blast Chamber disc is read for its file list and library strings only.
`py -3.12 -I tools/symset_consoles.py refresh-n64` refreshes docs/external/n64 (a GPL-3.0 snapshot of the public N64 table) from its clone.

Nearest existing: targets/gamecube_ppc/tools/symbol_inventory.py - its scan/report shape is copied and it calls this module, because
GameCube's readers (dol/elf/rel, CodeWarrior maps) read none of the Xbox, Dreamcast or Saturn files; symset_common is shared.
"""
import collections
import hashlib
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import symset_sega  # noqa: E402
import symset_xbox  # noqa: E402
from symset_common import below_normal, write_rows, yn  # noqa: E402

DOC = {"xbox": "XBOX_BUILD_VERSIONS.md", "dreamcast": "DREAMCAST_BUILD_VERSIONS.md", "saturn": "SATURN_BUILD_VERSIONS.md"}
STEM = {"xbox": "XBOX_SYMBOL_SETS", "dreamcast": "DREAMCAST_SYMBOL_SETS", "saturn": "SATURN_DISCS"}
SDK_DC = re.compile(r"^(Shinobi|KAMUI|Kamui|Ninja|Nindows|Katana|DirectX)", re.I)
LIB_MW = {"binkxbox": "bink", "bink": "bink", "ngl_xbox": "ngl_xbox lib", "nsl_xbox": "nsl_xbox lib", "nvl_xbox": "nvl_xbox lib"}
DC_CC = {"SHC\\INCLUDE": "Hitachi SHC (include path)", "Hitachi SH Includes": "Hitachi SH includes", "MSL C++\\Inc": "Metrowerks MSL C++",
         "Metrowerks": "Metrowerks", "CodeWarrior": "CodeWarrior"}


def list_sets(console, src):
    src = pathlib.Path(src)
    if console == "saturn":
        return sorted((p.stem, p) for p in src.rglob("*.cue") if "PS1" not in str(p) and ".work" not in p.parts)
    skip = re.compile(r"_GC_|_PS2_")
    return sorted((p.name, p) for p in src.iterdir() if p.is_dir() and not skip.search(p.name))


def files_key(path):
    base = path.parent if path.suffix == ".cue" else path
    items = sorted((str(p.relative_to(base)).lower().replace("\\", "/"), p.stat().st_size) for p in base.rglob("*") if p.is_file())
    return hashlib.sha1(json.dumps(items).encode()).hexdigest()[:16], items


def scan_one(console, name, path):
    t0 = time.time()
    key, items = files_key(path)
    rec = {"set": name, "files_key": key, "size": sum(s for _, s in items),
           "has": dict(sorted(collections.Counter(pathlib.Path(p).suffix.lower().lstrip(".") or "none" for p, _ in items).items()))}
    rec.update({"xbox": lambda: symset_xbox.scan_set(path), "dreamcast": lambda: symset_sega.scan_dc(path),
                "saturn": lambda: symset_sega.scan_disc(path)}[console]())
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def run(a):
    c = a.console
    if a.cmd == "one":
        p = pathlib.Path(a.path)
        print(json.dumps(scan_one(c, p.stem if p.suffix == ".cue" else p.name, p), indent=1))
        return 0
    out = pathlib.Path(a.out)
    inv = out / f"inventory-{c}.jsonl"
    if a.cmd == "scan":
        below_normal()
        out.mkdir(parents=True, exist_ok=True)
        done = {json.loads(ln)["set"] for ln in inv.read_text(encoding="utf-8").splitlines()} if inv.exists() else set()
        todo = [(n, p) for n, p in list_sets(c, a.src) if n not in done and (not a.only or re.search(a.only, n, re.I))]
        with open(inv, "a", encoding="utf-8") as fh:
            for n, p in todo:
                fh.write(json.dumps(scan_one(c, n, p)) + "\n")
                fh.flush()
        print(f"{len(todo)} sets added; {len(done) + len(todo)} in {inv}")
        return 0
    recs = [json.loads(ln) for ln in inv.read_text(encoding="utf-8").splitlines()]
    docs = pathlib.Path(a.docs)
    rows = [FLAT[c](r) for r in recs]
    rows.sort(key=lambda r: r["_sort"])
    for r in rows:
        r.pop("_sort")
    write_rows(docs, STEM[c], rows)
    head, cells = HEAD[c], CELLS[c]
    table = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(cells(r)) + " |" for r in rows]
    idx = INDEX[c](rows)
    doc = docs / DOC[c]
    t = doc.read_text(encoding="utf-8")
    for tag, body in (("sets", table), ("indexes", idx)):
        t = re.sub(rf"(<!-- {tag} -->\n).*?(\n<!-- /{tag} -->)", lambda m: m.group(1) + "\n".join(body) + m.group(2), t, flags=re.S)
    doc.write_text(t, encoding="utf-8")
    print(f"{len(rows)} rows into {doc.name}")
    return 0


# ------------------------------------------------------------------------------------------------------------------- Xbox
def xbox_flat(r):
    xb = max(r["xbes"], key=lambda x: len(x.get("libs", [])), default=None)
    libs = {l["name"]: l["build"] for l in xb["libs"]} if xb else {}
    xdk = libs.get("XAPILIB")
    other = {k: v for k, v in libs.items() if xdk is not None and v != xdk}
    pdb = max(r["pdbs"], key=lambda p: p["fn_names"], default=None)
    mp = max(r["maps"], key=lambda m: m["fns"], default=None)
    pe = r["pes"][0] if r["pes"] else None
    names = set(r["middleware"])
    libnames = set(pdb["libraries"]) if pdb else set(mp["libs"]) if mp else set()
    for ln in libnames:
        base = ln.replace(".lib", "")
        if LIB_MW.get(base):
            names.add(LIB_MW[base])
    rich = pe["rich"] if pe else {}
    cc = ["MASM " + k.split("Version ")[-1] for k in (pdb["compilers"] if pdb else {})][:1]
    if pdb and not rich:
        cc.append("C/C++ compiler not named in the PDB")
    if rich:
        cc = [f"Rich header: build {max(rich, key=rich.get).split(':')[1]} ({max(rich.values())} objs), linker {pe['linker']}"] + cc
    code = ("build id" if pdb and pdb["matches"] else "same folder" if mp and xb else "no" if pdb else "no")
    return {"set": r["set"], "_sort": (xdk is None, xdk or 0, r["set"]), "has": " ".join(f"{k}{'x' + str(v) if v > 1 else ''}" for k, v in r["has"].items()),
            "title": xb.get("title") if xb else None, "title_id": xb.get("title_id") if xb else None, "xdk_build": xdk,
            "xdk_libs": "; ".join(f"{k} {v}" for k, v in libs.items()) or None, "other_xdk_libs": "; ".join(f"{k} {v}" for k, v in other.items()) or None,
            "xbe_link_date": xb.get("link_date") if xb else None, "xbe_version": xb.get("version") if xb else None,
            "compiler": "; ".join(cc) or None, "middleware": "; ".join(sorted(names)) or None,
            "fn_names": max(pdb["fn_names"] if pdb else 0, mp["fns"] if mp else 0), "fn_sized": bool(pdb and pdb["fn_sized"]),
            "code": code, "lib_obj": "lib+obj" if (pdb or mp) else "no", "sdk_fns": pdb["sdk_fns"] if pdb else None, "crt_fns": pdb["crt_fns"] if pdb else None,
            "data_syms": pdb["data_syms"] if pdb else None,
            "debug": (f"types {pdb['types']:,}, locals {pdb['locals']:,}, lines in {pdb['line_modules']} of {pdb['modules']} modules" if pdb else "no"),
            "pdb_link_date": pdb["link_date"] if pdb else None, "xbe_count": len(r["xbes"]), "map_files": len(r["maps"])}


def xbox_cells(f):
    n = lambda v: "n/r" if v is None else yn(v, v)
    return [f["set"], f["has"], f["title_id"] or "not known", f"{f['xdk_build']} ({f['xbe_link_date']})" if f["xdk_build"] else "not known",
            f["other_xdk_libs"] or "none", (f["compiler"] or "not known").replace("|", "/"), f["middleware"] or "none found",
            yn(f["fn_names"], f["fn_names"]), "yes" if f["fn_sized"] else "no", f["code"], f["lib_obj"], n(f["sdk_fns"]), n(f["crt_fns"]), n(f["data_syms"]), f["debug"]]


def xbox_index(rows):
    g, m = collections.defaultdict(list), collections.defaultdict(list)
    for r in rows:
        g[f"XAPILIB {r['xdk_build']}" if r["xdk_build"] else "not known"].append(r["set"])
        for k in (r["middleware"] or "").split("; ") if r["middleware"] else []:
            m[k].append(r["set"])
    return index("By XDK build (XAPILIB build number)", g) + index("By middleware", m)


# ---------------------------------------------------------------------------------------------------------------- Dreamcast
def dc_flat(r):
    st = r["stamps"]
    sdk = {k: v for k, v in st.items() if SDK_DC.match(k)}
    fmt = lambda d: "; ".join(f"{k} {v[0][0]} ({v[0][1].split(' ')[0]} {v[0][1].split(' ')[2]})" if len(v[0][1].split(" ")) > 2 else f"{k} {v[0][0]}" for k, v in d.items())
    maps = sorted({m["kind"] for m in r["maps"]})
    elf = r.get("elf", [])
    dw = sorted({e["dwarf"] for e in elf if e["dwarf"]})
    return {"set": r["set"], "_sort": (not sdk, r["set"]), "has": " ".join(f"{k}{'x' + str(v) if v > 1 else ''}" for k, v in r["has"].items()),
            "sdk": fmt(sdk) or None, "other_stamps": len(st) - len(sdk), "all_stamps": "; ".join(f"{k} {v[0][0]}" for k, v in st.items()) or None,
            "compiler": "; ".join(r["compiler"][:2] + [DC_CC.get(x, x) for x in r["compiler_paths"][:2]]) or (f"not stated; map is {maps[0]}" if maps else None), "maps": "; ".join(maps) or None,
            "middleware": "; ".join(r["middleware"]) or None, "fn_names": max(r["fn_names"], max((m["symbols"] for m in r["maps"]), default=0), r["sym_lines"]),
            "fn_sized": bool(r["fn_sized"]), "data_syms": max(r["data_syms"], max((m.get("data", 0) for m in r["maps"]), default=0)), "dwarf": "; ".join(dw) or None, "lines": any(e["lines"] for e in elf),
            "elf_files": len(elf), "pe_files": len(r.get("pe", [])), "pe": "; ".join(f"{p['machine']} {p['subsystem']} link {p['linker']}" for p in r.get("pe", [])[:1]) or None}


def dc_cells(f):
    return [f["set"], f["has"], f["sdk"] or "not known" if f["sdk"] or not f["pe"] else f"Windows CE ({f['pe']})", f"+{f['other_stamps']} libs" if f["other_stamps"] else "none",
            (f["compiler"] or "not known").replace("|", "/"), f["maps"] or "none", f["middleware"] or "none found", yn(f["fn_names"], f["fn_names"]),
            "yes" if f["fn_sized"] else "no", yn(f["data_syms"], f["data_syms"]), f["dwarf"] or "no", "yes" if f["lines"] else "no"]


def dc_index(rows):
    g, m = collections.defaultdict(list), collections.defaultdict(list)
    for r in rows:
        for part in (r["sdk"] or "not known").split("; "):
            g[part].append(r["set"])
        for k in (r["middleware"] or "").split("; ") if r["middleware"] else []:
            m[k].append(r["set"])
    return index("By SDK library and version", g) + index("By middleware", m)


# ----------------------------------------------------------------------------------------------------------------- Saturn
def sat_flat(r):
    libs = {k: v for k, v in r["libs"].items() if k.endswith(("_SBL", "_SGL")) or k in ("SYS", "CDC", "BUP", "CPK", "PCM", "FLD", "HFLD")}
    fam = sorted({k.rsplit("_", 1)[1] for k in libs if k.endswith(("_SBL", "_SGL"))})
    snd = "; ".join(f"Ver{v} {d}" for v, d in r["sound_driver"]) or None
    return {"set": r["set"], "_sort": (not fam, "/".join(fam), r["set"]), "product": r["ip"]["product"], "version": r["ip"]["version"], "ip_date": r["ip"]["date"],
            "maker": r["ip"]["maker"], "area": r["ip"]["area"], "files": r["files"], "kit": "/".join(fam) or None, "has_driver_file": bool(r["sound_driver_files"]),
            "libs": "; ".join(f"{k} {v[0][0]} ({v[0][1]})" for k, v in libs.items()) or None,
            "sound_driver": snd, "sound_driver_files": "; ".join(r["sound_driver_files"][:3]) or None,
            "middleware": "; ".join(f"{k} ({n} files)" for k, n in sorted(r["middleware"].items())) or None,
            "compiler_strings": "; ".join(r["compiler_strings"]) or None, "bytes_read_mb": round(r["bytes_read"] / 2**20, 1)}


def sat_cells(f):
    return [f["set"], f"{f['product']} {f['version']}", str(f["files"]), f["kit"] or "no SBL/SGL stamp", f["libs"] or "none read", f["sound_driver"] or ("file present, no version string" if f["has_driver_file"] else "none found"),
            f["sound_driver_files"] or "none", f["middleware"] or "none found", f["compiler_strings"] or "not stated in the disc"]


def sat_index(rows):
    g, d = collections.defaultdict(list), collections.defaultdict(list)
    for r in rows:
        for part in (r["libs"] or "none read").split("; "):
            if part.split(" ")[0].endswith(("_SBL", "_SGL")):
                g[part].append(r["set"])
        d[r["sound_driver"] or "not read"].append(r["set"])
    return index("By SBL/SGL library stamp", g) + index("By sound driver", d)


def index(title, groups):
    return ["", f"### {title}", "", "| Key | Sets |", "|---|---|"] + [f"| {k} | {'; '.join(sorted(set(v)))} |" for k, v in sorted(groups.items())]


FLAT = {"xbox": xbox_flat, "dreamcast": dc_flat, "saturn": sat_flat}
CELLS = {"xbox": xbox_cells, "dreamcast": dc_cells, "saturn": sat_cells}
INDEX = {"xbox": xbox_index, "dreamcast": dc_index, "saturn": sat_index}
HEAD = {"xbox": ["Set", "Has", "Title ID", "XDK build (link date)", "Other XDK libs", "Compiler", "Middleware", "Fn names", "Sizes", "Code", "Lib/obj", "SDK fns", "CRT", "Data", "Debug"],
        "dreamcast": ["Set", "Has", "SDK libraries (version, build date)", "Other libs", "Compiler", "Maps", "Middleware", "Fn names", "Sizes", "Data", "DWARF", "Lines"],
        "saturn": ["Disc", "Product", "Files", "Kit", "Library stamps", "Sound driver", "Driver files", "Middleware", "Compiler"]}


# --------------------------------------------------------------------------------------------- mirror of the public N64 table
N64_URL = "https://github.com/TheGh0stShip/N64-Decomp-Kit"
N64_FILES = ["docs/research/N64_BUILD_VERSIONS.md", "docs/research/N64_BUILD_VERSIONS.csv", "docs/research/N64_BUILD_VERSIONS.json"]


def _git(clone, *args, timeout=180):
    import subprocess
    r = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def n64_changes(old_rows, new_rows):
    """Lines saying which games (by ROM sha1) were added or removed and which values changed."""
    o, n = {r["sha1"]: r for r in old_rows}, {r["sha1"]: r for r in new_rows}
    out = [f"added: {n[k]['file']}" for k in n if k not in o] + [f"removed: {o[k]['file']}" for k in o if k not in n]
    for k in n.keys() & o.keys():
        out += [f"changed: {n[k]['file']}: {f} {o[k].get(f)!r} -> {n[k].get(f)!r}" for f in sorted(set(n[k]) | set(o[k])) if n[k].get(f) != o[k].get(f)]
    return out


def refresh_n64(clone, dest):
    """Pull the clone of N64-Decomp-Kit and copy its build-version table into dest when the commit changed. Never overwrites on a
    failed pull, a missing file, an unreadable table or one with no rows. Returns 0 (done or unchanged) or 1 (nothing touched)."""
    import datetime
    import shutil
    clone, dest = pathlib.Path(clone), pathlib.Path(dest)
    rc, _o, err = _git(clone, "pull", "--ff-only", "--quiet")
    if rc:
        print(f"pull failed, snapshot untouched: {err[:200]}")
        return 1
    rc, head, _e = _git(clone, "rev-parse", "HEAD")
    rc2, when, _e = _git(clone, "log", "-1", "--format=%cI")
    src = [clone / f for f in N64_FILES] + [clone / "COPYING"]
    if rc or rc2 or not all(p.is_file() and p.stat().st_size for p in src):
        print("a source file is missing or empty, snapshot untouched")
        return 1
    try:
        new_rows = json.loads(src[2].read_text(encoding="utf-8"))
    except ValueError:
        new_rows = []
    if not new_rows:
        print("the table has no rows (or does not parse), snapshot untouched")
        return 1
    src_md, old = dest / "SOURCE.md", (json.loads((dest / "N64_BUILD_VERSIONS.json").read_text(encoding="utf-8")) if (dest / "N64_BUILD_VERSIONS.json").exists() else [])
    if src_md.exists() and head in src_md.read_text(encoding="utf-8") and old:
        print(f"unchanged at {head[:10]}: {len(old)} games")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    for p in src[:3]:
        shutil.copyfile(p, dest / p.name)
    shutil.copyfile(src[3], dest / "LICENSE")
    src_md.write_text(f"""# Source of this snapshot

**Do not edit these files: they are refreshed from upstream** (`py -3.12 -I tools/symset_consoles.py refresh-n64`), and kept apart from
our own tables so the licence stays clear.

- Upstream: {N64_URL} (branch `main`), local clone `C:/Projects/Phangrade/External/N64-Decomp-Kit`
- Files: `N64_BUILD_VERSIONS.md`, `.csv` and `.json` from `docs/research/`, unchanged
- Upstream commit: `{head}` (committed {when})
- Snapshot taken: {datetime.date.today().isoformat()} ({len(new_rows)} ROMs)
- Licence: GNU General Public License v3.0, the upstream `COPYING`, kept here as `LICENSE`. Nothing of ours is mixed into these files.
""", encoding="utf-8")
    ch = n64_changes(old, new_rows) if old else ["first snapshot"]
    print(f"snapshot at {head[:10]}: {len(new_rows)} games; {len(ch)} change(s)")
    print("\n".join(ch[:40]))
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=refresh_n64.__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["refresh-n64"])
    ap.add_argument("--clone", default=r"C:\Projects\Phangrade\External\N64-Decomp-Kit")
    ap.add_argument("--dest", default=str(HERE.parent / "docs" / "external" / "n64"))
    a = ap.parse_args()
    sys.exit(refresh_n64(a.clone, a.dest))

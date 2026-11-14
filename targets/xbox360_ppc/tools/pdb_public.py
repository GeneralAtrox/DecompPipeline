"""Builds the public Xbox 360 symbol-set table (docs/XBOX360_BUILD_VERSIONS.md plus .csv and .json) from the table made by
pdb_inventory.py and the content records pdb_content.py read from the symbol sets that are still at hand.

Every row keeps what the first inventory found. The sets whose files were re-read get the content columns (function names,
sizes, matching code, library attribution, SDK and runtime functions, data symbols, debug information) and any blank
version cell (title ID, XDK build, compiler) the files can fill; every other set reads "n/r" (not re-read), never a guess.
The text is written for an outside reader: no machine names, paths or project notes.

Nearest existing: pdb_inventory.py report - writes the first table from its own scan records, which no longer exist for most
sets; this edits the published table, so it works from the document itself. Shares symset_common (legend, yn, write_rows) with
the GameCube table builder (gamecube_ppc/tools/symbol_inventory.py report).

usage: py -3.12 -I pdb_public.py build --doc docs/XBOX360_BUILD_VERSIONS.md --content <content.jsonl> [--check]
"""
import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "tools"))
from symset_common import CONTENT_LEGEND, NOT_READ, write_rows, yn  # noqa: E402

OLD = ["set", "copies", "has", "title_id", "version", "xdk", "xdk_from", "compiler", "config", "other_xdk_libs", "middleware"]
HEAD = ["Set", "Copies", "Has", "Title ID", "Version", "XDK", "From", "Compiler", "Config", "Other XDK libs", "Middleware"]
NEW = ["fn_names", "sizes", "code", "lib_obj", "sdk_fns", "crt_fns", "data_syms", "debug", "content_read"]
NEWHEAD = ["Fn names", "Sizes", "Code", "Lib/obj", "SDK fns", "CRT", "Data", "Debug", "Read"]

# set folder -> the row of the first table that it is (the rows keep the names the first inventory gave them)
KEPT = {
    "Bourne_2008-01-13_PDB": "Robert Lundlum's The Bourne Supremacy [2008-01-13] (PDB)",
    "GearsOfWar_2006-09-21_PDB": "Gears of War [2006-09-21] (PDB)",
    "Gears_of_War_2_2008_09_14_PDB": "Gears of War 2 [2008-09-14] (PDB)",
    "Godfather2_MP_2008-11-18_PDB": "Godfather 2 [2008-11-18] (MP Build) (XEX+PDB)",
    "Godfather2_SP_2008-11-07_PDB_MAP": "Godfather 2 [2008-11-07] (SP Build) (EXE+XEX+PDB+MAP)",
    "Halo3_2007-03-09_MAP": "Halo 3 [2007-03-09] (MAP)",
    "Highlander_2008-04-08_PDB": "Highlander [2008-04-08] (PDB)",
    "MassEffect2_JAP_2010-09-10_PDB": "Mass Effect 2 (JAP) [2010-09-10] (PDB)",
    "NFSUndercover_2008-08-19_MAP_PDB": "Need for Speed Undercover [2008-08-19] (MAP+PDB)",
    "Saboteur_2008-06-26_MAP_PDB": "The Saboteur [2008-06-26] (MAP+PDB)",
    "SilentHillHomecoming_2008-06-19_MAP_PDB": "Silent Hill Homecoming [2008-06-19] (MAP+PDB)",
    "Skate2_2008-11-05_PDB_MAP": "Skate 2 [2008-11-05] (PDB+MAP)",
    "xamd.dll_2015_04_22_PDB": "xamd.dll [2015-04-22] (PDB)",
    "xboxkrnld.exe_2015_04_22_PDB": "xboxkrnld.exe [2015-04-22] (PDB)",
}
BLANK = "—"

INTRO = """# Xbox 360 symbol sets: where to find which versions

A lookup table of {n} Xbox 360 development symbol sets (PDB, MAP and XEX sets from development builds), so you can find the sets that match your game's XDK build and middleware, and that hold the data you need. The first version of the table was made with `targets/xbox360_ppc/tools/pdb_inventory.py` on 2026-11-08 from 445 archives; the content columns were added on {date} with `pdb_content.py` and `pdb_public.py` in the same folder, for the sets whose files could be read again.

**What this is made from.** Only facts read from the contents of the files: XEX headers, PDB and MAP contents, library and object names, symbol counts. Nothing is taken from file names except the set's own name, build date and configuration words written in it. A field that could not be read says "—" or "n/r". **It contains no symbol files, no name lists and no game code.** Only set names, version numbers, library names and counts are published.

**What was re-read.** The content columns are filled for the {k} sets whose symbol files were read again for this edition; every other row reads `n/r` (not re-read): the archive it was read from was not at hand, and no value is guessed. The version columns for those {k} rows were refilled from the files where the first table had a blank. The same facts are published as `XBOX360_SYMBOL_SETS.csv` and `XBOX360_SYMBOL_SETS.json` (stable field names, one row per set).

## How to use it

1. Read your game's XDK build from its XEX (next section).
2. Find that build, or the nearest on either side, in the table below. It is sorted by XDK build.
3. Check the **Has** column: a set needs a `pdb` (or at least a `map`) to give names. Prefer a shipping build (`XeReleaseLTCG`, `Opt`, `Final`, `f_mt`) over a debug one. Where the content columns are filled, check **Fn names**, **Sizes**, **Code** and **Lib/obj** for what the set can actually give.
4. For middleware (Havok, Wwise and so on), use the index at the end and match the library versions.
5. The name in the **Set** column is the development build to look for. **Copies** counts how many archives held the identical set.

## Reading your own game's XDK build (no tool needed)

An XEX2 file starts with the magic `XEX2`. At offset 0x14 is a big-endian count of optional headers, then that many 8-byte entries (key, value) from offset 0x18. Two keys matter:

- `0x000200FF`, the static libraries the game was linked with. The value is an offset to a block: a 4-byte size, then 16-byte records: an 8-byte name (`XAPILIB`, `D3D9`, `XGRAPHC`, `XAUDIO`, `LIBCMT`, ...), then big-endian 16-bit major, minor and build, a byte of approval and a byte of QFE. The **XDK build** is the build number most records share (`2.0.NNNN.0`); a record whose build differs is listed under **Other XDK libs**.
- `0x00040006`, the execution information: media ID, version, base version and **title ID** (all 32-bit big-endian). The title ID and version in the table come from here.

A game without static-library records lists import libraries under `0x000103FF` instead (the same version words, in the import table). A compressed or encrypted XEX still has these headers in the clear. Compare your build with the table's XDK column.

## Example

You are porting game X, and its XEX says XDK 6995. In the table:
- **6995:** `Need for Speed Undercover [2008-08-19] (MAP+PDB)`, an `Opt` build with a PDB. Look for it first.
- **Nearest on either side with a PDB:** 6534 (Bourne, Highlander, Silent Hill Homecoming) and 7645 or 7776 (Gears of War 2, Skate 2, Godfather 2). Builds on either side link largely the same XDK code, so use these next.
- **Middleware:** if X uses the old Wwise (`akaudioengine`), the index points to Mass Effect 2 and The Saboteur.

## Columns

- **Set:** the build's name. **Copies:** how many archives held the identical set.
- **Has:** the files in it: `pdb`, `map`, `xex`, `pe`, `exe`, `dll`, with a count when there is more than one.
- **Title ID** and **Version:** from the XEX header.
- **XDK:** the XDK build, `NNNN` of `2.0.NNNN.0`. **From:** `xex` (the header's static-library records, exact) or `version strings` (no XEX; can list more than one build).
- **Compiler:** `Optimizing Compiler` versions in the PDB (14.0 is Visual Studio 2005, 15.0 is 2008, 16.0 is 2010), or the XEX's `C2` record without a PDB.
- **Config:** build configuration read from the file names.
- **Other XDK libs:** static libraries linked from a different XDK build than the rest.
- **Middleware:** detected from library names, with version strings where present. A family can be listed without a version. Havok is listed as one family: check the library names (`hkcollide`/`hkdynamics` old, `hkp*` newer) in the set itself.
- **Content columns** (filled for the {k} re-read sets, `n/r` elsewhere):
{legend}
  **Code** `yes xex` means an executable image of the same name is in the set; `(build id matches)` means the PDB's build id was compared with the image's and is equal (not possible for a compressed XEX). **Debug** lists which of `types`, `locals` and `lines` (modules with line numbers) the PDB holds. **Read** is the files the counts come from; a set can hold more files than were kept.

## Sets

"""


def parse(doc):
    """(rows, tail): the table rows as dicts keyed by OLD, and the text from '## Middleware index' on."""
    text = pathlib.Path(doc).read_text(encoding="utf-8")
    head, _, tail = text.partition("\n## Middleware index")
    rows = []
    for ln in head.splitlines():
        if not ln.startswith("| ") or ln.startswith("| Set |") or ln.startswith("|---"):
            continue
        cells = [c.strip() for c in ln[2:-2].split(" | ")]
        if len(cells) == len(OLD):
            rows.append(dict(zip(OLD, cells)))
    return rows, "\n## Middleware index" + tail


def compilers(inv):
    out = []
    for c in inv.get("compilers", {}):
        m = re.search(r"(\d+\.\d+\.\d+)(\.\d+)?$", c)
        v = (m.group(1) + (m.group(2) or "")) if m else None
        if v and v.endswith(".0") and v.count(".") == 3:
            v = v[:-2]
        if v and v not in out:
            out.append(v)
    return ", ".join(sorted(out, key=lambda s: [int(x) for x in s.split(".")]))


def apply(row, rec, log):
    """Content cells from one content.jsonl record; blank version cells filled from the same files."""
    c, inv = rec["content"], rec.get("inventory") or {}
    nm, sz = c["fn_names"], c["fn_sizes"]
    row["fn_names"] = yn(nm > 0, nm) + (f" ({c['fn_names_from']})" if nm else "")
    row["sizes"] = "yes" if sz else "no" + (" (addresses only)" if nm else "")
    row["code"] = ("yes " + c["code_present"] + (" (build id matches)" if c.get("code_guid_match") else "")) if c["code_present"] else "no"
    row["lib_obj"] = c["lib_obj"] if nm else "no"
    row["sdk_fns"] = yn(c["sdk_fns"] > 0, c["sdk_fns"])
    row["crt_fns"] = yn(c["crt_fns"] > 0, c["crt_fns"])
    row["data_syms"] = yn(c["data_syms"] > 0, c["data_syms"])
    dbg = [n for n, v in (("types", c["debug_types"]), ("locals", c["debug_locals"]), ("lines", c["debug_line_modules"])) if v]
    row["debug"] = ", ".join(dbg) if dbg else "no"
    row["content_read"] = " ".join(f"{k}×{v}" if v > 1 else k for k, v in sorted(c["files_read"].items())) or "none readable"
    bad = sum(1 for e in c.get("errors", []) if ".pdb:" in e)
    if bad:   # a PDB the reader cannot open says so; its debug columns are not known, not "no"
        row["content_read"] += f" (pdb×{bad} not readable)"
        if "pdb" not in c["files_read"]:
            row["sizes"] = row["debug"] = "not known"
    fills = {"title_id": inv.get("title_id"), "version": "/".join(inv.get("game_versions") or []) or None,
             "xdk": ", ".join(map(str, inv.get("sdk_builds") or [])) or None, "xdk_from": inv.get("sdk_from"),
             "compiler": compilers(inv) or None}
    for k, v in fills.items():
        if row[k] == BLANK and v:
            log.append(f"{row['set']}: {k} {BLANK} -> {v}")
            row[k] = v
    return row


def render(rows, tail, n_read, date):
    legend = "\n".join(f"  - {ln}" for ln in CONTENT_LEGEND[:3])
    out = [INTRO.format(n=len(rows), k=n_read, date=date, legend=legend).rstrip("\n"), ""]
    out.append("| " + " | ".join(HEAD + NEWHEAD) + " |")
    out.append("|" + "---|" * (len(HEAD) + len(NEWHEAD)))
    for r in rows:
        out.append("| " + " | ".join(r[k] for k in OLD + NEW) + " |")
    return "\n".join(out) + "\n" + tail


def build(a):
    doc = pathlib.Path(a.doc)
    rows, tail = parse(doc)
    recs = {}
    for ln in pathlib.Path(a.content).read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        recs[r["folder"]] = r
    by_name = {}
    for r in rows:
        by_name.setdefault(r["set"], r)
    for r in rows:
        r.update({k: NOT_READ for k in NEW})
    log, n = [], 0
    for folder, name in KEPT.items():
        if folder not in recs or name not in by_name:
            print(f"missing: {folder} -> {name}: scanned {folder in recs}, row {name in by_name}")
            continue
        apply(by_name[name], recs[folder], log)
        n += 1
    text = render(rows, tail, n, a.date)
    banned = re.findall(r"(?i)(laptop2?|magnus|LOCATIONS|RESEARCH_SYMBOLS|[CD]:\\|Too Human's)", text)
    if banned:
        print("not public-ready:", sorted(set(banned)))
        return 1
    if a.check:
        print(text[:300])
        return 0
    doc.write_text(text, encoding="utf-8")
    flat = []
    for r in rows:
        f = dict(r)
        f["content_read"] = None if r["content_read"] == NOT_READ else r["content_read"]
        flat.append(f)
    write_rows(doc.parent, "XBOX360_SYMBOL_SETS", flat)
    print(f"{len(rows)} rows, {n} re-read; {len(log)} blank cells filled")
    print("\n".join(log))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--doc", required=True)
    b.add_argument("--content", required=True)
    b.add_argument("--date", default="2026-11-14")
    b.add_argument("--check", action="store_true")
    return build(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

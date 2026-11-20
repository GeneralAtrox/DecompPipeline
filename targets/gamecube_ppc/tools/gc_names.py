"""Names for a GameCube game from another build's symbols: a set folder (docs/GAMECUBE_SYMBOL_SETS.json) holding an ELF
with a symbol table, or a CodeWarrior map with the .dol it describes, matched to the retail workspace's Ghidra catalog
by code with targets/xbox360_ppc/tools/pdb_names.py's matcher (pair(): exact, shape, string, call and vtable tiers, and
--verify's callee check with a deliberately wrong pairing), unchanged. tools/AutoOnboard/onboard_symbols.py calls it per set and
decides with the same accept() and keep() as the Xbox 360.

The set's side: the ELF with the most sized function symbols (STT_FUNC), its loaded sections laid out by address
(gc_binary.elf_image); else each map (gc_maps.functions) paired with the .dol in the folder whose text sections hold at
least 90% of its function addresses. CodeWarrior vtables (__vt__ objects) start with the type info and an offset, so
their slots start 8 bytes in.
A function's "lib" is set only when it is a library's: the Dolphin SDK, the C runtime (MSL), MetroTRK or known
middleware (gc_binary.MIDDLEWARE). From a map, by the library file it names (gc_maps.is_sdk_lib, MSL_RE, TRK_RE,
MW_LIB; any other library is the game's own); from an ELF, which names no library, by name (gc_maps.SDK_NAME_RE, the C
runtime and MetroTRK names below; an SDK-looking name with an underscore after its prefix is the game's) and, for a
local symbol, its file's name. Everything else is the other game's own
code, which onboard_symbols.keep() never uses unless MIN_OWN of it pairs exactly.
The retail side: project.json's binary (main.dol or the linked.dol) laid out by its DOL sections, and the functions of
state/catalog.json (addr, bytes).

Nearest existing: targets/xbox360_ppc/tools/pdb_names.py - the matcher itself, reading a PDB or MSVC map with an XEX
only; gc_maps.py and gc_binary.py read GameCube maps and ELFs for the symbol inventory (names and counts, no addresses
laid out against code). This file only reads a GameCube set into pdb_names.pair()'s form.

usage: py -3.12 gc_names.py --set <set folder> --ws <retail workspace> --out <folder> [--verify]
writes <out>/dev_functions.json, names.json, coverage.txt, (--verify) verify.json; exit 3 when the set has nothing it
can match (the reason on the last line)
"""
import argparse
import json
import pathlib
import re
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "xbox360_ppc" / "tools")]
import gc_binary  # noqa: E402
import gc_maps  # noqa: E402
import gcdisc  # noqa: E402
import pdb_names  # noqa: E402

CRT_RE = re.compile(
    r"^_*(save|rest)(gpr|fpr)\w*$|^_*(mem(cpy|set|move|cmp|chr)|str(len|cpy|ncpy|cat|ncat|cmp|ncmp|chr|rchr|str|tok|"
    r"icmp|nicmp|toul?|tod|spn|cspn|pbrk)|v?s?n?printf|v?sscanf|atoi|atol|atof|tolower|toupper|labs|abs|qsort|bsearch|"
    r"s?rand|malloc|free|calloc|realloc|exit|abort|atexit|f(open|close|read|write|seek|tell|flush|puts|gets|printf|getc|"
    r"putc)|puts|putchar|getchar|wcs\w+|mbs\w+|wc\w*tomb\w*|mbto\w*|__va_arg|__div2[iu]|__mod2[iu]|__shl2i|__shr2[iu]|"
    r"__cvt_\w+|__ptmf_\w+|__construct_\w+|__destroy_\w+|__copy|__init_\w+|__fini_\w+|__flush_cache|__start|__end_of_"
    r"\w+|__sys_free|__sys_alloc|__pformatter|__sformatter|__StringRead|__FileWrite|__kernel_\w+|__ieee754_\w+|"
    r"__fpclassify\w*|__signbit\w*|__unregister_fragment|__register_fragment|__dla__\w+|__dl__\w+|__nw__\w+|__nwa__\w+|"
    r"__throw\w*|__end__catch|__ExceptionHandler|__set_unexpected|__terminate|__unexpected|"
    r"(sqrt|sin|cos|tan|atan2?|asin|acos|exp|log|log10|pow|fmod|floor|ceil|fabs|frexp|ldexp|modf|scalbn|copysign)f?)$")
MAX_RUN = 48  # library_runs(): the longest run of plain-named functions between two of one library's functions
TRK_RE = re.compile(r"^_*(TRK|trk|Metro|InitMetroTRK|EnableMetroTRK|nub|gTRK|__TRK|udp_cc_|gdev_cc_|ddh_cc_|usb_)")


def library_by_name(name, file=""):
    """An ELF symbol's library ("sdk", "msl", "trk" or a middleware family) from its name or its file's, else ''."""
    stem = re.split(r"[\\/]", file)[-1].rsplit(".", 1)[0] if file else ""
    for s in (name, stem):
        if not s:
            continue
        n = s.lstrip("_")
        if gc_maps.SDK_NAME_RE.match(n) and "_" not in n[2:]:  # OSReport, __DVDFSInit, C_MTXOrtho; not AIUnit_Move
            return "sdk"
        if TRK_RE.match(s) or gc_maps.TRK_RE.search(stem):
            return "trk"
        if CRT_RE.match(s):
            return "msl"
    for fam, rx in gc_binary.MW_LIB:
        if stem and rx.search(stem):
            return fam
    return ""


def library_of_map(lib, obj):
    """A map line's library file when it is a library's (SDK, MSL, MetroTRK, middleware), else ''."""
    base = gc_maps.libbase(lib) if lib else ""
    if lib and (gc_maps.is_sdk_lib(lib) or gc_maps.MSL_RE.search(base) or gc_maps.TRK_RE.search(lib)):
        return lib
    if gc_maps.TRK_RE.search(obj or ""):
        return lib or obj
    for _fam, rx in gc_binary.MW_LIB:
        if base and rx.search(base):  # by library only: a game's own objects can look like a family's prefix
            return lib or obj
    return ""


def dol_image(dol):
    """(image, base, text ranges) of a .dol: its text and data sections laid out by address."""
    secs = gcdisc.dol_sections(dol)
    parts = secs["text"] + secs["data"]
    base, end = min(a for _i, _o, a, _s in parts), max(a + s for _i, _o, a, s in parts)
    img = bytearray(end - base)
    for _i, off, a, size in parts:
        img[a - base:a - base + size] = dol[off:off + size]
    return bytes(img), base, [(a, a + s) for _i, _o, a, s in secs["text"]]


class SetError(Exception):
    pass


def from_elf(path):
    img, base, syms, ex = gc_binary.elf_image(path)
    funcs, vts = {}, []
    for name, val, size, typ, _bind, file in syms:
        if typ == 2 and size >= 4 and any(lo <= val < hi for lo, hi in ex) and val not in funcs:
            funcs[val] = {"name": name, "size": size, "module": file, "lib": library_by_name(name, file)}
        elif typ == 1 and name.startswith("__vt__"):
            vts.append(val)
    library_runs(funcs)
    return img, base, funcs, vts


def library_runs(funcs):
    """An ELF names no library, and the name rule misses library functions with plain names (DCFlushRange,
    PPCMfhid2, __copy_longs_aligned, stateTimeout ...). The linker lays each library out as one block, so a run of
    unclassified functions whose neighbours on both sides (by address) are library functions is library code too,
    of the family before it, when both sides are the same family and the run is at most MAX_RUN long (two stray
    library-looking names in the game's own code then cannot make a stretch of it library). Measured on Skies of Arcadia Legends (2026-11-15): without this, Black & Bruised's ELF
    gave 77 exact pairs in "its own code", all but one of them SDK, MSL or MetroTRK, so another game's set passed
    MIN_OWN."""
    order = sorted(funcs)
    i = 0
    while i < len(order):
        if funcs[order[i]]["lib"]:
            i += 1
            continue
        j = i
        while j < len(order) and not funcs[order[j]]["lib"]:
            j += 1
        fam = funcs[order[i - 1]]["lib"] if i > 0 else ""
        if fam and j < len(order) and funcs[order[j]]["lib"] == fam and j - i <= MAX_RUN:
            for a in order[i:j]:
                funcs[a]["lib"] = fam
        i = j


def from_map(path, dols):
    fns, vts = gc_maps.functions(path)
    if not fns:
        raise SetError(f"{path.name}: no CodeWarrior section layout with function addresses")
    for dol in dols:
        b = dol.read_bytes()
        try:
            img, base, ex = dol_image(b)
        except (struct.error, ValueError):
            continue
        inside = sum(1 for f in fns if any(lo <= f["addr"] < hi for lo, hi in ex))
        if inside >= 0.9 * len(fns):
            funcs = {}
            for f in fns:
                if f["addr"] not in funcs:
                    funcs[f["addr"]] = {"name": f["name"], "size": f["size"], "module": f["obj"],
                                        "lib": library_of_map(f["lib"], f["obj"])}
            return img, base, funcs, vts, dol
    raise SetError(f"{path.name}: no .dol in the folder holds 90% of its {len(fns)} function addresses")


def dev_set(folder):
    """(description, image, base, functions, vtables) of a set folder: its best ELF, else its best map with a .dol."""
    folder = pathlib.Path(folder)
    files = [p for p in folder.rglob("*") if p.is_file()]
    elfs, why = [], []
    for p in (p for p in files if p.suffix.lower() == ".elf"):
        try:
            r = from_elf(p)
        except (ValueError, struct.error, IndexError) as e:
            why.append(f"{p.name}: {e}")
            continue
        elfs.append((len(r[2]), p, r))
    if elfs and max(elfs, key=lambda e: e[0])[0]:
        n, p, (img, base, funcs, vts) = max(elfs, key=lambda e: e[0])
        return f"ELF {p.name} ({n} sized functions)", img, base, funcs, vts
    dols = [p for p in files if p.suffix.lower() == ".dol"]
    best = None
    for p in (p for p in files if p.suffix.lower() == ".map"):
        try:
            img, base, funcs, vts, dol = from_map(p, dols)
        except SetError as e:
            why.append(str(e))
            continue
        if best is None or len(funcs) > len(best[3]):
            best = (f"map {p.name} with {dol.name} ({len(funcs)} functions)", img, base, funcs, vts)
    if best:
        return best
    raise SetError("nothing to match by bytes: " + ("; ".join(why[:4]) or "no .elf, and no .map with its .dol"))


def retail(ws):
    """(image, base, {address: size}) of the workspace's binary and catalog."""
    ws = pathlib.Path(ws)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    img, base, _ex = dol_image((ws / proj["binary"]).read_bytes())
    cat = json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))
    return img, base, {int(f["addr"], 16): f["bytes"] for f in cat["functions"] if f.get("bytes", 0) >= 4}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", required=True, help="a set folder: an ELF with symbols, or a CodeWarrior map and its .dol")
    ap.add_argument("--ws", required=True, help="the retail workspace: project.json, its binary, state/catalog.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--verify", action="store_true", help="also write verify.json (pdb_names.verify)")
    a = ap.parse_args(argv)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        what, dimg, dbase, funcs, vts = dev_set(a.set)
    except SetError as e:
        print(f"gc_names: {a.set}: {e}")
        return 3
    (out / "dev_functions.json").write_text(json.dumps({f"{k:08x}": v for k, v in sorted(funcs.items())}, indent=0),
                                            encoding="utf-8")
    libs = sum(1 for f in funcs.values() if f["lib"])
    print(f"set: {what}; {libs} of {len(funcs)} in libraries; {len(vts)} vtables", flush=True)
    rimg, rbase, rfun = retail(a.ws)
    lines = pdb_names.pair(dimg, dbase, funcs, vts, rimg, rbase, rfun, out, a.verify,
                           about=[f"set: {what}; {libs} of its functions in libraries (SDK, MSL, MetroTRK, middleware)"],
                           vt_skip=8)
    print("\n".join(lines[:30]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

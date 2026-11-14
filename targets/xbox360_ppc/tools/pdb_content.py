"""What an Xbox 360 symbol set can give, read from the contents of its PDB and MAP files: function names and sizes, whether the
executable the symbols describe is in the set, which library and object each function came from, how many functions belong
to the Xbox 360 SDK's libraries and to the C/C++ runtime, data symbols, and which debug information the PDB holds (types,
locals, line numbers). The same questions the GameCube table answers (tools/symset_common.py CONTENT_LEGEND).

A set is a folder. content(folder) returns one record; scan runs the folders of a symbols directory, two at a time, and writes
<out>/content.jsonl (one line per folder: the content record plus the set's version facts from pdb_inventory.read_set, so a
blank XDK build or compiler can be filled from the files). Nothing but counts and library names leaves the record.

Nearest existing: pdb_inventory.py - reads versions, libraries and compilers (read_set is reused here for them) and counts
no symbols; pdb_parse.py reads one PDB's functions to name another build's and keeps no counts. Its Pdb and MapSymbols
readers are reused: this adds what they leave out (data symbols, locals, lines, types) and the per-set roll-up.

usage: py -3.12 -I pdb_content.py scan <symbols folder> --out <dir> [--only REGEX] [--slots 2]
"""
import argparse
import json
import multiprocessing
import pathlib
import re
import struct
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "tools"))
import pdb_inventory  # noqa: E402
from pdb_parse import PUBS, MapSymbols, Pdb, cstr, pstr  # noqa: E402
from symset_common import below_normal, wait_gate  # noqa: E402

CRT_RE = re.compile(r"^(libcmt|libcmtd|cmtd|libcpmt|libcpmtd|cpmtd|libc|libcd|msvcrt|msvcprt|oldnames|vcomp)", re.I)
SDK_RE = re.compile(r"^(xrtl|xgraph|xaud|xbox|xapi|xam|xnet|xhv|xmp|xmedia|xact|xmcore|xmahal|x3d|xparty|xmic|xonline|xbdm|d3d|xuir|"
                    r"xavatar|xhttp|xauth|nuiapi|xperf|xrnm|xjson|xsim|xbc|xtl|xgi|shvoui)", re.I)
CODE_EXT = (".xex", ".exe", ".pe", ".dll")
DATA = {0x110C: cstr, 0x110D: cstr, 0x1007: pstr, 0x1008: pstr}          # S_LDATA32, S_GDATA32 and the _ST forms
LOCALS = {0x113E, 0x110B, 0x1111, 0x1106, 0x1112, 0x1113}  # S_LOCAL, S_BPREL32, S_REGREL32, S_REGISTER, S_MANYREG


def classify(lib):
    b = re.sub(r"\.lib$", "", pathlib.PureWindowsPath(lib).name, flags=re.I) if lib else ""
    if not b:
        return None
    if CRT_RE.match(b):
        return "crt"
    return "sdk" if SDK_RE.match(b) else None


def read_one_pdb(path):
    """Counts from one PDB: function names, sized ones, attribution, SDK and runtime functions, data symbols, locals, lines, types."""
    pdb = Pdb(path)
    r = {"names": set(), "sized": set(), "sdk": set(), "crt": set(), "lib": False, "obj": False, "data": set(), "locals": 0,
         "line_modules": 0, "types": 0}
    for f in pdb.functions(0).values():
        nm = f["name"]
        r["names"].add(nm)
        if not f.get("public_only") and f["size"]:
            r["sized"].add(nm)
        r["lib"] |= bool(f["lib"])
        r["obj"] |= bool(f["module"])
        c = classify(f["lib"])
        if c:
            r[c].add(nm)
    for _name, _obj, si, symsz, c11, c13 in pdb.modules:
        if c11 or c13:
            r["line_modules"] += 1
        if si == 0xFFFF or not symsz:
            continue
        s = pdb.msf.stream(si)
        for kind, o in pdb.records(s, 4, symsz):
            if kind in LOCALS:
                r["locals"] += 1
            elif kind in DATA:
                try:
                    r["data"].add(DATA[kind](s, o + 14)[0])
                except (struct.error, ValueError, IndexError):
                    pass
    sym = pdb.msf.stream(pdb.symrec_si)
    for kind, o in pdb.records(sym, 0, len(sym)):
        if kind in PUBS:
            _flags, _off, seg = struct.unpack_from("<IIH", sym, o + 4)
            if not pdb.code_section(seg):
                r["data"].add(PUBS[kind](sym, o + 14)[0])
    t = pdb.msf.stream(2)
    if len(t) >= 20:
        _v, _h, begin, end, _n = struct.unpack_from("<5I", t, 0)
        r["types"] = max(0, end - begin)
    info = pdb.msf.stream(1)
    r["guid"] = info[12:28].hex() if len(info) >= 28 else None
    return r


def read_one_map(path):
    r = {"names": set(), "sdk": set(), "crt": set(), "lib": False, "obj": False, "data": set()}
    for _a, nm, isf, obj in MapSymbols(path).entries:
        lib, _, mod = obj.partition(":")
        has_lib = bool(mod) and not lib.startswith("CIL library")
        if not isf:
            r["data"].add(nm)
            continue
        r["names"].add(nm)
        r["lib"] |= has_lib
        r["obj"] |= bool(mod)
        c = classify(lib + ".lib") if has_lib else None
        if c:
            r[c].add(nm)
    return r


def exe_guid_match(path, guid):
    """True/False when the image carries a CodeView record to compare with the PDB's GUID; None when it does not (compressed XEX)."""
    if path.suffix.lower() == ".xex" or not guid or path.stat().st_size > 80_000_000:
        return None
    d = path.read_bytes()
    i = d.find(b"RSDS")
    return None if i < 0 else d[i + 4:i + 20].hex() == guid


def content(folder):
    folder = pathlib.Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    codes = {}
    for p in files:
        if p.suffix.lower() in CODE_EXT:
            codes.setdefault(p.stem.lower(), []).append(p)
    out = {"files_read": {}, "errors": []}
    agg = {k: set() for k in ("names", "sized", "sdk", "crt", "data")}
    lib = obj = False
    locals_n = lines = types = 0
    code_for, guid_ok, from_ = set(), [], set()
    for p in files:
        ext = p.suffix.lower()
        if ext not in (".pdb", ".map"):
            continue
        try:
            r = read_one_pdb(p) if ext == ".pdb" else read_one_map(p)
        except Exception as e:  # noqa: BLE001 - a damaged or old-format file must not lose the set
            out["errors"].append(f"{p.name}: {type(e).__name__}: {e}"[:160])
            continue
        out["files_read"][ext[1:]] = out["files_read"].get(ext[1:], 0) + 1
        if r["names"]:
            from_.add(ext[1:])
        for k in agg:
            agg[k] |= r.get(k, set())
        lib |= r["lib"]
        obj |= r["obj"]
        if ext == ".pdb":
            locals_n += r["locals"]
            lines += r["line_modules"]
            types = max(types, r["types"])
        for c in codes.get(p.stem.lower(), []):
            code_for.add(c.suffix.lower()[1:])
            if ext == ".pdb":
                g = exe_guid_match(c, r["guid"])
                if g is not None:
                    guid_ok.append(g)
    out.update(
        fn_names=len(agg["names"]), fn_names_from="+".join(sorted(from_)) or None, fn_sizes=len(agg["sized"]),
        code_present="/".join(sorted(code_for)) or None, code_guid_match=(all(guid_ok) if guid_ok else None),
        lib_obj="lib+obj" if lib else ("obj" if obj else "no"), sdk_fns=len(agg["sdk"]), crt_fns=len(agg["crt"]),
        data_syms=len(agg["data"]), debug_types=types, debug_locals=locals_n, debug_line_modules=lines)
    return out


def work(args):
    root, name = args
    t0 = time.time()
    folder = pathlib.Path(root) / name
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    rec = {"folder": name, "size": size, "content": content(folder)}
    try:   # the version facts, to fill a blank XDK build or compiler
        inv = pdb_inventory.read_set(name, folder, size)
        comp = {}
        for f in inv.get("files", []):
            for c, n in (f.get("compilers") or {}).items():
                comp[c] = comp.get(c, 0) + n
        rec["inventory"] = {k: inv.get(k) for k in ("sdk_builds", "sdk_from", "title_id", "game_versions", "versions", "families", "ue3")}
        rec["inventory"]["compilers"] = dict(sorted(comp.items(), key=lambda kv: -kv[1])[:12])
        rec["inventory"]["files"] = [{k: f.get(k) for k in ("kind", "file", "link_time", "linker", "sdk_build", "error")}
                                     for f in inv.get("files", [])]
    except Exception as e:  # noqa: BLE001
        rec["inventory_error"] = f"{type(e).__name__}: {e}"[:200]
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def scan(a):
    below_normal()
    if not wait_gate():
        return 4
    src, out = pathlib.Path(a.src), pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    inv = out / "content.jsonl"
    done = {json.loads(ln)["folder"] for ln in inv.read_text(encoding="utf-8").splitlines()} if inv.exists() else set()
    names = [p.name for p in sorted(src.iterdir()) if p.is_dir() and p.name not in done and (not a.only or re.search(a.only, p.name, re.I))]
    t0 = time.time()
    with multiprocessing.Pool(a.slots, initializer=below_normal) as pool, open(inv, "a", encoding="utf-8") as fh:
        for rec in pool.imap_unordered(work, [(str(src), n) for n in names]):
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{rec['folder']}: {rec['seconds']} s, {rec['content'].get('fn_names')} fns", flush=True)
    print(f"{len(names)} sets; {round(time.time() - t0)} s; {inv}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("src")
    s.add_argument("--out", required=True)
    s.add_argument("--only")
    s.add_argument("--slots", type=int, default=2)
    return scan(ap.parse_args(argv))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

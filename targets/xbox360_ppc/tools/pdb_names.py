"""Names for an Xbox 360 game from a development build's PDB. Reads the PDB (MSF 7.0: the DBI's modules and their procedure symbols, the
publics, the section headers, and the type stream for prototypes and structures), loads that build's XEX (xex.py), and matches its functions
to the retail catalogue by their code, so the retail functions get the development build's names, source modules and prototypes.

Matching, in order, each pair one to one:
  exact  the same instructions once addresses are masked (branch targets, and the low halves of lis-built addresses), unique on both sides
  shape  the same opcodes and extended opcodes (registers and immediates masked), unique on both sides
  string the same set of string literals, unique on both sides; then a string only one function names on each side, every such vote agreeing
  call   the callees of matched pairs aligned by sequence (difflib), a pair every alignment agrees on, with sizes within a third
  vtable a development virtual-function table (??_7Class@@6B@) and a retail run of code pointers two or more matched slots agree on:
         every slot; call and vtable repeat until nothing new
A name is only as good as its tier: exact and shape are the code itself, call is its place in the call graph.

Another game's build can name the SDK libraries (D3D, XAudio, XAPI, the C runtime) built into this one: --map reads a linker .map when that
build's PDB is damaged, --image takes its image already laid out by address (xexmagic -d) when xex.py can't decompress it, --libs keeps only
those libraries, and --prior counts what it adds to an earlier names.json (extra_names.json) and where the two disagree (disagree.json).

The PDB, type stream and .map readers are in pdb_parse.py.

Nearest existing: xex.py - loads the XEX the PDB belongs to but reads no symbols, and nothing in the pipeline reads a PDB or matches two
builds of one game.

usage: py -3.12 pdb_names.py --pdb <development build>.pdb --xex <development build>.xex --ws <retail workspace> --out <folder>
       py -3.12 pdb_names.py --map <other build>.map --image <xexmagic -d output> --libs "^(d3d|xaud|...)" --prior names.json ...
writes <out>/dev_functions.json (the development build's functions), dev_types.json (its structures), names.json (retail address ->
name, tier, development address, module, library, prototype) and coverage.txt
"""
import argparse
import collections
import hashlib
import json
import pathlib
import re
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xex  # noqa: E402
from pdb_parse import (AGG, MSF_MAGIC, PRIM, PROCS, PUBS, MapSymbols, Msf, Pdb, Types,  # noqa: E402,F401  (pdb_names.X
                       cstr, numeric, pstr)                                           # keeps working)


# ------------------------------------------------------------------------------------------------------------------ matching
D_FORM = set(range(32, 56)) | {14, 58, 62}


def c_string(img, base, at):
    """The C string at a guest address, when it is one: 4 to 200 printable characters and a 0"""
    o = at - base
    if not 0 <= o < len(img):
        return None
    e = img.find(b"\0", o, o + 201)
    s = img[o:e] if e >= 0 else b""
    return s.decode("latin-1") if len(s) >= 4 and all(32 <= c < 127 or c in (9, 10, 13) for c in s) else None


def code_keys(img, base, a, size):
    """(exact key, shape key, callee addresses, the strings it names) of the function at a: see the module's docstring"""
    lo = a - base
    words = struct.unpack_from(f">{size // 4}I", img, lo) if 0 <= lo and lo + size <= len(img) else ()
    exact, shape, calls, lis, strings = [], [], [], {}, []
    for i, w in enumerate(words):
        op, rd, ra = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        if op == 18:
            if w & 1:
                t = (w & 0x03FFFFFC) - (0x04000000 if w & 0x02000000 else 0)
                calls.append(t if w & 2 else a + 4 * i + t)
            w &= 0xFC000003
        elif op == 16:
            w &= 0xFFFF0003
        elif op == 15 and ra == 0:
            lis[rd] = (w & 0xFFFF) << 16
            w &= 0xFFFF0000
        elif op in D_FORM and ra in lis:
            if op == 14:
                s = c_string(img, base, (lis[ra] + ((w & 0xFFFF) ^ 0x8000) - 0x8000) & 0xFFFFFFFF)
                if s:
                    strings.append(s)
            w &= 0xFFFF0000
            if op == 14 and rd == ra:
                del lis[ra]
        exact.append(w)
        shape.append((op << 10) | ((w >> 1) & 0x3FF) if op in (4, 19, 30, 31, 59, 63) else op << 10)
    h = lambda xs: hashlib.blake2b(struct.pack(f">{len(xs)}I", *xs), digest_size=12).digest()
    return h(exact), h(shape), calls, tuple(sorted(set(strings)))


def match(ret, dev, dev_vt=None, r_ptr=None, r_word=None):
    """ret, dev: {address: (exact, shape, calls, strings, size)}; dev_vt: {vtable: [slot functions]}; r_ptr: {retail function: [addresses of
    data words that hold it]}; r_word(address): the retail word there. Returns {retail address: (development address, tier)}"""
    dev_vt, r_ptr = dev_vt or {}, r_ptr or {}
    d_count = collections.Counter(d for slots in dev_vt.values() for d in slots)
    import difflib
    pairs, used = {}, set()

    def unique(tier, idx, ok=lambda key: True):
        r_by, d_by = collections.defaultdict(list), collections.defaultdict(list)
        for a, k in ret.items():
            if a not in pairs:
                r_by[k[idx]].append(a)
        for a, k in dev.items():
            if a not in used:
                d_by[k[idx]].append(a)
        for key, rs in r_by.items():
            ds = d_by.get(key)
            if len(rs) == 1 and ds and len(ds) == 1 and ret[rs[0]][4] >= 8 and ok(key):
                pairs[rs[0]] = (ds[0], tier)
                used.add(ds[0])

    def rare_strings():
        """A string only one unmatched function names on each side votes for that pair; a pair every such vote of both agrees on"""
        r_by, d_by = collections.defaultdict(list), collections.defaultdict(list)
        for a, k in ret.items():
            if a not in pairs:
                for s in k[3]:
                    r_by[s].append(a)
        for a, k in dev.items():
            if a not in used:
                for s in k[3]:
                    d_by[s].append(a)
        votes_r, votes_d = collections.defaultdict(set), collections.defaultdict(set)
        for s, rs in r_by.items():
            ds = d_by.get(s)
            if len(rs) == 1 and ds and len(ds) == 1 and len(s) >= 6:
                votes_r[rs[0]].add(ds[0])
                votes_d[ds[0]].add(rs[0])
        for r, ds in votes_r.items():
            if len(ds) == 1 and votes_d[next(iter(ds))] == {r}:
                pairs[r] = (next(iter(ds)), "string")
                used.add(next(iter(ds)))

    def calls():
        """The callees of matched pairs aligned (difflib, matched callees as their partners): a pair every alignment of both agrees on"""
        votes = collections.defaultdict(set)
        for r, (d, _tier) in list(pairs.items()):
            rc = [pairs[x][0] if x in pairs else ("r", x) for x in ret[r][2]]
            dc = [y if y in used else ("d", y) for y in dev[d][2]]
            sm = difflib.SequenceMatcher(None, rc, dc, autojunk=False)
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op == "replace" and i2 - i1 == j2 - j1:
                    for x, y in zip(rc[i1:i2], dc[j1:j2]):
                        if isinstance(x, tuple) and isinstance(y, tuple) and x[1] in ret and y[1] in dev:
                            votes[x[1]].add(y[1])
        back = collections.defaultdict(set)
        for x, ys in votes.items():
            if len(ys) == 1:
                back[next(iter(ys))].add(x)
        new = 0
        for y, xs in back.items():
            if len(xs) != 1:
                continue
            x = next(iter(xs))
            sr, sd = ret[x][4], dev[y][4]
            if ret[x][0] == dev[y][0] or ret[x][1] == dev[y][1] or (sd and 0.75 <= sr / sd <= 1.33):
                pairs[x] = (y, "call")
                used.add(y)
                new += 1
        return new

    def tables():
        """A development vtable and a retail run of code pointers its matched slots agree on (two or more, three quarters of them): every
        slot of the pair paired"""
        votes, rev = collections.Counter(), {y: x for x, (y, _t) in pairs.items()}
        for vt, slots in dev_vt.items():
            for i, d in enumerate(slots):
                if d in rev and d_count[d] <= 32 and len(r_ptr.get(rev[d], ())) <= 32:   # shared stubs (purecall, empty destructors) don't vote
                    for at in r_ptr.get(rev[d], ()):
                        votes[(vt, at - 4 * i)] += 1
        best_v, best_r = {}, {}
        for (vt, start), n in votes.items():
            if n >= 2:
                if n > best_v.get(vt, (0, 0))[0]:
                    best_v[vt] = (n, start)
                if n > best_r.get(start, (0, 0))[0]:
                    best_r[start] = (n, vt)
        new = 0
        for vt, (n, start) in best_v.items():
            if best_r.get(start, (0, 0))[1] != vt:
                continue
            slots = dev_vt[vt]
            row = [r_word(start + 4 * k) for k in range(len(slots))]
            agree = sum(1 for r, d in zip(row, slots) if r in pairs and pairs[r][0] == d)
            known = sum(1 for r, d in zip(row, slots) if r in pairs or d in used)
            if agree < 2 or agree * 4 < known * 3:
                continue
            for r, d in zip(row, slots):
                if r in ret and d in dev and r not in pairs and d not in used:
                    pairs[r] = (d, "vtable")
                    used.add(d)
                    new += 1
        return new

    unique("exact", 0)
    unique("shape", 1)
    unique("string", 3, lambda key: sum(map(len, key)) >= 8)
    rare_strings()
    while calls() + tables():
        pass
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdb", help="the development build's PDB")
    ap.add_argument("--map", help="instead of --pdb: the linker's .map (publics by value with lib:object), for a build whose PDB is damaged")
    ap.add_argument("--xex", help="the PDB's own XEX (xex.py reads it)")
    ap.add_argument("--image", help="instead of --xex: the PDB build's image laid out by address (xexmagic -d output, for LZX XEXes xex.py can't read)")
    ap.add_argument("--base", default="82000000", help="--image's load address (hex)")
    ap.add_argument("--libs", help="keep only development functions whose library file name matches this regular expression (SDK libraries)")
    ap.add_argument("--prior", help="an earlier names.json: count the retail functions this run names that it doesn't, and where the two disagree")
    ap.add_argument("--ws", required=True, help="the retail workspace: image.bin, image.json, catalog_inputs.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.image:
        dimg, dbase = pathlib.Path(a.image).read_bytes(), int(a.base, 16)
    else:
        x = xex.Xex(pathlib.Path(a.xex).read_bytes())
        dimg, dbase = x.image(), x.load_address
    pdb = MapSymbols(a.map) if a.map else Pdb(a.pdb)
    types = None if a.map else Types(pdb.msf)
    funcs = pdb.functions(dbase)
    if a.libs:
        keep = re.compile(a.libs, re.I)
        funcs = {d: f for d, f in funcs.items() if keep.search(pathlib.PureWindowsPath(f["lib"]).name)}
    for f in funcs.values():
        ti = f.pop("type")
        f["proto"] = types.name(ti) if ti and types else ""
    (out / "dev_functions.json").write_text(json.dumps({f"{k:08x}": v for k, v in sorted(funcs.items())}, indent=0), encoding="utf-8")
    structs = types.structures() if types else {}
    (out / "dev_types.json").write_text(json.dumps(structs, indent=0), encoding="utf-8")
    print(f"development build: {len(funcs)} functions ({sum(1 for f in funcs.values() if f.get('public_only'))} from publics only), "
          f"{len(pdb.modules)} modules, {len(structs)} structures", flush=True)

    ws = pathlib.Path(a.ws)
    proj = json.loads((ws / "project.json").read_text())
    rimg = (ws / proj["binary"]).read_bytes()
    rbase = json.loads((ws / "image.json").read_text()).get("load_address", 0x82000000)
    cat = json.loads((ws / "catalog_inputs.json").read_text())
    rfun = {int(f["addr"], 16): f["size"] for f in cat["functions"] if f.get("kind") == "function"}
    ret = {r: (*code_keys(rimg, rbase, r, s), s) for r, s in rfun.items() if s >= 4}
    dev = {d: (*code_keys(dimg, dbase, d, f["size"] & ~3), f["size"]) for d, f in funcs.items() if f["size"] >= 4}
    dev_vt = {}
    for vt in pdb.vtables(dbase):
        slots, at = [], vt
        while 0 <= at - dbase + 4 <= len(dimg) and struct.unpack_from(">I", dimg, at - dbase)[0] in dev:
            slots.append(struct.unpack_from(">I", dimg, at - dbase)[0])
            at += 4
        if len(slots) >= 2:
            dev_vt[vt] = slots
    r_ptr = collections.defaultdict(list)
    code_lo, code_hi = min(rfun), max(rfun)
    words = [w for (w,) in struct.iter_unpack(">I", rimg[:len(rimg) & ~3])]
    is_fn = [code_lo <= w <= code_hi and w in ret for w in words]
    for i, w in enumerate(words):
        # only words in a run of two or more code pointers: a table, not a .pdata entry (address, then packed lengths)
        if is_fn[i] and ((i and is_fn[i - 1]) or (i + 1 < len(words) and is_fn[i + 1])):
            r_ptr[w].append(rbase + 4 * i)
    r_word = lambda at: struct.unpack_from(">I", rimg, at - rbase)[0] if 0 <= at - rbase <= len(rimg) - 4 else 0
    print(f"{len(dev_vt)} development vtables; {len(r_ptr)} retail functions some data word holds", flush=True)
    pairs = match(ret, dev, dev_vt, r_ptr, r_word)
    names = {}
    for r, (d, tier) in sorted(pairs.items()):
        f = funcs[d]
        names[f"{r:08x}"] = {"name": f["name"], "tier": tier, "dev": f"{d:08x}", "module": f["module"], "lib": f["lib"], "proto": f["proto"]}
    (out / "names.json").write_text(json.dumps(names, indent=0), encoding="utf-8")

    tiers = collections.Counter(v["tier"] for v in names.values())
    libs = collections.Counter(pathlib.PureWindowsPath(v["lib"]).name.lower() or "(the game's own modules)" for v in names.values())
    dlibs = collections.Counter(pathlib.PureWindowsPath(f["lib"]).name.lower() or "(the game's own modules)" for f in funcs.values())
    lines = [f"retail catalogue: {len(rfun)} functions; named from the development build: {len(names)} ({100 * len(names) / max(1, len(rfun)):.1f}%)",
             "by tier: " + ", ".join(f"{k} {v}" for k, v in tiers.most_common()),
             f"development build: {len(funcs)} functions, {len(structs)} structures", "", "named, by library (development build's count beside):"]
    lines += [f"  {n:7d}  of {dlibs[k]:7d}  {k}" for k, n in libs.most_common()]
    if a.prior:
        prior = json.loads(pathlib.Path(a.prior).read_text(encoding="utf-8"))
        extra = {r: v for r, v in names.items() if r not in prior}
        differ = {r: (prior[r]["name"], v["name"]) for r, v in names.items() if r in prior and prior[r]["name"] != v["name"]}
        (out / "extra_names.json").write_text(json.dumps(extra, indent=0), encoding="utf-8")
        (out / "disagree.json").write_text(json.dumps(differ, indent=0), encoding="utf-8")
        xl = collections.Counter(pathlib.PureWindowsPath(v["lib"]).name.lower() for v in extra.values())
        lines += ["", f"against {a.prior}: {len(extra)} retail functions named here and not there, {len(differ)} named differently",
                  "extra, by library: " + ", ".join(f"{k} {v}" for k, v in xl.most_common())]
    (out / "coverage.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:40] + (lines[-3:] if a.prior and len(lines) > 40 else [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

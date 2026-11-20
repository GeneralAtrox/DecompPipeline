"""What an original Xbox symbol set holds, read from the contents of its XBE, PE, MAP and PDB files: the title and the XDK library
versions in the XBE header (XAPILIB, D3D8, DSOUND, XNET, ...), the link time, the compiler, the libraries and objects the
symbols name, function, data and debug counts, and the middleware strings in the image. The same questions the GameCube and
Xbox 360 tables answer. Used by `symbol_inventory.py --console xbox` (scan / report / one).

The original Xbox's PDBs are the old "program database 2.00" (VC6-era, 16-bit page numbers, CodeView 4 records), which the Xbox 360
readers (MSF 7.00 only) cannot open; Msf2 and read_pdb2 read them. Only counts, set names, version words, library and object
names leave a record: no symbol names, no addresses of code, no keys (an XBE certificate holds signing keys; they are not read).

Nearest existing: targets/xbox360_ppc/tools/pdb_readers.py (read_pdb, read_map) and pdb_parse.py (Msf, Pdb) - same job for
Xbox 360 XEX/PDB, but MSF 7.00 only and PowerPC; kept as they are. The XBE header and the PDB 2.00 container are new here.
"""
import collections
import datetime
import pathlib
import re
import struct

MAGIC2 = b"Microsoft C/C++ program database 2.00\r\n\x1aJG\0\0"
CRT_RE = re.compile(r"^(libc|libcmt|libcd|libcmtd|msvcrt|libcp|libcpmt|oldnames|xapilib|xcrt)", re.I)
SDK_RE = re.compile(r"^(xapi|xboxkrnl|d3d|dsound|xnet|xonline|xgraphics|xmv|wmadec|xvoice|dmusic|dsstdfx|xmusic|xbdm|xact|xdbg)", re.I)
MIDDLEWARE = {   # family -> regex over the image's bytes (a version is kept when the match has one)
    "bink": rb"Bink[^\x00\r\n]{0,16}?(?<![\d.])\d\.\d+[a-z]?(?![\d.])|Smacker|RAD Game Tools",
    "miles": rb"Miles Sound System[^\x00\r\n]{0,24}?\d\.\d+[a-z]?",
    "renderware": rb"RenderWare[^\x00\r\n]{0,12}?\d\.\d+|Criterion Software",
    "havok": rb"Havok[-_ ]?v?\d\.\d+(?:\.\d+)?|hkBase|hkdynamics",
    "karma": rb"MathEngine|Karma[^\x00\r\n]{0,10}?\d\.\d+",
    "gamebryo": rb"Gamebryo[^\x00\r\n]{0,12}?\d\.\d+|NetImmerse[^\x00\r\n]{0,12}?\d\.\d+",
    "unreal": rb"Unreal Engine[^\x00\r\n]{0,10}?\d|UObject|UnrealEd",
    "fmod": rb"FMOD[^\x00\r\n]{0,16}?\d\.\d+",
    "lua": rb"Lua \d\.\d(?:\.\d)?",
    "zlib": rb"(?:deflate|inflate) \d\.\d\.\d(?:\.\d)?",
    "vorbis": rb"Xiph\.Org libVorbis[^\x00\r\n]{0,20}|vorbis[ _]?\d\.\d",
    "speedtree": rb"SpeedTree[^\x00\r\n]{0,12}?\d\.\d+",
    "indeo": rb"Intel Indeo|Indeo",
    "granny": rb"Granny[^\x00\r\n]{0,12}?\d\.\d+\.\d+",
    "openal": rb"OpenAL[^\x00\r\n]{0,12}",
}
MW_RE = re.compile(b"|".join(b"(?P<%s>%s)" % (k.encode(), v) for k, v in MIDDLEWARE.items()), re.I)


# ------------------------------------------------------------------------------------------------------------------ XBE
def _utc(ts):
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d") if ts > 0 else None
    except (OverflowError, OSError, ValueError):
        return None


def read_xbe(path):
    """Title, version words and library records of an XBE. The certificate's signing and LAN keys are not read."""
    d = pathlib.Path(path).read_bytes()
    out = {"file": pathlib.Path(path).name, "size": len(d)}
    if d[:4] != b"XBEH":
        out["error"] = "not an XBE"
        return out
    base, _hs, _is, _ihs, ts, cert, nsec, secs = struct.unpack_from("<8I", d, 0x104)
    out["link_date"] = _utc(ts)
    nlib, libs = struct.unpack_from("<2I", d, 0x160)
    dbg = struct.unpack_from("<I", d, 0x14C)[0]
    if dbg > base and dbg - base < len(d):
        e = d.index(b"\0", dbg - base)
        out["debug_name"] = pathlib.PureWindowsPath(d[dbg - base:e].decode("latin-1")).name   # the file name only, never the path
    c = cert - base
    if 0 < c < len(d) - 0xB0:
        tid, = struct.unpack_from("<I", d, c + 8)
        out["title_id"] = f"{tid:08X}"
        out["title"] = d[c + 12:c + 92].decode("utf-16-le", "replace").split("\0")[0]
        media, region, _rat, _disc, ver = struct.unpack_from("<5I", d, c + 0x9C)
        out["version"], out["region"], out["media"] = f"{ver:#x}", f"{region:#x}", f"{media:#x}"
    lib = []
    for i in range(min(nlib, 64)):
        o = libs - base + 16 * i
        if not 0 < o < len(d) - 16:
            break
        name = d[o:o + 8].rstrip(b"\0").decode("latin-1")
        ma, mi, bu, fl = struct.unpack_from("<4H", d, o + 8)
        lib.append({"name": name, "version": f"{ma}.{mi}.{bu}", "build": bu, "qfe": fl & 0x1FFF, "debug": bool(fl & 0x8000)})
    out["libs"] = lib
    sections = []
    for i in range(min(nsec, 64)):
        o = secs - base + 56 * i
        if not 0 < o < len(d) - 56:
            break
        n_addr = struct.unpack_from("<I", d, o + 20)[0] - base
        if 0 < n_addr < len(d) - 16:
            sections.append(d[n_addr:d.index(b"\0", n_addr)].decode("latin-1")[:12])
    out["sections"] = sections
    return out


def middleware(data):
    """{family: sorted version strings seen} for the image bytes (families found without a version map to [])."""
    found = collections.defaultdict(collections.Counter)
    for m in MW_RE.finditer(data):
        found[m.lastgroup][m.group(0).decode("latin-1")[:48]] += 1
    return {k: [s for s, _ in v.most_common(3)] for k, v in found.items()}


def rich_builds(data):
    """The Microsoft tool builds a PE's Rich header records: {(product id, build): objects}. Empty when it has none."""
    r = data.find(b"Rich", 0x40, 0x600)
    if r < 0:
        return {}
    key = struct.unpack_from("<I", data, r + 4)[0]
    vals, o = [], r - 4
    while o >= 0x80 and len(vals) < 600:
        v = struct.unpack_from("<I", data, o)[0] ^ key
        vals.append(v)
        if v == 0x536E6144:       # "DanS"
            break
        o -= 4
    vals.reverse()
    out = collections.Counter()
    for i in range(4, len(vals) - 1, 2):
        out[(vals[i] >> 16, vals[i] & 0xFFFF)] += vals[i + 1]
    return dict(out)


def read_pe(path):
    d = pathlib.Path(path).read_bytes()
    out = {"file": pathlib.Path(path).name, "size": len(d)}
    if d[:2] == b"MZ" and len(d) > 0x40:
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe:pe + 4] == b"PE\0\0":
            out["link_date"] = _utc(struct.unpack_from("<I", d, pe + 8)[0])
            out["linker"] = f"{d[pe + 26]}.{d[pe + 27]:02d}"
    out["rich"] = {f"{p}:{b}": n for (p, b), n in sorted(rich_builds(d).items(), key=lambda kv: -kv[1])[:6]}
    out["middleware"] = middleware(d)
    return out


# ------------------------------------------------------------------------------------------------------------------ PDB 2.00
class Msf2:
    """The multi-stream file of a PDB 2.00: stream(n) gives its bytes."""

    def __init__(self, data):
        if not data.startswith(MAGIC2):
            raise ValueError("not a PDB 2.00")
        ps, _start, _np, rs, _ptr = struct.unpack_from("<IHHII", data, 44)
        n = (rs + ps - 1) // ps
        root = b"".join(data[p * ps:(p + 1) * ps] for p in struct.unpack_from(f"<{n}H", data, 60))[:rs]
        ns = struct.unpack_from("<H", root, 0)[0]
        self.data, self.ps, self.sizes, self.blocks, at = data, ps, [], [], 4
        for _ in range(ns):
            sz = struct.unpack_from("<I", root, at)[0]
            self.sizes.append(0 if sz == 0xFFFFFFFF else sz)
            at += 8
        for sz in self.sizes:
            k = (sz + ps - 1) // ps
            self.blocks.append(struct.unpack_from(f"<{k}H", root, at))
            at += 2 * k

    def stream(self, i):
        if i >= len(self.sizes) or i == 0xFFFF:
            return b""
        return b"".join(self.data[b * self.ps:(b + 1) * self.ps] for b in self.blocks[i])[:self.sizes[i]]


def _p(b, o):
    n = b[o]
    return b[o + 1:o + 1 + n].decode("latin-1")


def _c(b, o):
    return b[o:b.index(b"\0", o)].decode("latin-1")


PROC = {0x0204: (33, _p), 0x0205: (33, _p), 0x100A: (35, _p), 0x100B: (35, _p), 0x110F: (35, _c), 0x1110: (35, _c)}
DATA = {0x0201: (12, _p), 0x0202: (12, _p), 0x1007: (14, _p), 0x1008: (14, _p), 0x110C: (14, _c), 0x110D: (14, _c)}
PUB = {0x0203: (12, _p), 0x1009: (14, _p), 0x110E: (14, _c)}
LOCAL = {0x0200, 0x0002, 0x1006, 0x1002, 0x1005, 0x110B, 0x1111, 0x113E}   # BPREL32, REGISTER and their 32-bit-type forms


def read_pdb2(path):
    """Counts from one PDB 2.00 (module/object names only for libraries): functions, sized, data, libraries, compilers, debug."""
    data = pathlib.Path(path).read_bytes()
    msf = Msf2(data)
    info = msf.stream(1)
    ver, sig, age = struct.unpack_from("<3I", info, 0)
    r = {"file": pathlib.Path(path).name, "size": len(data), "pdb_version": ver, "sig": sig, "age": age, "link_date": _utc(sig)}
    dbi = msf.stream(3)
    (_s, dver, _a, gsi, build, psi, _d, symrec, _r, modsz, *_x) = struct.unpack_from("<iIIHHHHHHiiiii", dbi, 0)
    r["dbi_version"] = dver
    names, sized, sdk, crt, dat = set(), set(), set(), set(), set()
    libs, objs, comp, line_mods, locs = {}, set(), collections.Counter(), 0, 0
    o = 64
    while o + 64 <= 64 + modsz:
        si, symsz, c11, c13 = struct.unpack_from("<HIII", dbi, o + 34)
        mod, p = _c(dbi, o + 64), 0
        p = o + 64 + len(mod) + 1
        obj = _c(dbi, p)
        p += len(obj) + 1
        o = (p + 3) & ~3
        lib = pathlib.PureWindowsPath(obj).name if obj.lower().endswith(".lib") else ""
        if lib:
            libs[lib.lower()] = libs.get(lib.lower(), 0) + 1
        objs.add(pathlib.PureWindowsPath(mod).name)
        line_mods += bool(c11 or c13)
        if si == 0xFFFF or not symsz:
            continue
        s = msf.stream(si)
        q = 4
        while q + 4 <= min(len(s), symsz):
            n, kind = struct.unpack_from("<HH", s, q)
            if n < 2:
                break
            try:
                if kind == 0x0001:
                    comp[_p(s, q + 8)] += 1
                elif kind in PROC:
                    at, rd = PROC[kind]
                    size = struct.unpack_from("<I", s, q + 4 + 12)[0]
                    nm = rd(s, q + 4 + at)
                    names.add(nm)
                    if size:
                        sized.add(nm)
                    if lib:
                        (crt if CRT_RE.match(lib) else sdk if SDK_RE.match(lib) else set()).add(nm)
                elif kind in DATA:
                    dat.add(DATA[kind][1](s, q + DATA[kind][0]))
                elif kind in LOCAL:
                    locs += 1
            except (struct.error, ValueError, IndexError):
                pass
            q += n + 2
    pubs = msf.stream(symrec)
    npub, q = 0, 0
    while q + 4 <= len(pubs):
        n, kind = struct.unpack_from("<HH", pubs, q)
        if n < 2:
            break
        npub += kind in PUB
        q += n + 2
    tpi = msf.stream(2)
    ntypes = (struct.unpack_from("<I", tpi, 12)[0] - struct.unpack_from("<I", tpi, 8)[0]) if len(tpi) > 16 else 0
    r.update(modules=len(objs), libraries=libs, compilers=dict(comp.most_common(6)), fn_names=len(names), fn_sized=len(sized),
             sdk_fns=len(sdk), crt_fns=len(crt), data_syms=len(dat), publics=npub, line_modules=line_mods, locals=locs, types=ntypes,
             lib_obj="lib+obj" if libs else ("obj" if objs else "no"))
    return r


def nb10_match(code_path, pdb_sig, pdb_age):
    """True when the executable or XBE holds the PDB's build id (an NB10 record with its time stamp and age)."""
    d = pathlib.Path(code_path).read_bytes()
    return d.find(b"NB10\0\0\0\0" + struct.pack("<II", pdb_sig, pdb_age)) >= 0


# ------------------------------------------------------------------------------------------------------------------ one set
MAP_LINE = re.compile(r"^\s*[0-9a-f]{4}:[0-9a-f]{8}\s+(\S+)\s+[0-9a-f]{8}\s+(f?)\s*(?:i\s+)?(\S+)?\s*$", re.M)


def read_msvc_map(path):
    """Publics of a Microsoft linker map: names, 'f' (function) ones, and the library:object column when present."""
    t = pathlib.Path(path).read_text(encoding="latin-1")
    names, fns, libs, objs = set(), set(), set(), set()
    for nm, f, lo in MAP_LINE.findall(t):
        names.add(nm)
        if f:
            fns.add(nm)
        if lo:
            lib, _, obj = lo.partition(":")
            (libs.add(lib.lower()) if obj else None)
            objs.add(obj or lo)
    return {"file": pathlib.Path(path).name, "linker": (re.search(r"Timestamp is ([0-9a-f]+)", t) or [None, None])[1],
            "publics": len(names), "fns": len(fns), "libs": sorted(libs), "objs": len(objs)}


def scan_set(root):
    """The Xbox facts of one folder (see the module docstring): XDK build, libraries, compiler, symbols, middleware."""
    root = pathlib.Path(root)
    rec = {"xbes": [], "pdbs": [], "pes": [], "maps": [], "middleware": {}, "errors": []}
    sigs = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".xbe":
                x = read_xbe(p)
                x["middleware"] = middleware(p.read_bytes())
                rec["xbes"].append(x)
            elif ext == ".pdb":
                r = read_pdb2(p)
                sigs[(r["sig"], r["age"])] = r["file"]
                rec["pdbs"].append(r)
            elif ext == ".exe":
                rec["pes"].append(read_pe(p))
            elif ext == ".map":
                rec["maps"].append(read_msvc_map(p))
        except Exception as e:  # noqa: BLE001 - one bad file must not lose the set
            rec["errors"].append(f"{p.name}: {type(e).__name__}: {e}"[:160])
    for r in rec["pdbs"]:
        r["matches"] = [p.name for p in sorted(root.rglob("*")) if p.suffix.lower() in (".xbe", ".exe") and nb10_match(p, r["sig"], r["age"])]
    for a in rec["xbes"] + rec["pes"]:
        for k, v in a.pop("middleware", {}).items():
            rec["middleware"].setdefault(k, v)
    return rec

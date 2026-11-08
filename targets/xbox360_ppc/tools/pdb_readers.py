"""Readers for pdb_inventory.py: the version facts in one XEX/XED, PDB, MAP or PE/EXE file of an Xbox 360 symbol set.

Each read_* takes a file and a {family: Counter} dict that collects the version strings found in it (SDK, Havok, Wwise,
PhysX, Bink, ...), and returns a dict of that file's facts (see pdb_inventory.py for the list).

Nearest existing: pdb_names.py - reads one PDB to name a retail build's functions and records no versions (its Msf, Pdb,
MapSymbols and cstr are reused here). xex.py - parses one XEX for the pipeline but raises on a title update (.xed, a
delta patch) and does not read the static-library versions. Split out of pdb_inventory.py to keep both under 400 lines.
"""
import collections
import datetime
import pathlib
import re
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pdb_names  # noqa: E402
import xex  # noqa: E402

VERSION_STRINGS = {
    "sdk": rb"(?<![\d.])2\.0\.\d{4}\.\d{1,2}(?![\d.])",
    "havok": rb"Havok[-_ ]v?\d\.\d+(?:\.\d+)?(?:-r\d+)?|(?<![a-z])hk\d{3}r\d(?!\d)",
    "wwise": rb"Wwise[ _]?v?\d{4}\.\d(?:\.\d)?(?:[ _]build[ _]\d+)?",
    "physx": rb"PhysX[-_ ]?(?:SDK[-_ ]?)?v?\d\.\d+(?:\.\d+)?",
    "bink": rb"Bink[^\x00\r\n]{0,16}?(?<![\d.])\d\.\d+[a-z]?(?![\d.])",
    "scaleform": rb"(?:GFx|Scaleform)[-_ ]?v?\d\.\d+(?:\.\d+)?",
    "speedtree": rb"SpeedTree[^\x00\r\n]{0,12}?\d\.\d+",
    "fmod": rb"FMOD[^\x00\r\n]{0,16}?\d\.\d+\.\d+",
    "miles": rb"Miles Sound System[^\x00\r\n]{0,24}?\d\.\d+[a-z]?|(?<![a-z])mss\d{2}(?!\d)",
    "lua": rb"Lua \d\.\d(?:\.\d)?",
    "zlib": rb"(?:deflate|inflate) \d\.\d\.\d(?:\.\d)?",
    "gamebryo": rb"Gamebryo[^\x00\r\n]{0,12}?\d\.\d+(?:\.\d+)?",
    "renderware": rb"RenderWare[^\x00\r\n]{0,12}?\d\.\d+",
    "granny": rb"Granny[^\x00\r\n]{0,12}?\d\.\d+\.\d+",
    "naturalmotion": rb"(?:euphoria|morpheme|NaturalMotion)[^\x00\r\n]{0,12}?\d\.\d+",
}
VERSION_RE = re.compile(b"|".join(b"(?P<%s>%s)" % (k.encode(), v) for k, v in VERSION_STRINGS.items()), re.I)
UE3_MARKERS = (b"UObject", b"UEngine", b"FName", b"UGameEngine", b"ULevel", b"UnrealScript")


# ------------------------------------------------------------------------------------------------------------- helpers
def vstr(v):
    """An Xbox version word (major 4 bits, minor 4, build 16, qfe 8) as a.b.c.d."""
    return f"{v >> 28}.{(v >> 24) & 0xF}.{(v >> 8) & 0xFFFF}.{v & 0xFF}"


def utc(ts):
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts > 0 else None
    except (OverflowError, OSError, ValueError):
        return None



def strings(data, found):
    """Adds every version string in data to found {family: Counter}."""
    for m in VERSION_RE.finditer(data):
        found.setdefault(m.lastgroup, collections.Counter())[m.group(0).decode("latin-1")[:60]] += 1



# ----------------------------------------------------------------------------------------------------------------- XEX
def read_xex(path, found):
    d = path.read_bytes()
    if d[:4] != xex.MAGIC:
        return {"file": path.name, "error": f"not XEX2 ({d[:4]!r})"}
    u32, u16 = xex.u32, xex.u16
    heads = {}
    for i in range(u32(d, 0x14)):
        k, v = struct.unpack_from(">II", d, 0x18 + 8 * i)
        heads[k] = v

    def sized(key):
        o = heads.get(key)
        return d[o:o + u32(d, o)] if o is not None and o + 4 <= len(d) else None
    out = {"file": path.name, "size": len(d), "delta_patch": xex.H_DELTA_PATCH in heads}
    blob = sized(xex.H_ORIGINAL_PE_NAME)
    if blob:
        out["original_pe_name"] = blob[4:].split(b"\0")[0].decode("ascii", "replace")
    o = heads.get(xex.H_EXECUTION_INFO)
    if o is not None:
        media, ver, base, title = struct.unpack_from(">IIII", d, o)
        _plat, _table, disc, discs = struct.unpack_from(">BBBB", d, o + 16)
        out.update(title_id=f"{title:08X}", version=vstr(ver), base_version=vstr(base), media_id=f"{media:08X}",
                   disc=f"{disc}/{discs}")
    blob = sized(xex.H_IMPORT_LIBRARIES)
    if blob:
        ssize, count = u32(blob, 4), u32(blob, 8)
        names = [n.decode("ascii", "replace") for n in blob[12:12 + ssize].split(b"\0") if n]
        libs, p = [], 12 + ssize
        for _ in range(count):
            size = u32(blob, p)
            ver, vmin, ni = u32(blob, p + 0x1C), u32(blob, p + 0x20), u16(blob, p + 0x24)
            libs.append({"name": names[ni] if ni < len(names) else f"lib{ni}", "version": vstr(ver), "min": vstr(vmin)})
            p += size
        out["import_libraries"] = libs
    blob = sized(xex.H_STATIC_LIBRARIES)
    if blob:
        libs = []
        for p in range(4, len(blob) - 15, 16):
            name = blob[p:p + 8].split(b"\0")[0].decode("ascii", "replace")
            major, minor, build = struct.unpack_from(">HHH", blob, p + 8)
            approval, qfe = blob[p + 14], blob[p + 15]
            libs.append({"name": name, "version": f"{major}.{minor}.{build}.{qfe}", "approval": approval})
        out["static_libraries"] = libs
    builds = collections.Counter(int(x["version"].split(".")[2]) for x in out.get("static_libraries", []))
    if not builds:
        builds = collections.Counter(int(x["version"].split(".")[2]) for x in out.get("import_libraries", []))
    out["sdk_build"] = builds.most_common(1)[0][0] if builds else None
    strings(d[:len(d)], found)   # plain headers and any uncompressed image bytes
    return out


# ----------------------------------------------------------------------------------------------------------------- PDB
S_COMPILE, S_COMPILE2, S_COMPILE3 = 0x0001, 0x1116, 0x113C


def compiler(rec_kind, b, o):
    """'name a.b.c.d' from a module's compile record, or None."""
    try:
        if rec_kind == S_COMPILE3:
            v = struct.unpack_from("<4H", b, o + 4 + 4 + 2 + 8)
            name = pdb_names.cstr(b, o + 4 + 4 + 2 + 16)[0]
        elif rec_kind == S_COMPILE2:
            v = struct.unpack_from("<3H", b, o + 4 + 4 + 2 + 6) + (0,)
            name = pdb_names.cstr(b, o + 4 + 4 + 2 + 12)[0]
        else:
            return None
        return f"{name.strip()} {'.'.join(map(str, v))}"
    except (struct.error, ValueError):
        return None


def read_pdb(path, found):
    out = {"file": path.name, "size": path.stat().st_size}
    try:
        pdb = pdb_names.Pdb(path)
    except Exception as e:  # noqa: BLE001 - an old (2.00) or damaged PDB: keep its strings
        data = path.read_bytes()
        out["error"] = f"{type(e).__name__}: {e}"[:200]
        out["format"] = data[:40].split(b"\x1a")[0].decode("latin-1", "replace")
        strings(data, found)
        out["ue3_markers"] = [m.decode() for m in UE3_MARKERS if m in data]
        return out
    msf = pdb.msf
    info = msf.stream(1)
    if len(info) >= 28:
        ver, sig, age = struct.unpack_from("<III", info, 0)
        out.update(pdb_version=ver, link_time=utc(sig), age=age, guid=info[12:28].hex())
    dbi = msf.stream(3)
    _s, _v, _a, _g, build, _p, dll, _r, rbld = struct.unpack_from("<iIIHHHHHH", dbi, 0)
    out.update(linker=f"{(build >> 8) & 0x7F}.{build & 0xFF}", pdb_dll=f"{dll}.{rbld}", machine=f"0x{pdb.machine:04X}",
               modules=len(pdb.modules))
    libs, compilers, by_lib = {}, collections.Counter(), {}
    for name, obj, si, symsz, _c11, _c13 in pdb.modules:
        lib = obj if obj.lower().endswith(".lib") else None
        if lib:
            libs.setdefault(pathlib.PureWindowsPath(lib).name.lower(), str(pathlib.PureWindowsPath(lib).parent))
        if si == 0xFFFF or not symsz:
            continue
        b = msf.stream(si)[:min(symsz, 4096)]
        o = 4
        for _ in range(8):
            if o + 4 > len(b):
                break
            n, kind = struct.unpack_from("<HH", b, o)
            if n < 2:
                break
            c = compiler(kind, b, o)
            if c:
                compilers[c] += 1
                if lib:
                    by_lib.setdefault(pathlib.PureWindowsPath(lib).name.lower(), collections.Counter())[c] += 1
                break
            o += n + 2
    out["libraries"] = libs
    out["compilers"] = dict(compilers.most_common(12))
    out["library_compilers"] = {k: v.most_common(1)[0][0] for k, v in sorted(by_lib.items())}
    strings(msf.data, found)
    strings("\n".join(f"{d}\\{n}" for n, d in libs.items()).encode("latin-1", "replace"), found)
    out["ue3_markers"] = [m.decode() for m in UE3_MARKERS if m in msf.data]
    return out


def read_map(path, found):
    text = path.read_text(encoding="latin-1")
    head = [ln.strip() for ln in text.splitlines()[:12] if ln.strip()]
    out = {"file": path.name, "size": path.stat().st_size, "module": head[0] if head else None}
    m = re.search(r"Timestamp is ([0-9a-f]{8})", text[:4000], re.I)
    if m:
        out["link_time"] = utc(int(m.group(1), 16))
    libs = set()
    for mod in pdb_names.MapSymbols(path).modules:
        lib, _, obj = mod.partition(":")
        if obj and not lib.startswith("CIL library"):
            libs.add(lib.lower() + ".lib")
    out["libraries"] = sorted(libs)
    strings(text.encode("latin-1"), found)
    return out


def read_pe(path, found):
    d = path.read_bytes()
    out = {"file": path.name, "size": len(d)}
    if d[:2] == b"MZ" and len(d) > 0x40:
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe:pe + 4] == b"PE\0\0":
            machine, _n, ts = struct.unpack_from("<HHI", d, pe + 4)
            out.update(link_time=utc(ts), machine=f"0x{machine:04X}")
    strings(d, found)
    out["ue3_markers"] = [m.decode() for m in UE3_MARKERS if m in d]
    return out

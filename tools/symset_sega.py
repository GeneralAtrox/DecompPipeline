"""What a Dreamcast symbol set or a Saturn disc holds, read from file contents: the Sega library build stamps ("Shinobi Ver 1.62
Build:Aug 30 1999 17:32:03", "GFS_SBL Version 2.10 1996-02-01"), the compiler and linker (GCC and Hitachi SHC strings, link-map
kind), the OS (Katana, Windows CE) or Saturn sound driver, CRI/Cinepak/Dolby middleware, and what the symbols give (function
names, library and object names, DWARF). Used by `symbol_inventory.py --console dreamcast|saturn` (scan / report / one).

A Dreamcast set is a folder of ELF/BIN/MAP/SYM files. A Saturn "set" is a disc (a .cue Redump image, read through
targets/saturn_sh2/tools/satdisc.py): its file list, library stamps and sound driver, and only the files that can hold library
strings are read (code-like files up to 4 MB; every other file only for a magic number), so no whole disc is read. Only counts,
set names, version stamps, library and object names leave a record: no code, no symbols, no disc data. SDK signature sets
(IDA FLIRT .sig) are listed by the library name and function count in their own header.

Nearest existing: targets/saturn_sh2/tools/satdisc.py (reads a disc, scores code, prints library strings, builds no table) and
targets/gamecube_ppc/tools/gc_binary.py (the same stamp scan for Dolphin SDK strings); both are reused or mirrored, not replaced.
"""
import collections
import pathlib
import re
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "targets" / "saturn_sh2" / "tools"))

STAMP = re.compile(rb"([A-Za-z][A-Za-z0-9_ ]{1,20}?)\s*Ver\.?\s*([0-9][0-9.,]*[a-z]{0,3})\s*Build:\s*([A-Z][a-z]{2} \d{1,2} \d{4}(?: \d\d:\d\d:\d\d)?)")
SATLIB = re.compile(rb"\b([A-Z][A-Z0-9_]{2,12}) Version (\d+\.\d+) (\d{4}-\d{2}-\d{2})")
SNDDRV = re.compile(rb"Ver(\d\.\d\d) (\d\d/\d\d/\d\d)")
CC = re.compile(rb"GCC: \(GNU\)[ 0-9a-zA-Z._-]{0,40}|GNU (?:C\+\+|C|AS) [0-9][ 0-9a-zA-Z._-]{0,40}|Microsoft \(R\) 32-bit C/C\+\+ Optimizing Compiler Version [0-9]+\.[0-9]+\.[0-9]+")
CC_PATH = re.compile(rb"SHC\\INCLUDE|Hitachi SH Includes|MSL C\+\+\\Inc|Metrowerks|CodeWarrior")
MW = {"cri adx": rb"\bADX[TFBP]? Ver|\(c\)CRI", "cri sofdec": rb"CRI SFD|Sofdec|mwSfd", "cri middleware": rb"CRI LSC|CRI SAN|mwRna|mwPly",
      "dolby": rb"DolbySurround|Dolby Pro Logic", "bink": rb"Bink(?:Open|Set|Pause)", "miles": rb"BinkOpenMiles|Miles Sound", "cinepak": rb"\bcvid\b"}
MW_RE = {k: re.compile(v) for k, v in MW.items()}
EXE_EXT = {"", ".bin", ".tsk", ".drv", ".exe", ".ovl", ".elf", ".prg", ".snd", ".mod", ".dat", ".sbn", ".xbn"}


def stamps(data):
    """{library: sorted [(version, built)]} for every library build stamp in data."""
    out = collections.defaultdict(set)
    for m in STAMP.finditer(data):
        out[m.group(1).strip().decode("latin-1")].add((m.group(2).decode(), m.group(3).decode()))
    return {k: sorted(v) for k, v in out.items()}


def elf_info(d):
    """Sections, function/data symbol counts and DWARF kind of a 32-bit ELF (either byte order)."""
    be = d[5] == 2
    e = ">" if be else "<"
    shoff, = struct.unpack_from(e + "I", d, 0x20)
    shentsize, shnum, shstr = struct.unpack_from(e + "HHH", d, 0x2E)
    sec = [struct.unpack_from(e + "10I", d, shoff + i * shentsize) for i in range(shnum)] if shoff else []
    strtab = d[sec[shstr][4]:sec[shstr][4] + sec[shstr][5]] if sec else b""
    name = lambda o: strtab[o:strtab.index(b"\0", o)].decode("latin-1")
    names = [name(s[0]) for s in sec]
    out = {"machine": struct.unpack_from(e + "H", d, 18)[0], "sections": len(sec),
           "dwarf": "DWARF 2" if ".debug_info" in names else ("DWARF 1" if ".debug" in names else None),
           "lines": ".line" in names or ".debug_line" in names, "fn": set(), "fn_sized": set(), "data": set(), "files": 0}
    for i, s in enumerate(sec):
        if s[1] == 2:      # SHT_SYMTAB
            st, strs = d[s[4]:s[4] + s[5]], sec[s[6]]
            sd = d[strs[4]:strs[4] + strs[5]]
            for o in range(0, len(st) - 15, 16):
                nm_o, _v, size, info, _oth, _sh = struct.unpack_from(e + "IIIBBH", st, o)
                nm = sd[nm_o:sd.index(b"\0", nm_o)].decode("latin-1") if nm_o < len(sd) else ""
                t = info & 15
                if t == 2 and nm:
                    out["fn"].add(nm)
                    if size:
                        out["fn_sized"].add(nm)
                elif t == 1 and nm:
                    out["data"].add(nm)
                elif t == 4:
                    out["files"] += 1
    return out


def map_kind(text):
    head = text[:3000]
    if "H SERIES LINKAGE EDITOR" in head:
        m = re.search(r"H SERIES LINKAGE EDITOR Ver\. ?([0-9.]+[A-Z]?)", head)
        return "Hitachi linkage editor " + (m.group(1) if m else "")
    if "Archive member included" in head:
        return "GNU ld map"
    if head.lstrip().startswith("Link Map of") or "start of segment" in head:
        return "CodeWarrior link map"
    return "other"


def read_map(path):
    t = pathlib.Path(path).read_text(encoding="latin-1")
    kind = map_kind(t)
    libs = {pathlib.PureWindowsPath(x).name.lower() for x in re.findall(r"([\w./\\:-]+\.(?:a|lib))\(?", t)}
    objs = {pathlib.PureWindowsPath(x).name.lower() for x in re.findall(r"([\w./\\:-]+\.(?:obj|o))\b", t)}
    syms = set(re.findall(r"^\s*(?:0x)?[0-9a-fA-F]{8}\s+(?:[A-Z]\s+)?([A-Za-z_][\w$.@]*)\s*$", t, re.M))
    hs = re.findall(r"^(\S+)\s+H'[0-9a-fA-F]{8}\s+(ENT|DAT)\s*$", t, re.M)      # Hitachi "externally defined symbols" list
    ent, dat = {n for n, k in hs if k == "ENT"}, {n for n, k in hs if k == "DAT"}
    return {"file": pathlib.Path(path).name, "kind": kind, "libs": sorted(libs)[:40], "objs": len(objs), "symbols": len(syms) + len(ent), "data": len(dat)}


def pe_info(d):
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    machine, = struct.unpack_from("<H", d, pe + 4)
    opt = pe + 24
    sub = struct.unpack_from("<H", d, opt + 68)[0]
    return {"machine": {0x1a2: "SH3", 0x1a6: "SH4", 0x14c: "x86"}.get(machine, hex(machine)), "subsystem": {9: "Windows CE"}.get(sub, str(sub)),
            "linker": f"{d[opt + 2]}.{d[opt + 3]:02d}"}


def scan_dc(root):
    """The Dreamcast facts of one folder of files (see the module docstring)."""
    rec = collections.defaultdict(list)
    allst, cc, paths, mw, maps = collections.defaultdict(set), collections.Counter(), set(), collections.Counter(), []
    fn, sized, data_syms, sym_lines = set(), set(), set(), 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() == ".txt":
            continue
        d = p.read_bytes()
        for lib, v in stamps(d).items():
            allst[lib] |= set(v)
        cc.update(m.group().decode("latin-1")[:70] for m in CC.finditer(d))
        paths.update(m.group().decode("latin-1") for m in CC_PATH.finditer(d[:40_000_000]))
        for k, rx in MW_RE.items():
            if rx.search(d):
                mw[k] += 1
        suf = p.suffix.lower()
        if d[:4] == b"\x7fELF":
            i = elf_info(d)
            fn |= i["fn"]
            sized |= i["fn_sized"]
            data_syms |= i["data"]
            rec["elf"].append({"file": p.name, "dwarf": i["dwarf"], "lines": i["lines"], "fn": len(i["fn"])})
        elif d[:2] == b"MZ":
            rec["pe"].append({"file": p.name, **pe_info(d)})
        elif suf == ".map":
            maps.append(read_map(p))
        elif suf == ".sym":
            sym_lines += len(re.findall(rb"^[0-9a-f]{8} [A-Z] \S", d, re.M))
    rec = dict(rec)
    rec.update(stamps={k: sorted(v) for k, v in sorted(allst.items())}, compiler=[k for k, _ in cc.most_common(3)], compiler_paths=sorted(paths)[:4],
               maps=maps, sym_lines=sym_lines, fn_names=len(fn), fn_sized=len(sized), data_syms=len(data_syms),
               middleware=sorted(mw))
    return rec


# ------------------------------------------------------------------------------------------------------------------ Saturn
def sig_info(path):
    """Library name and function count of an IDA FLIRT .sig (read from its own header)."""
    d = pathlib.Path(path).read_bytes()
    if d[:6] != b"IDASGN":
        return {"file": pathlib.Path(path).name, "error": "not a FLIRT signature file"}
    n = d[0x22]
    return {"file": pathlib.Path(path).name, "library": d[0x29:0x29 + n].decode("latin-1"), "functions": struct.unpack_from("<I", d, 0x25)[0],
            "size": len(d)}


def scan_disc(cue, budget=96 << 20):
    """The Saturn facts of one disc: IP.BIN header, library stamps, sound driver, middleware files, compiler strings."""
    import satdisc
    disc = satdisc.Disc(cue)
    ip = disc.ip()
    files = disc.walk()
    libs, drv, cc, mw, ext = collections.defaultdict(lambda: collections.defaultdict(set)), collections.defaultdict(set), collections.Counter(), collections.Counter(), collections.Counter()
    drvfiles, read, errors = [], 0, 0
    for path, lba, size in files:
        suf = pathlib.PurePosixPath(path).suffix.lower()
        ext[suf or "(none)"] += 1
        want = suf in EXE_EXT and size <= (4 << 20) and read < budget
        try:
            d = disc.read(lba, size if want else min(size, 4096))
        except ValueError:
            errors += 1
            continue
        read += len(d)
        head = d[:4096]
        if suf in (".cpk", ".film") and b"cvid" in head:
            mw["cinepak"] += 1
        if b"(c)CRI" in head[:96]:
            mw["cri adx"] += 1
        if suf == ".sfd":
            mw["cri sofdec"] += 1
        name = pathlib.PurePosixPath(path).name
        if re.match(r"(?i)sddrv|.*\.tsk$|.*\.drv$", name):
            drvfiles.append(name)
        if not want:
            continue
        for m in SATLIB.finditer(d):
            libs[m.group(1).decode()][(m.group(2).decode(), m.group(3).decode())].add(name)
        for m in SNDDRV.finditer(d):
            drv[(m.group(1).decode(), m.group(2).decode())].add(name)
        cc.update(m.group().decode("latin-1")[:60] for m in CC.finditer(d))
        for k in ("cri middleware", "cri sofdec"):
            if MW_RE[k].search(d):
                mw[k] += 1
    return {"ip": {k: ip[k] for k in ("maker", "product", "version", "date", "area", "peripherals", "title")},
            "files": len(files), "bytes_read": read, "unreadable": errors,
            "libs": {k: sorted(v) for k, v in sorted(libs.items())}, "lib_files": {k: sorted({f for fs in v.values() for f in fs})[:3] for k, v in libs.items()},
            "sound_driver": sorted(drv), "sound_driver_files": drvfiles[:6], "compiler_strings": [k for k, _ in cc.most_common(3)],
            "middleware": dict(mw), "extensions": dict(ext.most_common(12))}

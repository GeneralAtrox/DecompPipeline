"""GameCube executable readers for symbol_inventory.py: the strings that matter in a .dol, .elf or .rel (Dolphin SDK build lines,
the OS revision, compiler and MetroTRK strings, middleware names and versions), the DOL header's section ranges and an ELF's
sections, symbol table and DWARF section sizes; elf_image() lays an ELF out by address with its symbols (gc_names.py).

Nearest existing: gcrel.py parses .rel relocations for the translator and records no strings; symbol_inventory.py's previous
inline readers (split out at the 400-line cap).
"""
import pathlib
import re
import struct

MIDDLEWARE = [
    # family, regex on library / object / path names, regex on binary strings, version regex on strings
    ("MusyX", r"musyx|^(synth|snd_)|sndbase|salsnd|hw_dspctrl", rb"MusyX[^\0\n]{0,50}", rb"MusyX[^\0\n\d]{0,25}(\d+\.\d+(?:\.\d+)?)"),
    ("JSystem", r"^(jkernel|j3d|jaudio|jframework|jgadget|jmessage|jparticle|jstudio|jsupport|jutility|jmath|jhostio|jor)|"
                r"^j(kr|3d|ut|fw|pa|as|ai|au|ms|mc)\w", rb"JSystem[^\0\n]{0,40}", None),
    ("Bink", r"bink", rb"Bink[A-Za-z ]{0,20}[^\0\n]{0,40}", rb"Bink[A-Za-z ]{0,20}(?:v|ver\.?|version)?\s*(\d+\.\d+[a-z]?)"),
    ("HVQM", r"hvqm", rb"HVQM[^\0\n]{0,40}", rb"HVQM\w*\D{0,8}(\d+\.\d+)"),
    ("THP", r"^thp(player|read|dec|audio|video|simple|buffer|mix)|^thp\.a", rb"THP(Player|Read|Video|Audio|Decode|Simple|Buffer|Mix)\w*", None),
    ("RenderWare", r"^rw[a-z]|^rp[a-z]|renderware|rwsdk", rb"RenderWare[^\0\n]{0,50}", rb"RenderWare\D{0,25}(\d+\.\d+(?:\.\d+)*)"),
    ("CRI ADX/Sofdec", r"^adx|^sfd|^cri|^sofdec|adxt|criware", rb"(?:CRI Middleware|CRIWARE|Sofdec|ADX/GC|CRI ADX)[^\0\n]{0,50}",
     rb"(?:ADX|Sofdec|CRI)[^\0\n]{0,12}Ver\.?\s*(\d+\.\d+[\w.]*)"),
    ("NetImmerse/Gamebryo", r"^ni[a-z]{3,}|netimmerse|gamebryo", rb"(?:NetImmerse|Gamebryo)[^\0\n]{0,40}", rb"(?:NetImmerse|Gamebryo)\D{0,12}(\d+\.\d+(?:\.\d+)*)"),
    ("Havok", r"^hk[a-z]|havok", rb"Havok[^\0\n]{0,40}", rb"Havok\D{0,12}(\d+\.\d+(?:\.\d+)*)"),
    ("zlib", r"zlib|inflate|deflate", rb"(?:inflate|deflate) \d\.\d+\.\d+ Copyright[^\0\n]{0,30}", rb"(?:inflate|deflate) (\d\.\d+\.\d+)"),
    ("Lua", r"^lua|liblua", rb"Lua \d\.\d[^\0\n]{0,40}", rb"Lua (\d\.\d(?:\.\d+)?)"),
    ("FMOD", r"^(fmodgc|fmodex|fmod_|libfmod|fmodapi)|fmod\.a", rb"FMOD[^\0\n]{0,40}", rb"FMOD\D{0,12}(\d+\.\d+(?:\.\d+)?)"),
    ("Miles Sound System", r"^mss|miles", rb"Miles Sound System[^\0\n]{0,40}", rb"Miles Sound System\D{0,12}(\d+\.\d+\w*)"),
    ("Unreal Engine", r"^unreal|^engine\.a|^core\.a", rb"Unreal Engine[^\0\n]{0,40}", rb"Unreal Engine\D{0,6}(\d+(?:\.\d+)?)"),
    ("Alchemy", r"^alchemy|^igx", rb"Alchemy[^\0\n]{0,40}", None),
    ("GameSpy", r"gamespy|^gs[a-z]{3,}", rb"GameSpy[^\0\n]{0,40}", None),
    ("Dolby", r"dolby|dpl2", rb"Dolby[^\0\n]{0,40}", None),
    ("NintendoWare (nw4r)", r"^nw4r|^ef\.a|^lyt|^g3d|^snd\.a", rb"nw4r[^\0\n]{0,40}", None),
]
MW_LIB = [(n, re.compile(lib, re.I)) for n, lib, _, _ in MIDDLEWARE]
STR_RE = re.compile(b"|".join(b"(?P<g%d>%s)" % (i, s) for i, (_, _, s, _) in enumerate(MIDDLEWARE)))
VER_RE = {n: re.compile(v) for n, _, _, v in MIDDLEWARE if v}

SDK_STR = re.compile(rb"<< Dolphin SDK - (\S+)\s+(\w+) build: ([^()\0\n]{6,40}?) \((0x[0-9A-Fa-f]+)\) >>")
OS_REV = re.compile(rb"Dolphin OS \$Revision: (\d+) \$")
SDK_PATH = re.compile(rb"DolphinSDK(\d\.\d+)")
COMPILER_RES = [re.compile(p) for p in (rb"GNU C(?:\+\+)? \d[\d.]*[^\0\n]{0,40}", rb"MW (?:EABI )?(?:PPC )?C[^\0\n]{0,6}-?Compiler[^\0\n]{0,20}",
                                        rb"Metrowerks[^\0\n]{0,6}CodeWarrior[^\0\n]{0,50}", rb"CodeWarrior[^\0\n]{0,60}(?:\d\.\d)[^\0\n]{0,20}",
                                        rb"MetroTRK for [^\0\n]{0,30}", rb"SN Systems[^\0\n]{0,30}", rb"MW C/C\+\+[^\0\n]{0,40}",
                                        rb"Metrowerks[^\0\n]{0,6}Target Resident Kernel[^\0\n]{0,20}")]


def clean(b, n=90):
    return b.decode("latin1", "replace")[:n].strip()



# ---------------------------------------------------------------------------------------------------------- binary files
def scan_blob(blob, acc):
    """Strings that matter in one chunk of an executable: SDK builds, the OS revision, compilers, middleware."""
    for m in SDK_STR.finditer(blob):
        lib, kind, when, code = (clean(x, 60) for x in m.groups())
        acc["sdk"].add((lib, kind, " ".join(when.split()), code))
    for m in OS_REV.finditer(blob):
        acc["os_rev"].add(int(m.group(1)))
    for m in SDK_PATH.finditer(blob):
        acc["sdk_path"].add("DolphinSDK" + clean(m.group(1), 10))
    for rx in COMPILER_RES:
        for m in list(rx.finditer(blob))[:30]:
            acc["compiler"][clean(m.group(0), 80)] += 1
    n = 0
    for m in STR_RE.finditer(blob):
        n += 1
        if n > 400:
            break
        i = int(m.lastgroup[1:])
        name, _, _, _ = MIDDLEWARE[i]
        s = clean(m.group(0), 80)
        acc["mw_str"][name].add(s)
        vr = VER_RE.get(name)
        if vr:
            v = vr.search(m.group(0) + blob[m.end():m.end() + 40])
            if v:
                acc["mw_ver"][name].add(v.group(1).decode("latin1"))


def read_dol(path, acc):
    """DOL header: section file offsets, load addresses and sizes (7 text, 11 data). Scans the whole file for strings."""
    b = path.read_bytes()
    info = {"kind": "dol", "size": len(b), "ranges": []}
    if len(b) >= 0x100:
        off = struct.unpack(">18I", b[0:72])
        adr = struct.unpack(">18I", b[72:144])
        siz = struct.unpack(">18I", b[144:216])
        info["ranges"] = [(adr[i], adr[i] + siz[i], i < 7) for i in range(18) if siz[i] and adr[i]]
        info["entry"] = struct.unpack(">I", b[0xE0:0xE4])[0]
    scan_blob(b, acc)
    return info


def read_elf(path, acc):
    """32-bit big-endian ELF: section headers, symbol table, DWARF section sizes. Strings are searched in the loadable
    sections, .comment and the symbol strings only: the DWARF (most of a debug ELF's size) is not read."""
    info = {"kind": "elf", "ranges": [], "fn": set(), "fn_sized": set(), "data": set(), "files": 0, "dwarf": {}, "syms": 0}
    with open(path, "rb") as f:
        head = f.read(52)
        if head[:4] != b"\x7fELF" or head[4] != 1 or head[5] != 2:
            info["error"] = "not a 32-bit big-endian ELF"
            return info
        shoff, = struct.unpack(">I", head[32:36])
        shentsize, shnum, shstrndx = struct.unpack(">HHH", head[46:52])
        f.seek(shoff)
        raw = f.read(shentsize * shnum)
        secs = []
        for i in range(shnum):
            n, t, fl, ad, of, sz, lk, inf, al, es = struct.unpack(">10I", raw[i * shentsize:i * shentsize + 40])
            secs.append(dict(name=n, type=t, flags=fl, addr=ad, off=of, size=sz, link=lk, entsize=es))
        f.seek(secs[shstrndx]["off"])
        names = f.read(secs[shstrndx]["size"])
        for s in secs:
            e = names.find(b"\0", s["name"])
            s["name"] = names[s["name"]:e].decode("latin1")
        for s in secs:
            nm, t = s["name"], s["type"]
            if nm.startswith(".debug") or nm in (".line", ".debug_srcinfo") or nm.startswith(".line"):
                if t != 8 and s["size"]:
                    info["dwarf"][nm] = s["size"]
                    if nm in (".debug", ".debug_info", ".debug_str"):   # the first compile units name the compiler (producer)
                        f.seek(s["off"])
                        scan_blob(f.read(min(s["size"], 400_000)), acc)
                continue
            if s["flags"] & 2 and t == 1 and s["addr"] and s["size"]:
                info["ranges"].append((s["addr"], s["addr"] + s["size"], bool(s["flags"] & 4)))
            if t == 8 or not s["size"] or s["size"] > 200_000_000:
                continue
            if (s["flags"] & 2) or nm in (".comment", ".strtab", ".mwcats", ".note") or t == 3:
                f.seek(s["off"])
                scan_blob(f.read(s["size"]), acc)
        for s in secs:
            if s["type"] != 2:   # SHT_SYMTAB
                continue
            f.seek(s["off"])
            sym = f.read(s["size"])
            strs = secs[s["link"]]
            f.seek(strs["off"])
            st = f.read(strs["size"])
            n = len(sym) // 16
            info["syms"] = n
            for i in range(n):
                nm, val, sz, ia, oth, shn = struct.unpack(">IIIBBH", sym[i * 16:i * 16 + 16])
                typ = ia & 15
                if typ == 4:
                    info["files"] += 1
                    continue
                if not nm:
                    continue
                e = st.find(b"\0", nm)
                name = st[nm:e].decode("latin1")
                if typ == 2:
                    info["fn"].add(name)
                    if sz:
                        info["fn_sized"].add(name)
                elif typ in (0, 1) and shn != 0 and not name.startswith("."):
                    info["data"].add(name)
    return info


def elf_image(path):
    """(image, base, symbols, exec ranges) of a 32-bit big-endian ELF for gc_names.py: its loaded sections with
    contents laid out by address from base, and every symbol as (name, value, size, type, bind, file), file being the
    STT_FILE symbol before it (CodeWarrior lists each file's local symbols after its file symbol). ValueError when it is
    not such an ELF or its sections span over 64 MB."""
    b = pathlib.Path(path).read_bytes()
    if b[:4] != b"\x7fELF" or b[4] != 1 or b[5] != 2:
        raise ValueError("not a 32-bit big-endian ELF")
    shoff, = struct.unpack(">I", b[32:36])
    shentsize, shnum, _ = struct.unpack(">HHH", b[46:52])
    secs = [struct.unpack(">10I", b[shoff + i * shentsize:shoff + i * shentsize + 40]) for i in range(shnum)]
    load = [(ad, of, sz, bool(fl & 4)) for _n, t, fl, ad, of, sz, *_ in secs if t == 1 and fl & 2 and ad and sz]
    if not load:
        raise ValueError("no loaded sections with contents")
    base, end = min(s[0] for s in load), max(s[0] + s[2] for s in load)
    if end - base > 64 << 20:
        raise ValueError(f"its loaded sections span {(end - base) >> 20} MB")
    img = bytearray(end - base)
    for ad, of, sz, _x in load:
        img[ad - base:ad - base + sz] = b[of:of + sz]
    syms, cur = [], ""
    for _n, t, _fl, _ad, of, sz, lk, *_ in secs:
        if t != 2:
            continue
        st = secs[lk]
        strs = b[st[4]:st[4] + st[5]]
        for i in range(sz // 16):
            nm, val, size, ia, _oth, shn = struct.unpack_from(">IIIBBH", b, of + 16 * i)
            name = strs[nm:strs.find(b"\0", nm)].decode("latin1") if nm else ""
            if ia & 15 == 4:
                cur = name
            elif name:
                syms.append((name, val, size, ia & 15, ia >> 4, cur if ia >> 4 == 0 else ""))
    return bytes(img), base, syms, [(ad, ad + sz) for ad, _of, sz, x in load if x]

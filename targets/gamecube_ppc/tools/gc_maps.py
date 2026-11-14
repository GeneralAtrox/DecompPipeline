"""GameCube map readers for symbol_inventory.py: CodeWarrior link maps and section layouts, SN Systems (ld style) maps, address
lists, binary or encrypted maps. read_map(path) returns a MapStats: function and data names, sizes, the library and object
each function came from, SDK, runtime (MSL) and MetroTRK functions, and a sample of virtual addresses.

Nearest existing: symbol_inventory.py's own previous inline readers (split out at the 400-line cap); no other GameCube
tool reads a map (gcrel.py reads .rel modules only).
"""
import collections
import os
import pathlib
import re

# ------------------------------------------------------------------------------------------------ libraries and families
SDK_LIBS = {"os", "dvd", "vi", "pad", "ai", "ar", "arq", "dsp", "card", "gx", "exi", "si", "mtx", "base", "db", "ip", "eth",
            "thp", "mix", "axfx", "ax", "seq", "syn", "sp", "mcc", "fileio", "amc", "hio", "odem", "odenotstub", "gd", "demo",
            "dtk", "cx", "wpad", "pad", "tpl", "bba", "nand", "hio2", "vi1011", "perf", "dolphin", "gdev", "ode", "lg"}
MSL_RE = re.compile(r"^(msl|mslc|msl_c|runtime|fdlibm|mathlib|ansi|libc\b|cpp|nw4r_?msl)", re.I)
TRK_RE = re.compile(r"trk|metrotrk", re.I)
SDK_NAME_RE = re.compile(r"^(OS|DVD|VI|PAD|AI|AR|ARQ|DSP|CARD|GX|EXI|SI|MTX|C_MTX|PS(MTX|VEC|QUAT)|VEC|QUAT|DB|AX|MIX|SYN|SEQ|"
                         r"CX|TPL|THP|GD|IP|ETH|BBA)[A-Z0-9_]")
MAP_SECTIONS_TEXT = (".text", ".init")


def libbase(lib):
    b = re.split(r"[\\/]", lib)[-1].lower()
    b = re.sub(r"\.(a|lib)$", "", b)
    return re.sub(r"^lib", "", b)


def is_sdk_lib(lib, path=""):
    b = libbase(lib)
    if b in SDK_LIBS:
        return True
    return bool(re.search(r"dolphinsdk|dolphin[\\/](lib|hw2)", (path or lib).lower())) and b not in ("msl_c",)


# ------------------------------------------------------------------------------------------------------------ map files
HEX8 = r"[0-9a-fA-F]{8}"
LAYOUT = re.compile(r"^\s+(?:(%s)|UNUSED)\s+([0-9a-fA-F]{6,8})\s+(?:(%s)|\.{8})\s+(\d+)\s+(\S+)\s*(.*)$" % (HEX8, HEX8))
LAYOUT4 = re.compile(r"^\s+([0-9a-fA-F]{8})\s+([0-9a-fA-F]{6,8})\s+(\d+)\s+(\S+)\s*(.*)$")   # no virtual-address column
SECTION = re.compile(r"^(\.\S+) section layout")
LINK = re.compile(r"^\s*(\d+)\] (.+?) \((\w+),(\w+)\) found in (\S+)")
LD = re.compile(r"^([0-9a-fA-F]{8}) ([0-9a-fA-F]{8}) +(\d+)( +)(.*)$")
RANGE = re.compile(r"^([0-9a-fA-F]{8}) ([0-9a-fA-F]{8}) (\S.*)$")


class MapStats:
    def __init__(self):
        self.kind = "unknown"
        self.fn = set()
        self.fn_sized = set()
        self.data = set()
        self.libs = collections.Counter()      # library basename -> function count
        self.objs = collections.Counter()      # object basename -> function count (middleware by name)
        self.have_lib = False
        self.have_obj = False
        self.sdk_fn = set()
        self.msl_fn = set()
        self.trk_fn = set()
        self.vaddr_sample = []
        self.relative = 0                      # function lines whose address is module-relative (0-based)
        self.paths = set()


def read_map(path):
    st = MapStats()
    raw = path.read_bytes()
    if raw[:4] == b"SYM1":
        st.kind = "binary SYM1"
        return st
    text = raw.decode("latin1", "replace")
    head = text[:600]
    sample = raw[:4000]
    if sample and sum(1 for c in sample if c in (9, 10, 13) or 32 <= c < 127) < 0.85 * len(sample):
        st.kind = "not text (binary or encrypted)"
        return st
    lines = text.splitlines()
    if head.lstrip().startswith("/*") and "typedef struct" in head:
        st.kind = "file table (not symbols)"
        return st
    if re.match(r"^[0-9a-f]{32}\s*$", head.split("\n", 1)[0].strip()):
        st.kind = "address list (md5 header)"
        for ln in lines[1:]:
            m = RANGE.match(ln)
            if m:
                st.fn.add(m.group(3))
                st.fn_sized.add(m.group(3))
                if len(st.vaddr_sample) < 300:
                    st.vaddr_sample.append(int(m.group(1), 16))
        return st
    if head.startswith("Address  Size     Align Out"):
        st.kind = "ld style (SN Systems)"
        return read_ld(lines, st)
    section = None
    seen_layout = False
    for ln in lines:
        if ln[:1] == ".":
            m = SECTION.match(ln)
            if m:
                section = m.group(1)
                seen_layout = True
                continue
        if ln.startswith("Memory map") or ln.startswith("Link map") and False:
            section = None
        if not ln.startswith(" "):
            continue
        m = LAYOUT.match(ln)
        have_v = True
        if m:
            addr, size, vaddr, align, name, rest = m.groups()
        else:
            m = LAYOUT4.match(ln)
            if not m:
                continue
            addr, size, align, name, rest = m.groups()
            vaddr = addr
        rest = rest.strip()
        toks = rest.split()
        lib = obj = None
        if len(toks) >= 2:
            lib, obj = toks[0], toks[1]
        elif len(toks) == 1:
            obj = toks[0]
        if name.startswith(".") and addr and section and name == section:
            continue          # the section's own header line for an object
        isfn = section in MAP_SECTIONS_TEXT
        if not seen_layout:
            continue
        if isfn:
            st.fn.add(name)
            if int(size, 16):
                st.fn_sized.add(name)
            if vaddr and int(vaddr, 16) >= 0x80000000:
                if len(st.vaddr_sample) < 300:
                    st.vaddr_sample.append(int(vaddr, 16))
            else:
                st.relative += 1
            if lib:
                st.have_lib = True
                st.libs[libbase(lib)] += 1
                if is_sdk_lib(lib):
                    st.sdk_fn.add(name)
                if MSL_RE.search(libbase(lib)):
                    st.msl_fn.add(name)
                if TRK_RE.search(lib):
                    st.trk_fn.add(name)
            if obj:
                st.have_obj = True
                st.objs[os.path.basename(obj.replace("\\", "/"))] += 1
                if TRK_RE.search(obj):
                    st.trk_fn.add(name)
        elif section:
            st.data.add(name)
            if lib:
                st.have_lib = True
    if seen_layout:
        st.kind = "CodeWarrior section layout" if text.lstrip().startswith(".") or "section layout" in text[:3000] else "CodeWarrior link map"
        if "Link map of" in head:
            st.kind = "CodeWarrior link map + section layout"
        return st
    # a link map without a section layout: names and object attribution only
    n_link = 0
    for ln in lines:
        m = LINK.match(ln)
        if m:
            n_link += 1
            _, name, typ, scope, obj = m.groups()
            if typ == "func":
                st.fn.add(name)
                st.have_obj = True
                st.objs[os.path.basename(obj.replace("\\", "/"))] += 1
            elif typ in ("object", "notype"):
                st.data.add(name)
    if n_link:
        st.kind = "CodeWarrior link map (no layout)"
    return st


def read_ld(lines, st):
    """SN/ld style: columns Address Size Align Out In File Symbol; the depth is the indent after the Align column."""
    out_sec = file_ = None
    for ln in lines[2:]:
        m = LD.match(ln)
        if not m:
            continue
        addr, size, align, _, rest = m.groups()
        indent = len(ln) - len(ln.lstrip()) - 0
        col = ln.index(rest) if rest else 0
        txt = rest.strip()
        if not txt:
            continue
        if col < 30:
            out_sec, file_ = txt, None
            continue
        if col < 38:
            continue
        if col < 46:
            file_ = txt
            if out_sec in MAP_SECTIONS_TEXT:
                st.have_obj = True
                mo = re.match(r"^(.*?)\(([^)]+)\)$", txt)
                if mo:
                    st.have_lib = True
                    lb = libbase(mo.group(1))
                    st.libs[lb] += 1
                    st.objs[mo.group(2)] += 1
                    st.paths.add(mo.group(1).replace("\\", "/").lower())
                else:
                    st.objs[os.path.basename(txt.replace("\\", "/"))] += 1
            continue
        name = txt
        if out_sec in MAP_SECTIONS_TEXT:
            st.fn.add(name)
            if int(size, 16):
                st.fn_sized.add(name)
            if len(st.vaddr_sample) < 300:
                st.vaddr_sample.append(int(addr, 16))
            if file_:
                mo = re.match(r"^(.*?)\(([^)]+)\)$", file_)
                if mo:
                    lib = mo.group(1)
                    if is_sdk_lib(lib, lib):
                        st.sdk_fn.add(name)
                    if MSL_RE.search(libbase(lib)):
                        st.msl_fn.add(name)
                    if TRK_RE.search(lib):
                        st.trk_fn.add(name)
        elif out_sec:
            st.data.add(name)
    st.kind = "ld style (SN Systems)"
    return st



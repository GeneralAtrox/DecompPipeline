"""The readers pdb_names.py names functions from: a PDB (MSF 7.0: the DBI's modules and their procedure symbols, the
publics, the section headers, and the type stream for prototypes and structures), and a linker .map's publics in the
same shape. Split from pdb_names.py (over the 400-line cap); pdb_names imports every name here again, so
pdb_names.Pdb, pdb_names.MapSymbols, pdb_names.cstr and the rest keep working.

Nearest existing: pdb_names.py - it matches two builds and writes names; these readers are the half pdb_readers.py
(pdb_inventory) uses too, through pdb_names. Moved out unchanged.
"""
import pathlib
import re
import struct

MSF_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0"


# ------------------------------------------------------------------------------------------------------------------ the PDB
class Msf:
    """The multi-stream file a PDB is: stream n's bytes by stream(n)."""

    def __init__(self, data):
        if not data.startswith(MSF_MAGIC):
            raise ValueError("not an MSF 7.00 PDB")
        bs, _fpm, _nblocks, dirbytes, _unk = struct.unpack_from("<5I", data, 32)
        nd = (dirbytes + bs - 1) // bs
        nmap = (4 * nd + bs - 1) // bs   # the directory's block list can span several pages: their numbers follow at offset 52
        maps = struct.unpack_from(f"<{nmap}I", data, 52)
        dblocks = struct.unpack_from(f"<{nd}I", b"".join(data[m * bs:(m + 1) * bs] for m in maps))
        d = b"".join(data[b * bs:(b + 1) * bs] for b in dblocks)[:dirbytes]
        n = struct.unpack_from("<I", d, 0)[0]
        sizes = list(struct.unpack_from(f"<{n}I", d, 4))
        at = 4 + 4 * n
        self.data, self.bs, self.blocks, self.sizes = data, bs, [], []
        for s in sizes:
            s = 0 if s == 0xFFFFFFFF else s
            k = (s + bs - 1) // bs
            self.blocks.append(struct.unpack_from(f"<{k}I", d, at))
            self.sizes.append(s)
            at += 4 * k

    def stream(self, i):
        if i >= len(self.sizes) or i == 0xFFFF:
            return b""
        bs = self.bs
        return b"".join(self.data[b * bs:(b + 1) * bs] for b in self.blocks[i])[:self.sizes[i]]


class MapSymbols:
    """A linker .map's publics (the "Publics by Value" and "Static symbols" tables) in the shape Pdb gives: functions are the entries flagged
    f, sized to the next symbol in the code section; a library is the part before ':' in Lib:Object ("CIL library" is LTCG code, no library)"""
    LINE = re.compile(r"^\s*([0-9a-f]{4}):([0-9a-f]{8})\s+(\S+)\s+([0-9a-f]{8})\s+(f\s+(?:i\s+)?)?(.*?)\s*$", re.I)

    def __init__(self, path):
        self.entries = []
        for ln in pathlib.Path(path).read_text(encoding="latin-1").splitlines():
            m = self.LINE.match(ln)
            if m and int(m.group(4), 16):
                self.entries.append((int(m.group(4), 16), m.group(3), bool(m.group(5)), m.group(6)))
        self.modules = sorted({e[3] for e in self.entries})

    def vtables(self, base):
        return {a: nm[4:].split("@@")[0] for a, nm, _f, _o in self.entries if nm.startswith("??_7")}

    def functions(self, base):
        import bisect
        allsyms, out = sorted({a for a, *_ in self.entries}), {}
        for a, nm, f, obj in self.entries:
            if not f or a in out:
                continue
            i = bisect.bisect_right(allsyms, a)
            nxt = allsyms[i] if i < len(allsyms) else a + 4
            lib, _, mod = obj.partition(":")
            lib = "" if lib.startswith("CIL library") or not mod else lib + ".lib"
            out[a] = {"name": nm, "size": min(nxt - a, 0x10000), "module": mod.strip(), "lib": lib, "type": 0}
        return out


def cstr(b, o):
    e = b.index(b"\0", o)
    return b[o:e].decode("latin-1"), e + 1


def pstr(b, o):
    n = b[o]
    return b[o + 1:o + 1 + n].decode("latin-1"), o + 1 + n


def numeric(b, o):
    """A CodeView numeric leaf: (value, next offset)."""
    v = struct.unpack_from("<H", b, o)[0]
    if v < 0x8000:
        return v, o + 2
    fmt = {0x8000: "<b", 0x8001: "<h", 0x8002: "<H", 0x8003: "<i", 0x8004: "<I", 0x8009: "<q", 0x800A: "<Q"}.get(v)
    if not fmt:
        return 0, o + 2
    return struct.unpack_from(fmt, b, o + 2)[0], o + 2 + struct.calcsize(fmt)


PROCS = {0x1110: cstr, 0x110F: cstr, 0x1146: cstr, 0x1147: cstr, 0x100B: pstr, 0x100A: pstr}   # S_GPROC32, S_LPROC32, _ID, and the _ST forms
PUBS = {0x110E: cstr, 0x1009: pstr}


class Pdb:
    def __init__(self, path):
        self.msf = Msf(pathlib.Path(path).read_bytes())
        dbi = self.msf.stream(3)
        (_sig, _ver, _age, self.globals_si, _build, self.publics_si, _dll, self.symrec_si, _rbld, modsz, secconsz, secmapsz, srcsz,
         tsmsz, _mfc, dbgsz, ecsz, _flags, self.machine, _pad) = struct.unpack_from("<iIIHHHHHHiiiiiIiiHHI", dbi, 0)
        self.modules = []   # (module name, object or library file, symbol stream, symbol bytes, C13 bytes)
        o, end = 64, 64 + modsz
        while o + 64 <= end:
            si, symsz, c11, c13 = struct.unpack_from("<HIII", dbi, o + 34)
            name, p = cstr(dbi, o + 64)
            obj, p = cstr(dbi, p)
            self.modules.append((name, obj, si, symsz, c11, c13))
            o = (p + 3) & ~3
        dbg = 64 + modsz + secconsz + secmapsz + srcsz + tsmsz + ecsz
        hdrs = struct.unpack_from(f"<{dbgsz // 2}H", dbi, dbg) if dbgsz >= 12 else ()
        sec = self.msf.stream(hdrs[5]) if len(hdrs) > 5 else b""
        self.sections = [struct.unpack_from("<8sIIIIIIHHI", sec, i) for i in range(0, len(sec) - 39, 40)]

    def address(self, seg, off, base):
        if not 1 <= seg <= len(self.sections):
            return None
        return base + self.sections[seg - 1][2] + off   # VirtualAddress

    def code_section(self, seg):
        return 1 <= seg <= len(self.sections) and bool(self.sections[seg - 1][9] & 0x20000020)

    def records(self, b, o, end):
        while o + 4 <= end:
            n, kind = struct.unpack_from("<HH", b, o)
            if n < 2:
                return
            yield kind, o
            o += 2 + n

    def vtables(self, base):
        """{address: class} of the publics that name a virtual-function table (??_7Class@@6B@...)"""
        s, out = self.msf.stream(self.symrec_si), {}
        for kind, o in self.records(s, 0, len(s)):
            if kind in PUBS:
                _flags, off, seg = struct.unpack_from("<IIH", s, o + 4)
                nm, _ = PUBS[kind](s, o + 14)
                a = self.address(seg, off, base)
                if a is not None and nm.startswith("??_7"):
                    out[a] = nm[4:].split("@@")[0]
        return out

    def functions(self, base):
        """{address: {name, size, module, lib, type}} from the modules' procedures, then the publics in code the procedures lack."""
        out = {}
        for mi, (name, obj, si, symsz, _c11, _c13) in enumerate(self.modules):
            s = self.msf.stream(si)
            lib = obj if obj.lower().endswith(".lib") else ""
            for kind, o in self.records(s, 4, symsz):
                if kind in PROCS:
                    size, ti, off, seg = struct.unpack_from("<4xI8xIIH", s, o + 4 + 8)
                    nm, _ = PROCS[kind](s, o + 4 + 35)
                    a = self.address(seg, off, base)
                    if a is not None:
                        out[a] = {"name": nm, "size": size, "module": name, "lib": lib, "type": ti}
        s = self.msf.stream(self.symrec_si)
        pubs = []
        for kind, o in self.records(s, 0, len(s)):
            if kind in PUBS:
                flags, off, seg = struct.unpack_from("<IIH", s, o + 4)
                nm, _ = PUBS[kind](s, o + 14)
                a = self.address(seg, off, base)
                if a is not None and self.code_section(seg):
                    pubs.append((a, nm, flags))
        pubs.sort()
        starts = sorted(set(out) | {a for a, _, _ in pubs})
        import bisect
        for a, nm, flags in pubs:
            if a in out:
                out[a].setdefault("public", nm)
                continue
            i = bisect.bisect_right(starts, a)
            nxt = starts[i] if i < len(starts) else a + 4
            out[a] = {"name": nm, "size": min(nxt - a, 0x10000), "module": "", "lib": "", "type": 0, "public_only": True}
        return out


# ------------------------------------------------------------------------------------------------------------------ types
PRIM = {0x03: "void", 0x08: "HRESULT", 0x10: "char", 0x20: "unsigned char", 0x11: "short", 0x21: "unsigned short", 0x12: "long",
        0x22: "unsigned long", 0x13: "__int64", 0x23: "unsigned __int64", 0x30: "bool", 0x40: "float", 0x41: "double", 0x68: "__int8",
        0x69: "unsigned __int8", 0x70: "char", 0x71: "wchar_t", 0x72: "short", 0x73: "unsigned short", 0x74: "int", 0x75: "unsigned int",
        0x76: "__int64", 0x77: "unsigned __int64", 0x31: "bool16", 0x32: "bool32"}
AGG = {0x1504: ("class", cstr), 0x1505: ("struct", cstr), 0x1506: ("union", cstr), 0x1004: ("class", pstr), 0x1005: ("struct", pstr),
       0x1006: ("union", pstr)}


class Types:
    def __init__(self, msf):
        t = msf.stream(2)
        _ver, hdr, self.begin, self.end, nbytes = struct.unpack_from("<5I", t, 0)
        self.t, self.at = t, []
        o = hdr
        while o + 4 <= hdr + nbytes and len(self.at) < self.end - self.begin:
            n = struct.unpack_from("<H", t, o)[0]
            self.at.append(o)
            o += 2 + n
        self._names = {}

    def rec(self, ti):
        i = ti - self.begin
        if not 0 <= i < len(self.at):
            return None, None
        o = self.at[i]
        return struct.unpack_from("<H", self.t, o + 2)[0], o + 4

    def name(self, ti, depth=0):
        if ti < self.begin:
            base = PRIM.get(ti & 0xFF, f"prim{ti & 0xff:02x}")
            return base + " *" if ti & 0x700 else base
        if ti in self._names:
            return self._names[ti]
        if depth > 12:
            return "?"
        kind, o = self.rec(ti)
        t = self.t
        s = f"type{ti:x}"
        if kind == 0x1001:
            base, attr = struct.unpack_from("<IH", t, o)
            s = ("const " if attr & 1 else "") + ("volatile " if attr & 2 else "") + self.name(base, depth + 1)
        elif kind == 0x1002:
            base, attr = struct.unpack_from("<II", t, o)
            s = self.name(base, depth + 1) + (" &" if (attr >> 5) & 7 in (1, 4) else " *")
        elif kind in (0x1008, 0x1009):
            if kind == 0x1008:
                rv, _cc, _at, _n, al = struct.unpack_from("<IBBHI", t, o)
                cls = ""
            else:
                rv, ct, _this, _cc, _at, _n, al = struct.unpack_from("<IIIBBHI", t, o)
                cls = self.name(ct, depth + 1) + "::"
            s = f"{self.name(rv, depth + 1)} {cls}({self.args(al, depth + 1)})"
        elif kind in (0x1503, 0x1003):
            el, _ix = struct.unpack_from("<II", t, o)
            size, _ = numeric(t, o + 8)
            es = self.size(el)
            s = f"{self.name(el, depth + 1)}[{size // es if es else size}]"
        elif kind in AGG:
            _cnt, _prop, _fl = struct.unpack_from("<HHI", t, o)
            p = o + (8 if kind in (0x1506, 0x1006) else 16)
            _size, p = numeric(t, p)
            s = AGG[kind][1](t, p)[0]
        elif kind in (0x1507, 0x1007):
            s = (cstr if kind == 0x1507 else pstr)(t, o + 12)[0]
        elif kind == 0x1205:
            base, ln, pos = struct.unpack_from("<IBB", t, o)
            s = f"{self.name(base, depth + 1)} : {ln}"
        self._names[ti] = s
        return s

    def args(self, al, depth):
        kind, o = self.rec(al)
        if kind != 0x1201:
            return "?"
        n = struct.unpack_from("<I", self.t, o)[0]
        return ", ".join(self.name(x, depth) for x in struct.unpack_from(f"<{n}I", self.t, o + 4))

    def size(self, ti):
        if ti < self.begin:
            if ti & 0x700:
                return 4
            return {0x03: 0, 0x10: 1, 0x20: 1, 0x68: 1, 0x69: 1, 0x70: 1, 0x30: 1, 0x11: 2, 0x21: 2, 0x72: 2, 0x73: 2, 0x71: 2,
                    0x13: 8, 0x23: 8, 0x76: 8, 0x77: 8, 0x41: 8}.get(ti & 0xFF, 4)
        kind, o = self.rec(ti)
        if kind == 0x1002:
            return 4
        if kind == 0x1001:
            return self.size(struct.unpack_from("<I", self.t, o)[0])
        if kind in AGG:
            return numeric(self.t, o + (8 if kind in (0x1506, 0x1006) else 16))[0]
        if kind in (0x1503, 0x1003):
            return numeric(self.t, o + 8)[0]
        if kind in (0x1507, 0x1007):
            return self.size(struct.unpack_from("<I", self.t, o + 4)[0])
        return 0

    def members(self, fl):
        """[(offset, name, type)] of a field list's data members and base classes; None when a sub-record has no case."""
        kind, o = self.rec(fl)
        if kind != 0x1203:
            return []
        t, end, out = self.t, self.at[fl - self.begin] + 2 + struct.unpack_from("<H", self.t, self.at[fl - self.begin])[0], []
        while o + 2 <= end:
            if t[o] >= 0xF0:   # LF_PADn: n bytes of padding, this one among them
                o += (t[o] & 0x0F) or 1
                continue
            k = struct.unpack_from("<H", t, o)[0]
            if k in (0x150D, 0x1405):        # LF_MEMBER
                _attr, ty = struct.unpack_from("<HI", t, o + 2)
                off, p = numeric(t, o + 8)
                nm, p = (cstr if k == 0x150D else pstr)(t, p)
                out.append((off, nm, self.name(ty)))
            elif k == 0x1400:                 # LF_BCLASS
                _attr, ty = struct.unpack_from("<HI", t, o + 2)
                off, p = numeric(t, o + 8)
                out.append((off, "(base)", self.name(ty)))
            elif k in (0x1401, 0x1402):       # LF_VBCLASS, LF_IVBCLASS
                _, p = numeric(t, o + 12)
                _, p = numeric(t, p)
            elif k in (0x1502, 0x1403):       # LF_ENUMERATE
                _, p = numeric(t, o + 4)
                _, p = (cstr if k == 0x1502 else pstr)(t, p)
            elif k in (0x150E, 0x1406):       # LF_STMEMBER
                _, p = (cstr if k == 0x150E else pstr)(t, o + 8)
            elif k in (0x150F, 0x1407):       # LF_METHOD
                _, p = (cstr if k == 0x150F else pstr)(t, o + 8)
            elif k in (0x1510, 0x1408):       # LF_NESTTYPE
                _, p = (cstr if k == 0x1510 else pstr)(t, o + 8)
            elif k in (0x1511, 0x140B):       # LF_ONEMETHOD
                attr = struct.unpack_from("<H", t, o + 2)[0]
                p = o + 8 + (4 if (attr >> 2) & 7 in (4, 6) else 0)
                _, p = (cstr if k == 0x1511 else pstr)(t, p)
            elif k == 0x1409:                 # LF_VFUNCTAB
                p = o + 8
            elif k == 0x1404:                 # LF_INDEX: the list goes on in another record
                more = self.members(struct.unpack_from("<I", t, o + 4)[0])
                return out + (more or [])
            else:
                return out
            o = p
        return out

    def structures(self):
        """{name: {size, members}} of every complete class, structure and union, the first of each name"""
        out = {}
        for i in range(len(self.at)):
            ti = self.begin + i
            kind, o = self.rec(ti)
            if kind not in AGG:
                continue
            _cnt, prop, fl = struct.unpack_from("<HHI", self.t, o)
            if prop & 0x80:   # a forward reference
                continue
            nm = self.name(ti)
            if nm in out or nm.startswith("<unnamed"):
                continue
            out[nm] = {"kind": AGG[kind][0], "size": self.size(ti), "members": self.members(fl)}
        return out

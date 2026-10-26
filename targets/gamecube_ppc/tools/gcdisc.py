"""Read a GameCube disc image (GCM/ISO, or a WIA/RVZ file through rvz.py): disc header, file list, file extraction,
the code on it (main.dol and the REL modules, also inside Yaz0 files and RARC archives) and the workspace setup.

    python gcdisc.py info    <image.iso|.gcm|.rvz|.wia>
    python gcdisc.py list    <image> [out.tsv]
    python gcdisc.py extract <image> <out_dir> [NAME ...]   (default: main.dol; --code: main.dol and every REL file)
    python gcdisc.py scan    <image> [out.tsv]
        main.dol's sections and every REL module on the disc (loose, Yaz0-compressed, or inside a RARC archive):
        module id, sections, bss, the modules it links against
    python gcdisc.py dol     <main.dol>
        a DOL file's sections, the small-data bases its start-up code sets (r2, r13) and the GQR values it writes
    python gcdisc.py setup   <image> <disc_dir> <workspace> [--rels all|none|ID,ID...]
        everything a new DP workspace needs from the disc, with no hand steps: <disc_dir> (new) gets main.dol, the REL
        files and disc.json (header, files, sections, modules); <workspace> (new, or without a project.json) gets
        project.json (binary, binary_sha256, game_ranges, global_ranges, fixed_regs) and oracle/TcInputs.java.
        With REL modules (default: all, one per module id), <disc_dir> also gets linked.dol, link.json and seeds.txt
        (tools/gcrel.py: every module at a fixed address, its relocations applied) and the workspace works on
        linked.dol: its game and global ranges cover the modules' code and writable data, "seed_entries" names the
        REL functions Ghidra starts its analysis at

Read-only on the image. The layout (Nintendo's GameCube disc format, as documented publicly): boot.bin at 0 (game id
at 0, maker at 4, disc number at 6, version at 7, magic 0xc2339f3d at 0x1c, title at 0x20, main.dol's offset at 0x420,
the file system table's offset and size at 0x424/0x428), bi2.bin at 0x440 (region at 0x18), the apploader at 0x2440
(date, entry, size). The file system table is 12-byte entries (flags and a 24-bit name offset, then the file's disc
offset and size, or for a directory its parent and the index after its last entry), then the names. Offsets are
plain bytes (a Wii disc shifts them by 2 and is refused here).

A DOL is 7 text and 11 data sections (file offsets at 0x00, load addresses at 0x48, sizes at 0x90), then the bss
address and size and the entry point at 0xd8. A REL is a header (module id, section table, bss size, the import table
naming the modules it relocates against, module 0 being main.dol; prolog/epilog/unresolved as section and offset;
alignment from version 2), then its sections. A REL has no load address: the game's own loader places it at run time;
gcrel.py places it once, at an address recorded in link.json, so a function in a module is the same function in every
run (module id, section, offset: `gcrel.py where`).
"""
import hashlib
import json
import os
import pathlib
import shutil
import struct
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rvz  # noqa: E402

GC_MAGIC, WII_MAGIC = 0xC2339F3D, 0x5D1C9EA3
DOL_OFF, APPLOADER = 0x420, 0x2440
REGIONS = {0: "Japan", 1: "USA", 2: "Europe", 4: "Korea"}
TOOL = pathlib.Path(__file__).resolve().parents[1]


class Plain:
    """A whole-disc dump: disc offsets are file offsets."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        self.size = path.stat().st_size

    def close(self):
        self.f.close()

    def read(self, off, size):
        self.f.seek(off)
        b = self.f.read(size)
        if len(b) != size:
            raise ValueError(f"0x{off:x}+0x{size:x}: past the end of the image ({self.size} bytes)")
        return b


class Disc:
    def __init__(self, image):
        self.path = pathlib.Path(image)
        with open(self.path, "rb") as f:
            magic = f.read(4)
        try:
            self.img = rvz.Image(self.path) if magic in (b"RVZ\x01", b"WIA\x01") else Plain(self.path)
        except ValueError as e:
            raise SystemExit(str(e))
        self.size = self.img.size
        try:
            self.head = self.read(0, 0x440)
        except ValueError:
            self.head = bytes(0x440)
        why = (f"{self.path.name}: a Wii disc, not GameCube" if self.u32(0x18) == WII_MAGIC else
               f"{self.path.name}: no GameCube disc header (magic 0xc2339f3d at 0x1c)" if self.u32(0x1C) != GC_MAGIC
               else None)
        if why:
            self.img.close()
            raise SystemExit(why)

    def close(self):
        self.img.close()

    def read(self, off, size):
        return self.img.read(off, size)

    def u32(self, off):
        return struct.unpack(">I", self.head[off:off + 4])[0]

    def header(self):
        h = self.head
        t = lambda a, n: h[a:a + n].split(b"\0")[0].decode("ascii", "replace").strip()
        bi2 = self.read(0x440, 0x20)
        app = self.read(APPLOADER, 0x20)
        return {"game_id": t(0, 6), "maker": t(4, 2), "disc": h[6] + 1, "version": h[7], "title": t(0x20, 0x3E0),
                "region": REGIONS.get(struct.unpack(">I", bi2[0x18:0x1C])[0], "?"),
                "dol_offset": f"0x{self.u32(DOL_OFF):x}", "fst_offset": f"0x{self.u32(0x424):x}",
                "fst_size": f"0x{self.u32(0x428):x}",
                "apploader_date": app[:16].split(b"\0")[0].decode("ascii", "replace"),
                "apploader_entry": f"0x{struct.unpack('>I', app[0x10:0x14])[0]:08x}",
                "image_size": self.size}

    def walk(self):
        """[(path, offset, size)] of every file in the file system table, paths starting with '/'."""
        fst = self.read(self.u32(0x424), self.u32(0x428))
        count = struct.unpack(">I", fst[8:12])[0]
        names = fst[12 * count:]
        out, stack = [], [(count, "/")]  # (index after the directory's last entry, its path)
        for i in range(1, count):
            while i >= stack[-1][0]:
                stack.pop()
            w0, off, size = struct.unpack(">3I", fst[12 * i:12 * i + 12])
            name = names[w0 & 0xFFFFFF:].split(b"\0")[0].decode("shift_jis", "replace")
            if w0 >> 24:
                stack.append((size, stack[-1][1] + name + "/"))
            else:
                out.append((stack[-1][1] + name, off, size))
        return out

    def dol(self):
        off = self.u32(DOL_OFF)
        return self.read(off, dol_size(self.read(off, 0x100)))


# ---- DOL -------------------------------------------------------------------------------------------------------

def dol_size(head):
    offs = struct.unpack(">18I", head[0:72])
    sizes = struct.unpack(">18I", head[0x90:0xD8])
    return max(o + s for o, s in zip(offs, sizes) if s)


def dol_sections(dol):
    """{"text": [(index, file_offset, address, size)], "data": [...], "bss": (address, size), "entry": address}."""
    offs = struct.unpack(">18I", dol[0:72])
    addrs = struct.unpack(">18I", dol[0x48:0x90])
    sizes = struct.unpack(">18I", dol[0x90:0xD8])
    bss, bss_size, entry = struct.unpack(">3I", dol[0xD8:0xE4])
    secs = [(i, offs[i], addrs[i], sizes[i]) for i in range(18) if sizes[i]]
    return {"text": [s for s in secs if s[0] < 7], "data": [s for s in secs if s[0] >= 7],
            "bss": (bss, bss_size), "entry": entry}


def dol_word(dol, secs, addr):
    for _, off, a, size in secs["text"]:
        if a <= addr < a + size:
            return struct.unpack(">I", dol[off + addr - a:off + addr - a + 4])[0]
    return None


def text_words(dol, secs):
    for _, off, a, size in secs["text"]:
        for k in range(size // 4):
            yield a + 4 * k, struct.unpack(">I", dol[off + 4 * k:off + 4 * k + 4])[0]


def merged(ranges):
    out = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def sx16(v):
    return v - 0x10000 if v & 0x8000 else v


def constant_writes(words, regs):
    """{reg: value} for registers set by `lis rD,hi` then `ori`/`addi rD,rD,lo` (or a lone `li`) in a run of
    (address, word) pairs: how CodeWarrior's start-up code loads the stack pointer and the small-data bases."""
    val = {}
    for _, w in words:
        kind, d, v = writer(w, val)
        if kind == "const":
            val[d] = v
        elif kind:
            for r in d if isinstance(d, tuple) else (d,):
                val.pop(r, None)
    return {r: val[r] for r in regs if r in val}


def writer(w, val):
    """(kind, register, value) for an instruction that may set a general register: "const" (li, lis, and ori,
    oris, addi, addis on a known constant), "load" (lwz, lwzu, lwzx, lwzux, and lmw, which loads rD..r31: reported
    for rD), "other" (any other result; for an X-form word both rD and rA, as this scan does not decode them);
    (None, None, None) for branches, stores, mtspr and the rest."""
    op, d, a, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
    if op == 14 and a == 0:  # li rD, imm
        return "const", d, sx16(imm) & 0xFFFFFFFF
    if op == 15 and a == 0:  # lis rD, imm
        return "const", d, (imm << 16) & 0xFFFFFFFF
    if op in (24, 25) and a == d and d in val:  # ori / oris rA, rS, imm with rA = rS
        return "const", d, val[d] | (imm << (16 if op == 25 else 0))
    if op in (14, 15) and a == d and d in val:  # addi / addis rD, rD, imm
        return "const", d, (val[d] + (sx16(imm) << (16 if op == 15 else 0))) & 0xFFFFFFFF
    if op in (32, 33, 46) or (op == 31 and (w >> 1) & 0x3FF in (23, 55)):
        return "load", d, None
    if op in (7, 8, 12, 13, 14, 15, 34, 35, 40, 41, 42, 43):  # other D-form results in rD
        return "other", d, None
    if op in (20, 21, 23, 24, 25, 26, 27, 28, 29):  # rotates and logical immediates: the result is rA
        return "other", a, None
    if op == 31 and (w >> 1) & 0x3FF != 467:  # X-form: the result is rD or rA; either may be it
        return "other", (d, a), None
    return None, None, None


def start_registers(dol, secs, limit=256):
    """{"r1"/"r2"/"r13": value} the start-up code sets: the entry routine and the routines it calls with `bl`
    (CodeWarrior's __start calls __init_registers), each read up to its first `blr` or `limit` words."""
    found, seen, todo = {}, set(), [secs["entry"]]
    while todo and len(seen) < 32:
        addr = todo.pop(0)
        if addr in seen:
            continue
        seen.add(addr)
        run = []
        for k in range(limit):
            w = dol_word(dol, secs, addr + 4 * k)
            if w is None or w == 0x4E800020:  # blr
                break
            run.append((addr + 4 * k, w))
            if w >> 26 == 18 and w & 3 == 1:  # bl target
                todo.append((addr + 4 * k + ((w & 0x03FFFFFC) ^ 0x02000000) - 0x02000000) & 0xFFFFFFFF)
        for r, v in constant_writes(run, (1, 2, 13)).items():
            found.setdefault(f"r{r}", v)
        if "r2" in found and "r13" in found:
            break
    return found


def gqr_writes(dol, secs):
    """{n: {"const": set of values, "restore": count, "other": count}} for every `mtspr GQRn, rS`, by what set rS in
    the 16 words before it (straight-line code only): an immediate ("const"), a load from memory ("restore": a saved
    thread context or saved registers put back, as OSLoadContext does), or anything else ("other": an argument or a
    computed value, which this scan does not follow)."""
    out, recent = {}, []
    for addr, w in text_words(dol, secs):
        recent = (recent + [w])[-17:]
        if w >> 26 != 31 or (w >> 1) & 0x3FF != 467:  # mtspr
            continue
        x = (w >> 11) & 0x3FF
        spr = ((x & 31) << 5) | (x >> 5)
        if not 912 <= spr <= 919:
            continue
        s = (w >> 21) & 31
        prior = recent[:-1]
        cut = max([i for i, v in enumerate(prior) if v >> 26 in (16, 18, 19)] or [-1])  # a branch ends the run
        kind, val = "other", {}
        for v in prior[cut + 1:]:
            k, d, value = writer(v, val)
            if k == "const":
                val[d] = value
                if d == s:
                    kind = "const"
            elif k == "load":
                for r in range(d, 32 if v >> 26 == 46 else d + 1):
                    val.pop(r, None)
                    if r == s:
                        kind = "restore"
            elif k == "other":
                for r in d if isinstance(d, tuple) else (d,):
                    val.pop(r, None)
                    if r == s:
                        kind = "other"
        rec = out.setdefault(spr - 912, {"const": set(), "restore": 0, "other": 0})
        if kind == "const":
            rec["const"].add(val[s])
        else:
            rec[kind] += 1
    return out


def layout(dol):
    """Section names, ranges, and the fields of project.json that come from main.dol, with the rule each follows.

    CodeWarrior links .init and .text as text; .ctors, .dtors, .rodata, .data (and extab/extabindex in C++ games) as
    data before the bss; .sdata and .sdata2 as data inside the bss range, which also holds .bss (before .sdata),
    .sbss (between them) and .sbss2 (after .sdata2). r13 is _SDA_BASE_ (.sdata + 0x8000), r2 is _SDA2_BASE_ (.sdata2
    + 0x8000); both are read from the start-up code. The writable globals are .data, .bss, .sdata and .sbss."""
    secs = dol_sections(dol)
    bss, bss_size = secs["bss"]
    bss_end = bss + bss_size
    regs = start_registers(dol, secs)
    notes = []
    inside = [s for s in secs["data"] if bss <= s[2] < bss_end]
    before = [s for s in secs["data"] if s[2] + s[3] <= bss]
    by_addr = {s[2]: s for s in inside}
    sdata = by_addr.get(regs.get("r13", -1) - 0x8000)
    sdata2 = by_addr.get(regs.get("r2", -1) - 0x8000)
    if sdata is None and inside:
        sdata = inside[0]
        notes.append(".sdata taken as the first data section inside the bss (r13 does not point 0x8000 past one)")
    if sdata2 is None and len(inside) > 1:
        sdata2 = inside[1]
        notes.append(".sdata2 taken as the second data section inside the bss (r2 does not point 0x8000 past one)")
    names = {}
    for s in secs["text"]:
        names[s[0]] = ".init" if s[0] == 0 and len(secs["text"]) > 1 else ".text" if s == secs["text"][-1] \
            else f".text{s[0]}"
    data_sec = max(before, key=lambda s: s[2]) if before else None
    if data_sec and data_sec[2] + data_sec[3] != bss:
        notes.append(f".data (D{data_sec[0] - 7}) does not end at the bss start: check global_ranges")
    for s in secs["data"]:
        names[s[0]] = (".data" if s is data_sec else ".sdata" if s is sdata else ".sdata2" if s is sdata2
                       else f"D{s[0] - 7}")
    writable = []
    if data_sec:
        writable.append((data_sec[2], data_sec[2] + data_sec[3]))
    lo = bss
    for s in (sdata, sdata2):
        if s is None:
            continue
        if s[2] > lo:
            writable.append((lo, s[2]))  # .bss before .sdata, .sbss before .sdata2
        if s is sdata:
            writable.append((s[2], s[2] + s[3]))
        lo = s[2] + s[3]
    if sdata2 is None:
        writable.append((lo, bss_end))  # no .sdata2: the rest of the bss is writable
    fixed = {}
    for r in ("r2", "r13"):
        if r in regs:
            fixed[r] = f"{regs[r]:08x}"
        else:
            notes.append(f"{r}: the start-up code does not load it with lis/ori or lis/addi: set fixed_regs by hand")
    for n, rec in sorted(gqr_writes(dol, secs).items()):  # pinned when the program gives it one value only
        if len(rec["const"]) == 1 and not rec["other"]:
            fixed[f"GQR{n}"] = f"{next(iter(rec['const'])):08x}"
        elif rec["const"] or rec["other"]:
            shown = ", ".join(f"{v:08x}" for v in sorted(rec["const"])) or "no constant"
            other = f" and {rec['other']} time(s) from a value this scan cannot follow" if rec["other"] else ""
            notes.append(f"GQR{n}: not pinned: written with {shown}{other}")
    ranges = merged((s[2], s[2] + s[3]) for s in secs["text"])
    return {
        "entry": f"{secs['entry']:08x}",
        "bss": [f"{bss:08x}", f"{bss_end:08x}"],
        "sections": [{"name": names[s[0]], "kind": "text" if s[0] < 7 else "data", "index": s[0] if s[0] < 7
                      else s[0] - 7, "file_offset": f"0x{s[1]:x}", "start": f"{s[2]:08x}", "end": f"{s[2] + s[3]:08x}",
                      "size": s[3]} for s in secs["text"] + secs["data"]],
        "start_registers": {k: f"{v:08x}" for k, v in sorted(regs.items())},
        "project": {"game_ranges": [[f"{lo:08x}", f"{hi:08x}"] for lo, hi in ranges],
                    "global_ranges": [[f"{lo:08x}", f"{hi:08x}"] for lo, hi in writable if hi > lo],
                    "fixed_regs": fixed},
        "notes": notes,
    }


# ---- Yaz0, RARC, REL -------------------------------------------------------------------------------------------

def yaz0(data, limit=None):
    """Decompress Yaz0 (a 16-byte header: 'Yaz0', the decompressed size; then groups of a code byte, MSB first,
    1 = one literal byte, 0 = a back-reference: 12-bit distance - 1, and a 4-bit length - 2, or 0 and a third byte,
    length - 0x12). `limit` stops after that many output bytes."""
    if data[:4] != b"Yaz0":
        raise ValueError("not Yaz0")
    size = struct.unpack(">I", data[4:8])[0]
    end = size if limit is None else min(size, limit)
    out = bytearray()
    i = 16
    while len(out) < end:
        code = data[i]
        i += 1
        for bit in range(7, -1, -1):
            if len(out) >= end:
                break
            if code >> bit & 1:
                out.append(data[i])
                i += 1
                continue
            b1, b2 = data[i], data[i + 1]
            i += 2
            dist = ((b1 & 15) << 8 | b2) + 1
            n = b1 >> 4
            if n == 0:
                n = data[i] + 0x12
                i += 1
            else:
                n += 2
            src = len(out) - dist
            if src < 0:
                raise ValueError("Yaz0: a back-reference before the start")
            if dist >= n:
                out += out[src:src + n]
            else:
                for k in range(n):
                    out.append(out[src + k])
    return bytes(out[:end])


def rarc(data, names_only=False):
    """[(path, bytes)] of the files in a RARC archive (header 'RARC'; at 0x20 the node and entry tables and the
    names; a node is a directory: type, name, entry count, first entry; an entry is 20 bytes: index, hash, flags
    (0x02 directory) and name offset, data offset (a directory's node index) and size). `names_only`: [path] only,
    from the tables alone (rarc_tables bytes are enough)."""
    if data[:4] != b"RARC":
        raise ValueError("not RARC")
    base = 0x20
    file_data = base + struct.unpack(">I", data[0x0C:0x10])[0]
    n_nodes, node_off, _, ent_off, _, str_off = struct.unpack(">6I", data[base:base + 24])
    node_off, ent_off, str_off = base + node_off, base + ent_off, base + str_off
    name = lambda o: data[str_off + o:].split(b"\0")[0].decode("shift_jis", "replace")  # noqa: E731
    out = []

    def visit(node, prefix, depth):
        if depth > 64:
            raise ValueError("RARC: directories nested too deep")
        _, _, _, count, first = struct.unpack(">4sIHHI", data[node_off + 16 * node:node_off + 16 * node + 16])
        for e in range(first, first + count):
            _, _, tn, off, size = struct.unpack(">HHIII", data[ent_off + 20 * e:ent_off + 20 * e + 16])
            nm = name(tn & 0xFFFF)
            if nm in (".", ".."):
                continue
            if (tn >> 24) & 2:
                visit(off, prefix + nm + "/", depth + 1)
            else:
                out.append(prefix + nm if names_only else (prefix + nm, data[file_data + off:file_data + off + size]))

    if n_nodes:
        visit(0, "", 0)
    return out


def rarc_tables(head):
    """How many bytes from the start of a RARC hold its tables and names (from its first 0x40 bytes)."""
    _, _, _, _, str_size, str_off = struct.unpack(">6I", head[0x20:0x38])
    return 0x20 + str_off + str_size


REL_HEAD = 0x40


def rel_info(b):
    """The header of a REL module, or None when the bytes are not one (version 1-3, a section table inside the
    file, sections inside the file or bss)."""
    if len(b) < REL_HEAD:
        return None
    mid, _, _, nsec, sec_off, name_off, name_size, ver, bss_size, rel_off, imp_off, imp_size = \
        struct.unpack(">12I", b[:0x30])
    if not 1 <= ver <= 3 or not 0 < nsec < 64 or sec_off < REL_HEAD or sec_off + 8 * nsec > len(b):
        return None
    if imp_size % 8 or imp_off + imp_size > len(b) or rel_off > len(b):
        return None
    sections = []
    for k in range(nsec):
        off, size = struct.unpack(">2I", b[sec_off + 8 * k:sec_off + 8 * k + 8])
        if off & ~1 and (off & ~1) + size > len(b):
            return None
        sections.append({"index": k, "file_offset": f"0x{off & ~1:x}", "size": size, "exec": bool(off & 1),
                         "bss": off == 0 and size > 0})
    psec, esec, usec, bsec = b[0x30], b[0x31], b[0x32], b[0x33]
    prolog, epilog, unres = struct.unpack(">3I", b[0x34:0x40])
    imports = [struct.unpack(">2I", b[imp_off + 8 * k:imp_off + 8 * k + 8])[0] for k in range(imp_size // 8)]
    info = {"module_id": mid, "version": ver, "sections": [s for s in sections if s["size"]],
            "bss_size": bss_size, "imports": imports,
            "prolog": [psec, f"0x{prolog:x}"], "epilog": [esec, f"0x{epilog:x}"], "unresolved": [usec, f"0x{unres:x}"],
            "code_size": sum(s["size"] for s in sections if s["exec"]),
            "load_address": None}
    if ver >= 2:
        info["align"], info["bss_align"] = struct.unpack(">2I", b[0x40:0x48])
    if ver >= 3:
        info["fix_size"] = struct.unpack(">I", b[0x48:0x4C])[0]
    return info


def modules(disc, files=None):
    """[(path, how, rel_info, bytes)] for every REL on the disc: a loose .rel, a Yaz0 file holding one, or a REL
    inside a RARC archive (itself loose or Yaz0). `how` says how the bytes were held: "rel", "yaz0+rel",
    "yaz0+rarc+rel"... An archive member's path is the archive's, a colon, and its path inside."""
    out = []
    for path, off, size in sorted(files or disc.walk()):
        low = path.lower()
        if size < REL_HEAD or not low.endswith(REL_FILES + (".arc", ".carc")):
            continue
        out += held(path, disc.read(off, size), "")
    return out


REL_FILES = (".rel", ".szs", ".yaz0")


def rel_plausible(head):
    """A REL header's version (1-3), section count and section table offset, from its first 0x40 bytes."""
    nsec, sec_off = struct.unpack(">2I", head[0x0C:0x14])
    return 1 <= struct.unpack(">I", head[0x1C:0x20])[0] <= 3 and 0 < nsec < 64 and sec_off >= REL_HEAD


def held(path, b, how):
    if b[:4] == b"Yaz0":  # decompress the whole file only when it holds a REL: most .szs/.arc files are game data
        head = yaz0(b, 0x40)
        if len(head) < 0x40:
            return []
        if head[:4] == b"RARC":
            names = rarc(yaz0(b, rarc_tables(head)), names_only=True)
            if not any(n.lower().endswith(REL_FILES + (".arc",)) for n in names):
                return []
        elif not (path.lower().split(":")[-1].endswith(REL_FILES) and rel_plausible(head)):
            return []
        return held(path, yaz0(b), how + "yaz0+")
    if b[:4] == b"RARC":
        rows = []
        for inner, ib in rarc(b):
            if inner.lower().endswith(REL_FILES + (".arc",)):
                rows += held(path + ":" + inner, ib, how + "rarc+")
        return rows
    info = rel_info(b) if path.lower().split(":")[-1].endswith(REL_FILES) else None
    return [(path, how + "rel", info, b)] if info else []


def rel_name(path, how):
    """Where setup writes a REL (decompressed): files/<its disc path>, a Yaz0 file without its .szs/.yaz0 suffix, a
    REL inside an archive in a folder named after the archive."""
    if ":" not in path and how.startswith("yaz0+") and path.lower().endswith((".szs", ".yaz0")):
        path = path.rsplit(".", 1)[0]
    return local_name("files/" + path.replace(":", "/").lstrip("/"))


def local_name(rel):
    """`rel` (a path made of disc or archive names, '/' between them) when it stays inside the folder it is written
    to: no empty, '.' or '..' part, no '\\' or ':' (a drive) in one; anything else is refused."""
    if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in rel.split("/")):
        raise ValueError(f"a file name on the disc would be written outside its folder: {rel!r}")
    return rel


# ---- commands --------------------------------------------------------------------------------------------------

def sha(b, algo="sha256"):
    return hashlib.new(algo, b).hexdigest()


def disc_summary(disc):
    dol = disc.dol()
    lay = layout(dol)
    rels = modules(disc)
    return dol, lay, rels


def print_dol(dol, lay):
    print(f"main.dol  {len(dol)} bytes  sha1 {sha(dol, 'sha1')}  entry {lay['entry']}  bss {'..'.join(lay['bss'])}")
    for s in lay["sections"]:
        print(f"  {s['name']:9} {s['kind']:4} {s['start']}..{s['end']}  {s['size']:9}")
    print(f"  start-up registers: {lay['start_registers']}")
    for k, v in lay["project"].items():
        print(f"  {k:14} {json.dumps(v)}")
    for n in lay["notes"]:
        print(f"  note: {n}")


def scan(disc, out=None):
    dol, lay, rels = disc_summary(disc)
    print_dol(dol, lay)
    print(f"{len(rels)} REL modules")
    for path, how, info, b in rels:
        print(f"  {path:40} {how:14} id {info['module_id']:4}  code {info['code_size']:8}  bss {info['bss_size']:8}"
              f"  imports {info['imports']}")
    if out:
        pathlib.Path(out).write_text("path\tform\tmodule_id\tsize\tcode_size\tbss_size\timports\n" + "".join(
            f"{p}\t{h}\t{i['module_id']}\t{len(b)}\t{i['code_size']}\t{i['bss_size']}\t"
            f"{','.join(map(str, i['imports']))}\n" for p, h, i, b in rels), encoding="utf-8")


def project_fields(disc_dir, workspace, hdr, dol, lay, linked=None):
    """project.json for main.dol, or with linked = (linked DOL bytes, link record) for the linked image."""
    gid = hdr["game_id"]
    image = "linked.dol" if linked else "main.dol"
    rel = pathlib.Path(os.path.relpath(disc_dir / image, workspace)).as_posix()
    cfg = {"name": f"{hdr['title']} ({gid})", "target": "gamecube_ppc", "ghidra_name": gid.lower(),
           "binary": rel, "binary_sha256": sha(linked[0] if linked else dol)}
    cfg.update(lay["project"])
    if linked:
        cfg.update(linked[1]["project"])
        cfg["seed_entries"] = pathlib.Path(os.path.relpath(disc_dir / "seeds.txt", workspace)).as_posix()
    cfg.update({"port": 8800 + zlib.crc32(gid.encode()) % 100, "references": []})
    return cfg


def chosen(rels, which="all"):
    """The REL modules to link: one per module id (the first by disc path; a later one with the same id is a copy or
    a variant), all of them, none, or the ids listed ("12,13"); and notes on the ones left out."""
    if which == "none":
        return [], [f"REL modules not linked (--rels none): {len(rels)}"] if rels else []
    want = None if which == "all" else {int(x, 0) for x in which.split(",")}
    out, seen, notes = [], {}, []
    for r in rels:
        mid = r[2]["module_id"]
        if want is not None and mid not in want:
            continue
        if mid in seen:
            notes.append(f"{r[0]}: module id {mid} again (linked: {seen[mid]})")
            continue
        seen[mid] = r[0]
        out.append(r)
    if want is not None and want - set(seen):
        raise SystemExit(f"no REL module with id {sorted(want - set(seen))} on the disc")
    return out, notes


def setup(disc, disc_dir, workspace, which="all"):
    import gcrel  # gcrel imports this module
    disc_dir, workspace = pathlib.Path(disc_dir), pathlib.Path(workspace)
    if (workspace / "project.json").exists():
        raise SystemExit(f"{workspace / 'project.json'} exists: setup never overwrites a workspace")
    hdr = disc.header()
    files = disc.walk()
    dol, lay, rels = disc_summary(disc)
    link, notes = chosen(rels, which)
    linked = None
    if link:
        oracle = workspace / "oracle" / "TcInputs.java"
        try:  # before anything is written: a set that does not fit stops setup with nothing half done
            out, rec, seeds = gcrel.link(dol, [(rel_name(p, how), b) for p, how, _, b in link],
                                         gcrel.reserved_windows(oracle if oracle.exists() else None))
        except gcrel.LinkError as e:
            raise SystemExit(f"gcdisc setup: {e} (choose the modules with --rels ID,ID... or --rels none)")
        linked = (out, rec)
    for path, how, _, _ in rels:  # a name that would leave disc_dir stops setup before anything is written
        rel_name(path, how)
    disc_dir.mkdir(parents=True, exist_ok=False)  # a new folder: never overwrite earlier extractions
    (disc_dir / "main.dol").write_bytes(dol)
    for path, how, info, b in rels:
        dst = disc_dir / rel_name(path, how)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b)  # decompressed when the disc holds it in Yaz0 or an archive
    record = {"image": str(disc.path.resolve()), "header": hdr,
              "main_dol": {"file": "main.dol", "size": len(dol), "sha1": sha(dol, "sha1"), "sha256": sha(dol),
                           **lay},
              "rels": [{"path": p, "form": how, "file": rel_name(p, how), "size": len(b), "sha1": sha(b, "sha1"),
                        **info} for p, how, info, b in rels],
              "files": [{"path": p, "offset": f"0x{o:x}", "size": s} for p, o, s in sorted(files)]}
    if linked:
        gcrel.write_outputs(disc_dir, out, rec, seeds)
        record["linked"] = {"file": "linked.dol", "link": "link.json", "seeds": "seeds.txt", "size": len(out),
                            "sha256": rec["linked_sha256"], "modules": [m["module_id"] for m in rec["modules"]],
                            "not_linked": notes}
    (disc_dir / "disc.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = project_fields(disc_dir.resolve(), workspace.resolve(), hdr, dol, lay, linked)
    (workspace / "project.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    oracle = workspace / "oracle" / "TcInputs.java"
    if not oracle.exists():
        oracle.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TOOL / "template" / "TcInputs.java", oracle)
    print(f"{disc_dir}: main.dol ({len(dol)} bytes), {len(rels)} REL modules, disc.json ({len(files)} files)")
    print(f"{workspace / 'project.json'}: {json.dumps({k: cfg[k] for k in lay['project']})}")
    for n in lay["notes"]:
        print(f"note: {n}")
    if linked:
        print(f"{disc_dir / 'linked.dol'}: main.dol and {len(link)} REL modules ({len(seeds)} seeded functions); "
              f"where each went: link.json")
        for m in rec["modules"]:
            if m["relocations"]["against_missing"]:
                print(f"note: {m['name']} refers to modules not linked: {m['relocations']['against_missing']}")
    for n in notes:
        print(f"note: {n}")
    return cfg


def main(argv):
    if len(argv) < 2 or argv[0] not in ("info", "list", "extract", "scan", "dol", "setup"):
        raise SystemExit(__doc__)
    if argv[0] == "dol":
        dol = pathlib.Path(argv[1]).read_bytes()
        print_dol(dol, layout(dol))
        return
    disc = Disc(argv[1])
    try:
        run(disc, argv)
    finally:
        disc.close()


def run(disc, argv):
    if argv[0] == "info":
        for k, v in disc.header().items():
            print(f"{k:16} {v}")
        dol = disc.dol()
        print(f"{'main.dol':16} {len(dol)} bytes, sha1 {sha(dol, 'sha1')}")
    elif argv[0] == "scan":
        scan(disc, argv[2] if len(argv) > 2 else None)
    elif argv[0] == "setup":
        which = "all"
        if argv[4:5] == ["--rels"] and len(argv) == 6:
            which, argv = argv[5], argv[:4]
        if len(argv) != 4:
            raise SystemExit(__doc__)
        setup(disc, argv[2], argv[3], which)
    elif argv[0] == "list":
        rows = sorted(disc.walk())
        text = "".join(f"{size}\t0x{off:x}\t{path}\n" for path, off, size in rows)
        if len(argv) > 2:
            pathlib.Path(argv[2]).write_text(text, encoding="utf-8")
        print(text if len(argv) <= 2 else f"{len(rows)} files -> {argv[2]}", end="" if len(argv) <= 2 else "\n")
    else:
        out = pathlib.Path(argv[2])
        out.mkdir(parents=True, exist_ok=False)  # a new folder: never overwrite earlier extractions
        names = argv[3:] or ["main.dol"]
        files = disc.walk()
        want = set(names)
        if "--code" in want:
            want = (want - {"--code"}) | {"main.dol"} | {p.split(":")[0] for p, *_ in modules(disc, files)}
        if "main.dol" in want:
            dol = disc.dol()
            (out / "main.dol").write_bytes(dol)
            print(f"{len(dol):10}  main.dol (boot.bin 0x420)")
            want.discard("main.dol")
        lw = {w.lstrip("/").lower() for w in want}
        found = [(p, off, size) for p, off, size in files
                 if p.lstrip("/").lower() in lw or p.split("/")[-1].lower() in lw]
        for p, off, size in sorted(found):
            dst = out / local_name(p.lstrip("/"))
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(disc.read(off, size))
            print(f"{size:10}  {p}")
        missing = lw - {p.lstrip("/").lower() for p, *_ in found} - {p.split("/")[-1].lower() for p, *_ in found}
        if missing:
            raise SystemExit(f"not on the disc: {sorted(missing)}")


if __name__ == "__main__":
    main(sys.argv[1:])

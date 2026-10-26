"""The PE image inside an Xbox 360 executable: its section table and its `.pdata` function table.

DecompPipeline's own reader, written from the Microsoft PE/COFF specification (REFERENCES.md). The headers are little
endian, as on every PE, though the code is big-endian PowerPC. Split from xex.py, which uses it on the loaded image.
"""
import struct

SCN_CODE = 0x00000020
SCN_EXECUTE = 0x20000000
SCN_READ = 0x40000000
SCN_WRITE = 0x80000000
PE_MACHINE_POWERPCBE = 0x01F2


class PeError(Exception):
    pass


def le16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def le32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def pe_sections(img, base):
    """[{name, start, end, raw_end, code, execute, read, write}] from the image's PE header (little endian, as on
    every PE). start/end are virtual addresses; raw_end is where the section's file data ends (the rest is zero)."""
    if img[:2] != b"MZ":
        raise PeError("image has no MZ header")
    pe = le32(img, 0x3C)
    if img[pe:pe + 4] != b"PE\0\0":
        raise PeError("image has no PE header")
    machine, nsec = le16(img, pe + 4), le16(img, pe + 6)
    if machine != PE_MACHINE_POWERPCBE:
        raise PeError(f"PE machine 0x{machine:04x} is not big-endian PowerPC (0x01f2)")
    opt_size = le16(img, pe + 20)
    table = pe + 24 + opt_size
    out = []
    for i in range(nsec):
        o = table + 40 * i
        name = img[o:o + 8].split(b"\0")[0].decode("ascii", "replace")
        vsize, va, raw_size = le32(img, o + 8), le32(img, o + 12), le32(img, o + 16)
        ch = le32(img, o + 36)
        start = base + va
        out.append({"name": name, "start": start, "end": start + max(vsize, raw_size),
                    "raw_end": start + min(raw_size, vsize) if vsize else start + raw_size,
                    "code": bool(ch & SCN_CODE), "execute": bool(ch & SCN_EXECUTE), "read": bool(ch & SCN_READ),
                    "write": bool(ch & SCN_WRITE)})
    return out


def pe_directory(img, index):
    """(rva, size) of PE data directory `index` (3 = exception table, i.e. .pdata)."""
    pe = le32(img, 0x3C)
    opt = pe + 24
    magic = le16(img, opt)
    dirs = opt + (96 if magic == 0x10B else 112)
    count = le32(img, dirs - 4)
    if index >= count:
        return 0, 0
    return le32(img, dirs + 8 * index), le32(img, dirs + 8 * index + 4)


def pdata(img, base, sections):
    """[(begin, length in bytes, prolog length in words)] of the .pdata table. Each entry is two big-endian words:
    the function's address, then prolog length (bits 0-7), function length in words (bits 8-29), a 32-bit flag
    (bit 30) and an exception flag (bit 31)."""
    rva, size = pe_directory(img, 3)
    if not size:
        sec = next((s for s in sections if s["name"] == ".pdata"), None)
        if not sec:
            return []
        rva, size = sec["start"] - base, sec["raw_end"] - sec["start"]
    out = []
    for o in range(rva, rva + size - 7, 8):
        begin, packed = struct.unpack_from(">II", img, o)
        if begin == 0:
            break
        out.append((begin, ((packed >> 8) & 0x3FFFFF) * 4, packed & 0xFF))
    return out

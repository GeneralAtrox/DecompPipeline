"""Extract an InstallShield 5 cabinet set (data1.hdr + data1.cab, one volume) without the installer.

usage: python tools/is5cab.py HDR_FILE OUT_DIR [--list] [--only RX]

Written from the format's public description (the header's cab descriptor, file table, and raw-deflate chunks with a
2-byte length prefix). Only what Katana SDK installers use: version 5, one data volume, files neither split nor
obfuscated (such a file is reported and skipped). Each extracted file is checked against its stored MD5.
Re-runnable: a file already present with the right size and MD5 is left alone.
"""
import hashlib
import pathlib
import re
import struct
import sys
import zlib

SIGNATURE = 0x28635349  # "ISc("
FLAG_SPLIT, FLAG_OBFUSCATED, FLAG_COMPRESSED, FLAG_INVALID = 1, 2, 4, 8


def major_version(version):
    """The InstallShield major version from the common header's version word."""
    if version >> 24 == 1:
        return (version >> 12) & 0xF
    if version >> 24 in (2, 4):
        v = version & 0xFFFF
        return v // 100 if v else 0
    return 0


def _cstr(data, off):
    end = data.index(b"\0", off)
    return data[off:end].decode("latin-1")


def read_header(hdr):
    """{'files': [{'name', 'dir', 'flags', 'size', 'csize', 'offset', 'md5'}], 'dirs': [...], 'major': N}."""
    sig, version, _vol, cdo, _cds = struct.unpack_from("<5I", hdr, 0)
    if sig != SIGNATURE:
        raise ValueError("not an InstallShield header (signature)")
    major = major_version(version)
    if major != 5:
        raise ValueError(f"InstallShield version {major} is not supported (only 5)")
    p = cdo + 0x0C
    ft_off = struct.unpack_from("<I", hdr, p)[0]
    dir_count = struct.unpack_from("<I", hdr, p + 0x10)[0]  # after the table offset, 4 reserved, 2 table sizes
    file_count = struct.unpack_from("<I", hdr, p + 0x1C)[0]  # after 8 more reserved bytes
    base = cdo + ft_off
    table = struct.unpack_from(f"<{dir_count + file_count}I", hdr, base)
    dirs = [_cstr(hdr, base + table[i]) for i in range(dir_count)]
    groups = read_groups(hdr, cdo)
    files = []
    for i in range(file_count):
        q = base + table[dir_count + i]
        name_off, dir_index, flags, size, csize = struct.unpack_from("<IIHII", hdr, q)
        offset = struct.unpack_from("<I", hdr, q + 18 + 0x14)[0]
        md5 = hdr[q + 22 + 0x14:q + 22 + 0x14 + 16].hex()
        group = next((g for lo, hi, g in groups if lo <= i <= hi), "")
        files.append({"name": _cstr(hdr, base + name_off), "dir": dirs[dir_index] if dir_index < len(dirs) else "",
                      "group": group, "flags": flags, "size": size, "csize": csize, "offset": offset, "md5": md5})
    return {"major": major, "dirs": dirs, "files": files}


def read_groups(hdr, cdo):
    """[(first file, last file, group name)]: the file groups (71 hash buckets of offset lists after the descriptor's
    second file table offset); a group's files are a contiguous index range."""
    out, seen = [], set()
    bucket = cdo + 0x30 + 0x0E
    for i in range(71):
        off = struct.unpack_from("<I", hdr, bucket + 4 * i)[0]
        while off and off not in seen:
            seen.add(off)
            _name, desc, nxt = struct.unpack_from("<III", hdr, cdo + off)
            g = cdo + desc
            name = _cstr(hdr, cdo + struct.unpack_from("<I", hdr, g)[0])
            first, last = struct.unpack_from("<II", hdr, g + 4 + 0x48)
            out.append((first, last, name))
            off = nxt
    return out


def inflate_chunks(blob, expected):
    """IS5 compressed data: [u16 n][n bytes of raw deflate] repeated."""
    out = bytearray()
    p = 0
    while p + 2 <= len(blob) and len(out) < expected:
        n = struct.unpack_from("<H", blob, p)[0]
        p += 2
        d = zlib.decompressobj(-15)
        out += d.decompress(blob[p:p + n]) + d.flush()
        p += n
    return bytes(out)


def file_path(entry):
    """GROUP/DIR/NAME: the file group keeps files of different install roots with one relative path apart."""
    parts = [entry.get("group", ""), entry["dir"].replace("\\", "/").strip("/"), entry["name"]]
    return "/".join(p for p in parts if p)


def extract(hdr_path, out_dir, only=None, log=print):
    hdr_path, out_dir = pathlib.Path(hdr_path), pathlib.Path(out_dir)
    info = read_header(hdr_path.read_bytes())
    cab = hdr_path.with_suffix(".cab")
    counts = {"written": 0, "kept": 0, "skipped": 0, "bad": 0}
    rx = re.compile(only, re.I) if only else None
    with open(cab, "rb") as f:
        for e in info["files"]:
            rel = file_path(e)
            if e["flags"] & FLAG_INVALID or (rx and not rx.search(rel)):
                continue
            dest = out_dir / rel
            if dest.is_file() and dest.stat().st_size == e["size"] and \
                    hashlib.md5(dest.read_bytes()).hexdigest() == e["md5"]:
                counts["kept"] += 1
                continue
            if e["flags"] & (FLAG_SPLIT | FLAG_OBFUSCATED):
                log(f"is5cab: skipped {rel} (split or obfuscated: not supported)")
                counts["skipped"] += 1
                continue
            f.seek(e["offset"])
            blob = f.read(e["csize"])
            data = inflate_chunks(blob, e["size"]) if e["flags"] & FLAG_COMPRESSED else blob
            if len(data) != e["size"] or hashlib.md5(data).hexdigest() != e["md5"]:
                log(f"is5cab: BAD {rel} (size {len(data)} of {e['size']}, or MD5 differs)")
                counts["bad"] += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            counts["written"] += 1
    return counts


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    if "--list" in argv:
        for e in read_header(pathlib.Path(argv[0]).read_bytes())["files"]:
            print(f"{e['size']:>10} {e['flags']:#04x} {file_path(e)}")
        return 0
    counts = extract(argv[0], argv[1], only)
    print("is5cab:", " ".join(f"{k}={v}" for k, v in counts.items()))
    return 1 if counts["bad"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

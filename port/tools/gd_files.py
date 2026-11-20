"""Lists or copies files out of a Dreamcast GD-ROM image (.gdi, or a Redump .cue with raw 2352-byte tracks), read-only.

Nearest existing: runtime/gdi.c - the same reading inside port.exe, with no command line to list or copy a file; this is
its track and ISO 9660 logic in Python, for tools that read a game's data files (a per-mission table, say) without a
build. Disc data it copies goes to a folder the caller names, never into a repository.

    py -3.12 -I gd_files.py list  DISC.cue [PATTERN]          one line per file: size, path (fnmatch PATTERN, case-blind)
    py -3.12 -I gd_files.py copy  DISC.cue PATTERN OUT_DIR    copies each matching file into OUT_DIR (flat)
"""
import fnmatch
import os
import re
import struct
import sys

HD_LBA = 45000
RAW, DATA, HEAD = 2352, 2048, 16


def _tracks_cue(path):
    """Data tracks of a Redump .cue: (first lba, file, byte offset of that sector). The HD area's first file starts
    at LBA 45000 and later files follow it; INDEX 01 is the pregap kept at the start of each file."""
    base = os.path.dirname(path)
    out, file, track, data, file_lba, prev = [], None, 0, False, HD_LBA, None
    for line in open(path, encoding="latin-1"):
        line = line.strip()
        m = re.match(r'FILE "([^"]+)"', line)
        if m:
            if track >= 3 and prev is not None:
                file_lba += prev // RAW
            file = os.path.join(base, m.group(1))
            continue
        m = re.match(r"TRACK (\d+) (\S+)", line)
        if m:
            track, data = int(m.group(1)), "MODE1" in m.group(2)
            continue
        m = re.match(r"INDEX 01 (\d+):(\d+):(\d+)", line)
        if m and track >= 3:
            idx = (int(m.group(1)) * 60 + int(m.group(2))) * 75 + int(m.group(3))
            prev = os.path.getsize(file)
            if data:
                out.append((file_lba + idx, file, idx * RAW))
    return out


def _tracks_gdi(path):
    base = os.path.dirname(path)
    out = []
    for line in open(path, encoding="latin-1").read().splitlines()[1:]:
        m = re.match(r'\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+("[^"]+"|\S+)\s+(-?\d+)', line)
        if m and int(m.group(3)) == 4 and int(m.group(2)) >= HD_LBA and int(m.group(4)) == RAW:
            out.append((int(m.group(2)), os.path.join(base, m.group(5).strip('"')), int(m.group(6))))
    return out


class Disc:
    def __init__(self, path):
        tracks = _tracks_gdi(path) if path.lower().endswith(".gdi") else _tracks_cue(path)
        self.tracks = sorted(tracks)
        if not self.tracks:
            raise SystemExit(f"{path}: no high-density data track")
        self.files = {}

    def sector(self, lba):
        track = None
        for t in self.tracks:
            if t[0] <= lba:
                track = t
        first, file, offset = track
        f = self.files.get(file)
        if f is None:
            f = self.files[file] = open(file, "rb")
        f.seek(offset + (lba - first) * RAW + HEAD)
        return f.read(DATA)

    def read(self, lba, size):
        out = bytearray()
        while len(out) < size:
            out += self.sector(lba)
            lba += 1
        return bytes(out[:size])

    def walk(self):
        """(path, lba, size) for every file, from the primary volume descriptor at LBA 45016."""
        pvd = self.sector(HD_LBA + 16)
        if pvd[1:6] != b"CD001":
            raise SystemExit("no ISO 9660 volume at LBA 45016")
        root = pvd[156:190]
        stack = [("", struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0])]
        while stack:
            prefix, lba, size = stack.pop()
            block = self.read(lba, size)
            pos = 0
            while pos < len(block):
                n = block[pos]
                if n == 0:
                    pos = (pos // DATA + 1) * DATA
                    continue
                rec = block[pos:pos + n]
                pos += n
                name = rec[33:33 + rec[32]]
                if name in (b"\0", b"\1"):
                    continue
                name = name.decode("latin-1").split(";")[0]
                e_lba, e_size = struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
                path = prefix + name
                if rec[25] & 2:
                    stack.append((path + "/", e_lba, e_size))
                else:
                    yield path, e_lba, e_size


def main(argv):
    if len(argv) < 2 or argv[0] not in ("list", "copy"):
        raise SystemExit(__doc__)
    disc = Disc(argv[1])
    pattern = (argv[2] if len(argv) > 2 else "*").upper()
    hits = sorted(f for f in disc.walk() if fnmatch.fnmatch(f[0].upper(), pattern))
    if argv[0] == "list":
        for path, _, size in hits:
            print(f"{size}\t{path}")
        return 0
    if len(argv) < 4:
        raise SystemExit(__doc__)
    os.makedirs(argv[3], exist_ok=True)
    for path, lba, size in hits:
        with open(os.path.join(argv[3], path.rsplit("/", 1)[-1]), "wb") as f:
            f.write(disc.read(lba, size))
    print(f"copied {len(hits)} files to {argv[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

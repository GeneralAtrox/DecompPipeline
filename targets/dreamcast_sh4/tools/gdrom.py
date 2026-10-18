"""Read a Dreamcast GD-ROM image (Redump .cue or .gdi): boot header, file list, and file extraction.

    python gdrom.py info    <image.cue|.gdi>
    python gdrom.py list    <image.cue|.gdi> [out.tsv]
    python gdrom.py extract <image.cue|.gdi> <out_dir> [NAME ...]   (default: the boot file named in IP.BIN)
    python gdrom.py scan    <image.cue|.gdi> [out.tsv]
        every file scored as SH-4 code (sts.l pr,@-r15 and lds.l @r15+,pr per KB): the boot file plus any overlay or second
        program to look at; for each code file the link form (pointers into it as 0x8c... P1 or 0x0c... P0: a P0
        program needs project.json "seed_functions": true) and the library version strings it holds

Read-only on the image. The game files live in the high-density area, whose first data track starts at LBA
45000; IP.BIN is its first 16 sectors and the ISO 9660 volume descriptor is at 45016. Directory records hold
absolute LBAs. Sectors are raw 2352-byte Mode 1 (16-byte header, 2048 bytes of data); the boot file is not
scrambled on GD-ROM (scrambling only applies to MIL-CD boots).
"""
import pathlib
import re
import shlex
import struct
import sys

HD_LBA = 45000
RAW, DATA, HEAD = 2352, 2048, 16


def tracks(image):
    """[(number, start_lba, path, byte_offset)] of the data tracks in the high-density area, in order: the track
    starts (INDEX 01) at start_lba, found byte_offset bytes into its file (Redump keeps a 225-sector pregap in
    every HD track after the first)."""
    image = pathlib.Path(image)
    out = []
    if image.suffix.lower() == ".gdi":  # number lba type sector_size file offset
        lines = image.read_text(encoding="utf-8").splitlines()[1:]
        for ln in lines:
            f = shlex.split(ln, posix=False)
            if len(f) >= 5 and f[2] == "4" and int(f[1]) >= HD_LBA:
                p = pathlib.Path(f[4].strip('"'))
                off = int(f[5]) if len(f) > 5 else 0
                out.append((int(f[0]), int(f[1]), p if p.is_absolute() else image.parent / p, off))
    else:  # Redump cue: one FILE per track; the HD area's first file starts at LBA 45000, later files follow
        cur, entries = None, []
        for ln in image.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*FILE\s+"(.+)"\s+BINARY', ln)
            if m:
                cur = image.parent / m.group(1)
            m = re.match(r"\s*TRACK\s+(\d+)\s+(\S+)", ln)
            if m:
                entries.append([int(m.group(1)), m.group(2), cur, 0])
            m = re.match(r"\s*INDEX\s+01\s+(\d+):(\d+):(\d+)", ln)
            if m and entries:
                mm, ss, ff = map(int, m.groups())
                entries[-1][3] = (mm * 60 + ss) * 75 + ff  # frames into this file
        file_lba = HD_LBA
        for n, mode, p, idx in (e for e in entries if e[0] >= 3):
            if mode.startswith("MODE"):
                out.append((n, file_lba + idx, p, idx * RAW))
            file_lba += p.stat().st_size // RAW
    if not out:
        raise SystemExit("no high-density data track found")
    return out


class Disc:
    def __init__(self, image):
        self.tracks = tracks(image)
        self.files = {}

    def sector(self, lba):
        for n, start, p, off in reversed(self.tracks):
            if lba >= start:
                f = self.files.get(p) or self.files.setdefault(p, open(p, "rb"))
                f.seek(off + (lba - start) * RAW)
                raw = f.read(RAW)
                if len(raw) != RAW or raw[0:12] != b"\x00" + b"\xff" * 10 + b"\x00" or raw[15] != 1:
                    raise ValueError(f"LBA {lba}: not a Mode 1 data sector in track {n}")
                return raw[HEAD:HEAD + DATA]
        raise ValueError(f"LBA {lba} is before the high-density area")

    def read(self, lba, size):
        buf = bytearray()
        while len(buf) < size:
            buf += self.sector(lba)
            lba += 1
        return bytes(buf[:size])

    def ip(self):
        b = self.read(HD_LBA, 16 * DATA)
        t = lambda a, n: b[a:a + n].decode("ascii", "replace").strip()
        return {"hardware": t(0x00, 16), "maker": t(0x10, 16), "device": t(0x20, 16), "area": t(0x30, 8),
                "peripherals": t(0x38, 8), "product": t(0x40, 10), "version": t(0x4A, 6), "date": t(0x50, 16),
                "boot_file": t(0x60, 16), "company": t(0x70, 16), "title": t(0x80, 128)}

    def walk(self):
        """[(path, lba, size)] of every file, from the ISO 9660 primary volume descriptor."""
        pvd = self.read(HD_LBA + 16, DATA)
        if pvd[1:6] != b"CD001":
            raise ValueError("no ISO 9660 volume descriptor at LBA 45016")
        out = []

        def visit(lba, size, prefix):
            d = self.read(lba, size)
            i = 0
            while i < len(d):
                n = d[i]
                if n == 0:  # records do not cross sector boundaries
                    i = (i // DATA + 1) * DATA
                    continue
                r = d[i:i + n]
                ext, ln, flags = int.from_bytes(r[2:6], "little"), int.from_bytes(r[10:14], "little"), r[25]
                name = r[33:33 + r[32]]
                i += n
                if name in (b"\x00", b"\x01"):
                    continue
                name = name.decode("ascii", "replace").split(";")[0]
                if flags & 2:
                    visit(ext, ln, prefix + name + "/")
                else:
                    out.append((prefix + name, ext, ln))

        root = pvd[156:190]
        visit(int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little"), "")
        return out


SCAN_MAX = 64 << 20  # bigger files are video or archives
CODE_PER_KB = 0.3  # sts.l pr,@-r15 + lds.l @r15+,pr words per KB: data stays under 0.1, code is above 0.5
VERSION_RE = re.compile(rb"[ -~]{0,40}(?:\bVer(?:sion)?\b\.?\s*\d|Copyright|\(C\))[ -~]{0,60}", re.I)


def code_score(data, base=0x8C010000):
    """(prologue + epilogue words per KB, P1 pointers, P0 pointers, version strings) for one file's bytes."""
    n = len(data) // 2
    words = struct.unpack(f"<{n}H", data[:2 * n])
    kb = max(len(data), 1) / 1024
    score = (words.count(0x4F22) + words.count(0x4F26)) / kb
    end = base + len(data)
    longs = struct.unpack(f"<{len(data) // 4}I", data[:len(data) // 4 * 4])
    p1 = sum(1 for x in longs if base <= x < end)
    p0 = sum(1 for x in longs if base - 0x80000000 <= x < end - 0x80000000)
    vers = sorted({m.group(0).decode("ascii").strip() for m in VERSION_RE.finditer(data)})
    return score, p1, p0, vers


def scan(disc, out=None):
    boot = disc.ip()["boot_file"]
    rows = []
    for path, lba, size in sorted(disc.walk()):
        if size == 0 or size > SCAN_MAX:
            continue
        score, p1, p0, vers = code_score(disc.read(lba, size))
        rows.append((path, size, score, p1, p0, vers))
    code = [r for r in rows if r[2] >= CODE_PER_KB or r[0] == boot]
    print(f"{len(rows)} files scanned (larger than {SCAN_MAX >> 20} MB skipped); {len(code)} look like SH-4 code:")
    for path, size, score, p1, p0, vers in sorted(code, key=lambda r: -r[2]):
        form = "P0 (0x0c...): seed_functions" if p0 > 4 * max(p1, 1) else "P1 (0x8c...)" if p1 > 4 * max(p0, 1) else "?"
        print(f"  {'BOOT ' if path == boot else '     '}{path:28} {size:9}  {score:5.2f}/KB  linked {form}"
              f"  (pointers P1 {p1}, P0 {p0})")
        for v in vers[:40]:
            print(f"        {v}")
    if out:
        pathlib.Path(out).write_text("path\tsize\tcode_per_kb\tp1\tp0\n" + "".join(
            f"{p}\t{s}\t{c:.3f}\t{a}\t{b}\n" for p, s, c, a, b, _ in rows), encoding="utf-8")


def main(argv):
    if len(argv) < 2 or argv[0] not in ("info", "list", "extract", "scan"):
        raise SystemExit(__doc__)
    disc = Disc(argv[1])
    if argv[0] == "info":
        for k, v in disc.ip().items():
            print(f"{k:12} {v}")
        print(f"{'tracks':12} {[(n, lba, p.name) for n, lba, p, _ in disc.tracks]}")
    elif argv[0] == "scan":
        scan(disc, argv[2] if len(argv) > 2 else None)
    elif argv[0] == "list":
        rows = sorted(disc.walk())
        text = "".join(f"{size}\t{lba}\t{path}\n" for path, lba, size in rows)
        if len(argv) > 2:
            pathlib.Path(argv[2]).write_text(text, encoding="utf-8")
        print(text if len(argv) <= 2 else f"{len(rows)} files -> {argv[2]}", end="" if len(argv) <= 2 else "\n")
    else:
        out = pathlib.Path(argv[2])
        out.mkdir(parents=True, exist_ok=False)  # a new folder: never overwrite earlier extractions
        want = set(argv[3:]) or {disc.ip()["boot_file"]}
        found = {p: (lba, size) for p, lba, size in disc.walk() if p in want or p.split("/")[-1] in want}
        for p, (lba, size) in found.items():
            dst = out / p
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(disc.read(lba, size))
            print(f"{size:10}  {p}")
        missing = want - set(found) - {p.split("/")[-1] for p in found}
        if missing:
            raise SystemExit(f"not on the disc: {sorted(missing)}")


if __name__ == "__main__":
    main(sys.argv[1:])

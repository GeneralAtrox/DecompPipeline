"""Read a Sega Saturn CD image (Redump .cue, or a raw 2352-byte / cooked 2048-byte track 1): boot header, file
list, file extraction and a code scan.

    python satdisc.py info    <image.cue|.bin|.iso>
    python satdisc.py list    <image> [out.tsv]
    python satdisc.py extract <image> <out_dir> [NAME ...]   (default: the first-read file; --code: every scan hit)
    python satdisc.py scan    <image> [out.tsv]
        every file scored as SH-2 code (big-endian sts.l pr,@-r15 / lds.l @r15+,pr / rts words per KB) with the
        pointers it holds into work RAM high (0x060xxxxx) and low (0x002xxxxx), and the library strings in it

Read-only on the image. The data track is Mode 1 (raw 2352-byte sectors: 16-byte header, 2048 bytes of data;
streamed files may be Mode 2 Form 1, read past the 8-byte subheader, or XA Form 2, skipped) and starts at the first byte of track 1's file; ISO 9660 extents count from there. IP.BIN is the first 16 sectors
(Sega's "Disc format standards": hardware id, maker, product, version, date, device, area, peripherals, title;
at 0xe0 the IP size, 0xe8/0xec the master and slave stacks, 0xf0/0xf4 the first-read address and size). The BIOS
loads the first file of the root directory (the record after "." and "..") to the first-read address.
"""
import pathlib
import re
import struct
import sys

RAW, DATA, HEAD = 2352, 2048, 16
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def data_track(image):
    """(path, sector_size) of the image's first data track."""
    image = pathlib.Path(image)
    if image.suffix.lower() == ".cue":
        cur = None
        for ln in image.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*FILE\s+"(.+)"\s+BINARY', ln)
            if m:
                cur = image.parent / m.group(1)
            m = re.match(r"\s*TRACK\s+\d+\s+MODE1/(2352|2048)", ln)
            if m and cur:
                return cur, int(m.group(1))
        raise SystemExit("no MODE1 data track in the cue sheet")
    with open(image, "rb") as f:
        head = f.read(16)
    return image, RAW if head[:12] == SYNC else DATA


class Disc:
    def __init__(self, image):
        self.path, self.ss = data_track(image)
        self.f = open(self.path, "rb")

    def sector(self, lba):
        self.f.seek(lba * self.ss)
        raw = self.f.read(self.ss)
        if self.ss == DATA:
            if len(raw) != DATA:
                raise ValueError(f"LBA {lba}: past the end of the track")
            return raw
        if len(raw) != RAW or raw[:12] != SYNC or raw[15] not in (1, 2):
            raise ValueError(f"LBA {lba}: not a data sector")
        if raw[15] == 1:
            return raw[HEAD:HEAD + DATA]
        if raw[18] & 0x20:  # Mode 2 Form 2 (2324-byte XA audio/video): not file data this tool reads
            raise ValueError(f"LBA {lba}: a Mode 2 Form 2 sector (XA stream)")
        return raw[24:24 + DATA]  # Mode 2 Form 1: 8-byte subheader

    def read(self, lba, size):
        buf = bytearray()
        while len(buf) < size:
            buf += self.sector(lba)
            lba += 1
        return bytes(buf[:size])

    def ip(self):
        b = self.read(0, 16 * DATA)
        if b[:16] != b"SEGA SEGASATURN ":
            raise ValueError("no Saturn IP.BIN (hardware id 'SEGA SEGASATURN ') at LBA 0")
        t = lambda a, n: b[a:a + n].decode("ascii", "replace").strip()
        u = lambda a: struct.unpack(">I", b[a:a + 4])[0]
        return {"hardware": t(0x00, 16), "maker": t(0x10, 16), "product": t(0x20, 10), "version": t(0x2A, 6),
                "date": t(0x30, 8), "device": t(0x38, 8), "area": t(0x40, 10), "peripherals": t(0x50, 16),
                "title": t(0x60, 112), "ip_size": f"0x{u(0xE0):x}", "stack_master": f"0x{u(0xE8):08x}",
                "stack_slave": f"0x{u(0xEC):08x}", "first_read_addr": f"0x{u(0xF0):08x}",
                "first_read_size": f"0x{u(0xF4):x}", "first_read_file": self.first_file()}

    def _records(self, lba, size):
        d = self.read(lba, size)
        i = 0
        while i < len(d):
            n = d[i]
            if n == 0:  # records do not cross sector boundaries
                i = (i // DATA + 1) * DATA
                continue
            r = d[i:i + n]
            i += n
            name = r[33:33 + r[32]]
            if name in (b"\x00", b"\x01"):
                continue
            yield (name.decode("ascii", "replace").split(";")[0], int.from_bytes(r[2:6], "little"),
                   int.from_bytes(r[10:14], "little"), bool(r[25] & 2))

    def _root(self):
        pvd = self.read(16, DATA)
        if pvd[1:6] != b"CD001":
            raise ValueError("no ISO 9660 volume descriptor at LBA 16")
        root = pvd[156:190]
        return int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little")

    def first_file(self):
        """The root directory's first file record: what the BIOS loads to the first-read address."""
        for name, _, _, is_dir in self._records(*self._root()):
            if not is_dir:
                return "/" + name
        return None

    def walk(self):
        """[(path, lba, size)] of every file, paths starting with '/'."""
        out = []

        def visit(lba, size, prefix):
            for name, ext, ln, is_dir in self._records(lba, size):
                if is_dir:
                    visit(ext, ln, prefix + name + "/")
                else:
                    out.append((prefix + name, ext, ln))

        visit(*self._root(), "/")
        return out


SCAN_MAX = 4 << 20  # bigger files are video, sound or archives (Grandia's largest overlay is 291 KB)
CODE_PER_KB = 2.5  # (sts.l pr,@-r15 + lds.l @r15+,pr + rts) words per KB: code is 3-22 (Grandia), data under 2
CODE_PTRS = 50  # and at least this many pointers into work RAM high: text files scoring 3/KB hold none
LIB_RE = re.compile(rb"[ -~]{0,40}(?:\bVer(?:sion)?\b\.?\s*\d|Copyright|\(C\)|SEGA|SGL|SBL|GNU|Hitachi)[ -~]{0,60}")


def code_score(data):
    """(prologue+epilogue+rts words per KB, high-RAM pointers, low-RAM pointers, library strings) for one file."""
    n = len(data) // 2
    words = struct.unpack(f">{n}H", data[:2 * n])
    kb = max(len(data), 1) / 1024
    score = (words.count(0x4F22) + words.count(0x4F26) + words.count(0x000B)) / kb
    longs = struct.unpack(f">{len(data) // 4}I", data[:len(data) // 4 * 4])
    hi = sum(1 for x in longs if 0x06000000 <= x < 0x06100000)
    lo = sum(1 for x in longs if 0x00200000 <= x < 0x00300000)
    libs = sorted({m.group(0).decode("ascii").strip() for m in LIB_RE.finditer(data)})
    return score, hi, lo, libs


def scan_rows(disc):
    rows = []
    for path, lba, size in sorted(disc.walk()):
        if 0 < size <= SCAN_MAX:
            try:
                rows.append((path, lba, size) + code_score(disc.read(lba, size)))
            except ValueError:  # an XA stream (Mode 2 Form 2): not code
                pass
    return rows


def is_code(row):
    return row[3] >= CODE_PER_KB and row[4] >= CODE_PTRS


def scan(disc, out=None):
    first = disc.first_file()
    rows = scan_rows(disc)
    code = [r for r in rows if is_code(r) or r[0] == first]
    print(f"{len(rows)} files scanned (larger than {SCAN_MAX >> 20} MB skipped); {len(code)} look like SH-2 code:")
    for path, lba, size, score, hi, lo, libs in sorted(code, key=lambda r: -r[2]):
        print(f"  {'FIRST ' if path == first else '      '}{path:24} {size:8}  {score:6.2f}/KB"
              f"  pointers high {hi}, low {lo}")
        for v in libs[:12]:
            print(f"          {v}")
    if out:
        pathlib.Path(out).write_text("path\tlba\tsize\tcode_per_kb\tptr_high\tptr_low\n" + "".join(
            f"{p}\t{l}\t{s}\t{c:.3f}\t{a}\t{b}\n" for p, l, s, c, a, b, _ in rows), encoding="utf-8")


def main(argv):
    if len(argv) < 2 or argv[0] not in ("info", "list", "extract", "scan"):
        raise SystemExit(__doc__)
    disc = Disc(argv[1])
    try:
        run(disc, argv)
    finally:
        disc.f.close()


def run(disc, argv):
    if argv[0] == "info":
        for k, v in disc.ip().items():
            print(f"{k:16} {v}")
        print(f"{'track':16} {disc.path.name} ({disc.ss}-byte sectors)")
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
        names = argv[3:]
        if names == ["--code"]:
            first = disc.first_file()
            want = {r[0] for r in scan_rows(disc) if is_code(r) or r[0] == first}
        else:
            want = {n if n.startswith("/") else "/" + n for n in names} if names else {disc.first_file()}
        found = {p: (lba, size) for p, lba, size in disc.walk()
                 if p.upper() in {w.upper() for w in want} or p.split("/")[-1].upper() in {w.strip("/").upper() for w in want}}
        for p, (lba, size) in sorted(found.items()):
            dst = out / p.lstrip("/")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(disc.read(lba, size))
            print(f"{size:10}  {p}")
        missing = {w for w in want if w.upper() not in {p.upper() for p in found}
                   and w.strip("/").upper() not in {p.split("/")[-1].upper() for p in found}}
        if missing:
            raise SystemExit(f"not on the disc: {sorted(missing)}")


if __name__ == "__main__":
    main(sys.argv[1:])

"""Xbox 360 game discs (XDVDFS): list the files of a disc image and extract one (default.xex).

DecompPipeline's own reader, from the published XDVDFS layout (REFERENCES.md). A disc image holds the game
partition at a fixed offset: 0 for a bare XISO, 0x18300000 (XGD1), 0xFD90000 (XGD2, Too Human) or 0x2080000 (XGD3).
The partition's volume descriptor (sector 32) starts and ends with "MICROSOFT*XBOX*MEDIA" and names the root
directory. A directory is a binary tree of entries, each: left and right child offsets (16-bit, in 4-byte units
from the directory's start, 0 = none), start sector, size, attributes (0x10 = directory), name length, name;
entries are 4-byte aligned. All little endian; sectors are 2048 bytes from the partition start.

usage: python xiso.py <disc.iso> [list | extract <name> <out_file>]
"""
import pathlib
import struct
import sys

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
PARTITIONS = (0, 0x18300000, 0x0FD90000, 0x02080000)
ATTR_DIR = 0x10


class DiscError(Exception):
    pass


class Disc:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.fh = open(self.path, "rb")
        self.base = None
        for off in PARTITIONS:
            vd = self._read_abs(off + 32 * SECTOR, SECTOR)
            if vd[:20] == MAGIC and vd[0x7EC:0x7EC + 20] == MAGIC:
                self.base = off
                self.root_sector, self.root_size = struct.unpack_from("<II", vd, 20)
                break
        if self.base is None:
            self.fh.close()
            raise DiscError(f"{self.path.name}: no XDVDFS volume descriptor at any known partition offset")

    def close(self):
        self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read_abs(self, off, size):
        self.fh.seek(off)
        return self.fh.read(size)

    def read(self, sector, size):
        return self._read_abs(self.base + sector * SECTOR, size)

    def entries(self, sector=None, size=None, prefix=""):
        """Every entry below a directory (the root by default), depth first: (path, sector, size, is_dir)."""
        sector = self.root_sector if sector is None else sector
        size = self.root_size if size is None else size
        if size == 0:
            return []
        table = self.read(sector, size)
        out, stack, seen = [], [0], set()
        while stack:
            off = stack.pop()
            if off in seen or off + 14 > len(table):
                continue
            seen.add(off)
            left, right, start, length, attr, nlen = struct.unpack_from("<HHIIBB", table, off)
            if left == 0xFFFF:  # sector padding
                continue
            name = table[off + 14:off + 14 + nlen].decode("ascii", "replace")
            path = f"{prefix}{name}"
            out.append((path, start, length, bool(attr & ATTR_DIR)))
            if attr & ATTR_DIR:
                out += self.entries(start, length, path + "/")
            if right:
                stack.append(4 * right)
            if left:
                stack.append(4 * left)
        return out

    def find(self, name):
        """The entry whose path matches name, ignoring case (XDVDFS names are case-insensitive)."""
        for e in self.entries():
            if e[0].lower() == name.lower().replace("\\", "/"):
                return e
        raise DiscError(f"{self.path.name}: no file {name}")

    def extract(self, name, out_file):
        path, sector, size, is_dir = self.find(name)
        if is_dir:
            raise DiscError(f"{path} is a directory")
        out = pathlib.Path(out_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as fo:
            done = 0
            while done < size:
                n = min(8 << 20, size - done)
                fo.write(self._read_abs(self.base + sector * SECTOR + done, n))
                done += n
        return size


def main(argv):
    if len(argv) < 2 or (len(argv) > 2 and argv[2] not in ("list", "extract")):
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    try:
        with Disc(argv[1]) as d:
            if len(argv) == 2 or argv[2] == "list":
                print(f"partition at 0x{d.base:x}")
                for path, sector, size, is_dir in d.entries():
                    print(f"{'d' if is_dir else '-'} {size:>10} {path}")
            else:
                n = d.extract(argv[3], argv[4])
                print(f"xiso: wrote {argv[4]} ({n} bytes)")
    except DiscError as e:
        print(f"xiso: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

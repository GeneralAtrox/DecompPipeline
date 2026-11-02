"""Xbox 360 STFS packages (Xbox Live Arcade, Arcade titles, DLC: magic LIVE / PIRS / CON): list the files and extract one.

DecompPipeline's own reader, from the published STFS layout (REFERENCES.md). The header holds the volume descriptor
(at 0x379): the block separation byte (bit 0 clear = two hash blocks between runs, set = one), the file table's block
count (16-bit) and first block (24-bit), both little endian. Data is in 4 KB blocks starting at the header size rounded
up to 4 KB; after every 0xAA data blocks comes a hash block (0x18 bytes per block: a 20-byte hash, a status byte and
the 24-bit big-endian number of the next block of the file), a level-1 block after every 0x70E4, a level-2 after
every 0x4AF768. The file table is 0x40-byte entries: name (0x28), flags (bits 0-5 name length, bit 6 blocks are
consecutive, bit 7 directory), block count (24-bit LE at 0x29), start block (24-bit LE at 0x2F), parent entry
(16-bit BE at 0x32, 0xFFFF = root), size (32-bit BE at 0x34). Blocks of a file that are not consecutive are chained
through the hash blocks.

usage: python stfs.py <package> [list | extract <path in package> <out_file>]
Nearest existing: xiso.py - it reads the XDVDFS of a game disc; an Arcade title ships as this container instead.
"""
import pathlib
import struct
import sys

BLOCK = 0x1000
PER_HASH = 0xAA
PER_L1 = 0x70E4
PER_L2 = 0x4AF768


class PackageError(Exception):
    pass


class Package:
    def __init__(self, path):
        self.fh = open(path, "rb")
        head = self.fh.read(0x400)
        if head[:4] not in (b"LIVE", b"PIRS", b"CON "):
            raise PackageError(f"{path}: not an STFS package (magic {head[:4]!r})")
        self.magic = head[:4]
        self.header_size = struct.unpack_from(">I", head, 0x340)[0]
        self.first = (self.header_size + 0xFFF) & 0xFFFFF000
        d = 0x379
        if head[d] != 0x24:
            raise PackageError(f"unexpected volume descriptor size {head[d]:#x}")
        self.separation = head[d + 2]
        self.ft_blocks = struct.unpack_from("<H", head, d + 3)[0]
        self.ft_first = int.from_bytes(head[d + 5:d + 8], "little")
        self.allocated = struct.unpack_from(">I", head, d + 0x1C)[0]
        self.sex = 0 if self.separation & 1 else 1  # hash blocks per run: read-only layout 1, else 2
        self.files = self._table()

    # -- block addressing
    def data_block(self, n):
        """The backing block index of data block n (hash blocks counted)."""
        sex = self.sex
        r = (((n + PER_HASH) // PER_HASH) << sex) + n
        if n < PER_HASH:
            return r
        if n < PER_L1:
            return r + (((n + PER_L1) // PER_L1) << sex)
        return (1 << sex) + r + (((n + PER_L1) // PER_L1) << sex)

    def level0_hash_block(self, n):
        """The backing block index of the hash block that holds data block n's entry."""
        if n < PER_HASH:
            return 0
        r = (n // PER_HASH) * ((1 << self.sex) + PER_HASH)
        r += ((n // PER_L1) + 1) << self.sex
        if n // PER_L1 == 0:
            return r
        return r + (1 << self.sex)

    def read_block(self, n):
        self.fh.seek(self.first + (self.data_block(n) << 12))
        return self.fh.read(BLOCK)

    def next_block(self, n):
        h = self.level0_hash_block(n)
        self.fh.seek(self.first + (h << 12) + (n % PER_HASH) * 0x18 + 0x15)
        return int.from_bytes(self.fh.read(3), "big")

    def chain(self, start, count, consecutive):
        if consecutive:
            return list(range(start, start + count))
        out, n = [], start
        while len(out) < count and n != 0xFFFFFF:
            out.append(n)
            n = self.next_block(n)
        return out

    # -- file table
    def _table(self):
        blocks = self.chain(self.ft_first, self.ft_blocks, True)
        raw = b"".join(self.read_block(b) for b in blocks)
        entries = []
        for i in range(0, len(raw) - 0x3F, 0x40):
            e = raw[i:i + 0x40]
            flags = e[0x28]
            ln = flags & 0x3F
            if ln == 0:
                break
            name = e[:ln].decode("latin-1")
            entries.append({
                "name": name, "dir": bool(flags & 0x80), "consecutive": bool(flags & 0x40),
                "blocks": int.from_bytes(e[0x29:0x2C], "little"), "start": int.from_bytes(e[0x2F:0x32], "little"),
                "parent": struct.unpack_from(">h", e, 0x32)[0], "size": struct.unpack_from(">I", e, 0x34)[0]})
        for e in entries:
            parts, p = [e["name"]], e["parent"]
            while p >= 0:
                parts.append(entries[p]["name"])
                p = entries[p]["parent"]
            e["path"] = "/".join(reversed(parts))
        return entries

    def read(self, entry):
        out = bytearray()
        for b in self.chain(entry["start"], entry["blocks"], entry["consecutive"]):
            out += self.read_block(b)
        return bytes(out[:entry["size"]])

    def find(self, path):
        want = path.replace("\\", "/").lower()
        for e in self.files:
            if not e["dir"] and e["path"].lower() == want:
                return e
        raise PackageError(f"{path}: not in the package")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pkg = Package(argv[1])
    cmd = argv[2] if len(argv) > 2 else "list"
    if cmd == "list":
        for e in pkg.files:
            print(f"{'d' if e['dir'] else '-'} {e['size']:>10}  {e['path']}")
        return 0
    if cmd == "extract" and len(argv) == 5:
        pathlib.Path(argv[4]).write_bytes(pkg.read(pkg.find(argv[3])))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

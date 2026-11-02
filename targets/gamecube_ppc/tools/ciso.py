"""Read a CISO file (a GameCube disc with its empty 2 MiB blocks left out) as the whole disc image.

Header: "CISO", the block size (u32, little endian), then a one-byte-per-block map up to 0x8000 (1 = the block is in the
file, 0 = it is all zeros). The present blocks follow the header in order. Image(path).read(offset, size) returns the
disc's bytes, an absent block as zeros, like rvz.Image and gcdisc.Plain. Read-only.

Nearest existing: rvz.py - it reads WIA/RVZ, another container; CISO has no compression, only absent blocks.
"""
import pathlib
import struct

HEADER = 0x8000


class Image:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.f = open(self.path, "rb")
        head = self.f.read(HEADER)
        if len(head) < HEADER or head[:4] != b"CISO":
            self.f.close()
            raise ValueError(f"{self.path.name}: not a CISO file")
        self.block = struct.unpack("<I", head[4:8])[0]
        if self.block < 0x800 or self.block & (self.block - 1):
            self.f.close()
            raise ValueError(f"{self.path.name}: CISO block size {self.block} is not a power of two of at least 2 KiB")
        self.at, n = {}, 0  # block index -> offset in the file
        for i, m in enumerate(head[8:]):
            if m == 1:
                self.at[i] = HEADER + n * self.block
                n += 1
            elif m != 0:
                self.f.close()
                raise ValueError(f"{self.path.name}: CISO block map entry {i} is {m}, not 0 or 1")
        self.size = (max(self.at) + 1) * self.block if self.at else 0

    def close(self):
        self.f.close()

    def read(self, off, size):
        if off < 0 or off + size > self.size:
            raise ValueError(f"0x{off:x}+0x{size:x}: past the end of the image ({self.size} bytes)")
        out = bytearray()
        while size > 0:
            k, o = divmod(off, self.block)
            n = min(size, self.block - o)
            if k in self.at:
                self.f.seek(self.at[k] + o)
                b = self.f.read(n)
                out += b + bytes(n - len(b))  # the last block may be cut short in the file
            else:
                out += bytes(n)
            off, size = off + n, size - n
        return bytes(out)

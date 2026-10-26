"""A GameCube DOL executable as a big-endian address space: the 7 text and 11 data sections at their load
addresses (the DOL header layout: 18 file offsets at 0x00, 18 load addresses at 0x48, 18 sizes at 0x90)."""
from __future__ import annotations

import struct

from .decode import decode


class ImageError(Exception):
    pass


class Dol:
    def __init__(self, data: bytes):
        self.sections = []          # (addr, bytes, is_text)
        for k in range(18):
            off, addr, size = (struct.unpack_from(">I", data, base + 4 * k)[0] for base in (0, 0x48, 0x90))
            if size:
                self.sections.append((addr, data[off:off + size], k < 7))
        self.bss = struct.unpack_from(">II", data, 0xD8)
        self.entry = struct.unpack_from(">I", data, 0xE0)[0]
        self._cache = {}

    def find(self, a, n=1):
        for base, data, text in self.sections:
            if base <= a and a + n <= base + len(data):
                return base, data, text
        return None

    def has(self, a, n=1):
        return self.find(a, n) is not None

    def is_text(self, a):
        s = self.find(a, 4)
        return bool(s and s[2])

    def u32(self, a):
        s = self.find(a, 4)
        if s is None:
            raise ImageError(f"read outside the image at {a:08x}")
        base, data, _ = s
        return struct.unpack_from(">I", data, a - base)[0]

    def insn(self, a):
        i = self._cache.get(a)
        if i is None:
            i = decode(self.u32(a), a)
            self._cache[a] = i
        return i

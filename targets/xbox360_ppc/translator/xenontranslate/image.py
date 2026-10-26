"""An Xbox 360 executable's loaded image as a big-endian address space: the decrypted, decompressed image DecompPipeline's
Xbox 360 workspace keeps (image.bin, loaded at image.json's load_address), read only."""
from __future__ import annotations

import struct

from .decode import decode


class ImageError(Exception):
    pass


class XexImage:
    def __init__(self, data: bytes, base: int = 0x82000000, sections=None):
        self.data, self.base = data, base
        self.end = base + len(data)
        self.sections = sections or []      # [(start, end, is_code)]
        self._cache = {}

    def has(self, a, n=1):
        return self.base <= a and a + n <= self.end

    def is_text(self, a):
        return any(lo <= a < hi and code for lo, hi, code in self.sections)

    def u8(self, a):
        if not self.has(a, 1):
            raise ImageError(f"read outside the image at {a:08x}")
        return self.data[a - self.base]

    def u16(self, a):
        if not self.has(a, 2):
            raise ImageError(f"read outside the image at {a:08x}")
        return struct.unpack_from(">H", self.data, a - self.base)[0]

    def u32(self, a):
        if not self.has(a, 4):
            raise ImageError(f"read outside the image at {a:08x}")
        return struct.unpack_from(">I", self.data, a - self.base)[0]

    def insn(self, a):
        i = self._cache.get(a)
        if i is None:
            i = decode(self.u32(a), a)
            self._cache[a] = i
        return i

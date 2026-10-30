"""Texture packs for the kit's renderer (pvr/pvr_pack.c): the key it computes for a guest texture, and a pack writer.

A pack is a folder: index.txt ("KEY FILE" per line, '#' comments) and PNG images. KEY is the FNV-1a 64 hash of the
TCW's format bits, width, height and stride (little-endian u32s), then the texture memory the decoder reads from the
TCW address (pvr_texture_bytes; a 16-bit twiddled mip chain from its 1x1 level at +6). A game's pack builder takes
its texture bytes as the game hands them to kmLoadTexture, gets the bytes at the TCW address with kamui_vram() and
the key with key(); images may be any size.

    python texture_pack.py check PACK_DIR     every index line's image exists and is a PNG; prints the count

Nearest existing: pvr/pvr_twiddle.c (the decoder, C, reads the emulated VRAM; this mirrors only its byte counts)
and sdk/kamui2_tex.c (where kmLoadTexture puts the bytes); neither is reachable from a Python pack builder.
"""
import pathlib
import struct
import sys
import zlib

MIP, VQ, NON_TWIDDLED, STRIDE = 1 << 31, 1 << 30, 1 << 26, 1 << 25
FORMAT_BITS = 0xfe000000
VQ_CODEBOOK = 2048
MIP16_FIRST = 6  # a 16-bit twiddled mip chain's 1x1 level, from the TCW address
MIP16_LEAD = 4   # kmLoadTexture writes such a chain's data 4 bytes after the TCW address
FNV_BASIS, FNV_PRIME, MASK64 = 0xcbf29ce484222325, 0x100000001b3, (1 << 64) - 1


def _log2(v):
    n = 0
    while (1 << n) < v:
        n += 1
    return n


def _mipped(tcw):
    return bool(tcw & MIP) and not tcw & NON_TWIDDLED


def texture_bytes(tcw, w, h, stride=0):
    """pvr_twiddle.c pvr_texture_bytes: the bytes of texture memory the decoder reads from the TCW address."""
    i = _log2(w)
    if tcw & VQ:
        return VQ_CODEBOOK + ((1 + ((1 << (2 * (i - 1))) - 1) // 3 if i else 0) if _mipped(tcw) else 0) + w * h // 4
    if tcw & NON_TWIDDLED:
        return ((stride if tcw & STRIDE and stride else w)) * h * 2
    return (6 + 2 * (((1 << (2 * i)) - 1) // 3) if _mipped(tcw) else 0) + w * h * 2


def fnv(data, h=FNV_BASIS):
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & MASK64
    return h


def key(tcw, w, h, vram, stride=0):
    """pvr_pack.c pvr_pack_key: vram = the texture memory from the TCW address (at least texture_bytes long)."""
    n = texture_bytes(tcw, w, h, stride)
    if len(vram) < n:
        raise ValueError(f"{len(vram)} bytes given, the texture reads {n}")
    skip = MIP16_FIRST if tcw & MIP and not tcw & (VQ | NON_TWIDDLED) else 0
    head = struct.pack("<4I", tcw & FORMAT_BITS, w, h, stride)
    return fnv(bytes(vram[skip:n]), fnv(head))


def kamui_tcw(kamui_type):
    """The TCW format bits kmCreateTextureSurface gives a Kamui texture type (sdk/kamui2_tex.c), for the 16-bit
    twiddled, mip-mapped, VQ and rectangle types; None for others (palettised, small VQ, stride)."""
    kind, fmt = kamui_type & 0xff00, kamui_type & 0xff
    flags = {0x0100: 0, 0x0200: MIP, 0x0300: VQ, 0x0400: VQ | MIP, 0x0900: NON_TWIDDLED, 0x0d00: 0}.get(kind)
    if flags is None or fmt > 5:
        return None
    return flags | ((0 if fmt == 5 else fmt) << 27)


def kamui_vram(kamui_type, data):
    """The texture memory from the TCW address after kmLoadTexture(surface, data) (both kits' Kamui: the data as
    stored; a 16-bit twiddled mip chain 4 bytes in, the bytes before it never written: zeros here, never hashed)."""
    tcw = kamui_tcw(kamui_type)
    if tcw is not None and tcw & MIP and not tcw & VQ:
        return bytes(MIP16_LEAD) + bytes(data)
    return bytes(data)


def png(rgba_rows, w, h):
    """A PNG (8-bit RGBA, no filter) of w x h pixels: rgba_rows = w * h * 4 bytes, top row first."""
    raw = bytearray()
    for y in range(h):
        raw += b"\0" + bytes(rgba_rows[y * w * 4:(y + 1) * w * 4])

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


class Pack:
    """Writes a pack: add(key, rgba, w, h) per image (one file per distinct key), then close() writes index.txt."""

    def __init__(self, folder, title=""):
        self.dir = pathlib.Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rows, self.title = {}, title

    def add(self, k, rgba, w, h, name=None, note=""):
        if k in self.rows:
            return False
        name = name or f"{k:016x}.png"
        (self.dir / name).write_bytes(png(rgba, w, h))
        self.rows[k] = (name, note)
        return True

    def close(self):
        lines = ["# texture pack (port/pvr/pvr_pack.c): KEY FILE"] + ([f"# {self.title}"] if self.title else [])
        lines += [f"{k:016x} {n}" + (f"  # {note}" if note else "") for k, (n, note) in sorted(self.rows.items())]
        (self.dir / "index.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return len(self.rows)


def read_index(folder):
    rows = {}
    for line in (pathlib.Path(folder) / "index.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2:
            rows[int(parts[0], 16)] = parts[1]
    return rows


def main(argv):
    if len(argv) != 2 or argv[0] != "check":
        print(__doc__.split("\n\n")[2])
        return 2
    rows = read_index(argv[1])
    files = [pathlib.Path(argv[1]) / f for f in rows.values()]
    bad = [f.name for f in files if not f.is_file() or f.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"]
    print(f"{len(rows)} images, {len(bad)} unreadable{': ' + ', '.join(bad[:5]) if bad else ''}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

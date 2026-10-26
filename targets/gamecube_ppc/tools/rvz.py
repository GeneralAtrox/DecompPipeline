"""Random-access reads from a WIA or RVZ GameCube disc image, written from the format description (Dolphin's
docs/WiaAndRvz.md, from wit's WIA 1.00) and RFC 8878 (Zstandard). Used by gcdisc.py; read-only on the image.

    python rvz.py <image.rvz|.wia> <out.iso>     write the plain disc image (to a new file)

A WIA/RVZ file is a header (0x48 bytes: magic, sizes), the disc struct (compression, chunk size, the disc's first
0x80 bytes, where the tables are), then the raw-data table (disc ranges, each a run of groups) and the group table
(each group: file offset / 4, compressed size, and in RVZ the packed size), both stored compressed. A group is one
chunk of the disc compressed on its own. RVZ adds Zstandard, a per-group "stored uncompressed" bit, and packing:
the padding GameCube discs fill unused space with (a lagged Fibonacci generator) is stored as its 68-byte seed.
Wii partitions are not read (a GameCube disc has none).
"""
import bz2
import lzma
import pathlib
import struct
import sys

NONE, PURGE, BZIP2, LZMA, LZMA2, ZSTD = range(6)


# ---- Zstandard (RFC 8878): frames of raw, RLE and compressed blocks; Huffman literals; FSE sequences -------------

class ZstdError(ValueError):
    pass


MAX_BLOCK = 128 << 10  # a block never holds or decodes to more (RFC 8878 3.1.1.2.4)


class Forward:
    """Little-endian bits, least significant first (FSE table descriptions)."""

    def __init__(self, data, pos):
        self.data, self.start, self.bit = data, pos, pos * 8

    def read(self, n):
        lo = self.bit
        v = int.from_bytes(self.data[lo >> 3:(lo + n + 7 >> 3) + 1], "little") >> (lo & 7)
        self.bit += n
        return v & ((1 << n) - 1)

    def peek(self, n):
        v = self.read(n)
        self.bit -= n
        return v

    def end(self):
        return (self.bit + 7) >> 3


class Backward:
    """A stream read from its last byte towards its first, most significant bits first, after the final byte's
    highest set bit (the end marker). Past the start it gives zero bits (`pad` of them sit below the real ones);
    consuming any of those is an overflow."""

    def __init__(self, data):
        if not data or data[-1] == 0:
            raise ZstdError("bitstream without an end marker")
        self.data, self.pos = data, len(data) - 1
        last = data[-1]
        self.nbits = last.bit_length() - 1
        self.acc = last & ((1 << self.nbits) - 1)
        self.pad = 0

    def _fill(self, n):
        while self.nbits < n and self.pos > 0:
            take = min(self.pos, 7)
            self.pos -= take
            self.acc = (self.acc << 8 * take) | int.from_bytes(self.data[self.pos:self.pos + take], "little")
            self.nbits += 8 * take
        if self.nbits < n:
            self.acc <<= n - self.nbits
            self.pad += n - self.nbits
            self.nbits = n

    def peek(self, n):
        self._fill(n)
        return self.acc >> (self.nbits - n)

    def skip(self, n):
        self.nbits -= n
        self.acc &= (1 << self.nbits) - 1

    def read(self, n):
        if n == 0:
            return 0
        self._fill(n)
        self.nbits -= n
        v = self.acc >> self.nbits
        self.acc &= (1 << self.nbits) - 1
        return v

    def overflowed(self):
        return self.nbits < self.pad

    def done(self):
        return self.pos == 0 and self.nbits == self.pad


def fse_counts(r, max_al):
    """(accuracy_log, counts) of an FSE table description; a count of -1 is a 'less than 1' probability."""
    al = r.read(4) + 5
    if al > max_al:
        raise ZstdError(f"FSE accuracy log {al} over {max_al}")
    remaining, threshold, nb = (1 << al) + 1, 1 << al, al + 1
    counts = []
    while remaining > 1:
        mx = 2 * threshold - 1 - remaining
        v = r.peek(nb)
        if v & (threshold - 1) < mx:
            c = v & (threshold - 1)
            r.read(nb - 1)
        else:
            c = v & (2 * threshold - 1)
            if c >= threshold:
                c -= mx
            r.read(nb)
        c -= 1
        remaining -= -c if c < 0 else c
        counts.append(c)
        if c == 0:
            while True:
                rep = r.read(2)
                counts += [0] * rep
                if rep != 3:
                    break
        while remaining < threshold:
            nb -= 1
            threshold >>= 1
    if remaining != 1:
        raise ZstdError("FSE counts do not add up")
    return al, counts


def fse_table(al, counts):
    """[(symbol, bits, baseline)] for every state of an FSE decoding table."""
    size = 1 << al
    sym = [0] * size
    high = size - 1
    nxt = {}
    for s, c in enumerate(counts):
        if c == -1:
            sym[high] = s
            high -= 1
            nxt[s] = 1
    pos, step = 0, (size >> 1) + (size >> 3) + 3
    for s, c in enumerate(counts):
        if c > 0:
            nxt[s] = c
            for _ in range(c):
                sym[pos] = s
                pos = (pos + step) & (size - 1)
                while pos > high:
                    pos = (pos + step) & (size - 1)
    if pos:
        raise ZstdError("FSE table spread does not return to 0")
    table = []
    for u in range(size):
        s = sym[u]
        n = nxt[s]
        nxt[s] += 1
        bits = al - (n.bit_length() - 1)
        table.append((s, bits, (n << bits) - size))
    return table


def rle_table(symbol):
    return [(symbol, 0, 0)]


LL_DEFAULT = (6, [4, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 1, 1, 1, 1, 1,
                  -1, -1, -1, -1])
ML_DEFAULT = (6, [1, 4, 3, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                  1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1, -1, -1])
OF_DEFAULT = (5, [1, 1, 1, 1, 1, 1, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1])
LL_CODES = [(i, 0) for i in range(16)] + [(16, 1), (18, 1), (20, 1), (22, 1), (24, 2), (28, 2), (32, 3), (40, 3),
                                         (48, 4), (64, 6), (128, 7), (256, 8), (512, 9), (1024, 10), (2048, 11),
                                         (4096, 12), (8192, 13), (16384, 14), (32768, 15), (65536, 16)]
ML_CODES = [(i + 3, 0) for i in range(32)] + [(35, 1), (37, 1), (39, 1), (41, 1), (43, 2), (47, 2), (51, 3),
                                             (59, 3), (67, 4), (83, 4), (99, 5), (131, 7), (259, 8), (515, 9),
                                             (1027, 10), (2051, 11), (4099, 12), (8195, 13), (16387, 14),
                                             (32771, 15), (65539, 16)]
DEFAULT_TABLES = {}


def default_table(kind):
    if kind not in DEFAULT_TABLES:
        DEFAULT_TABLES[kind] = fse_table(*{"ll": LL_DEFAULT, "ml": ML_DEFAULT, "of": OF_DEFAULT}[kind])
    return DEFAULT_TABLES[kind]


def huffman_weights(data, pos):
    """(weights of symbols 0..n-1 with the implied last one, position after the description)."""
    h = data[pos]
    pos += 1
    if h >= 128:
        n = h - 127
        raw = data[pos:pos + (n + 1) // 2]
        weights = [(raw[k // 2] >> (4 if k % 2 == 0 else 0)) & 15 for k in range(n)]
        pos += (n + 1) // 2
    else:
        r = Forward(data, pos)
        al, counts = fse_counts(r, 6)
        table = fse_table(al, counts)
        b = Backward(data[r.end():pos + h])
        s1, s2 = b.read(al), b.read(al)
        weights = []
        while len(weights) < 255:
            for which in (0, 1):
                s = s1 if which == 0 else s2
                sym, bits, base = table[s]
                weights.append(sym)
                s = base + b.read(bits)
                if which == 0:
                    s1 = s
                else:
                    s2 = s
                if b.overflowed():
                    weights.append(table[s2 if which == 0 else s1][0])
                    break
            else:
                continue
            break
        pos += h
    total = sum(1 << (w - 1) for w in weights if w)
    if total == 0:
        raise ZstdError("Huffman weights all zero")
    max_bits = total.bit_length()
    if max_bits > 11:
        raise ZstdError("Huffman code over 11 bits")
    rest = (1 << max_bits) - total
    if rest & (rest - 1):
        raise ZstdError("Huffman weights do not leave a power of two")
    weights.append(rest.bit_length())
    return weights, pos


def huffman_table(weights):
    """(max_bits, [(symbol, bits)] indexed by the next max_bits bits)."""
    total = sum(1 << (w - 1) for w in weights if w)
    max_bits = total.bit_length() - 1
    rank = {}
    start = 0
    for w in range(1, max_bits + 1):
        rank[w] = start
        start += sum(1 for x in weights if x == w) << (w - 1)
    table = [None] * (1 << max_bits)
    for s, w in enumerate(weights):
        if w:
            n = 1 << (w - 1)
            table[rank[w]:rank[w] + n] = [(s, max_bits + 1 - w)] * n
            rank[w] += n
    return max_bits, table


def huffman_stream(data, count, max_bits, table):
    b = Backward(data)
    out = bytearray(count)
    for k in range(count):
        s, n = table[b.peek(max_bits)]
        out[k] = s
        b.skip(n)
    if not b.done():
        raise ZstdError("Huffman stream not consumed exactly")
    return out


class Zstd:
    """Decompress Zstandard frames (no dictionaries; the content checksum is not checked). `limit`: the most bytes
    the data may decode to (a corrupt or hostile input is refused past it instead of filling memory)."""

    def __init__(self, limit=None):
        self.limit = limit
        self.cap = None
        self.huffman = None
        self.tables = {}
        self.reps = [1, 4, 8]

    def literals(self, data, pos):
        b0 = data[pos]
        kind, fmt = b0 & 3, (b0 >> 2) & 3
        if kind in (0, 1):
            if fmt in (0, 2):
                size, pos = b0 >> 3, pos + 1
            elif fmt == 1:
                size, pos = (b0 >> 4) + (data[pos + 1] << 4), pos + 2
            else:
                size, pos = (b0 >> 4) + (data[pos + 1] << 4) + (data[pos + 2] << 12), pos + 3
            if kind == 0:
                return data[pos:pos + size], pos + size
            return bytes([data[pos]]) * size, pos + 1
        n_head = {0: 3, 1: 3, 2: 4, 3: 5}[fmt]
        v = int.from_bytes(data[pos:pos + n_head], "little")
        bits = {3: 10, 4: 14, 5: 18}[n_head]
        regen, comp = (v >> 4) & ((1 << bits) - 1), (v >> (4 + bits)) & ((1 << bits) - 1)
        pos += n_head
        end = pos + comp
        if kind == 2:
            weights, pos = huffman_weights(data, pos)
            self.huffman = huffman_table(weights)
        elif self.huffman is None:
            raise ZstdError("treeless literals without an earlier Huffman table")
        max_bits, table = self.huffman
        if fmt == 0:
            return huffman_stream(data[pos:end], regen, max_bits, table), end
        sizes = struct.unpack("<3H", data[pos:pos + 6])
        pos += 6
        each = (regen + 3) // 4
        out = bytearray()
        for k in range(4):
            size = sizes[k] if k < 3 else end - pos
            out += huffman_stream(data[pos:pos + size], each if k < 3 else regen - 3 * each, max_bits, table)
            pos += size
        return out, end

    def seq_table(self, kind, mode, data, pos, max_al):
        if mode == 0:
            self.tables[kind] = default_table(kind)
        elif mode == 1:
            self.tables[kind] = rle_table(data[pos])
            pos += 1
        elif mode == 2:
            r = Forward(data, pos)
            self.tables[kind] = fse_table(*fse_counts(r, max_al))
            pos = r.end()
        elif kind not in self.tables:
            raise ZstdError(f"repeat mode for {kind} without an earlier table")
        return pos

    def block(self, data, pos, end, out):
        lits, pos = self.literals(data, pos)
        b0 = data[pos]
        if b0 == 0:
            out += lits
            return
        if b0 < 128:
            n, pos = b0, pos + 1
        elif b0 < 255:
            n, pos = ((b0 - 128) << 8) + data[pos + 1], pos + 2
        else:
            n, pos = data[pos + 1] + (data[pos + 2] << 8) + 0x7F00, pos + 3
        modes = data[pos]
        pos += 1
        pos = self.seq_table("ll", modes >> 6, data, pos, 9)
        pos = self.seq_table("of", (modes >> 4) & 3, data, pos, 8)
        pos = self.seq_table("ml", (modes >> 2) & 3, data, pos, 9)
        ll_t, of_t, ml_t = self.tables["ll"], self.tables["of"], self.tables["ml"]
        b = Backward(data[pos:end])
        ll_s = b.read(len(ll_t).bit_length() - 1)
        of_s = b.read(len(of_t).bit_length() - 1)
        ml_s = b.read(len(ml_t).bit_length() - 1)
        reps = self.reps
        lp = 0
        for k in range(n):
            of_code, ll_code, ml_code = of_t[of_s][0], ll_t[ll_s][0], ml_t[ml_s][0]
            if of_code > 31 or ll_code > 35 or ml_code > 52:
                raise ZstdError("sequence code out of range")
            ov = (1 << of_code) + b.read(of_code)
            base, extra = ML_CODES[ml_code]
            ml = base + b.read(extra)
            base, extra = LL_CODES[ll_code]
            ll = base + b.read(extra)
            if ov > 3:
                off = ov - 3
                reps[:] = [off, reps[0], reps[1]]
            else:
                idx = ov - 1 + (ll == 0)
                if idx == 0:
                    off = reps[0]
                elif idx == 3:
                    off = reps[0] - 1
                    reps[:] = [off, reps[0], reps[1]]
                elif idx == 1:
                    off = reps[1]
                    reps[:] = [off, reps[0], reps[2]]
                else:
                    off = reps[2]
                    reps[:] = [off, reps[0], reps[1]]
            if k < n - 1:
                _, bits, base = ll_t[ll_s]
                ll_s = base + b.read(bits)
                _, bits, base = ml_t[ml_s]
                ml_s = base + b.read(bits)
                _, bits, base = of_t[of_s]
                of_s = base + b.read(bits)
            out += lits[lp:lp + ll]
            lp += ll
            if off <= 0 or off > len(out):
                raise ZstdError("match offset before the start")
            src = len(out) - off
            if off >= ml:
                out += out[src:src + ml]
            else:
                while ml > 0:
                    piece = out[src:src + min(ml, off)]
                    out += piece
                    src += len(piece)
                    ml -= len(piece)
            if len(out) > self.cap:
                raise ZstdError("a block decodes to more than 128 KiB")
        if not b.done():
            raise ZstdError("sequence bitstream not consumed exactly")
        out += lits[lp:]
        if len(out) > self.cap:
            raise ZstdError("a block decodes to more than 128 KiB")

    def frame(self, data, pos, limit=None):
        if pos + 5 > len(data):
            raise ZstdError("frame header past the end of the data")
        magic = struct.unpack("<I", data[pos:pos + 4])[0]
        if magic & 0xFFFFFFF0 == 0x184D2A50:  # skippable frame
            return b"", pos + 8 + struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if magic != 0xFD2FB528:
            raise ZstdError("not a Zstandard frame")
        d = data[pos + 4]
        pos += 5
        fcs_flag, single, checksum, dict_flag = d >> 6, (d >> 5) & 1, (d >> 2) & 1, d & 3
        if d & 8:
            raise ZstdError("reserved frame header bit set")
        if not single:
            pos += 1  # window descriptor: the whole frame is decoded in memory
        if dict_flag:
            if int.from_bytes(data[pos:pos + (1, 2, 4)[dict_flag - 1]], "little"):
                raise ZstdError("a dictionary is needed")
            pos += (1, 2, 4)[dict_flag - 1]
        pos += (1 if single else 0, 2, 4, 8)[fcs_flag]
        self.huffman, self.tables, self.reps = None, {}, [1, 4, 8]
        out = bytearray()
        while True:
            if pos + 3 > len(data):
                raise ZstdError("block header past the end of the data")
            h = int.from_bytes(data[pos:pos + 3], "little")
            pos += 3
            last, kind, size = h & 1, (h >> 1) & 3, h >> 3
            if size > MAX_BLOCK:
                raise ZstdError("block over 128 KiB")
            if pos + (1 if kind == 1 else size) > len(data):
                raise ZstdError("block past the end of the data")
            self.cap = len(out) + MAX_BLOCK
            if kind == 0:
                out += data[pos:pos + size]
                pos += size
            elif kind == 1:
                out += bytes([data[pos]]) * size
                pos += 1
            elif kind == 2:
                self.block(data, pos, pos + size, out)
                pos += size
            else:
                raise ZstdError("reserved block type")
            if limit is not None and len(out) > limit:
                raise ZstdError(f"decodes to more than the {limit} bytes expected")
            if last:
                break
        return bytes(out), pos + 4 * checksum

    def decompress(self, data):
        out, pos = bytearray(), 0
        while pos < len(data):
            part, pos = self.frame(data, pos, None if self.limit is None else self.limit - len(out))
            out += part
        return bytes(out)


def zstd_decompress(data, limit=None):
    return Zstd(limit).decompress(bytes(data))


# ---- the padding generator (lagged Fibonacci, f = xor, j = 32, k = 521) ----------------------------------------

def junk(seed, disc_offset, size):
    """`size` bytes of GameCube padding from a 68-byte seed, for data starting at `disc_offset` (the generator
    restarts every 32 KiB: the bytes before the offset within its 32 KiB block are generated and dropped)."""
    buf = list(struct.unpack(">17I", seed)) + [0] * (521 - 17)
    for i in range(17, 521):
        buf[i] = ((buf[i - 17] << 23) ^ (buf[i - 16] >> 9) ^ buf[i - 1]) & 0xFFFFFFFF

    def forward():
        for i in range(32):
            buf[i] ^= buf[i + 521 - 32]
        for i in range(32, 521):
            buf[i] ^= buf[i - 32]

    for _ in range(4):
        forward()
    skip = disc_offset % 0x8000
    out = bytearray()
    need = skip + size
    while len(out) < need:
        out += b"".join(bytes(((w >> 24) & 0xFF, (w >> 18) & 0xFF, (w >> 8) & 0xFF, w & 0xFF)) for w in buf)
        forward()
    return bytes(out[skip:need])


# ---- the container ---------------------------------------------------------------------------------------------

class Image:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.f = open(self.path, "rb")
        try:
            self._open()
        except Exception:
            self.f.close()
            raise
        self.cache = {}

    def _open(self):
        head = self._at(0, 0x48)
        self.rvz = head[:4] == b"RVZ\x01"
        if not self.rvz and head[:4] != b"WIA\x01":
            raise ValueError(f"{self.path.name}: not a WIA or RVZ file")
        disc_size = struct.unpack(">I", head[0x0C:0x10])[0]
        self.size = struct.unpack(">Q", head[0x24:0x2C])[0]
        d = self._at(0x48, disc_size).ljust(0xDC, b"\0")
        disc_type, self.method, _, self.chunk = struct.unpack(">4I", d[0:0x10])
        self.dhead = d[0x10:0x90]
        n_part = struct.unpack(">I", d[0x90:0x94])[0]
        n_raw, raw_off, raw_size, n_groups, group_off, group_size = struct.unpack(">IQIIQI", d[0xB4:0xD4])
        self.props = d[0xD5:0xD5 + d[0xD4]]
        if disc_type != 1 or n_part:
            raise ValueError(f"{self.path.name}: not a GameCube disc (disc type {disc_type}, {n_part} partitions)")
        if self.method == PURGE or self.method > ZSTD or (self.method == ZSTD and not self.rvz):
            raise ValueError(f"{self.path.name}: compression method {self.method} is not read here")
        raw = self._unpack_table(self._at(raw_off, raw_size), 24 * n_raw)
        self.raw = []
        for k in range(n_raw):
            off, size, first, count = struct.unpack(">QQII", raw[24 * k:24 * k + 24])
            start = off - off % 0x8000  # the first entry starts at 0x80 but holds data from 0 (WiaAndRvz.md)
            self.raw.append((start, off + size, first, count))
        g = 12 if self.rvz else 8
        groups = self._unpack_table(self._at(group_off, group_size), g * n_groups)
        self.groups = [struct.unpack(">III" if self.rvz else ">II", groups[g * k:g * k + g]) + ((0,) if not self.rvz
                                                                                                 else ())
                       for k in range(n_groups)]

    def close(self):
        self.f.close()

    def _at(self, off, size):
        self.f.seek(off)
        b = self.f.read(size)
        if len(b) != size:
            raise ValueError(f"{self.path.name}: 0x{off:x}+0x{size:x} past the end of the file")
        return b

    def _decompress(self, b, size):
        if self.method == NONE:
            return b
        if self.method == BZIP2:
            return bz2.BZ2Decompressor().decompress(b, max_length=size)
        if self.method == ZSTD:
            return zstd_decompress(b, size)
        if self.method == LZMA:
            p = self.props[0]
            filt = {"id": lzma.FILTER_LZMA1, "lc": p % 9, "lp": (p // 9) % 5, "pb": p // 45,
                    "dict_size": struct.unpack("<I", self.props[1:5])[0]}
        else:
            p = self.props[0]
            filt = {"id": lzma.FILTER_LZMA2, "dict_size": 0xFFFFFFFF if p == 40 else (2 | (p & 1)) << (p // 2 + 11)}
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[filt]).decompress(b, size)

    def _unpack_table(self, b, size):
        out = self._decompress(b, size)
        if len(out) < size:
            raise ValueError(f"{self.path.name}: a table is shorter than its entries")
        return out[:size]

    def _group(self, index, disc_off, size):
        """[(disc_offset, is_padding, bytes or a 68-byte seed, length)] for one group."""
        if index in self.cache:
            return self.cache[index]
        off4, stored, packed = self.groups[index]
        compressed = bool(stored >> 31) if self.rvz else True
        stored &= 0x7FFFFFFF if self.rvz else 0xFFFFFFFF
        if stored == 0:
            parts = [(disc_off, False, bytes(size), size)]
        else:
            b = self._at(off4 * 4, stored)
            data = self._decompress(b, packed or size) if compressed else b
            if packed:
                parts, p, at = [], 0, disc_off
                while p < packed:
                    n = struct.unpack(">I", data[p:p + 4])[0]
                    p += 4
                    if n >> 31:
                        n &= 0x7FFFFFFF
                        parts.append((at, True, data[p:p + 68], n))
                        p += 68
                    else:
                        parts.append((at, False, data[p:p + n], n))
                        p += n
                    at += n
            else:
                parts = [(disc_off, False, data[:size], size)]
        if len(self.cache) > 8:
            self.cache.clear()
        self.cache[index] = parts
        return parts

    def read(self, off, size):
        if off + size > self.size:
            raise ValueError(f"0x{off:x}+0x{size:x}: past the end of the image ({self.size} bytes)")
        out = bytearray()
        pos, end = off, off + size
        while pos < end:
            for start, stop, first, count in self.raw:
                if start <= pos < stop:
                    k = (pos - start) // self.chunk
                    g_off = start + k * self.chunk
                    g_size = min(self.chunk, stop - g_off)
                    if k >= count:
                        raise ValueError(f"0x{pos:x}: past the groups of its raw-data entry")
                    for at, padding, body, n in self._group(first + k, g_off, g_size):
                        lo, hi = max(pos, at), min(end, at + n, g_off + g_size)
                        if lo >= hi:
                            continue
                        out += junk(body, at, hi - at)[lo - at:] if padding else body[lo - at:hi - at]
                        pos = hi
                    pos = max(pos, min(end, g_off + g_size))
                    break
            else:  # a range no raw-data entry covers holds zeros
                nxt = min([s for s, *_ in self.raw if s > pos] + [end])
                out += bytes(nxt - pos)
                pos = nxt
        if off < 0x80:
            n = min(0x80, end) - off
            out[0:n] = self.dhead[off:off + n]
        return bytes(out)


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    img = Image(argv[0])
    try:
        out = pathlib.Path(argv[1])
        with open(out, "xb") as f:  # a new file: never overwrite
            step = 1 << 22
            for pos in range(0, img.size, step):
                f.write(img.read(pos, min(step, img.size - pos)))
    finally:
        img.close()
    print(f"{out}: {img.size} bytes")


if __name__ == "__main__":
    main(sys.argv[1:])

"""Main RAM over time, stored as deltas: frame 0 and every `keyframe`-th frame in full, every other frame as the ranges
that changed since the frame before it (compared in 32-byte blocks), each frame zstd-compressed on its own.

Files: NAME.zram (the compressed frames, back to back) and NAME.zidx (one line per frame: "offset size kind tag", kind
k for a keyframe and d for a delta; tag is the caller's, e.g. "vblank label"). A frame is rebuilt from the nearest
keyframe at or before it plus at most keyframe - 1 deltas.

A delta frame is: u32 run count, u32 run starts[count], u32 run lengths[count] (bytes), then the runs' bytes.
The writer keeps compressed frames in memory up to `buffer_mb` and appends them to disk past that (and at close).
"""
import mmap
import pathlib
import struct
import time

import numpy as np
import zstandard

RAM_SIZE = 0x01000000
RAMF = 0x464D4152          # a port record (runtime/ram_frames.c)
BLOCK = 32
LEVEL = 3


def changed_runs(prev, cur):
    """The 32-byte blocks that differ, merged into runs: (starts, lengths) in bytes."""
    a = np.frombuffer(prev, dtype=np.uint64).reshape(-1, BLOCK // 8)
    b = np.frombuffer(cur, dtype=np.uint64).reshape(-1, BLOCK // 8)
    idx = np.flatnonzero((a != b).any(axis=1)).astype(np.uint32)
    if idx.size == 0:
        return idx, idx
    cut = np.flatnonzero(np.diff(idx) != 1) + 1
    starts = idx[np.concatenate(([0], cut))]
    ends = idx[np.concatenate((cut - 1, [idx.size - 1]))] + 1
    return starts * BLOCK, (ends - starts) * BLOCK


def changed_words(prev, cur):
    """How many 4-byte words differ."""
    return int(np.count_nonzero(np.frombuffer(prev, dtype=np.uint32) != np.frombuffer(cur, dtype=np.uint32)))


class Writer:
    def __init__(self, path, keyframe=300, buffer_mb=256):
        self.path = pathlib.Path(path)
        self.keyframe, self.cap = keyframe, buffer_mb << 20
        self.zram = open(self.path.with_suffix(".zram"), "wb")
        self.zidx = open(self.path.with_suffix(".zidx"), "w", encoding="ascii", newline="\n")
        self.cctx = zstandard.ZstdCompressor(level=LEVEL)
        self.prev, self.count, self.offset, self.pending, self.held = None, 0, 0, [], 0
        self.stats = {"frames": 0, "keyframes": 0, "raw_bytes": 0, "stored_bytes": 0, "changed_bytes": [],
                      "block_bytes": [], "seconds": 0.0}

    def add(self, ram, tag="", runs=None):
        """runs: the (starts, lengths) that changed since the frame before, when the caller knows them."""
        t0 = time.perf_counter()
        ram = bytes(ram)
        assert len(ram) == RAM_SIZE
        if self.prev is None or self.count % self.keyframe == 0:
            kind, body = "k", ram
            self.stats["keyframes"] += 1
        else:
            starts, lengths = runs if runs is not None else changed_runs(self.prev, ram)
            parts = [np.uint32(starts.size).tobytes(), starts.astype("<u4").tobytes(), lengths.astype("<u4").tobytes()]
            parts += [ram[s:s + n] for s, n in zip(starts.tolist(), lengths.tolist())]
            kind, body = "d", b"".join(parts)
            self.stats["changed_bytes"].append(4 * changed_words(self.prev, ram))
            self.stats["block_bytes"].append(int(lengths.sum()))
        blob = self.cctx.compress(body)
        self.pending.append(blob)
        self.held += len(blob)
        self.zidx.write(f"{self.offset} {len(blob)} {kind} {tag}\n")
        self.offset += len(blob)
        self.prev, self.count = ram, self.count + 1
        self.stats["frames"] += 1
        self.stats["raw_bytes"] += len(body)
        self.stats["stored_bytes"] += len(blob)
        if self.held >= self.cap:
            self.spill()
        self.stats["seconds"] += time.perf_counter() - t0

    def spill(self):
        for blob in self.pending:
            self.zram.write(blob)
        self.pending, self.held = [], 0

    def close(self):
        self.spill()
        self.zram.close()
        self.zidx.close()
        return self.stats


class Reader:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.index = []
        for line in self.path.with_suffix(".zidx").read_text(encoding="ascii").splitlines():
            off, size, kind, *tag = line.split()
            self.index.append((int(off), int(size), kind, tag))
        self.file = open(self.path.with_suffix(".zram"), "rb")  # mapped, not read: a recording can be large
        self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        self.dctx = zstandard.ZstdDecompressor()

    def __len__(self):
        return len(self.index)

    def tag(self, i):
        return self.index[i][3]

    def _body(self, i):
        off, size, _, _ = self.index[i]
        return self.dctx.decompress(self.data[off:off + size])

    def frames(self, start=0):
        """(i, RAM after frame i) from frame start on, applying each delta once; the RAM is reused between yields."""
        ram = None
        for i in range(start, len(self)):
            if ram is None:
                ram = self.frame(i)
            elif self.index[i][2] == "k":
                ram[:] = self._body(i)
            else:
                self._apply(ram, self._body(i))
            yield i, ram

    @staticmethod
    def _apply(ram, body):
        n = int.from_bytes(body[:4], "little")
        starts = np.frombuffer(body, dtype="<u4", count=n, offset=4)
        lengths = np.frombuffer(body, dtype="<u4", count=n, offset=4 + 4 * n)
        at = 4 + 8 * n
        for s, length in zip(starts.tolist(), lengths.tolist()):
            ram[s:s + length] = body[at:at + length]
            at += length

    def frame(self, i):
        """Main RAM after frame i, as a bytearray."""
        k = i
        while self.index[k][2] != "k":
            k -= 1
        ram = bytearray(self._body(k))
        for j in range(k + 1, i + 1):
            self._apply(ram, self._body(j))
        return ram


def differing_words(a, b, lo=0x8C000000, hi=0x8D000000, base=0x8C000000):
    """The guest addresses of the 4-byte words in [lo, hi) that differ between two RAM images."""
    x = np.frombuffer(a, dtype=np.uint32, count=(hi - lo) // 4, offset=lo - base)
    y = np.frombuffer(b, dtype=np.uint32, count=(hi - lo) // 4, offset=lo - base)
    return [lo + 4 * int(i) for i in np.flatnonzero(x != y)]


def read_raw(path):
    """The port's --ram-frames records: (port frame, label, emulator frame, starts, lengths, the runs' bytes)."""
    with open(path, "rb") as f:
        while len(head := f.read(20)) == 20:
            magic, frame, label, emu, n = struct.unpack("<IIIiI", head)
            if magic != RAMF:
                raise ValueError(f"{path}: not a RAM frame record at {f.tell() - 20}")
            runs = np.frombuffer(f.read(8 * n), dtype="<u4").reshape(-1, 2)
            yield frame, label, emu, runs[:, 0], runs[:, 1], f.read(int(runs[:, 1].sum()))


def pack_raw(raw, dest, keyframe=300, buffer_mb=256):
    """The port's raw file into the stored format (tag "frame emu-frame label"); the raw file is removed."""
    w, ram = Writer(dest, keyframe, buffer_mb), bytearray(RAM_SIZE)
    for frame, label, emu, starts, lengths, data in read_raw(raw):
        at = 0
        for s, n in zip(starts.tolist(), lengths.tolist()):
            ram[s:s + n] = data[at:at + n]
            at += n
        w.add(ram, f"{frame} {emu} {label:08x}", (starts, lengths))
    stats = w.close()
    pathlib.Path(raw).unlink()
    return stats

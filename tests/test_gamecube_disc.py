"""The GameCube disc stage (targets/gamecube_ppc/tools/gcdisc.py), offline, on a synthetic disc:
- the disc header, the file system table (nested directories) and file reads; a Wii disc and a non-disc refused;
- main.dol's sections, the CodeWarrior layout (.data, .bss, .sdata, .sbss writable; .sdata2 not), r2/r13 read from
  the start-up code, GQR pins (one constant value pinned; a saved-context restore ignored; a value the scan cannot
  follow blocks the pin);
- REL modules found loose, in Yaz0 and inside a Yaz0 RARC archive, with their sections, bss and imports;
- WIA and RVZ (rvz.py) read as the disc they hold: every compression method, all-zero groups, RVZ's stored-as-is
  groups and packed padding (a test writer here, from the format description), a Zstandard RVZ made by Dolphin's
  converter from this file's generator, and Zstandard frames reaching the decoder's remaining paths;
- extract and setup write new folders only, never touch the image, and the project.json fields come from the disc;
  setup links the disc's REL modules onto main.dol (linked.dol; all, some or none of them: test_gamecube_rel.py has
  the link itself).

usage: python tests/test_gamecube_disc.py
"""
import hashlib
import json
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "targets" / "gamecube_ppc" / "tools"))
import gcdisc  # noqa: E402
import rvz  # noqa: E402

WORK = TOOL / "tests" / ".work"


def tmpdir(prefix):
    WORK.mkdir(parents=True, exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=WORK))


def be(*ws):
    return struct.pack(f">{len(ws)}I", *ws)


# ---------- PowerPC words ----------

def li(d, v):
    return 14 << 26 | d << 21 | (v & 0xFFFF)


def lis(d, v):
    return 15 << 26 | d << 21 | (v & 0xFFFF)


def ori(d, v):
    return 24 << 26 | d << 21 | d << 16 | (v & 0xFFFF)


def oris(d, v):
    return 25 << 26 | d << 21 | d << 16 | (v & 0xFFFF)


def addi(d, v):
    return 14 << 26 | d << 21 | d << 16 | (v & 0xFFFF)


def lwz(d, a, off):
    return 32 << 26 | d << 21 | a << 16 | (off & 0xFFFF)


def mr(d, s):
    return 31 << 26 | s << 21 | d << 16 | s << 11 | 444 << 1


def mtgqr(n, s):
    spr = 912 + n
    return 31 << 26 | s << 21 | ((spr & 31) << 5 | spr >> 5) << 11 | 467 << 1


def bl(src, dst):
    return 18 << 26 | ((dst - src) & 0x03FFFFFC) | 1


BLR, NOP = 0x4E800020, 0x60000000

# ---------- a CodeWarrior-shaped main.dol ----------
INIT, TEXT = 0x80003100, 0x80003200
CTORS, RODATA, DATA = 0x80010000, 0x80010020, 0x80010100
BSS, SDATA, SDATA2 = 0x80010200, 0x80010400, 0x80010600
BSS_END = 0x80010700
SDA, SDA2 = SDATA + 0x8000, SDATA2 + 0x8000


def text_code():
    """__start at TEXT (calls __init_registers), __init_registers, a fast-cast setup (GQR2 constant), a context
    restore (GQR2, GQR3 loaded from memory) and a setter of GQR4 from its argument (as well as one constant)."""
    start = [NOP, NOP, bl(TEXT + 8, TEXT + 0x40), NOP, BLR]
    start += [NOP] * (16 - len(start))
    init_regs = [lis(1, 0x8040), ori(1, 0x0000), lis(2, SDA2 >> 16), ori(2, SDA2 & 0xFFFF),
                 lis(13, (SDA + 0x8000) >> 16), addi(13, SDA & 0xFFFF), BLR]  # @ha / @l, as CodeWarrior
    init_regs += [NOP] * (16 - len(init_regs))
    fast_cast = [li(3, 0), mtgqr(0, 3), li(3, 4), oris(3, 4), mtgqr(2, 3), li(3, 6), oris(3, 6), mtgqr(4, 3), BLR]
    restore = [lwz(4, 3, 0x1A4), mtgqr(2, 4), lwz(4, 3, 0x1A8), mtgqr(3, 4), BLR]
    setter = [mr(5, 3), mtgqr(4, 5), BLR]
    return be(*(start + init_regs + fast_cast + restore + setter))


def make_dol():
    init = be(NOP, BLR).ljust(TEXT - INIT, b"\0")
    text = text_code()
    secs_t = [(INIT, init), (TEXT, text)]
    secs_d = [(CTORS, b"\x11" * 0x20), (RODATA, b"\x22" * 0xE0), (DATA, b"\x33" * 0x100), (SDATA, b"\x44" * 0x80),
              (SDATA2, b"\x55" * 0x40)]
    offs, addrs, sizes = [0] * 18, [0] * 18, [0] * 18
    body, pos = bytearray(), 0x100
    for slot, (a, b) in list(enumerate(secs_t)) + [(7 + i, s) for i, s in enumerate(secs_d)]:
        offs[slot], addrs[slot], sizes[slot] = pos, a, len(b)
        body += b
        pos += len(b)
    head = struct.pack(">18I18I18I3I", *offs, *addrs, *sizes, BSS, BSS_END - BSS, TEXT).ljust(0x100, b"\0")
    return head + bytes(body)


# ---------- REL, Yaz0, RARC ----------

def make_rel(mid, code=b"\x60\x00\x00\x00" * 8, data=b"\x01\x02\x03\x04", bss=0x40, imports=(0,), version=3):
    nsec = 4  # null, .text (exec), .data, .bss
    sec_off = 0x4C
    text_off = sec_off + 8 * nsec
    data_off = text_off + len(code)
    imp_off = data_off + len(data)
    rel_off = imp_off + 8 * len(imports)
    rel = bytearray(rel_off + 8)
    struct.pack_into(">12I", rel, 0, mid, 0, 0, nsec, sec_off, 0, 0, version, bss, rel_off, imp_off, 8 * len(imports))
    rel[0x30:0x34] = bytes([1, 1, 1, 3])
    struct.pack_into(">3I", rel, 0x34, 0, 4, 8)
    struct.pack_into(">3I", rel, 0x40, 32, 8, 0)
    struct.pack_into(">8I", rel, sec_off, 0, 0, text_off | 1, len(code), data_off, len(data), 0, bss)
    rel[text_off:text_off + len(code)] = code
    rel[data_off:data_off + len(data)] = data
    for k, m in enumerate(imports):
        struct.pack_into(">2I", rel, imp_off + 8 * k, m, rel_off)
    struct.pack_into(">HBBI", rel, rel_off, 0, 203, 0, 0)  # R_DOLPHIN_END
    return bytes(rel)


def yaz0_encode(data):
    """A plain greedy Yaz0 encoder (tests only): back-references of 3..0x111 bytes up to 0x1000 back."""
    out = bytearray(b"Yaz0" + struct.pack(">I", len(data)) + bytes(8))
    i = 0
    while i < len(data):
        code_at = len(out)
        out.append(0)
        code = 0
        for bit in range(7, -1, -1):
            if i >= len(data):
                break
            best_n, best_d = 0, 0
            for dist in range(1, min(i, 0x1000) + 1):
                if best_n == 0x111:
                    break
                n = 0
                while n < 0x111 and i + n < len(data) and data[i + n - dist] == data[i + n]:
                    n += 1
                if n > best_n:
                    best_n, best_d = n, dist
            if best_n >= 3:
                d = best_d - 1
                if best_n >= 0x12:
                    out += bytes([d >> 8, d & 0xFF, best_n - 0x12])
                else:
                    out += bytes([(best_n - 2) << 4 | d >> 8, d & 0xFF])
                i += best_n
            else:
                code |= 1 << bit
                out.append(data[i])
                i += 1
        out[code_at] = code
    return bytes(out)


def make_rarc(files):
    """A RARC archive of {name: bytes} in one root directory ("." and ".." entries first, as Nintendo's are)."""
    names = bytearray(b".\0..\0root\0")
    def name(s):
        off = len(names)
        names.extend(s.encode() + b"\0")
        return off
    entries, blob = [], bytearray()
    entries.append((0xFFFF, 0, 0x02 << 24 | 0, 0, 0x10))  # "."
    entries.append((0xFFFF, 0, 0x02 << 24 | 2, 0xFFFFFFFF, 0x10))  # ".."
    for k, (nm, b) in enumerate(files.items()):
        entries.append((k, 0, 0x11 << 24 | name(nm), len(blob), len(b)))
        blob += b + bytes(-len(b) % 32)
    root_name = 5
    node = struct.pack(">4sIHHI", b"ROOT", root_name, 0, len(entries), 0)
    ent = b"".join(struct.pack(">HHIIII", *e, 0) for e in entries)
    info_size = 0x20
    node_off = info_size
    ent_off = node_off + len(node)
    str_off = ent_off + len(ent)
    data_rel = str_off + len(names)
    data_rel += -data_rel % 32
    info = struct.pack(">6IHBB", 1, node_off, len(entries), ent_off, len(names), str_off, len(files), 1, 0)
    body = (info.ljust(info_size, b"\0") + node + ent + bytes(names)).ljust(data_rel, b"\0") + bytes(blob)
    head = b"RARC" + struct.pack(">7I", 0x20 + len(body), 0x20, data_rel, len(blob), 0, 0, 0)
    return head + body


# ---------- the disc ----------
REL_A = make_rel(1, imports=(0, 1))
REL_B = make_rel(2, code=b"\x4e\x80\x00\x20" * 64 + bytes(range(64)) * 4, imports=(0, 1, 2))
REL_C = make_rel(3, imports=(0,))
FILES = {
    "/sys.txt": b"just data\n" * 10,
    "/rel/a.rel": REL_A,
    "/rel/b.rel.szs": yaz0_encode(REL_B),
    "/rel/packs/RELS.arc": yaz0_encode(make_rarc({"c.rel": REL_C, "readme.txt": b"not a rel"})),
    "/rel/empty.rel": b"\0" * 0x80,  # a .rel-named file that is not one
    "/movie/opening.thp": bytes(range(256)) * 8,
    "/res/stage.arc": yaz0_encode(make_rarc({"model.bdl": bytes(range(200)) * 4})),  # game data: no REL inside
    "/res/font.szs": yaz0_encode(b"FONT" + bytes(range(100)) * 3),
}


def lcg(seed):
    while True:
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        yield seed >> 8


WORDS = ("the sky pirate ship sails over an island of moon stone while crew and captain look for treasure in old "
         "ruins below clouds of silver wind; blue rogues fight the armada at dawn").split()


def text(n, seed):
    """n bytes of word-salad text: something Zstandard compresses with Huffman literals and FSE sequences."""
    r, out = lcg(seed), bytearray()
    while len(out) < n:
        out += WORDS[next(r) % len(WORDS)].encode() + (b".\n" if next(r) % 13 == 0 else b" ")
    return bytes(out[:n])


def table(n, seed):
    """n bytes of record-like binary: fixed-size records with small varying fields."""
    r, out = lcg(seed), bytearray()
    while len(out) < n:
        out += struct.pack(">IHHf", 0x80100000 + 16 * (next(r) % 4096), next(r) % 7, 3, (next(r) % 100) / 8)
    return bytes(out[:n])


def noise(n, seed):
    """n bytes nothing compresses (raw blocks)."""
    out, k = bytearray(), 0
    while len(out) < n:
        out += hashlib.sha256(b"%d %d" % (seed, k)).digest()
        k += 1
    return bytes(out[:n])


def digits(n, seed):
    """n bytes of 0..9, skewed: a Huffman table small enough to be stored as direct weights."""
    r = lcg(seed)
    return bytes(min(next(r) % 16, next(r) % 16) % 10 for _ in range(n))


# The disc behind tests/data/gc_fixture_zstd.rvz (32 KiB groups, Zstandard; made once with Dolphin's converter from
# this generator's image, so the file holds no game data; see test_zstd_fixture)
FIXTURE_SEEDS = tuple(bytes(next(r) & 0xFF for _ in range(68)) for r in (lcg(s) for s in (11, 12)))


def fixture_files():
    return {"/doc/story.txt": text(60000, 1), "/data/table.bin": table(24000, 2), "/data/zeros.bin": bytes(40000),
            "/data/ramp.bin": bytes(range(256)) * 64, "/rel/x.rel": make_rel(7, imports=(0, 7))}


# What tests/data/gc_zstd_frames.zst decodes to: one frame of small blocks, each flushed on its own (made once with the
# reference zstd library, level 19, checksum on), so later blocks reuse the earlier block's Huffman table (treeless
# literals) and FSE tables (repeat mode), the noise is a raw block, the run an RLE block, the digits a Huffman table of
# direct weights; then a single-segment frame with its content size, a skippable frame, and a hand-built
# single-segment frame of one block of RLE literals and no sequences ("zzzzz")
def zstd_pieces():
    return [text(3000, 21), text(3000, 22), text(3000, 23), noise(2000, 1), bytes([7]) * 5000, digits(3000, 3),
            table(3000, 24), text(700, 25), b"", b"zzzzz"]


ZSTD_RLE_LITERALS = bytes.fromhex("28b52ffd" "20" "05" "1d0000" "297a00")


# ---------- WIA / RVZ (a writer for the tests, from the same format description the reader follows) ----------

LZMA_PROPS = {rvz.LZMA: bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", 1 << 20), rvz.LZMA2: bytes([16])}


def compress(method, b):
    import bz2
    import lzma
    if method == rvz.NONE:
        return b
    if method == rvz.BZIP2:
        return bz2.compress(b)
    filt = ({"id": lzma.FILTER_LZMA1, "dict_size": 1 << 20, "lc": 3, "lp": 0, "pb": 2} if method == rvz.LZMA else
            {"id": lzma.FILTER_LZMA2, "dict_size": 1 << 20})
    return lzma.compress(b, format=lzma.FORMAT_RAW, filters=[filt])


def make_wia(img, method, is_rvz=True, chunk=0x8000, padding=None):
    """A WIA (or RVZ) file of a GameCube image: one raw-data entry from 0x80, one group per chunk. All-zero chunks
    are stored as nothing; in RVZ a chunk holding 32 KiB padding blocks (padding: {disc offset: seed}) is packed,
    and a group compression does not shrink is stored uncompressed."""
    padding = padding or {}
    n = -(-len(img) // chunk)
    data_off = 0x48 + 0xDC  # the group data, then the tables
    groups, blob = [], bytearray()
    for k in range(n):
        at, part = k * chunk, img[k * chunk:(k + 1) * chunk]
        if not any(part):
            groups.append((0, 0, 0))
            continue
        packed = 0
        if is_rvz and any(at <= o < at + len(part) for o in padding):
            body = bytearray()
            for o in range(at, at + len(part), 0x8000):
                piece = img[o:min(o + 0x8000, at + len(part))]
                if o in padding:
                    body += struct.pack(">I", 1 << 31 | len(piece)) + padding[o]
                else:
                    body += struct.pack(">I", len(piece)) + piece
            body = bytes(body)
            packed = len(body)
        else:
            body = part
        stored = compress(method, body)
        flag = 0
        if is_rvz:
            if method != rvz.NONE and len(stored) < len(body):
                flag = 1 << 31
            else:
                stored = body
        blob += bytes(-len(blob) % 4)
        groups.append(((data_off + len(blob)) // 4, len(stored) | flag, packed))
        blob += stored
    raw = compress(method, struct.pack(">QQII", 0x80, len(img) - 0x80, 0, n))
    gtab = compress(method, b"".join(struct.pack(">III" if is_rvz else ">II", *g[:3 if is_rvz else 2]) for g in groups))
    props = LZMA_PROPS.get(method, b"")
    raw_off = data_off + len(blob)
    group_off = raw_off + len(raw)
    disc = struct.pack(">4I", 1, method, 5, chunk) + img[:0x80] + struct.pack(
        ">IIQ20sIQIIQIB7s", 0, 0x30, 0, bytes(20), 1, raw_off, len(raw), n, group_off, len(gtab), len(props), props)
    assert len(disc) == 0xDC
    head = (b"RVZ\x01" if is_rvz else b"WIA\x01") + struct.pack(">III20sQQ", 0x00010000 if is_rvz else 0x01000000,
                                                                0x00030000, 0xDC, hashlib.sha1(disc).digest(),
                                                                len(img), group_off + len(gtab))
    head += hashlib.sha1(head).digest()
    assert len(head) == 0x48
    return head + disc + bytes(blob) + raw + gtab


def fst(files, data_base):
    """The file system table of {path: bytes} (directories from the paths, entries depth first) and the files'
    data laid out from data_base, each 32-byte aligned."""
    tree = {}
    for path in files:
        node = tree
        parts = path.strip("/").split("/")
        for d in parts[:-1]:
            node = node.setdefault(d, {})
        node[parts[-1]] = path
    entries, names, payload = [[1 << 24, 0, 0]], bytearray(), bytearray()

    def visit(node, parent):
        for nm in sorted(node, key=str.lower):
            off = len(names)
            names.extend(nm.encode() + b"\0")
            if isinstance(node[nm], dict):
                me = len(entries)
                entries.append([1 << 24 | off, parent, 0])
                visit(node[nm], me)
                entries[me][2] = len(entries)
            else:
                b = files[node[nm]]
                entries.append([off, data_base + len(payload), len(b)])
                payload.extend(b + bytes(-len(b) % 32))

    visit(tree, 0)
    entries[0][2] = len(entries)
    return b"".join(struct.pack(">3I", *e) for e in entries) + bytes(names), bytes(payload)


def make_disc(wii=False, files=None, padding=()):
    """(image, main.dol) of a disc holding `files` (default FILES), and after them one 32 KiB block of GameCube
    padding (rvz.junk) per 68-byte seed in `padding`."""
    files = FILES if files is None else files
    dol = make_dol()
    head = bytearray(0x440)
    head[0:6] = b"GTSE01"
    head[6], head[7] = 0, 1
    struct.pack_into(">I", head, 0x18, gcdisc.WII_MAGIC if wii else 0)
    struct.pack_into(">I", head, 0x1C, 0 if wii else gcdisc.GC_MAGIC)
    head[0x20:0x20 + 9] = b"Test Game"
    bi2 = bytearray(0x2000)
    struct.pack_into(">I", bi2, 0x18, 1)
    app = bytearray(0x20)
    app[0:10] = b"2003/01/01"
    struct.pack_into(">3I", app, 0x10, 0x81200000, 0, 0)
    img = bytearray(head + bi2 + app)
    img = img.ljust(0x3000, b"\0")
    dol_off = len(img)
    img += dol
    img += bytes(-len(img) % 0x800)
    fst_off = len(img)
    table, _ = fst(files, 0)
    data_base = fst_off + len(table) + (-(fst_off + len(table)) % 0x8000)
    table, payload = fst(files, data_base)
    img = (img + table).ljust(data_base, b"\0") + payload
    img += bytes(-len(img) % 0x8000)
    for seed in padding:
        img += rvz.junk(seed, len(img), 0x8000)
    struct.pack_into(">3I", img, 0x420, dol_off, fst_off, len(table))
    return bytes(img), dol


class Header(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tmpdir("gcdisc_")
        cls.img, cls.dol = make_disc()
        cls.iso = cls.d / "game.iso"
        cls.iso.write_bytes(cls.img)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def disc(self):
        disc = gcdisc.Disc(self.iso)
        self.addCleanup(disc.close)
        return disc

    def test_header(self):
        h = self.disc().header()
        self.assertEqual((h["game_id"], h["maker"], h["disc"], h["version"], h["title"], h["region"]),
                         ("GTSE01", "01", 1, 1, "Test Game", "USA"))
        self.assertEqual((h["apploader_date"], h["apploader_entry"]), ("2003/01/01", "0x81200000"))

    def test_files(self):
        disc = self.disc()
        rows = disc.walk()
        self.assertEqual(sorted(p for p, _, _ in rows), sorted(FILES))
        for p, off, size in rows:
            self.assertEqual(disc.read(off, size), FILES[p], p)

    def test_dol(self):
        self.assertEqual(self.disc().dol(), self.dol)

    def test_refused(self):
        wii = self.d / "wii.iso"
        wii.write_bytes(make_disc(wii=True)[0])
        with self.assertRaisesRegex(SystemExit, "Wii"):
            gcdisc.Disc(wii)
        junk = self.d / "junk.iso"
        junk.write_bytes(bytes(0x1000))
        with self.assertRaisesRegex(SystemExit, "no GameCube disc header"):
            gcdisc.Disc(junk)


class Layout(unittest.TestCase):
    def test_codewarrior_layout(self):
        lay = gcdisc.layout(make_dol())
        names = [s["name"] for s in lay["sections"]]
        self.assertEqual(names, [".init", ".text", "D0", "D1", ".data", ".sdata", ".sdata2"])
        self.assertEqual(lay["start_registers"], {"r1": "80400000", "r2": f"{SDA2:08x}", "r13": f"{SDA:08x}"})
        p = lay["project"]
        self.assertEqual(p["game_ranges"], [[f"{INIT:08x}", f"{TEXT + len(text_code()):08x}"]])
        self.assertEqual(p["global_ranges"], [[f"{a:08x}", f"{b:08x}"] for a, b in (
            (DATA, DATA + 0x100), (BSS, SDATA), (SDATA, SDATA + 0x80), (SDATA + 0x80, SDATA2))])

    def test_fixed_regs(self):
        lay = gcdisc.layout(make_dol())
        # GQR0, GQR2 one constant each (GQR2's restore ignored); GQR3 restored only (no value); GQR4 also set from an
        # argument: not pinned
        self.assertEqual(lay["project"]["fixed_regs"], {"r2": f"{SDA2:08x}", "r13": f"{SDA:08x}",
                                                        "GQR0": "00000000", "GQR2": "00040004"})
        self.assertTrue(any(n.startswith("GQR4: not pinned: written with 00060006 and 1") for n in lay["notes"]))
        self.assertFalse(any(n.startswith("GQR3") for n in lay["notes"]))

    def test_no_start_registers(self):
        """A start-up that does not load r2/r13 as lis/ori: named in the notes, the sections fall back to order."""
        dol = bytearray(make_dol())
        start = 0x100 + (TEXT - INIT) + 0x40
        dol[start:start + 28] = be(*[NOP] * 7)
        lay = gcdisc.layout(bytes(dol))
        self.assertNotIn("r2", lay["project"]["fixed_regs"])
        self.assertTrue(any("r13: the start-up code does not load it" in n for n in lay["notes"]))
        self.assertEqual(len(lay["project"]["global_ranges"]), 4)


class Compressed(unittest.TestCase):
    def test_yaz0_round_trip(self):
        for data in (b"", b"a", b"abcabcabcabc" * 40, bytes(range(256)) * 3, b"\0" * 5000, REL_B):
            self.assertEqual(gcdisc.yaz0(yaz0_encode(data)), data)
        self.assertEqual(gcdisc.yaz0(yaz0_encode(b"xyz" * 100), limit=10), b"xyzxyzxyzx")
        with self.assertRaises(ValueError):
            gcdisc.yaz0(b"Yay0" + bytes(12))

    def test_rarc(self):
        files = {"c.rel": REL_C, "two.bin": b"\x01\x02"}
        self.assertEqual(gcdisc.rarc(make_rarc(files)), list(files.items()))


class Rels(unittest.TestCase):
    def test_rel_info(self):
        info = gcdisc.rel_info(REL_A)
        self.assertEqual((info["module_id"], info["version"], info["bss_size"], info["imports"]), (1, 3, 0x40, [0, 1]))
        self.assertEqual([(s["index"], s["exec"], s["bss"], s["size"]) for s in info["sections"]],
                         [(1, True, False, 32), (2, False, False, 4), (3, False, True, 0x40)])
        self.assertEqual((info["align"], info["bss_align"], info["fix_size"]), (32, 8, 0))
        self.assertIsNone(info["load_address"])
        self.assertIsNone(gcdisc.rel_info(b"\0" * 0x80))
        self.assertIsNone(gcdisc.rel_info(b"\0" * 8))

    def test_found_in_every_form(self):
        d = tmpdir("gcdisc_")
        self.addCleanup(shutil.rmtree, d, True)
        (d / "g.iso").write_bytes(make_disc()[0])
        disc = gcdisc.Disc(d / "g.iso")
        self.addCleanup(disc.close)
        rows = gcdisc.modules(disc)
        self.assertEqual([(p, how, i["module_id"]) for p, how, i, _ in rows],
                         [("/rel/a.rel", "rel", 1), ("/rel/b.rel.szs", "yaz0+rel", 2),
                          ("/rel/packs/RELS.arc:c.rel", "yaz0+rarc+rel", 3)])
        self.assertEqual([b for *_, b in rows], [REL_A, REL_B, REL_C])

    def test_game_data_not_decompressed(self):
        """Yaz0 files without a REL (most .szs/.arc files are models, stages, fonts) are read only as far as their
        header and name table: TP has hundreds."""
        d = tmpdir("gcdisc_")
        self.addCleanup(shutil.rmtree, d, True)
        (d / "g.iso").write_bytes(make_disc()[0])
        disc = gcdisc.Disc(d / "g.iso")
        self.addCleanup(disc.close)
        real, whole = gcdisc.yaz0, []

        def spy(data, limit=None):
            if limit is None:
                whole.append(struct.unpack(">I", data[4:8])[0])
            return real(data, limit)

        with mock.patch.object(gcdisc, "yaz0", spy):
            gcdisc.modules(disc)
        self.assertEqual(sorted(whole), sorted([len(REL_B), len(gcdisc.yaz0(FILES["/rel/packs/RELS.arc"]))]))


FIXTURES = TOOL / "tests" / "data"


class Names(unittest.TestCase):
    def test_stay_inside(self):  # extract and setup write only under their new folder
        for bad in ("../x.rel", "files/a/../../x", "C:/x", "a\\..\\x", "a//b", "", "."):
            with self.assertRaises(ValueError):
                gcdisc.local_name(bad)
        with self.assertRaises(ValueError):
            gcdisc.rel_name("/files/../../x.rel", "")
        self.assertEqual(gcdisc.rel_name("/files/a.arc:rels/m.rel", "rarc+"), "files/files/a.arc/rels/m.rel")


class Containers(unittest.TestCase):
    """WIA and RVZ images read as the plain disc they hold."""

    @classmethod
    def setUpClass(cls):
        cls.d = tmpdir("gcdisc_")
        files = dict(fixture_files(), **{"/data/blank.bin": bytes(0x12000)})  # two all-zero groups
        cls.img = make_disc(files=files, padding=FIXTURE_SEEDS)[0]
        end = len(cls.img) - 0x8000 * len(FIXTURE_SEEDS)
        cls.padding = {end + 0x8000 * k: s for k, s in enumerate(FIXTURE_SEEDS)}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def image(self, path):
        img = rvz.Image(path)
        self.addCleanup(img.close)
        return img

    def check(self, path, img):
        r = self.image(path)
        self.assertEqual(r.size, len(img))
        self.assertEqual(r.read(0, len(img)), img)
        for off, n in ((0, 0x20), (0x70, 0x20), (0x7FF0, 0x30), (0x8000 * 3 - 5, 0x8000 + 10), (len(img) - 0x8001, 1),
                       (len(img) - 0x10, 0x10)):  # across the header override and group edges
            self.assertEqual(r.read(off, n), img[off:off + n], (off, n))
        with self.assertRaises(ValueError):
            r.read(len(img) - 4, 8)

    def test_wia_and_rvz_methods(self):
        names = {rvz.NONE: "none", rvz.BZIP2: "bzip2", rvz.LZMA: "lzma", rvz.LZMA2: "lzma2"}
        for is_rvz in (False, True):
            for method, name in names.items():
                with self.subTest(rvz=is_rvz, method=name):
                    path = self.d / f"t_{name}.{'rvz' if is_rvz else 'wia'}"
                    path.write_bytes(make_wia(self.img, method, is_rvz, padding=self.padding))
                    self.check(path, self.img)
                    groups = self.image(path).groups
                    self.assertEqual(sum(1 for g in groups if g[1] == 0), 2)  # the blank file
                    self.assertEqual(sum(1 for g in groups if g[2]), 2 if is_rvz else 0)  # the padding, packed
                    if is_rvz and method != rvz.NONE:
                        flags = {g[1] >> 31 for g in groups if g[1]}
                        self.assertEqual(flags, {0, 1})  # packed padding (a few seeds) does not shrink: stored as is

    def test_bigger_groups(self):
        """A group of several 32 KiB blocks: packing mixes data and seeds; the last group is short."""
        img = self.img + bytes(0x1000)
        path = self.d / "big.rvz"
        path.write_bytes(make_wia(img, rvz.LZMA2, chunk=0x20000, padding=self.padding))
        self.check(path, img)

    def test_refused(self):
        b = bytearray(make_wia(self.img, rvz.NONE, is_rvz=False))
        for patch, why in (((0x48 + 4, rvz.PURGE), "compression method 1"), ((0x48 + 4, rvz.ZSTD), "method 5"),
                           ((0x48, 2), "not a GameCube disc")):
            bad = bytearray(b)
            struct.pack_into(">I", bad, *patch)
            path = self.d / "bad.wia"
            path.write_bytes(bad)
            with self.assertRaisesRegex(SystemExit, why):
                gcdisc.Disc(path)

    def test_zstd_fixture(self):
        """tests/data/gc_fixture_zstd.rvz: Dolphin's converter run once (rvz, zstd level 19, 32 KiB groups) on
        make_disc(files=fixture_files(), padding=FIXTURE_SEEDS); its two padding blocks are packed as seeds."""
        img = make_disc(files=fixture_files(), padding=FIXTURE_SEEDS)[0]
        self.assertEqual(hashlib.sha256(img).hexdigest(),
                         "22c9be9dd95798970b5e8a9ba091221166d2e6937588f45de01f08f9ea9312be")  # the generator unchanged
        path = FIXTURES / "gc_fixture_zstd.rvz"
        r = self.image(path)
        self.assertEqual(r.method, rvz.ZSTD)
        self.assertEqual(sum(1 for g in r.groups if g[2]), 2)
        self.check(path, img)
        disc = gcdisc.Disc(path)
        self.addCleanup(disc.close)
        self.assertEqual([i["module_id"] for _, _, i, _ in gcdisc.modules(disc)], [7])

    def test_zstd_frames(self):
        """tests/data/gc_zstd_frames.zst: Zstandard features the disc fixture does not reach (see zstd_pieces)."""
        frames = (FIXTURES / "gc_zstd_frames.zst").read_bytes()
        self.assertEqual(rvz.zstd_decompress(frames), b"".join(zstd_pieces()))
        self.assertEqual(rvz.zstd_decompress(ZSTD_RLE_LITERALS), b"zzzzz")
        for bad in (frames[:-40], b"\x28\xb5\x2f\xfe" + frames[4:], frames[:4] + bytes([frames[4] | 8]) + frames[5:]):
            with self.assertRaises(ValueError):
                rvz.zstd_decompress(bad)

    def test_zstd_hostile(self):
        """A frame or block cut short, and output past the size expected, are refused: never a hang or memory filled
        (an image's groups and tables are decoded with their expected size as the limit)."""
        for bad in (bytes.fromhex("28b52ffd2005"), bytes.fromhex("28b52ffd2005") + (40 << 3).to_bytes(3, "little")):
            with self.assertRaises(rvz.ZstdError):
                rvz.zstd_decompress(bad)
        rle = (0x20000 << 3 | 1 << 1).to_bytes(3, "little") + b"z"  # an RLE block: 128 KiB of 'z'
        bomb = bytes.fromhex("28b52ffd2000") + rle * 20 + (1 << 3 | 1 << 1 | 1).to_bytes(3, "little") + b"z"
        self.assertEqual(rvz.zstd_decompress(bomb), b"z" * (20 * 0x20000 + 1))
        with self.assertRaises(rvz.ZstdError):
            rvz.zstd_decompress(bomb, 0x20000)
        with self.assertRaises(rvz.ZstdError):  # one block over 128 KiB
            rvz.zstd_decompress(bytes.fromhex("28b52ffd2000") + (0x20001 << 3 | 1 << 1 | 1).to_bytes(3, "little") + b"z")


class Commands(unittest.TestCase):
    def setUp(self):
        self.d = tmpdir("gcdisc_")
        self.img = make_disc()[0]
        self.iso = self.d / "game.iso"
        self.iso.write_bytes(self.img)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_main(self, *argv):
        with mock.patch("sys.stdout"):
            gcdisc.main([str(a) for a in argv])

    def test_extract(self):
        out = self.d / "out"
        self.run_main("extract", self.iso, out)
        self.assertEqual([p.name for p in out.iterdir()], ["main.dol"])
        out2 = self.d / "out2"
        self.run_main("extract", self.iso, out2, "--code")
        self.assertEqual(sorted(p.relative_to(out2).as_posix() for p in out2.rglob("*") if p.is_file()),
                         ["main.dol", "rel/a.rel", "rel/b.rel.szs", "rel/packs/RELS.arc"])
        self.assertEqual((out2 / "rel" / "b.rel.szs").read_bytes(), FILES["/rel/b.rel.szs"])  # as on the disc
        with self.assertRaises(FileExistsError):
            self.run_main("extract", self.iso, out)
        with self.assertRaisesRegex(SystemExit, "not on the disc"):
            self.run_main("extract", self.iso, self.d / "out3", "nope.bin")

    def test_setup(self):
        before = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        disc_dir, ws = self.d / "artifacts" / "disc", self.d / "ws"
        self.run_main("setup", self.iso, disc_dir, ws)
        self.assertEqual(hashlib.sha256(self.iso.read_bytes()).hexdigest(), before)  # the image is never changed
        dol = (disc_dir / "main.dol").read_bytes()
        self.assertEqual(dol, make_dol())
        cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        linked = (disc_dir / "linked.dol").read_bytes()  # the three modules linked onto main.dol (test_gamecube_rel)
        self.assertEqual((cfg["target"], cfg["binary"], cfg["binary_sha256"], cfg["ghidra_name"], cfg["seed_entries"]),
                         ("gamecube_ppc", "../artifacts/disc/linked.dol", hashlib.sha256(linked).hexdigest(), "gtse01",
                          "../artifacts/disc/seeds.txt"))
        link = json.loads((disc_dir / "link.json").read_text(encoding="utf-8"))
        self.assertEqual([m["name"] for m in link["modules"]], ["files/rel/a.rel", "files/rel/b.rel",
                                                                "files/rel/packs/RELS.arc/c.rel"])
        lay = gcdisc.layout(dol)
        for k, v in lay["project"].items():
            self.assertEqual(cfg[k], link["project"].get(k, v), k)
            if isinstance(v, list):
                self.assertEqual(cfg[k][:len(v)], v, k)  # main.dol's own ranges first
        self.assertEqual(len(cfg["game_ranges"]), len(lay["project"]["game_ranges"]) + 3)  # each module's code
        ws_none = self.d / "ws_none"
        self.run_main("setup", self.iso, self.d / "disc_none", ws_none, "--rels", "none")
        cfg = json.loads((ws_none / "project.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["binary"], cfg["binary_sha256"]),
                         ("../disc_none/main.dol", hashlib.sha256(dol).hexdigest()))
        self.assertNotIn("seed_entries", cfg)
        self.assertFalse((self.d / "disc_none" / "linked.dol").exists())
        self.run_main("setup", self.iso, self.d / "disc_two", self.d / "ws_two", "--rels", "1,3")
        link = json.loads((self.d / "disc_two" / "link.json").read_text(encoding="utf-8"))
        self.assertEqual([m["module_id"] for m in link["modules"]], [1, 3])
        self.assertEqual((ws / "oracle" / "TcInputs.java").read_bytes(),
                         (TOOL / "targets" / "gamecube_ppc" / "template" / "TcInputs.java").read_bytes())
        rec = json.loads((disc_dir / "disc.json").read_text(encoding="utf-8"))
        self.assertEqual([r["file"] for r in rec["rels"]], ["files/rel/a.rel", "files/rel/b.rel",
                                                            "files/rel/packs/RELS.arc/c.rel"])
        for r, want in zip(rec["rels"], (REL_A, REL_B, REL_C)):  # decompressed
            self.assertEqual((disc_dir / r["file"]).read_bytes(), want)
        self.assertEqual(rec["main_dol"]["sha1"], hashlib.sha1(dol).hexdigest())
        self.assertEqual(len(rec["files"]), len(FILES))
        with self.assertRaisesRegex(SystemExit, "never overwrites"):  # a second run stops before writing
            self.run_main("setup", self.iso, self.d / "disc2", ws)
        self.assertFalse((self.d / "disc2").exists())
        with self.assertRaises(FileExistsError):
            self.run_main("setup", self.iso, disc_dir, self.d / "ws2")


if __name__ == "__main__":
    unittest.main(verbosity=1)

"""The Xbox 360 target (targets/xbox360_ppc), without Ghidra:
- aes.py: the FIPS-197 vector; the pure-Python and Windows CBC paths agree;
- xex.py on a synthetic XEX2 (encrypted, basic compression, a PE image with .pdata, imports): the image, sections,
  functions, imports and their resolution; LZX and delta files are refused;
- xiso.py on a synthetic XDVDFS disc: the directory tree and extraction;
- the real Too Human disc, when it is on this machine (catalog counts).
test_xbox360_glue.py (scanner, judge glue, runner) and test_xbox360_helpers.py (instruction helpers) use the
synthetic image built here.

usage: python tests/test_xbox360_ppc.py
"""
import json
import os
import pathlib
import random
import shutil
import struct
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
X360 = TOOL / "targets" / "xbox360_ppc"
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
sys.path += [str(X360), str(X360 / "tools")]  # last: the target folder has a hooks.py, as gamecube_ppc does
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

import aes  # noqa: E402
import xex  # noqa: E402
import xiso  # noqa: E402

from core import toolcfg  # noqa: E402

# the real disc: this machine's "xbox360_disc" (config.local.json); the test skips without it
TOO_HUMAN = pathlib.Path(toolcfg.load(TOOL).get("xbox360_disc") or "no-disc.iso")


def tmpdir(prefix):
    root = TOOL / "tests" / ".work"
    root.mkdir(exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=root))


# ---------- AES encryption, for building test files (the target only decrypts) ----------

def _enc_block(rk, block):
    s = [b ^ k for b, k in zip(block, rk[0])]
    for rnd in range(1, 11):
        s = [aes.SBOX[s[i % 4 + 4 * ((i // 4 + i % 4) % 4)]] for i in range(16)]
        if rnd < 10:
            out = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                m2 = [aes._xtime(x) for x in a]
                out += [m2[0] ^ m2[1] ^ a[1] ^ a[2] ^ a[3], a[0] ^ m2[1] ^ m2[2] ^ a[2] ^ a[3],
                        a[0] ^ a[1] ^ m2[2] ^ m2[3] ^ a[3], m2[0] ^ a[0] ^ a[1] ^ a[2] ^ m2[3]]
            s = out
        s = [b ^ k for b, k in zip(s, rk[rnd])]
    return bytes(s)


def encrypt_cbc(key, iv, data):
    rk, out, prev = aes.expand_key(key), bytearray(), iv
    for o in range(0, len(data), 16):
        prev = _enc_block(rk, bytes(a ^ b for a, b in zip(data[o:o + 16], prev)))
        out += prev
    return bytes(out)


class Aes(unittest.TestCase):
    def test_fips197(self):
        key = bytes(range(16))
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        cipher = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(aes.decrypt_ecb_block(key, cipher), plain)
        self.assertEqual(_enc_block(aes.expand_key(key), plain), cipher)

    def test_cbc_paths_agree(self):
        rng = random.Random(1)
        key, iv = bytes(rng.randrange(256) for _ in range(16)), bytes(rng.randrange(256) for _ in range(16))
        plain = bytes(rng.randrange(256) for _ in range(16 * 300))
        enc = encrypt_cbc(key, iv, plain)
        self.assertEqual(aes.decrypt_cbc_python(key, iv, enc), plain)
        fast = aes.decrypt_cbc_windows(key, iv, enc)
        if fast is not None:
            self.assertEqual(fast, plain)
        self.assertEqual(aes.decrypt_cbc(key, iv, enc), plain)


# ---------- a synthetic title ----------

BASE = 0x82000000
IMAGE_SIZE = 0x40000
TEXT = 0x82010000
PDATA = 0x82008000
IDATA = 0x82030000
THUNK = 0x82012000


def w32(buf, addr, v):
    struct.pack_into(">I", buf, addr - BASE, v)


def bl(src, dst, link=True):
    return 0x48000000 | ((dst - src) & 0x03FFFFFC) | int(link)


def synthetic_image():
    """A PE image: .rdata (+ .pdata inside it), .text with three functions, the GPR save helper and an import thunk,
    .data with a function pointer, .idata with two import slots."""
    img = bytearray(IMAGE_SIZE)
    img[0:2] = b"MZ"
    pe = 0x80
    struct.pack_into("<I", img, 0x3C, pe)
    img[pe:pe + 4] = b"PE\0\0"
    sections = [(".rdata", 0x1000, 0xF000, 0x40000000), (".text", 0x10000, 0x10000, 0x60000020),
                (".data", 0x20000, 0x10000, 0xC0000040), (".idata", 0x30000, 0x1000, 0xC0000040),
                (".reloc", 0x50000, 0x1000, 0x42000040)]
    struct.pack_into("<HH", img, pe + 4, xex.PE_MACHINE_POWERPCBE, len(sections))
    opt = pe + 24
    struct.pack_into("<H", img, pe + 20, 224)
    struct.pack_into("<H", img, opt, 0x10B)
    struct.pack_into("<I", img, opt + 92, 16)  # NumberOfRvaAndSizes
    struct.pack_into("<II", img, opt + 96 + 8 * 3, PDATA - BASE, 3 * 8)
    for i, (name, va, size, ch) in enumerate(sections):
        o = opt + 224 + 40 * i
        img[o:o + 8] = name.encode().ljust(8, b"\0")
        struct.pack_into("<IIII", img, o + 8, size, va, size, va)
        struct.pack_into("<I", img, o + 36, ch)
    # .text: f1 at TEXT (calls leaf, the import thunk and the helper), the helper, leaf (no .pdata), vmx function
    f1 = TEXT
    code = [0x7D8802A6, bl(f1 + 4, TEXT + 0x100), bl(f1 + 8, TEXT + 0x200), bl(f1 + 12, THUNK),
            0xF8610010, 0x7C6802A6 & 0, 0x4E800020]
    code[5] = 0xFC22007A  # fmadd f1,f2,f1,f0
    for k, v in enumerate(code):
        w32(img, f1 + 4 * k, v)
    helper = TEXT + 0x100  # __savegprlr_14 .. 31, stw r12,-8(r1), blr
    for n in range(14, 32):
        w32(img, helper + 4 * (n - 14), (62 << 26) | (n << 21) | (1 << 16) | ((-0x98 + 8 * (n - 14)) & 0xFFFF))
    w32(img, helper + 72, 0x9181FFF8)
    w32(img, helper + 76, 0x4E800020)
    leaf = TEXT + 0x200
    for k, v in enumerate([0x38600001, 0x4E800020]):
        w32(img, leaf + 4 * k, v)
    vec = TEXT + 0x300
    for k, v in enumerate([0x100B61CB, 0x4E800020]):  # stvx128 v64,r11,r12
        w32(img, vec + 4 * k, v)
    ptr_target = TEXT + 0x400
    for k, v in enumerate([0x38600002, 0x4E800020]):
        w32(img, ptr_target + 4 * k, v)
    w32(img, 0x82020010, ptr_target)  # a vtable word in .data
    # .pdata: f1, the helper, the vmx function
    for k, (begin, words) in enumerate([(f1, 7), (helper, 20), (vec, 2)]):
        w32(img, PDATA + 8 * k, begin)
        w32(img, PDATA + 8 * k + 4, (words << 8) | 3)
    # imports: xboxkrnl #5 is a function (slot + thunk), #7 a variable (slot)
    w32(img, IDATA, 0x00000005)
    w32(img, IDATA + 4, 0x00000007)
    for k, v in enumerate([0x01000005, 0x02000005, 0x7D6903A6, 0x4E800420]):
        w32(img, THUNK + 4 * k, v)
    return bytes(img)


TEST_MASTER = bytes(range(0xA0, 0xB0))  # stands in for the console's key, which is not in the repository


def synthetic_xex(img, encrypt=True, compression=xex.COMP_BASIC, zero_run=0x8000):
    """An XEX2 around img: optional headers (file format, entry, base, imports), security info with the file key."""
    file_key = bytes(range(0x10, 0x20))
    if compression == xex.COMP_BASIC:  # drop one run of zeros (inside .data, which is all zero there)
        cut = 0x28000
        payload = img[:cut] + img[cut + zero_run:]
        blocks = [(cut, zero_run), (len(img) - cut - zero_run, 0)]
        ff = struct.pack(">IHH", 8 + 8 * len(blocks), 1 if encrypt else 0, 1) + b"".join(
            struct.pack(">II", *b) for b in blocks)
    else:
        payload = img
        ff = struct.pack(">IHHI", 12, 1 if encrypt else 0, compression, 0x8000)
    payload += bytes(-len(payload) % 16)
    if encrypt:
        session = file_key
        enc_key = _enc_block(aes.expand_key(TEST_MASTER), session)
        payload = encrypt_cbc(session, bytes(16), payload)
    else:
        enc_key = file_key
    names = b"xboxkrnl.exe\0\0\0\0"
    records = [IDATA, THUNK, IDATA + 4]
    lib = struct.pack(">I", 0x28 + 4 * len(records)) + bytes(20) + struct.pack(">III", 0, 0x20000000, 0) + \
        struct.pack(">HH", 0, len(records)) + b"".join(struct.pack(">I", r) for r in records)
    imports = struct.pack(">III", 12 + len(names) + len(lib), len(names), 1) + names + lib
    headers = [(xex.H_FILE_FORMAT, ff), (xex.H_ENTRY_POINT, TEXT), (xex.H_IMAGE_BASE, BASE),
               (xex.H_IMPORT_LIBRARIES, imports), (xex.H_DEFAULT_STACK_SIZE, 0x40000)]
    if compression == xex.COMP_DELTA:
        headers.append((xex.H_DELTA_PATCH, struct.pack(">I", 8) + bytes(4)))
    opt_end = 0x18 + 8 * len(headers)
    sec_off = 0x200
    data_off = 0x400
    table, blobs = bytearray(), bytearray()
    for key, value in headers:
        if isinstance(value, int):
            table += struct.pack(">II", key, value)
        else:
            table += struct.pack(">II", key, data_off + len(blobs))
            blobs += value + bytes(-len(value) % 4)
    pe_off = (data_off + len(blobs) + 0xFFF) & ~0xFFF
    head = bytearray(pe_off)
    head[0:4] = xex.MAGIC
    struct.pack_into(">IIIII", head, 4, 0, pe_off, 0, sec_off, len(headers))
    head[0x18:opt_end] = table
    head[data_off:data_off + len(blobs)] = blobs
    struct.pack_into(">II", head, sec_off, 0x184, IMAGE_SIZE)
    struct.pack_into(">II", head, sec_off + 0x10C, 0, BASE)
    head[sec_off + 0x150:sec_off + 0x160] = enc_key
    struct.pack_into(">I", head, sec_off + 0x180, 1)
    struct.pack_into(">I", head, sec_off + 0x184, ((IMAGE_SIZE // 0x10000) << 4) | 1)
    return bytes(head) + payload


class Xex(unittest.TestCase):
    def test_image_and_facts(self):
        img = synthetic_image()
        for encrypt in (True, False):
            x = xex.Xex(synthetic_xex(img, encrypt=encrypt))
            got = x.image(keys=[xex.DEVKIT_KEY, TEST_MASTER])
            self.assertEqual(got, img, f"encrypt={encrypt}")
        d = xex.describe(x, got)
        self.assertEqual((d["load_address"], d["image_size"], d["entry_point"]), (BASE, IMAGE_SIZE, TEXT))
        names = [s["name"] for s in d["sections"]]
        self.assertEqual(names, [".rdata", ".text", ".data", ".idata", ".reloc"])
        text = d["sections"][1]
        self.assertEqual((text["start"], text["end"], text["execute"], text["write"]), (TEXT, TEXT + 0x10000, True, False))
        self.assertFalse(d["sections"][4]["loaded"])  # past the image: the XEX drops .reloc
        self.assertEqual(d["pdata"], [[TEXT, 28, 3], [TEXT + 0x100, 80, 3], [TEXT + 0x300, 8, 3]])
        imps = sorted((e["ordinal"], e["kind"], e["slot"], e["thunk"]) for e in d["imports"])
        self.assertEqual(imps, [(5, "function", IDATA, THUNK), (7, "variable", IDATA + 4, None)])
        self.assertEqual(d["pages"], [{"start": BASE, "end": BASE + IMAGE_SIZE, "kind": "code"}])
        loaded = xex.resolve_imports(got, d)
        self.assertEqual(struct.unpack_from(">II", loaded, IDATA - BASE), (THUNK, xex.KERNEL_VARS))

    def test_retail_key_is_local(self):
        import re  # no 16-byte key literal in the reader: the console's key stays in config.local.json
        self.assertIsNone(re.search(r"[0-9a-fA-F]{32}", (X360 / "tools" / "xex.py").read_text(encoding="utf-8")))
        d = tmpdir("x360key_")
        old = os.environ.pop(xex.KEY_ENV, None)
        try:
            self.assertIsNone(xex.retail_key(d))
            (d / "config.local.json").write_text(json.dumps({xex.KEY_CONFIG: TEST_MASTER.hex()}), encoding="utf-8")
            self.assertEqual(xex.retail_key(d), TEST_MASTER)
            os.environ[xex.KEY_ENV] = "00" * 16
            self.assertEqual(xex.retail_key(d), bytes(16))
            for bad in ("abcd", "zz" * 16):  # too short, not hex: XexError (catalog_inputs reports it), no ValueError
                os.environ[xex.KEY_ENV] = bad
                with self.assertRaises(xex.XexError):
                    xex.retail_key(d)
        finally:
            os.environ.pop(xex.KEY_ENV, None)
            if old is not None:
                os.environ[xex.KEY_ENV] = old
            shutil.rmtree(d, ignore_errors=True)

    def test_refusals(self):
        img = synthetic_image()
        with self.assertRaisesRegex(xex.XexError, "LZX"):
            xex.Xex(synthetic_xex(img, compression=xex.COMP_NORMAL)).image()
        with self.assertRaisesRegex(xex.XexError, "delta"):
            xex.Xex(synthetic_xex(img, compression=xex.COMP_DELTA))
        with self.assertRaisesRegex(xex.XexError, "not an XEX2"):
            xex.Xex(b"XEX1" + bytes(100))
        bad = bytearray(synthetic_xex(img))
        bad[0x200 + 0x150] ^= 1  # a wrong file key: the image does not decrypt to MZ
        with self.assertRaisesRegex(xex.XexError, "MZ"):
            xex.Xex(bytes(bad)).image(keys=[TEST_MASTER])


# ---------- a synthetic disc ----------

def synthetic_disc(partition=0):
    sec = xiso.SECTOR
    disc = bytearray(partition + 40 * sec)

    def entry(left, right, start, size, attr, name):
        e = struct.pack("<HHIIBB", left, right, start, size, attr, len(name)) + name.encode()
        return e + b"\xff" * (-len(e) % 4)

    # root (sector 33): b.xex (root of the tree), left child a.bin, right child sub/ ; sub (sector 34): DEFAULT.XEX
    e_a = entry(0, 0, 36, 5, 0, "a.bin")
    e_sub = entry(0, 0, 34, sec, xiso.ATTR_DIR, "sub")
    first = entry(0, 0, 35, 9, 0, "b.xex")
    off_a, off_sub = len(first), len(first) + len(e_a)
    root = entry(off_a // 4, off_sub // 4, 35, 9, 0, "b.xex") + e_a + e_sub
    sub = entry(0, 0, 37, 4, 0, "DEFAULT.XEX")
    vd = bytearray(sec)
    vd[0:20] = xiso.MAGIC
    struct.pack_into("<II", vd, 20, 33, len(root))
    vd[0x7EC:0x7EC + 20] = xiso.MAGIC
    for s, data in ((32, vd), (33, root), (34, sub), (35, b"XEX2body!"), (36, b"hello"), (37, b"XEX2")):
        disc[partition + s * sec:partition + s * sec + len(data)] = data
    return bytes(disc)


class Xiso(unittest.TestCase):
    def test_tree_and_extract(self):
        d = tmpdir("xiso_")
        try:
            for part in (0, 0x02080000):
                (d / "disc.iso").write_bytes(synthetic_disc(part))
                with xiso.Disc(d / "disc.iso") as disc:
                    self.assertEqual(disc.base, part)
                    got = sorted((p, s, n, isdir) for p, s, n, isdir in disc.entries())
                    self.assertEqual(got, [("a.bin", 36, 5, False), ("b.xex", 35, 9, False),
                                           ("sub", 34, xiso.SECTOR, True), ("sub/DEFAULT.XEX", 37, 4, False)])
                    self.assertEqual(disc.extract("SUB/default.xex", d / "out.xex"), 4)
                    self.assertEqual((d / "out.xex").read_bytes(), b"XEX2")
            (d / "bad.iso").write_bytes(bytes(40 * xiso.SECTOR))
            with self.assertRaises(xiso.DiscError):
                xiso.Disc(d / "bad.iso").close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------- the scan ----------

# ---------- the real disc ----------

def key_set():
    """A retail key is configured; a malformed one counts, so TooHuman fails on it instead of every test module
    that imports this one failing to load."""
    try:
        return xex.retail_key() is not None
    except xex.XexError:
        return True


def too_human_key():
    """What the real disc's figures depend on: the disc (size, mtime), every .py of the target and its tools, and the
    retail key (its hash only)."""
    import hashlib
    h = hashlib.sha256()
    st = TOO_HUMAN.stat()
    h.update(f"{TOO_HUMAN.resolve()} {st.st_size} {st.st_mtime_ns}\n".encode())
    for p in sorted([*X360.glob("*.py"), *(X360 / "tools").glob("*.py")]):
        h.update(p.name.encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    h.update(hashlib.sha256(xex.retail_key() or b"").digest())
    return h.hexdigest()


@unittest.skipUnless(TOO_HUMAN.exists() and key_set(), "no Too Human disc image or no retail key here")
class TooHuman(unittest.TestCase):
    """The figures are kept in tests/.work/x360_too_human.json by too_human_key: the disc is decrypted again (6 s)
    only when it or the code that reads it changed."""
    def figures(self):
        import catalog_inputs
        memo, key = TOOL / "tests" / ".work" / "x360_too_human.json", too_human_key()
        try:
            got = json.loads(memo.read_text(encoding="utf-8"))
            if got.get("key") == key:
                return got["figures"]
        except (OSError, ValueError):
            pass
        d = tmpdir("x360th_")
        try:
            cat = catalog_inputs.build(TOO_HUMAN, d)
            desc = json.loads((d / "image.json").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        figures = {"image_sha1": desc["image_sha1"], "load": [desc["load_address"], desc["entry_point"]],
                   "pdata": len(desc["pdata"]), "helpers": len(cat["helpers"]), "by_source": cat["stats"]["by_source"],
                   "vmx": cat["stats"]["refused"]["unsupported: VMX"]}
        memo.write_text(json.dumps({"key": key, "figures": figures}), encoding="utf-8")
        return figures

    def test_catalog_inputs(self):
        f = self.figures()
        self.assertEqual(f["image_sha1"], "dac2c9fe5b5df7a18570f694e63066f91e385648")
        self.assertEqual(tuple(f["load"]), (0x82000000, 0x82A46B18))
        self.assertEqual(f["pdata"], 72768)
        self.assertEqual(f["helpers"], 236)
        self.assertEqual(f["by_source"], {"pdata": 72768, "bl": 2520, "pointer": 12905})
        self.assertEqual(f["vmx"], 1403)


if __name__ == "__main__":
    unittest.main()

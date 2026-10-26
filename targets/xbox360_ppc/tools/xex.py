"""Xbox 360 executables (XEX2): the header, decryption, decompression and the loaded PE image.

DecompPipeline's own reader, written from the published XEX2 layout (REFERENCES.md); no third-party code. What it
gives the rest of the target:
- the image exactly as the console maps it at its load address (0x82000000 for a title): encrypted payloads are
  decrypted (AES-128-CBC with the title's file key, itself decrypted with the retail or the development key) and
  "basic" compression is expanded (runs of data, then runs of zeros);
- the PE section table of that image (names, ranges, code/data flags), the entry point, the `.pdata` function table
  (begin address and length of every function with unwind data), the imports (library, ordinal, the import address
  slot and, for a function, the 16-byte call thunk), TLS and the default stack size.

Refused, with the reason: "normal" (LZX) compression and delta patches (`XexError`). Too Human uses "basic"
compression; LZX is a later item (DESIGN.md, open questions).

usage: python xex.py <default.xex> <out_dir>     writes image.bin and image.json (see extract())
"""
import hashlib
import json
import os
import pathlib
import struct
import sys

import aes
from pe import PE_MACHINE_POWERPCBE, PeError, pdata, pe_sections  # noqa: F401  (the tests use xex.PE_MACHINE_...)

MAGIC = b"XEX2"
DEVKIT_KEY = bytes(16)
# The console's retail key is never in this repository: it comes from DecompPipeline/config.local.json (git-ignored)
# "xex_retail_key" (32 hex digits), or the environment variable DP_XEX_RETAIL_KEY.
KEY_CONFIG = "xex_retail_key"
KEY_ENV = "DP_XEX_RETAIL_KEY"
DP_ROOT = pathlib.Path(__file__).resolve().parents[3]


def retail_key(root=DP_ROOT):
    """The retail key from the environment or config.local.json, or None when this machine has none."""
    text = os.environ.get(KEY_ENV)
    if not text:
        local = pathlib.Path(root) / "config.local.json"
        try:
            text = json.loads(local.read_text(encoding="utf-8")).get(KEY_CONFIG)
        except (OSError, ValueError, AttributeError):
            text = None
    if not text:
        return None
    try:
        key = bytes.fromhex(text)
    except (TypeError, ValueError):
        key = b""
    if len(key) != 16:
        raise XexError(f"{KEY_CONFIG} must be 32 hex digits")
    return key

# Optional header keys (the low byte is the size in words: 0x00 or 0x01 = the value itself, 0xff = sized data).
H_RESOURCE_INFO = 0x000002FF
H_FILE_FORMAT = 0x000003FF
H_DELTA_PATCH = 0x000005FF
H_ENTRY_POINT = 0x00010100
H_IMAGE_BASE = 0x00010201
H_IMPORT_LIBRARIES = 0x000103FF
H_ORIGINAL_PE_NAME = 0x000183FF
H_STATIC_LIBRARIES = 0x000200FF
H_TLS_INFO = 0x00020104
H_DEFAULT_STACK_SIZE = 0x00020200
H_SYSTEM_FLAGS = 0x00030000
H_EXECUTION_INFO = 0x00040006

ENC_NONE, ENC_NORMAL = 0, 1
COMP_NONE, COMP_BASIC, COMP_NORMAL, COMP_DELTA = 0, 1, 2, 3


class XexError(Exception):
    pass


def u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


class Xex:
    """A parsed XEX2 file. Attributes: flags, pe_offset, security_offset, headers {key: value or offset}, image_size,
    load_address, image_flags, file_key, encryption, compression, basic_blocks [(data, zeros)], window_size."""

    def __init__(self, data):
        if data[:4] != MAGIC:
            raise XexError(f"not an XEX2 file (magic {data[:4]!r})")
        self.data = data
        self.flags = u32(data, 0x04)
        self.pe_offset = u32(data, 0x08)
        self.security_offset = u32(data, 0x10)
        count = u32(data, 0x14)
        self.headers = {}
        for i in range(count):
            key, value = struct.unpack_from(">II", data, 0x18 + 8 * i)
            self.headers[key] = value
        s = self.security_offset
        self.image_size = u32(data, s + 0x004)
        self.image_flags = u32(data, s + 0x10C)
        self.load_address = u32(data, s + 0x110)
        self.file_key = bytes(data[s + 0x150:s + 0x160])
        self.page_descriptors = []
        for i in range(u32(data, s + 0x180)):
            v = u32(data, s + 0x184 + 24 * i)
            self.page_descriptors.append((v >> 4, v & 0xF))
        if H_DELTA_PATCH in self.headers:
            raise XexError("delta patch XEX (an update over a base file) is not supported")
        ff = self.headers.get(H_FILE_FORMAT)
        if ff is None:
            raise XexError("no file format header")
        size = u32(data, ff)
        self.encryption, self.compression = u16(data, ff + 4), u16(data, ff + 6)
        self.basic_blocks, self.window_size = [], None
        if self.compression == COMP_BASIC:
            for o in range(ff + 8, ff + size, 8):
                self.basic_blocks.append(struct.unpack_from(">II", data, o))
        elif self.compression == COMP_NORMAL:
            self.window_size = u32(data, ff + 8)

    def header_value(self, key, default=None):
        """The value of an inline optional header (size byte 0x00 or 0x01)."""
        return self.headers.get(key, default)

    def sized(self, key):
        """The bytes of a sized optional header (its first word is its total size), or None."""
        off = self.headers.get(key)
        if off is None:
            return None
        return self.data[off:off + u32(self.data, off)]

    def entry_point(self):
        return self.header_value(H_ENTRY_POINT)

    def page_size(self):
        """Pages are 4 KB when image flag 0x10000000 is set, else 64 KB."""
        return 0x1000 if self.image_flags & 0x10000000 else 0x10000

    # -- payload -----------------------------------------------------------------------------------------------

    def session_key(self, master):
        return aes.decrypt_ecb_block(master, self.file_key)

    def payload(self, key=DEVKIT_KEY):
        """The PE payload after decryption with the master key `key` (still compressed)."""
        raw = self.data[self.pe_offset:]
        if self.encryption == ENC_NONE:
            return bytes(raw)
        if self.encryption != ENC_NORMAL:
            raise XexError(f"unknown encryption type {self.encryption}")
        usable = len(raw) - len(raw) % 16
        return aes.decrypt_cbc(self.session_key(key), bytes(16), bytes(raw[:usable]))

    def image(self, keys=None):
        """The loaded image: image_size bytes from load_address. An encrypted file is tried with each master key of
        `keys` (default: the retail key when this machine has one, then the development key)."""
        if self.compression == COMP_NORMAL:
            raise XexError("LZX ('normal') compression is not supported yet")
        if self.compression not in (COMP_NONE, COMP_BASIC):
            raise XexError(f"unknown compression type {self.compression}")
        if self.encryption != ENC_NORMAL:
            keys = (None,)
        elif keys is None:
            keys = tuple(k for k in (retail_key(), DEVKIT_KEY) if k)
        for key in keys:
            img = self._expand(self.payload(key))
            if img[:2] == b"MZ":
                return img
        hint = "" if self.encryption != ENC_NORMAL or retail_key() else (
            f"; a retail title needs the console's retail key: set \"{KEY_CONFIG}\" in config.local.json or {KEY_ENV}")
        raise XexError("the decrypted image does not start with MZ (wrong key or a damaged file)" + hint)

    def _expand(self, payload):
        if self.compression == COMP_NONE:
            out = bytearray(payload[:self.image_size])
        else:
            out = bytearray()
            pos = 0
            for data_size, zero_size in self.basic_blocks:
                if pos + data_size > len(payload):
                    raise XexError("basic compression block runs past the end of the file")
                out += payload[pos:pos + data_size]
                out += bytes(zero_size)
                pos += data_size
        if len(out) < self.image_size:
            out += bytes(self.image_size - len(out))
        return bytes(out[:self.image_size])

    # -- import libraries --------------------------------------------------------------------------------------

    def import_libraries(self):
        """[(name, version, [record address, ...])] from the import libraries header."""
        blob = self.sized(H_IMPORT_LIBRARIES)
        if blob is None:
            return []
        str_size, count = u32(blob, 4), u32(blob, 8)
        names = [n.decode("ascii", "replace") for n in blob[12:12 + str_size].split(b"\0") if n]
        out, o = [], 12 + str_size
        for _ in range(count):
            size = u32(blob, o)
            version = u32(blob, o + 0x1C)
            name_index, n = u16(blob, o + 0x24), u16(blob, o + 0x26)
            records = [u32(blob, o + 0x28 + 4 * k) for k in range(n)]
            out.append((names[name_index] if name_index < len(names) else f"lib{name_index}", version, records))
            o += size
        return out

    def tls_info(self):
        off = self.headers.get(H_TLS_INFO)
        if off is None:
            return None
        slots, raw, data_size, raw_size = struct.unpack_from(">IIII", self.data, off)
        return {"slots": slots, "raw_data": raw, "data_size": data_size, "raw_data_size": raw_size}

    def execution_info(self):
        off = self.headers.get(H_EXECUTION_INFO)
        if off is None:
            return None
        media_id, version, base_version, title_id = struct.unpack_from(">IIII", self.data, off)
        return {"media_id": media_id, "version": version, "base_version": base_version, "title_id": title_id}

    def original_pe_name(self):
        blob = self.sized(H_ORIGINAL_PE_NAME)
        return blob[4:].split(b"\0")[0].decode("ascii", "replace") if blob else None


def imports(xex, img):
    """[{library, ordinal, kind ("function" or "variable"), slot, thunk}] from the import records: every record
    addresses an image word holding (type << 24) | (library index << 16) | ordinal. Type 0 is the import's address
    slot, type 1 a function's 16-byte call thunk; a function has both, a variable only the slot."""
    base = xex.load_address
    out = []
    for lib, version, records in xex.import_libraries():
        by_ord = {}
        for addr in records:
            v = u32(img, addr - base)
            kind, ordinal = v >> 24, v & 0xFFFF
            e = by_ord.setdefault(ordinal, {"library": lib, "version": version, "ordinal": ordinal, "slot": None,
                                            "thunk": None})
            if kind == 0:
                e["slot"] = addr
            elif kind == 1:
                e["thunk"] = addr
            else:
                raise XexError(f"import record at 0x{addr:08x} has unknown type {kind}")
        for e in by_ord.values():
            e["kind"] = "function" if e["thunk"] is not None else "variable"
            out.append(e)
    return out


def describe(xex, img):
    """The facts image.json records about a loaded XEX (everything but the image bytes)."""
    base = xex.load_address
    try:
        sections = pe_sections(img, base)
    except PeError as e:
        raise XexError(str(e)) from e
    top = base + xex.image_size
    for sec in sections:  # the XEX drops what lies past the image (.reloc): such a section is not loaded
        sec["loaded"] = sec["start"] < top
        sec["end"], sec["raw_end"] = min(sec["end"], top), min(sec["raw_end"], top)
    funcs = pdata(img, base, sections)
    pages = {1: "code", 2: "data", 3: "readonly"}
    page_map, at = [], base
    for count, info in xex.page_descriptors:
        size = count * xex.page_size()
        page_map.append({"start": at, "end": at + size, "kind": pages.get(info, f"info{info}")})
        at += size
    return {
        "format": "xex2",
        "original_pe_name": xex.original_pe_name(),
        "load_address": base,
        "image_size": xex.image_size,
        "entry_point": xex.entry_point(),
        "encryption": xex.encryption,
        "compression": xex.compression,
        "image_sha1": hashlib.sha1(img).hexdigest(),
        "execution": xex.execution_info(),
        "tls": xex.tls_info(),
        "default_stack_size": xex.header_value(H_DEFAULT_STACK_SIZE),
        "system_flags": xex.header_value(H_SYSTEM_FLAGS),
        "sections": sections,
        "pages": page_map,
        "pdata": [[b, n, p] for b, n, p in funcs],
        "imports": imports(xex, img),
    }


KERNEL_VARS = 0x7FFF8000  # target.json memory "kernel": the imported variables' blocks
KERNEL_VAR_SIZE = 0x100


def resolve_imports(img, desc, var_base=KERNEL_VARS):
    """The image as the console's loader leaves it for the judge: each imported variable's slot holds the address of
    a zero-filled KERNEL_VAR_SIZE block in the kernel region (one per variable, in import order), and each imported
    function's slot holds its thunk's address, so a call through the slot reaches the same mocked target as a bl
    to the thunk. The thunks stay as they are: a call to one is mocked, never run. Records each slot's value in
    desc["imports"] ("value")."""
    out = bytearray(img)
    base, k = desc["load_address"], 0
    for e in desc["imports"]:
        if e["slot"] is None:
            continue
        if e["kind"] == "variable":
            e["value"] = var_base + KERNEL_VAR_SIZE * k
            k += 1
        else:
            e["value"] = e["thunk"]
        struct.pack_into(">I", out, e["slot"] - base, e["value"])
    return bytes(out)


def load(path):
    """(Xex, image bytes, description) of the XEX at path."""
    xex = Xex(pathlib.Path(path).read_bytes())
    img = xex.image()
    return xex, img, describe(xex, img)


def extract(path, out_dir):
    """Writes out_dir/image.bin (the loaded image, imports resolved: resolve_imports) and out_dir/image.json
    (describe(); image_sha1 is the image as decompressed, loaded_sha1 image.bin); returns the description."""
    _, img, desc = load(path)
    img = resolve_imports(img, desc)
    desc["loaded_sha1"] = hashlib.sha1(img).hexdigest()
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "image.bin").write_bytes(img)
    (out / "image.json").write_text(json.dumps(desc, indent=1), encoding="utf-8")
    return desc


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    try:
        d = extract(argv[1], argv[2])
    except XexError as e:
        print(f"xex: {e}")
        return 1
    code = sum(s["end"] - s["start"] for s in d["sections"] if s["code"])
    print(f"xex: {d['original_pe_name']} base 0x{d['load_address']:08x} size 0x{d['image_size']:x} entry "
          f"0x{d['entry_point']:08x}; {len(d['sections'])} sections ({code / 1048576:.1f} MB code), "
          f"{len(d['pdata'])} .pdata functions, {len(d['imports'])} imports; sha1 {d['image_sha1']}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    sys.exit(main(sys.argv))

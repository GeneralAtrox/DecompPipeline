"""dpimg.py: the Python side of dpimg (port/imgtool, docs/IMGTOOL.md), the pipeline's own image library, so nothing we
ship or author needs Pillow. Standard library only (ctypes); numpy is used only when a caller hands over or asks for
an array. The names follow Pillow's, so a script moves over by changing its imports:

    from dpimg import Image, ImageDraw, ImageFont      # instead of: from PIL import Image, ImageDraw, ImageFont
    im = Image.open("a.png").resize((64, 64), Image.LANCZOS); im.save("b.png")

Images are RGBA underneath; mode "L" (and "1") is grey with alpha 255 and mode "RGB" has alpha 255, so each
operation gives what Pillow gives for that mode (docs/proofs/2026-11-07/dpimg_vs_pillow.py). Text is close to Pillow,
not equal (stb_truetype, not FreeType). thumbnail() skips Pillow's reducing_gap step, so it is close, not equal.

The library is built on first use, and again when its sources change, into port/imgtool/build/<x64|x86> with CMake and
Ninja under the Visual Studio environment config.local.json's "vcvars" names (the one the kit's build uses), for this
Python's bitness. DPIMG_DLL names a prebuilt dpimg.dll instead.

    py -3.12 port/tools/dpimg.py build              build (or rebuild) the library and dpimg-cli
    py -3.12 port/tools/dpimg.py ARGS...            run dpimg-cli ARGS (see docs/IMGTOOL.md)

Nearest existing: port/tools/texture_pack.py - it writes unfiltered PNGs with zlib but reads none and has no image
operations; Pillow - a third-party package the launcher and the art tools must stop needing (the user, 2026-11-07).
"""
import ctypes as C
import os
import pathlib
import shutil
import struct
import subprocess
import sys

KIT = pathlib.Path(__file__).resolve().parents[1]
SRC = KIT / "imgtool"
OUT = SRC / "build" / ("x64" if struct.calcsize("P") == 8 else "x86")
NEAREST, LANCZOS, BILINEAR, BICUBIC, BOX, HAMMING = range(6)
FLIP_LEFT_RIGHT, FLIP_TOP_BOTTOM, ROTATE_90, ROTATE_180, ROTATE_270, TRANSPOSE, TRANSVERSE = range(7)
AFFINE = 0
FILTERS = {"nearest": NEAREST, "lanczos": LANCZOS, "bilinear": BILINEAR, "bicubic": BICUBIC, "box": BOX,
           "hamming": HAMMING}


def _stale(dll):
    if not dll.exists():
        return True
    newest = max(p.stat().st_mtime for p in [*SRC.glob("src/*"), *SRC.glob("include/*"), SRC / "CMakeLists.txt",
                                             KIT / "pvr" / "pvr_twiddle.c"])
    return newest > dll.stat().st_mtime


def build(quiet=True):
    """Configure and build port/imgtool into OUT; returns the DLL's path. RuntimeError with the build's output."""
    sys.path.insert(0, str(KIT.parent))
    from core import toolcfg  # noqa: E402  (no imports from the rest of core)
    cfg = toolcfg.load(KIT.parent)
    vc = toolcfg.need(cfg, "vcvars")
    bat = pathlib.Path(vc).with_name("vcvars64.bat" if struct.calcsize("P") == 8 else "vcvars32.bat")
    r = subprocess.run(toolcfg.vcvars_command(bat, cfg), capture_output=True, text=True, timeout=300)
    env = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    toolcfg.loaded(r.returncode, env, bat, cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cmake, ninja = (shutil.which(t, path=path) or shutil.which(t) for t in ("cmake", "ninja"))
    if not cmake or not ninja:
        raise RuntimeError(f"dpimg build: no {'cmake' if not cmake else 'ninja'} in the Visual Studio environment "
                           f"({bat}) or on PATH")
    for cmd in ([cmake, "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja}", "-S", str(SRC), "-B", str(OUT),
                 "-DCMAKE_BUILD_TYPE=Release"], [cmake, "--build", str(OUT)]):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        if r.returncode:
            raise RuntimeError(f"dpimg build failed ({' '.join(cmd[:2])}):\n{r.stdout}{r.stderr}")
        if not quiet:
            print(r.stdout, end="")
    return OUT / "dpimg.dll"


class _Raw(C.Structure):
    _fields_ = [("w", C.c_int), ("h", C.c_int), ("px", C.POINTER(C.c_uint8))]


class _Cmp(C.Structure):
    _fields_ = [("differing", C.c_longlong), ("max_delta", C.c_int), ("bbox", C.c_int * 4)]


P, I, U, D = C.POINTER(_Raw), C.c_int, C.c_uint32, C.c_double
_SIGS = {"new": (P, I, I, U), "from_rgba": (P, I, I, C.c_char_p), "copy": (P, P), "free": (None, P),
         "load": (P, C.c_char_p), "save_as": (I, P, C.c_char_p, I), "error": (C.c_char_p,),
         "crop": (P, P, I, I, I, I), "transpose": (P, P, I), "paste": (None, P, P, I, I, P, I),
         "fill": (None, P, U, P, I, I, I), "alpha_composite": (None, P, P, I, I), "difference": (P, P, P),
         "compare": (C.c_longlong, P, P, I, C.POINTER(_Cmp)), "grey": (None, P), "bbox": (I, P, I, I * 4),
         "contact_sheet": (P, C.POINTER(P), I, I, I, I, I, U, I), "resize": (P, P, I, I, I),
         "transform": (P, P, I, I, D * 6, I, U), "rect": (None, P, I, I, I, I, U, U, I, I),
         "ellipse": (None, P, I, I, I, I, U, U, I, I), "polygon": (None, P, C.POINTER(I), I, U, U, I, I),
         "line": (None, P, C.POINTER(I), I, U, I), "font_load": (C.c_void_p, C.c_char_p, C.c_float, I),
         "font_free": (None, C.c_void_p), "font_metrics": (None, C.c_void_p, C.POINTER(I), C.POINTER(I)),
         "text_bbox": (None, C.c_void_p, C.c_char_p, C.c_char_p, I, I * 4),
         "text_length": (I, C.c_void_p, C.c_char_p),
         "text_mask": (P, C.c_void_p, C.c_char_p, I, C.POINTER(I), C.POINTER(I)),
         "text": (None, P, C.c_void_p, I, I, C.c_char_p, U, C.c_char_p, I),
         "decode_pvr": (P, U, I, I, I, C.c_char_p, C.c_size_t, C.POINTER(U), I, I)}
_lib = None


def lib():
    """The loaded library (built first when missing or older than its sources)."""
    global _lib
    if _lib is None:
        dll = pathlib.Path(os.environ["DPIMG_DLL"]) if os.environ.get("DPIMG_DLL") else OUT / "dpimg.dll"
        if not os.environ.get("DPIMG_DLL") and _stale(dll):
            dll = build()
        so = C.CDLL(str(dll))
        for name, (res, *args) in _SIGS.items():
            fn = getattr(so, "dpimg_" + name)
            fn.restype, fn.argtypes = res, args
        _lib = so
    return _lib


def _rgba(colour, mode="RGBA"):
    """A colour as 0xRRGGBBAA: an int is grey in L / 1 mode, a 3-tuple is opaque."""
    if colour is None:
        colour = 0
    if isinstance(colour, (int, float)):
        v = int(colour) * (255 if mode == "1" and colour in (0, 1) else 1)
        colour = (v, v, v, 255) if mode in ("L", "1", "RGB") else (v, v, v, v)
    r, g, b, *a = colour
    return (r & 255) << 24 | (g & 255) << 16 | (b & 255) << 8 | ((a[0] if a else 255) & 255)


_BITS = bytes([48] + [49] * 255)  # a grey byte -> "0" or "1"
_GREY = bytes(range(48)) + bytes((0, 255)) + bytes(range(50, 256))  # "0" or "1" -> a grey byte


def _pack1(grey, w, h):
    """Mode "1" bytes as Pillow packs them: a bit per pixel (set when not 0), first pixel the top bit, rows whole bytes."""
    rb = (w + 7) // 8
    return b"".join(int(grey[y * w:(y + 1) * w].translate(_BITS).ljust(rb * 8, b"0"), 2).to_bytes(rb, "big")
                    for y in range(h))


def _unpack1(data, w, h):
    rb = (w + 7) // 8
    return b"".join(bin(int.from_bytes(data[y * rb:(y + 1) * rb], "big") | 1 << rb * 8)[3:3 + w].encode().translate(_GREY)
                    for y in range(h))


class Image:
    """An image: Pillow's methods that our tools use, on dpimg."""
    NEAREST, LANCZOS, BILINEAR, BICUBIC, BOX, HAMMING = NEAREST, LANCZOS, BILINEAR, BICUBIC, BOX, HAMMING
    FLIP_LEFT_RIGHT, FLIP_TOP_BOTTOM, ROTATE_90, ROTATE_180, ROTATE_270 = range(5)
    TRANSPOSE, TRANSVERSE, AFFINE = TRANSPOSE, TRANSVERSE, AFFINE

    def __init__(self, raw, mode="RGBA"):
        if not raw:
            raise MemoryError(f"dpimg: {lib().dpimg_error().decode()}")
        self._raw, self.mode = raw, mode

    def __del__(self):
        if getattr(self, "_raw", None) and _lib is not None:
            _lib.dpimg_free(self._raw)
            self._raw = None

    # ---- making and reading ----
    @staticmethod
    def new(mode, size, color=0):
        return Image(lib().dpimg_new(size[0], size[1], _rgba(color, mode)), mode)

    @staticmethod
    def open(path):
        return Image(lib().dpimg_load(str(path).encode()), "RGBA")

    @staticmethod
    def frombytes(mode, size, data):
        w, h = size
        n, data = w * h, bytes(data)
        if mode == "1":
            data = _unpack1(data, w, h)
        if mode in ("L", "1"):
            px = bytearray(n * 4)
            px[0::4] = px[1::4] = px[2::4] = data[:n]
            px[3::4] = b"\xff" * n
        elif mode == "RGB":
            px = bytearray(b"\xff" * (n * 4))
            for c in range(3):
                px[c::4] = data[c:n * 3:3]
        else:
            px = data[:n * 4]
        return Image(lib().dpimg_from_rgba(w, h, bytes(px)), mode)

    @staticmethod
    def fromarray(arr, mode=None):
        h, w = arr.shape[:2]
        ch = arr.shape[2] if arr.ndim == 3 else 1
        mode = mode or {1: "L", 3: "RGB", 4: "RGBA"}[ch]
        return Image.frombytes(mode, (w, h), arr.astype("uint8").tobytes())

    @property
    def size(self):
        return self._raw.contents.w, self._raw.contents.h

    width = property(lambda self: self.size[0])
    height = property(lambda self: self.size[1])

    def tobytes(self):
        w, h = self.size
        raw = C.string_at(self._raw.contents.px, w * h * 4)
        if self.mode == "1":
            return _pack1(raw[0::4], w, h)
        if self.mode == "L":
            return raw[0::4]
        if self.mode == "RGB":
            out = bytearray(w * h * 3)
            for c in range(3):
                out[c::3] = raw[c::4]
            return bytes(out)
        return raw

    def __array__(self, dtype=None, copy=None):
        import numpy as np
        w, h = self.size
        if self.mode == "1":  # Pillow gives a bool array
            return np.frombuffer(C.string_at(self._raw.contents.px, w * h * 4)[0::4], np.uint8).reshape(h, w) != 0
        a = np.frombuffer(self.tobytes(), np.uint8).reshape((h, w) if self.mode == "L" else (h, w, -1))
        return a.astype(dtype) if dtype else a.copy()

    def getpixel(self, xy):
        w, _ = self.size
        p = self._raw.contents.px[(xy[1] * w + xy[0]) * 4:(xy[1] * w + xy[0]) * 4 + 4]
        return p[0] if self.mode in ("L", "1") else tuple(p[:3]) if self.mode == "RGB" else tuple(p)

    def save(self, path):
        ch = {"L": 1, "1": 1, "RGB": 3}.get(self.mode, 4)
        if lib().dpimg_save_as(self._raw, str(path).encode(), ch):
            raise OSError(f"dpimg: {path}: {lib().dpimg_error().decode()}")

    # ---- geometry ----
    def copy(self):
        return Image(lib().dpimg_copy(self._raw), self.mode)

    def convert(self, mode):
        out = self.copy()
        if mode == "L" and self.mode not in ("L", "1"):
            lib().dpimg_grey(out._raw)
        elif mode == "RGB" and self.mode == "RGBA":
            w, h = self.size
            px = bytearray(C.string_at(out._raw.contents.px, w * h * 4))
            px[3::4] = b"\xff" * (w * h)
            out = Image(lib().dpimg_from_rgba(w, h, bytes(px)), "RGB")
        out.mode = mode
        return out

    def crop(self, box):
        return Image(lib().dpimg_crop(self._raw, *map(int, box)), self.mode)

    def resize(self, size, resample=BICUBIC):
        resample = FILTERS.get(resample, resample)
        return Image(lib().dpimg_resize(self._raw, int(size[0]), int(size[1]), resample), self.mode)

    def thumbnail(self, size, resample=BICUBIC):
        """In place, aspect kept, never enlarged: Pillow's sizes, without its reducing_gap step (close, not equal)."""
        w, h = self.size
        if w <= size[0] and h <= size[1]:
            return
        x, y = (size[0], max(1, round(h * size[0] / w))) if w / size[0] >= h / size[1] else \
            (max(1, round(w * size[1] / h)), size[1])
        self._raw, old = lib().dpimg_resize(self._raw, x, y, resample), self._raw
        lib().dpimg_free(old)

    def transpose(self, method):
        return Image(lib().dpimg_transpose(self._raw, method), self.mode)

    def transform(self, size, method, data, resample=NEAREST, fillcolor=None):
        if method != AFFINE:
            raise NotImplementedError("dpimg transforms are AFFINE only")
        a = (D * 6)(*data[:6])
        return Image(lib().dpimg_transform(self._raw, size[0], size[1], a, resample, _rgba(fillcolor, self.mode)),
                     self.mode)

    def getbbox(self, alpha_only=True):
        b = (I * 4)()
        ch = 3 if self.mode == "RGBA" and alpha_only else 0 if self.mode in ("L", "1") else -1
        return tuple(b) if lib().dpimg_bbox(self._raw, ch, b) else None

    # ---- compositing ----
    def paste(self, im, box=None, mask=None):
        x, y = (box or (0, 0))[:2]
        ch = 0 if mask is not None and mask.mode in ("L", "1") else 3
        mraw = mask._raw if mask is not None else None
        if not isinstance(im, Image) and mask is None and box is not None and len(box) == 4:
            im = Image.new(self.mode, (box[2] - box[0], box[3] - box[1]), im)  # a colour into a box
        if isinstance(im, Image):
            lib().dpimg_paste(self._raw, im._raw, int(x), int(y), mraw, ch)
        else:
            lib().dpimg_fill(self._raw, _rgba(im, self.mode), mraw, ch, int(x), int(y))

    def alpha_composite(self, im, dest=(0, 0)):
        lib().dpimg_alpha_composite(self._raw, im._raw, int(dest[0]), int(dest[1]))


def open(path):  # noqa: A001  (Pillow's name)
    return Image.open(path)


def new(mode, size, color=0):
    return Image.new(mode, size, color)


def alpha_composite(a, b):
    out = a.copy()
    out.alpha_composite(b)
    return out


def difference(a, b):
    """ImageChops.difference."""
    return Image(lib().dpimg_difference(a._raw, b._raw), a.mode)


def logical_xor(a, b):
    """ImageChops.logical_xor on two mode "1" images."""
    pa, pb = (C.string_at(i._raw.contents.px, i.width * i.height * 4)[0::4] for i in (a, b))
    out = Image.frombytes("L", a.size, bytes(255 if (p != 0) != (q != 0) else 0 for p, q in zip(pa, pb)))
    out.mode = "1"
    return out


def compare(a, b, tolerance=0):
    """{"differing": pixels with a channel more than tolerance apart, "max_delta", "bbox"}."""
    r = _Cmp()
    lib().dpimg_compare(a._raw, b._raw, tolerance, C.byref(r))
    return {"differing": r.differing, "max_delta": r.max_delta, "bbox": tuple(r.bbox)}


def contact_sheet(images, cols, cell, pad=4, bg=(32, 32, 32, 255), resample=LANCZOS):
    arr = (P * len(images))(*[im._raw for im in images])
    return Image(lib().dpimg_contact_sheet(arr, len(images), cols, cell[0], cell[1], pad, _rgba(bg), resample))


def decode_pvr(tcw, size, data, stride=0, palette=None, palette_format=0):
    """A Dreamcast texture's bytes to an RGBA image through the kit's pvr_twiddle.c (dpimg_decode_pvr)."""
    pal = (U * len(palette))(*palette) if palette else None
    raw = lib().dpimg_decode_pvr(tcw, size[0], size[1], stride, bytes(data), len(data), pal,
                                 len(palette) if palette else 0, palette_format)
    if not raw:
        raise ValueError(f"dpimg: texture tcw {tcw:08x} {size[0]}x{size[1]} not decoded")
    return Image(raw)


Image.Image = Image  # isinstance(x, Image.Image), as with Pillow
ImageChops = __import__("types").SimpleNamespace(difference=difference, logical_xor=logical_xor)
if __name__ != "__main__":  # run as a script, dpimg_draw would import this file a second time
    from dpimg_draw import ImageDraw, ImageFont  # noqa: E402,F401  (Pillow's other two names)
else:
    if sys.argv[1:2] == ["build"]:
        print(build(quiet=False))
    else:
        lib()
        sys.exit(subprocess.run([str(OUT / "dpimg-cli.exe"), *sys.argv[1:]]).returncode)

"""dpimg_draw.py: ImageDraw and ImageFont for dpimg.py (docs/IMGTOOL.md): shapes as Pillow draws them (the same
pixels), and TrueType text through stb_truetype (close to Pillow's FreeType text, not equal). Imported by dpimg.py;
use it as `from dpimg import ImageDraw, ImageFont`.

Nearest existing: dpimg.py - this is its drawing half, split off to keep each file under DecompPipeline's 400 lines.
"""
import ctypes as C
import os
import pathlib
import types

from dpimg import I, Image, _rgba, lib


def _points(xy):
    """Pillow's coordinate forms ((x, y), ...) or (x0, y0, x1, y1, ...) as ints, truncated as Pillow's C does."""
    flat = [v for p in xy for v in p] if xy and isinstance(xy[0], (tuple, list)) else list(xy)
    return [int(v) for v in flat]


class Font:
    """ImageFont.truetype(path, size, index): size is the em in pixels."""

    def __init__(self, path, size=10, index=0):
        self.path, self.size = str(path), size
        self._f = lib().dpimg_font_load(self.path.encode(), float(size), index)
        if not self._f:
            raise OSError(f"dpimg: cannot open font {path}")

    def __del__(self):
        if getattr(self, "_f", None):
            lib().dpimg_font_free(self._f)
            self._f = None

    def getmetrics(self):
        a, d = I(), I()
        lib().dpimg_font_metrics(self._f, C.byref(a), C.byref(d))
        return a.value, -d.value

    def getbbox(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None):
        b = (I * 4)()
        lib().dpimg_text_bbox(self._f, str(text).encode(), anchor.encode() if anchor else None, int(stroke_width), b)
        return tuple(b)

    def getlength(self, text):
        return lib().dpimg_text_length(self._f, str(text).encode()) / 64

    def getmask(self, text, mode="L", stroke_width=0):
        """The text's coverage as an L image cut to its ink (Pillow's getmask, without its origin offset)."""
        ox, oy = I(), I()
        raw = lib().dpimg_text_mask(self._f, str(text).encode(), int(stroke_width), C.byref(ox), C.byref(oy))
        m = Image(raw)
        return Image.frombytes("L", m.size, m.tobytes()[3::4])


def truetype(font=None, size=10, index=0, encoding="", layout_engine=None):
    p = pathlib.Path(font)
    if not p.is_absolute() and not p.exists():
        p = pathlib.Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / p
    return Font(p, size, index)


def load_default(size=10):
    """Arial at `size` (Pillow's built-in Aileron is not ours to ship)."""
    return truetype("arial.ttf", size)


class Draw:
    """ImageDraw.Draw(im): the ink replaces the pixels (Pillow's default, no blend)."""

    def __init__(self, im, mode=None):
        self.im = im

    def _ink(self, c):
        return _rgba(c, self.im.mode)

    @staticmethod
    def _flags(fill, outline):
        return (1 if fill is not None else 0) | (2 if outline is not None else 0)

    def rectangle(self, xy, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = _points(xy)
        lib().dpimg_rect(self.im._raw, x0, y0, x1, y1, self._ink(fill), self._ink(outline), width,
                         self._flags(fill, outline))

    def ellipse(self, xy, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = _points(xy)
        lib().dpimg_ellipse(self.im._raw, x0, y0, x1, y1, self._ink(fill), self._ink(outline), width,
                            self._flags(fill, outline))

    def polygon(self, xy, fill=None, outline=None, width=1):
        p = _points(xy)
        lib().dpimg_polygon(self.im._raw, (I * len(p))(*p), len(p) // 2, self._ink(fill), self._ink(outline), width,
                            self._flags(fill, outline))

    def line(self, xy, fill=None, width=0, joint=None):
        if joint is not None:
            raise NotImplementedError("dpimg: line joints are not drawn (no tool of ours uses them)")
        p = _points(xy)
        if fill is not None and width != 0:
            lib().dpimg_line(self.im._raw, (I * len(p))(*p), len(p) // 2, self._ink(fill), width)

    def text(self, xy, text, fill=None, font=None, anchor=None, spacing=4, align="left", direction=None,
             features=None, language=None, stroke_width=0, stroke_fill=None, embedded_color=False, font_size=None):
        font = font or load_default(font_size or 10)
        ink = self._ink(fill if fill is not None else 255 if self.im.mode in ("L", "1") else (0, 0, 0, 255))
        x, y = int(xy[0]), int(xy[1])
        a = anchor.encode() if anchor else None
        if stroke_width:
            stroke_ink = self._ink(stroke_fill) if stroke_fill is not None else ink
            lib().dpimg_text(self.im._raw, font._f, x, y, str(text).encode(), stroke_ink, a, int(stroke_width))
            if ink != stroke_ink:
                lib().dpimg_text(self.im._raw, font._f, x, y, str(text).encode(), ink, a, 0)
        else:
            lib().dpimg_text(self.im._raw, font._f, x, y, str(text).encode(), ink, a, 0)

    def textbbox(self, xy, text, font=None, anchor=None, stroke_width=0, **_):
        b = (font or load_default()).getbbox(text, anchor=anchor, stroke_width=stroke_width)
        return b[0] + int(xy[0]), b[1] + int(xy[1]), b[2] + int(xy[0]), b[3] + int(xy[1])


ImageDraw = types.SimpleNamespace(Draw=Draw)
ImageFont = types.SimpleNamespace(truetype=truetype, load_default=load_default, FreeTypeFont=Font)

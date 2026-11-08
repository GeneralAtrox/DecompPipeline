"""dpimg_vs_pillow.py: dpimg (port/imgtool, port/tools/dpimg.py) against Pillow, feature by feature, on real inputs.

Every feature but text must give Pillow's exact bytes (tolerance 0). Text goes through stb_truetype where Pillow uses
FreeType (other hinting and anti-aliasing), so it is held to a stated tolerance instead: each ink box within 2 pixels
of Pillow's on every side, at least 90% of Pillow's inked pixels (coverage over half) inked in dpimg's too, and the
reverse. A planted check proves the comparison sees a difference: one changed byte, and LANCZOS against BICUBIC, must
both be caught. txb.py's old numpy decoder is the reference for Dreamcast textures (TXB types 1, 0xd, 9, 3, 4 and
pixel formats 1555, 565, 4444, at square and rectangular sizes, on random texture bytes and on the disc's own banks
when --disc is given). Needs Pillow and numpy (the machine running the proof, not the tools using dpimg).

    py -3.12 dpimg_vs_pillow.py --inputs DIR [--fonts F.ttf ...] [--txb TXB_PY] [--disc ISO] [--limit N]
Exit 0: every exact feature equal and text inside its tolerance; 1 otherwise.

Nearest existing: port/tools/replay_compare.py and frames.py - they compare a port's frames, not an image library
against Pillow, and use Pillow themselves.
"""
import argparse
import importlib.util
import pathlib
import random
import sys

import numpy as np
from PIL import Image as P, ImageChops as PC, ImageDraw as PD, ImageFont as PF

KIT_TOOLS = pathlib.Path(__file__).resolve().parents[3] / "port" / "tools"
sys.path.insert(0, str(KIT_TOOLS))
import dpimg  # noqa: E402
from dpimg import Image as D, ImageDraw as DD, ImageFont as DF  # noqa: E402

RESULTS = {}


def same(feature, a, b):
    """Record whether the Pillow image a and the dpimg image b hold the same mode and bytes."""
    ok = a.size == b.size and a.tobytes() == b.tobytes()
    r = RESULTS.setdefault(feature, [0, 0, ""])
    r[0 if ok else 1] += 1
    if not ok and not r[2]:
        r[2] = f"{a.mode} {a.size} vs {b.size}"
        if a.size == b.size:
            x = np.frombuffer(a.tobytes(), np.uint8).astype(int) - np.frombuffer(b.tobytes(), np.uint8)
            r[2] += f": {np.count_nonzero(x)} bytes differ, largest {np.abs(x).max()}"
    return ok


def to_d(pim):
    return D.frombytes(pim.mode, pim.size, pim.tobytes())


def images(folder, limit):
    files = sorted(pathlib.Path(folder).rglob("*.png"))
    random.Random(7).shuffle(files)
    for f in files[:limit]:
        yield f, P.open(f).convert("RGBA"), D.open(f)


def pixels(folder, limit, out):
    for f, p, d in images(folder, limit):
        same("load PNG", p, d)
        d.save(out / "rt.png")
        same("save PNG (read back)", p, P.open(out / "rt.png").convert("RGBA"))
        w, h = p.size
        for box in ((0, 0, w // 2, h // 2), (-5, -3, w // 3 + 7, h + 4), (w // 4, h // 5, w - 1, h - 2)):
            same("crop", p.crop(box), d.crop(box))
        for op in range(7):
            same("transpose", p.transpose(op), d.transpose(op))
        for mode in ("L", "RGB"):
            same(f"convert {mode}", p.convert(mode), d.convert(mode))
        same("getbbox", P.new("L", (1, 1)), D.new("L", (1, 1))) if p.getbbox() == d.getbbox() else \
            same("getbbox", P.new("L", (1, 1)), D.new("L", (1, 1), 9))
        for f_ in range(6):
            for s in ((max(1, w * 37 // 100), max(1, h * 41 // 100)), (min(900, w * 8 // 5), min(900, h * 3 // 2))):
                same(f"resize {['NEAREST','LANCZOS','BILINEAR','BICUBIC','BOX','HAMMING'][f_]}",
                     p.resize(s, f_), d.resize(s, f_))
            same("resize L", p.convert("L").resize((w // 2 + 1, h // 3 + 1), f_), d.convert("L").resize((w // 2 + 1, h // 3 + 1), f_))
        bg_p, bg_d = P.new("RGBA", (w + 20, h + 10), (40, 40, 48, 255)), D.new("RGBA", (w + 20, h + 10), (40, 40, 48, 255))
        half_p, half_d = p.copy(), d.copy()
        half_p.putalpha(p.getchannel("A").point(lambda v: v // 2 + 30))
        half_d = to_d(half_p)
        bg_p.alpha_composite(half_p, (7, 3)), bg_d.alpha_composite(half_d, (7, 3))
        same("alpha_composite", bg_p, bg_d)
        bg_p.paste(half_p, (-4, 5), half_p), bg_d.paste(half_d, (-4, 5), half_d)
        same("paste with RGBA mask", bg_p, bg_d)
        m_p = p.convert("L")
        bg_p.paste((200, 30, 90, 255), (3, 2), m_p), bg_d.paste((200, 30, 90, 255), (3, 2), to_d(m_p))
        same("paste colour with L mask", bg_p, bg_d)
        clear_p, clear_d = P.new("RGBA", (w, h)), D.new("RGBA", (w, h))
        clear_p.paste((250, 250, 10, 255), (0, 0), m_p), clear_d.paste((250, 250, 10, 255), (0, 0), to_d(m_p))
        same("paste colour on transparent", clear_p, clear_d)
        rgb_p = P.new("RGB", (w, h), (9, 9, 9))
        rgb_p.paste(p.convert("RGB"), (2, 2), m_p)
        rgb_d = D.new("RGB", (w, h), (9, 9, 9))
        rgb_d.paste(d.convert("RGB"), (2, 2), to_d(m_p))
        same("paste RGB with L mask", rgb_p, rgb_d)
        same("difference", PC.difference(p, p.transpose(1)), dpimg.difference(d, d.transpose(1)))
        ang = np.radians(11)
        for img_p, img_d in ((m_p, to_d(m_p)), (p, d)):
            a = (np.cos(ang), np.sin(ang) + 0.2, -9.5, -np.sin(ang), np.cos(ang), 14.25)
            for f_ in (P.NEAREST, P.BILINEAR, P.BICUBIC):
                same(f"transform AFFINE {['NEAREST', '', 'BILINEAR', 'BICUBIC'][f_]} {img_p.mode}",
                     img_p.transform((w + 9, h + 5), P.AFFINE, a, f_), img_d.transform((w + 9, h + 5), dpimg.AFFINE, a, f_))


def shapes():
    rnd = random.Random(11)
    for mode, ink, ink2 in (("RGBA", (255, 200, 0, 255), (10, 20, 230, 200)), ("L", 255, 128), ("1", 1, 1)):
        for n in range(60):
            size = (rnd.randint(40, 300), rnd.randint(40, 300))
            p, d = P.new(mode, size), D.new(mode, size)
            dp, dd = PD.Draw(p), DD.Draw(d)
            x0, y0 = rnd.randint(-20, size[0] // 2), rnd.randint(-20, size[1] // 2)
            box = (x0, y0, x0 + rnd.randint(0, size[0]), y0 + rnd.randint(0, size[1]))
            w = rnd.randint(1, 12)
            fill, outline = (ink, ink2) if n % 3 else (None, ink) if n % 2 else (ink, None)
            if mode == "1":
                outline = outline and 1
            dp.ellipse(box, fill=fill, outline=outline, width=w), dd.ellipse(box, fill=fill, outline=outline, width=w)
            same(f"ellipse {mode}", p, d)
            dp.rectangle(box, fill=None, outline=ink2 if mode != "1" else 1, width=w)
            dd.rectangle(box, fill=None, outline=ink2 if mode != "1" else 1, width=w)
            same(f"rectangle {mode}", p, d)
            pts = [(rnd.randint(-10, size[0] + 10), rnd.randint(-10, size[1] + 10)) for _ in range(rnd.randint(3, 7))]
            dp.polygon(pts, fill=fill, outline=outline, width=w if n % 4 else 1)
            dd.polygon(pts, fill=fill, outline=outline, width=w if n % 4 else 1)
            same(f"polygon {mode}", p, d)
            line = [(rnd.uniform(-10, size[0] + 10), rnd.uniform(-10, size[1] + 10)) for _ in range(rnd.randint(2, 4))]
            dp.line(line, fill=ink, width=w if n % 5 else 1), dd.line(line, fill=ink, width=w if n % 5 else 1)
            same(f"line {mode}", p, d)
    for mid, r, q, w in ((64, 56, 26, 9), (32, 28, 13, 4), (128, 112, 52, 18)):  # pad_icons.py's face buttons
        p, d = P.new("RGBA", (mid * 2, mid * 2)), D.new("RGBA", (mid * 2, mid * 2))
        for draw in (PD.Draw(p), DD.Draw(d)):
            draw.ellipse((mid - r, mid - r, mid + r, mid + r), fill=(24, 24, 28, 255))
            draw.line((mid - q, mid - q, mid + q, mid + q), fill=(255, 255, 255, 255), width=w)
            draw.ellipse((mid - q, mid - q, mid + q, mid + q), outline=(255, 255, 255, 255), width=w)
            draw.rectangle((mid - q, mid - q, mid + q, mid + q), outline=(255, 255, 255, 255), width=w)
            draw.polygon(((mid, mid - q * 1.2), (mid + q * 1.15, mid + q * 0.8), (mid - q * 1.15, mid + q * 0.8)),
                         outline=(255, 255, 255, 255), width=w)
        same("pad_icons shapes", p, d)


def text(fonts):
    ok = True
    for path, size, words in fonts:
        pf, df = PF.truetype(path, size), DF.truetype(path, size)
        for stroke in (0, 3):
            for anchor in ("ls", "mm", None):
                pb, db = pf.getbbox(words, anchor=anchor, stroke_width=stroke), df.getbbox(words, anchor=anchor,
                                                                                         stroke_width=stroke)
                W, H = pb[2] - pb[0] + 40, pb[3] - pb[1] + 40
                at = (20 - pb[0], 20 - pb[1])
                p, d = P.new("L", (W, H)), D.new("L", (W, H))
                PD.Draw(p).text(at, words, fill=255, font=pf, anchor=anchor, stroke_width=stroke)
                DD.Draw(d).text(at, words, fill=255, font=df, anchor=anchor, stroke_width=stroke)
                a, b = np.asarray(p) > 127, np.asarray(d) > 127
                box_off = max(abs(x - y) for x, y in zip(pb, db))
                cover = min((a & b).sum() / max(a.sum(), 1), (a & b).sum() / max(b.sum(), 1))
                good = box_off <= 2 and cover >= 0.90
                ok &= bool(good)
                print(f"  text {pathlib.Path(path).name} {size}px stroke {stroke} anchor {anchor}: box off by "
                      f"{box_off} px, shared ink {cover:.1%}, mean |diff| {np.abs(np.asarray(p, int) - np.asarray(d)).mean():.2f}"
                      f" -> {'within' if good else 'OUTSIDE'} tolerance")
    return ok


def textures(txb_py, disc):
    spec = importlib.util.spec_from_file_location("txb_ref", txb_py)
    txb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(txb)
    rnd = random.Random(5)
    for t in (1, 0xD, 9, 3, 4):
        for pf in (0, 1, 2):
            for w, h in ((8, 8), (64, 32), (32, 128), (256, 256)):
                n = w * h * 2 if t not in (3, 4) else 2048 + (w // 2) * (h // 2) + (37 if t == 4 else 0)
                e = dict(w=w, h=h, fm=t << 8 | pf, data=bytes(rnd.getrandbits(8) for _ in range(n)))
                same("PVR texture (random bytes)", txb.decode(e), dpimg_txb(e))
    if disc:
        print("  disc banks: not run here (the launcher's HD step checks every pack image against expected.tsv)")


def dpimg_txb(e):
    """The TXB entry through dpimg, as the switched txb.py does it."""
    w, h, t, pf = e["w"], e["h"], e["fm"] >> 8, e["fm"] & 0xFF
    fmt = (pf if pf in (0, 1) else 2) << 27
    data = e["data"]
    if t in (3, 4):
        return dpimg.decode_pvr(fmt | 1 << 30, (w, h), data[:2048] + data[len(data) - (w // 2) * (h // 2):])
    return dpimg.decode_pvr(fmt | (1 << 26 if t == 9 else 0), (w, h), data[:w * h * 2])


def planted(folder, out):
    f, p, d = next(images(folder, 1))
    bad = d.copy()
    raw = bytearray(bad.tobytes())
    raw[len(raw) // 2] ^= 0x10
    caught_byte = not same("planted: one byte changed (must FAIL)", p, D.frombytes("RGBA", p.size, bytes(raw)))
    s = (max(1, p.width // 3), max(1, p.height // 3))
    caught_filter = not same("planted: LANCZOS vs BICUBIC (must FAIL)", p.resize(s, P.LANCZOS), d.resize(s, P.BICUBIC))
    cli = dpimg.compare(d, D.frombytes("RGBA", p.size, bytes(raw)))["differing"] == 1
    return caught_byte and caught_filter and cli


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--fonts", nargs="*", default=[])
    ap.add_argument("--txb")
    ap.add_argument("--disc")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pixels(a.inputs, a.limit, out)
    shapes()
    if a.txb:
        textures(a.txb, a.disc)
    print("text (tolerance: ink box within 2 px, shared ink at least 90% both ways):")
    text_ok = text([(f, s, w) for f in a.fonts for s, w in ((48, "Spawn Ag 0123"), (96, "In The Demon's Hand"))])
    caught = planted(a.inputs, out)
    bad = 0
    for k, (n_ok, n_bad, why) in RESULTS.items():
        must_fail = k.startswith("planted")
        verdict = ("caught" if n_bad else "NOT CAUGHT") if must_fail else ("PASS" if not n_bad else "FAIL")
        bad += (not n_bad) if must_fail else bool(n_bad)
        print(f"{verdict:10} {k}: {n_ok} equal, {n_bad} different {why}")
    print(f"planted faults caught: {caught}; text within tolerance: {text_ok}")
    return 0 if not bad and caught and text_ok else 1


if __name__ == "__main__":
    sys.exit(main())

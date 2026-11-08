# dpimg: the image tool (instead of Pillow)

The user, 2026-11-07: "Make the small texture tool now so we dont need Pillow for future work. We will be doing more
art authoring." dpimg is one small C library (`port/imgtool`) with a command-line front end and a Python wrapper
(`port/tools/dpimg.py`, `port/tools/dpimg_draw.py`) that needs no third-party package. Every console kit shares it.
New art and texture tools use it; old ones move to it one at a time (what still imports PIL is in the last section).

## What it does, and how close to Pillow

Images are RGBA8 underneath (`px[(y * w + x) * 4 + c]`). Pillow's modes "L" and "1" are grey with alpha 255, and
"RGB" has alpha 255, so each operation gives what Pillow gives for that mode. A colour is `0xRRGGBBAA` in C, and a
tuple or an int in Python.

| Feature | C | Python (Pillow's names) | Against Pillow 13 |
|---------|---|-------------------------|-------------------|
| Load PNG, BMP, TGA, JPEG, GIF; save PNG, BMP, TGA | `dpimg_load`, `dpimg_save_as` | `open`, `Image.save` | equal |
| Crop, flip and rotate by 90s | `dpimg_crop`, `dpimg_transpose` | `crop`, `transpose` | equal |
| To grey, to RGB, ink box | `dpimg_grey`, `dpimg_bbox` | `convert("L"/"RGB")`, `getbbox` | equal |
| Resize: nearest, bilinear, bicubic, lanczos, box, hamming | `dpimg_resize` | `resize` | equal |
| Affine transform: nearest, bilinear, bicubic | `dpimg_transform` | `transform(size, AFFINE, a)` | equal |
| Paste (with an L or RGBA mask), fill a colour through a mask | `dpimg_paste`, `dpimg_fill` | `paste` | equal |
| Alpha composite, difference | `dpimg_alpha_composite`, `dpimg_difference` | `alpha_composite`, `ImageChops.difference` | equal |
| Rectangles, ellipses, polygons, lines (any width) | `dpimg_rect`, `dpimg_ellipse`, `dpimg_polygon`, `dpimg_line` | `ImageDraw.Draw(im).rectangle` and the rest | equal |
| TrueType and OpenType (CFF) text, anchors, stroke | `dpimg_font_load`, `dpimg_text`, `dpimg_text_bbox` | `ImageFont.truetype`, `Draw.text`, `getbbox`, `getlength` | close (below) |
| Dreamcast PVR textures to RGBA | `dpimg_decode_pvr` | `decode_pvr(tcw, (w, h), data)` | equal to txb.py's old decoder |
| Compare (count, largest difference, box) | `dpimg_compare` | `compare(a, b, tolerance)` | (ours) |
| Contact sheet | `dpimg_contact_sheet` | `contact_sheet(images, cols, cell)` | (ours) |
| Thumbnail | | `Image.thumbnail` | close: no reducing_gap step |

The resize, transform, paste, composite and drawing code is Pillow's arithmetic ported line for line, fixed point
where Pillow uses it, so it gives the same bytes. Text cannot: stb_truetype draws without FreeType's hinting. Pen
positions, advances, kerning, ascender and descender, anchors and the text box follow Pillow's basic layout (its
Windows wheels have no libraqm) to the 1/64 pixel; glyph edges differ by up to a pixel.

Proof: `docs/proofs/2026-11-07/dpimg_vs_pillow.py`, with its results in `dpimg_vs_pillow.md` beside it. Every exact
feature gave Pillow's bytes on 40 real Spawn art PNGs and on random shapes (tolerance 0). Text was held to each box
within 2 pixels and at least 90% shared ink both ways; it gave 92% to 99.7%. A changed byte and the wrong filter
were both caught. The PVR decoder is the kit's own (`port/pvr/pvr_twiddle.c`, included, not copied).

Not covered: other modes (P, I, F, CMYK), JPEG writing, multi-line text, line joints, filters (blur, sharpen), and
GameCube, Xbox 360 and Saturn texture formats (no tool of ours decodes them yet).

## From Python

```python
import sys; sys.path.insert(0, "port/tools")         # the kit's tools folder
from dpimg import Image, ImageDraw, ImageFont, decode_pvr, compare

im = Image.open("in.png")                          # RGBA underneath; .mode says how Pillow would see it
im = im.resize((256, 256), Image.LANCZOS)
d = ImageDraw.Draw(im)
d.rectangle((8, 8, 120, 40), fill=(0, 0, 0, 160), outline=(255, 255, 255, 255), width=2)
d.text((64, 24), "Spawn", fill=(255, 220, 0, 255), font=ImageFont.truetype("arialbd.ttf", 24), anchor="mm")
im.save("out.png")
tex = decode_pvr(0x08000000, (64, 64), raw)        # TCW: pixel format bits 27-29, VQ bit 30, non-twiddled bit 26
print(compare(im, Image.open("golden.png")))      # {"differing": 0, "max_delta": 0, "bbox": (0, 0, 0, 0)}
```

`np.asarray(im)` works as it does with Pillow. `ImageFont.truetype` looks in `%WINDIR%\Fonts` for a bare file name.

## From C

Link `dpimg.dll` and include `port/imgtool/include/dpimg.h`, which describes every function. Images are `dpimg *`
(`w`, `h`, `px`); free each with `dpimg_free`. A function that returns an image returns NULL on failure, and
`dpimg_error()` says why.

```c
dpimg *im = dpimg_load("in.png"), *small = dpimg_resize(im, 128, 128, DPIMG_LANCZOS);
dpimg_save(small, "out.png");
dpimg_free(im), dpimg_free(small);
```

## The command line

`py -3.12 port/tools/dpimg.py ARGS` runs `dpimg-cli ARGS`, building it first if needed:

```
info IN
convert IN OUT                                   any readable file to .png .bmp .tga
crop IN OUT X0 Y0 X1 Y1
resize IN OUT W H [nearest|bilinear|bicubic|lanczos|box|hamming]     bicubic, Pillow's default
transpose IN OUT flip_lr|flip_tb|rot90|rot180|rot270|transpose|transverse
compare A B [TOLERANCE] [DIFF_OUT]               exit 0 equal, 1 different, 2 error
sheet OUT COLS CELL_W CELL_H IN...
pvr RAW OUT TCW_HEX W H [OFFSET]                 a Dreamcast texture from a file of texture bytes
text OUT FONT SIZE TEXT [STROKE]                 white text on transparent, cut to its ink
```

## Building

The wrapper builds the library on first use, and again when a source is newer than the DLL, into
`port/imgtool/build/<x64|x86>` (git-ignored). It uses CMake and Ninja under the Visual Studio environment that
config.local.json's `vcvars` names (the kit's own build environment), for the running Python's bitness.
`py -3.12 port/tools/dpimg.py build` does it by hand; `DPIMG_DLL` names a prebuilt DLL instead. A build takes about
10 seconds. The vendored `third_party/stb` headers (public domain) are listed with versions and hashes in their
README.

## Still on Pillow

Switched so far: Spawn's HD art step (`txb.py`). The rest move one at a time, each proven by its own outputs:
`port/tools` gen_sy_font.py, pad_icons.py, frames.py, interp_truth.py and emu_capture.py; the tests
test_port_regress_filled, test_test_rules and test_kit_vmulcd; Spawn_art's `_tools` (build.py, build2.py,
big_font.py, title_font.py, comicpairs.py, sheet.py, final.py) and `logo/_spec` (compose.py, hires_logo.py,
prep.py); and sprite-vectoriser.

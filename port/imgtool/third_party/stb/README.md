# Vendored stb headers

Single-file libraries by Sean Barrett and contributors, from https://github.com/nothings/stb (master, fetched
2026-11-07). Each file is dual-licensed at its end: MIT, or public domain (Unlicense). We use them under the public
domain alternative; nothing here is modified, so a newer copy can replace a file as it is.

| File | Version | sha256 |
|------|---------|--------|
| stb_image.h | v2.30 | 594c2fe35d49488b4382dbfaec8f98366defca819d916ac95becf3e75f4200b3 |
| stb_image_write.h | v1.16 | cbd5f0ad7a9cf4468affb36354a1d2338034f2c12473cf1a8e32053cb6914a05 |
| stb_truetype.h | v1.26 | ecd30b05e0dd4fea3a13c26810dd9e1992dc379049482c393d5a19e6b5090aab |

They are compiled once, in `src/dpimg_io.c` (image files) and `src/dpimg_text.c` (TrueType). Line endings were made LF.
`tools/sizes.py` leaves `third_party/` out of the 400-line cap: vendored code is not ours to split or read whole.

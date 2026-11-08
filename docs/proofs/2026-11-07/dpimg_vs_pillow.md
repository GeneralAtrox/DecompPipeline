# dpimg against Pillow (2026-11-07)

The user (2026-11-07): "Make the small texture tool now so we dont need Pillow for future work. We will be doing more
art authoring." dpimg is that tool (`docs/IMGTOOL.md`). This record shows it gives Pillow's results.

## How it was run

`dpimg_vs_pillow.py` ran on build box A with these inputs:

- dpimg built there with CMake and Ninja (x64).
- Build box A's Pillow: basic layout, FreeType 2.14.3, no raqm.
- 40 real PNG inputs (art that is not tracked).
- Three fonts: arialbd.ttf (TrueType), FrankRuhlHofshi-Bold.otf (CFF, cubic curves) and DejaVuSansMono.ttf.
- `--txb` set to Spawn's old numpy texture decoder.

It exited with 0.

## Results

| Feature | Cases | Result |
|---|---|---|
| Load PNG, save PNG (read back) | 40, 40 | equal |
| crop, transpose | 120, 280 | equal |
| convert L, convert RGB, getbbox | 40 each | equal |
| resize NEAREST, LANCZOS, BILINEAR, BICUBIC, BOX, HAMMING (RGBA) | 80 each | equal |
| resize on L | 240 | equal |
| alpha_composite, difference | 40 each | equal |
| paste: RGBA mask; colour with an L mask; colour on transparent; RGB with an L mask | 40 each | equal |
| transform AFFINE NEAREST, BILINEAR and BICUBIC, on L and on RGBA | 40 each | equal |
| ellipse, rectangle, polygon and line, on RGBA, L and 1 | 60 each | equal |
| pad_icons shapes | 3 | equal |
| Dreamcast textures (TXB types 1, 0xd, 9, 3, 4; 1555, 565, 4444), random bytes | 60 | equal |
| Text: 3 fonts x 48 and 96 px x stroke 0 and 3 x anchors ls, mm and none | 36 | within tolerance |

"Equal" means byte for byte (tolerance 0). Text goes through stb_truetype, where Pillow uses FreeType, so it is held
to a tolerance instead:

- The ink box is within 2 px of Pillow's. All 36 cases were 0 or 1 px off.
- At least 90% of the ink is shared, both ways. The cases shared 92.0% to 99.7%:
  - 48 px Arial Bold: 92.0% with no stroke, 96.3% stroked.
  - 96 px cases: 99.0% to 99.7%.

## Planted faults

The comparison must catch both of these, and did:

- One changed byte: caught (1 byte differs).
- LANCZOS swapped for BICUBIC: caught (4629 bytes differ).

## On Spawn's texture pack

Spawn's HD art step was switched from Pillow to dpimg (`art/hd/tools/txb.py`). That changed copy was then built
through the kit's own pack step, the one `build.py` runs, on the launcher path:

- dpimg built itself on first use in a fresh kit.
- The pack checked out with "all 37 images match expected.tsv".
- An A/B with the old Pillow decoder from the same commit gave 39 files each, with none missing and none different
  (byte-identical).

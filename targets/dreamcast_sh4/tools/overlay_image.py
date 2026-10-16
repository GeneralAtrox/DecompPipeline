"""Build a combined image: the main binary at 0x8c010000 plus overlays at their load addresses, for a project that
decompiles overlays (code files a game loads at a fixed RAM address at run time).

  python overlay_image.py MAIN OUT [--context] FILE@BASE ... [--context FILE@BASE ...]

BASE is the overlay's load address (0x0c... or 0x8c... form). Overlays after --context are only there for their
code and data (callees, globals); the others are the ones to decompile. Prints the project.json fields:
binary_sha256, image_gaps (padding that TcSetup turns into uninitialized RAM) and game_ranges.

Only overlays that are in memory at the same time belong in one image; overlays sharing a load address each need
their own image (and project).
"""
import hashlib
import json
import pathlib
import sys

LOAD = 0x8C010000
RAM_END = 0x8D000000


def parse(spec):
    f, base = spec.rsplit("@", 1)
    b = int(base, 16)
    if 0x0C000000 <= b < 0x0D000000:
        b |= 0x80000000
    return pathlib.Path(f), b


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        sys.exit(__doc__)
    main_bin, out = pathlib.Path(args[0]), pathlib.Path(args[1])
    parts, context = [], False
    for a in args[2:]:
        if a == "--context":
            context = True
            continue
        f, base = parse(a)
        parts.append((base, f.read_bytes(), f.name, context))
    image = bytearray(main_bin.read_bytes())
    end = LOAD + len(image)
    gaps, ranges = [], []
    for base, data, name, ctx in sorted(parts):
        if base < end:
            sys.exit(f"{name} at {base:08x} overlaps the image so far (ends at {end:08x})")
        if base + len(data) > RAM_END:
            sys.exit(f"{name} does not fit in RAM")
        if base > end:
            gaps.append(f"{end:08x}-{base:08x}")
            image += bytes(base - end)
        image += data
        end = base + len(data)
        if not ctx:
            ranges.append([f"{base:08x}", f"{end:08x}"])
        print(f"{name:16s} {base:08x}-{end:08x}{'  (context)' if ctx else ''}", file=sys.stderr)
    out.write_bytes(image)
    print(json.dumps({"binary_sha256": hashlib.sha256(image).hexdigest(), "image_gaps": gaps, "game_ranges": ranges},
                     indent=2))


if __name__ == "__main__":
    main()

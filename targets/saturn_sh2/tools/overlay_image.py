"""Build a Saturn workspace binary: a work RAM high image from 0x06000000 holding a loader and the overlays at their
load addresses (code files a game loads at a fixed RAM address at run time, such as satdisc.py's scan finds).

  python overlay_image.py OUT [--context] FILE@BASE ... [--context FILE@BASE ...]

BASE is the file's load address (0x06... or its cache-through form 0x26...). Files after --context are only there
for their code and data (callees, globals); the others are the ones to decompile. Prints the project.json fields:
load_address (always 0x06000000: the judge and the C runner put the image there), binary_sha256, image_gaps (padding,
from 0x06000000 to the first file included, that TcSetup turns into uninitialized RAM) and game_ranges.

Only files that are in memory at the same time belong in one image; overlays sharing a load address each need their
own image (and project).
"""
import hashlib
import json
import pathlib
import sys

LOAD = 0x06000000
RAM_END = 0x06100000


def parse(spec):
    f, base = spec.rsplit("@", 1)
    b = int(base, 16)
    if LOAD + 0x20000000 <= b < RAM_END + 0x20000000:
        b -= 0x20000000
    return pathlib.Path(f), b


def build(parts):
    """(image, gaps, ranges) for parts [(base, data, name, context)]."""
    image, end = bytearray(), LOAD
    gaps, ranges = [], []
    for base, data, name, ctx in sorted(parts):
        if base < end:
            raise ValueError(f"{name} at {base:08x} overlaps the image so far (ends at {end:08x})")
        if base + len(data) > RAM_END:
            raise ValueError(f"{name} does not fit in work RAM high")
        if base > end:
            gaps.append(f"{end:08x}-{base:08x}")
            image += bytes(base - end)
        image += data
        end = base + len(data)
        if not ctx:
            ranges.append([f"{base:08x}", f"{end:08x}"])
        print(f"{name:16s} {base:08x}-{end:08x}{'  (context)' if ctx else ''}", file=sys.stderr)
    return bytes(image), gaps, ranges


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    out = pathlib.Path(args[0])
    parts, context = [], False
    for a in args[1:]:
        if a == "--context":
            context = True
            continue
        f, base = parse(a)
        if not LOAD <= base < RAM_END:
            sys.exit(f"{f.name}: {base:08x} is not in work RAM high")
        parts.append((base, f.read_bytes(), f.name, context))
    try:
        image, gaps, ranges = build(parts)
    except ValueError as e:
        sys.exit(str(e))
    out.write_bytes(image)
    print(json.dumps({"load_address": f"0x{LOAD:08x}", "binary_sha256": hashlib.sha256(image).hexdigest(),
                      "image_gaps": gaps, "game_ranges": ranges}, indent=2))


if __name__ == "__main__":
    main()

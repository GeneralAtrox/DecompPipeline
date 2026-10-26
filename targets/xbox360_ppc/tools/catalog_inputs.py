"""The catalog inputs of an Xbox 360 title, without Ghidra: the loaded image and its facts (xex.py) and the
function entries, helpers and per-function features (ppcscan.py).

usage: python catalog_inputs.py <default.xex | disc.iso> <out_dir>
A disc image is read with xiso.py and its default.xex written to out_dir first. Writes out_dir/image.bin, image.json, catalog_inputs.json (compact) and code_ranges.txt (the instruction byte
ranges the runner's R8 check reads: every function body, helper and import thunk, "%08x %08x" per line).
"""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ppcscan  # noqa: E402
import xex  # noqa: E402
import xiso  # noqa: E402


def code_ranges(cat):
    spans = [(int(f["addr"], 16), int(f["addr"], 16) + f["size"]) for f in cat["functions"]]
    spans += [(lo, hi) for _, lo, hi in cat["helper_ranges"]]
    spans += [(e["thunk"], e["thunk"] + 16) for e in cat["imports"] if e["thunk"]]
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def build(xex_path, out_dir):
    out = pathlib.Path(out_dir)
    with open(xex_path, "rb") as fh:
        is_xex = fh.read(4) == xex.MAGIC
    if not is_xex:
        with xiso.Disc(xex_path) as disc:
            disc.extract("default.xex", out / "default.xex")
        xex_path = out / "default.xex"
    desc = xex.extract(xex_path, out)
    img = (out / "image.bin").read_bytes()
    cat = ppcscan.scan(img, desc)
    (out / "catalog_inputs.json").write_text(json.dumps(cat, separators=(",", ":")), encoding="utf-8")
    (out / "code_ranges.txt").write_text("".join(f"{lo:08x} {hi:08x}\n" for lo, hi in code_ranges(cat)),
                                         encoding="utf-8")
    return cat


def main(argv):
    if len(argv) != 3:
        print("usage: python catalog_inputs.py <default.xex | disc.iso> <out_dir>")
        return 2
    t = time.time()
    try:
        cat = build(argv[1], argv[2])
    except (xex.XexError, xiso.DiscError) as e:
        print(f"catalog_inputs: {e}")
        return 1
    print(json.dumps(cat["stats"], indent=1))
    print(f"helpers: {sum(1 for k in cat['helpers'] if not k.endswith('ambiguous'))} entries; "
          f"{time.time() - t:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

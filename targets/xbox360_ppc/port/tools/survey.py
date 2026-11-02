"""Survey of a loaded Xbox 360 image, the first step of a port (DESIGN_PORT.md): which imports the game's code reaches and how
often, which instructions build an address in the GPU window (0x7FC8xxxx), the library and format strings, and the
compiled-shader containers. Reads the workspace (catalog_inputs.json, image.json, image.bin, code_ranges.txt) and writes one
JSON file; it decides nothing, the design table is written from it by hand.

Nearest existing: targets/xbox360_ppc/translator (catalog, decode) - they translate; this only counts and lists.

  python survey.py --ws WORKSPACE --out survey.json
"""
import argparse
import bisect
import collections
import json
import pathlib
import re

STRINGS = re.compile(r"d3d|xgr|xaud|xma|xmp|xui|xam|xdk|\.fx|\.xpu|\.xvu|shader|vs_|ps_|\.dll|\.xex|xbox|dsound|audio|sound|"
                     r"\.bik|\.wmv|\.xmv|thread|\.sdt|\.arc|\.cpk|\.pak|\.dat|\.bin|\.wma|\.xma|texture|\.dds|\.xpr|nui|live|"
                     r"gamer|achiev|leaderboard|storage|content|save", re.I)


def code_ranges(ws):
    out = []
    for line in (ws / "code_ranges.txt").read_text().splitlines():
        t = line.split()
        if len(t) >= 2 and not line.startswith("#"):
            try:
                out.append((int(t[0], 16), int(t[1], 16)))
            except ValueError:
                pass
    return out


def survey(ws):
    cat = json.loads((ws / "catalog_inputs.json").read_text())
    img = json.loads((ws / "image.json").read_text())
    data = (ws / "image.bin").read_bytes()
    base = img.get("load_address", 0x82000000)
    starts = sorted(int(f["addr"], 16) for f in cat["functions"])

    def owner(a):
        i = bisect.bisect_right(starts, a) - 1
        return starts[i] if i >= 0 else 0

    imps = {i["thunk"]: i for i in cat["imports"] if i.get("thunk")}
    calls = collections.defaultdict(list)
    gpu = collections.defaultdict(list)
    for lo, hi in code_ranges(ws):
        for a in range(lo, hi, 4):
            o = a - base
            if o < 0 or o + 4 > len(data):
                continue
            w = int.from_bytes(data[o:o + 4], "big")
            op = w >> 26
            if op == 18 and not (w & 2):                  # b / bl to an import thunk
                li = w & 0x03FFFFFC
                if li & 0x02000000:
                    li -= 0x04000000
                t = (a + li) & 0xFFFFFFFF
                if t in imps:
                    calls[t].append(a)
            elif op == 15 and ((w >> 16) & 31) == 0:      # lis rD, imm with imm in the GPU window
                imm = w & 0xFFFF
                if 0x7FC8 <= imm <= 0x7FD0:
                    gpu[imm].append(a)
    out = {"imports": [], "gpu_lis": {hex(k): [(hex(a), hex(owner(a))) for a in v] for k, v in gpu.items()}}
    for t, im in sorted(imps.items(), key=lambda kv: -len(calls.get(kv[0], []))):
        cs = calls.get(t, [])
        out["imports"].append({"lib": im["library"], "ord": im["ordinal"], "thunk": hex(t), "sites": len(cs),
                               "callers": len({owner(a) for a in cs}),
                               "first_callers": sorted({hex(owner(a)) for a in cs})[:6]})
    out["variables"] = [(i["library"], i["ordinal"], hex(i["slot"])) for i in cat["imports"] if i.get("kind") == "variable"]
    strs = {}
    for m in re.finditer(rb"[\x20-\x7e]{6,}", data):
        s = m.group().decode()
        if STRINGS.search(s):
            strs[s] = m.start() + base
    out["strings"] = [(s, hex(a)) for s, a in list(strs.items())[:400]]
    blobs = collections.Counter(data[m.start():m.start() + 4].hex() for m in re.finditer(rb"\x10\x2a\x11[\x00-\x03]", data))
    out["shader_magic_102A11xx"] = dict(blobs)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = survey(pathlib.Path(a.ws))
    pathlib.Path(a.out).write_text(json.dumps(out, indent=0))
    used = [i for i in out["imports"] if i["sites"]]
    print(f"imports reached: {len(used)} of {len(out['imports'])}; GPU-window constants: "
          f"{sum(len(v) for v in out['gpu_lis'].values())} instructions; shader containers: {out['shader_magic_102A11xx']}")


if __name__ == "__main__":
    main()

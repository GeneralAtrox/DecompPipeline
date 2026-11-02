"""Checks a --ta-dump scene's modifier volumes: the parameters and the volumes' shape, read the way the TA reads them.

    py -3.12 port/tools/ta_volumes.py ta_scene_N.txt [-v]

Reads each pass's opaque (list 1) and translucent (list 3) modifier volume list. A volume is its triangles up to and
including those of an inclusion or exclusion header. Exit 1 when a list holds a word that is no volume parameter, a vertex
with 1/w <= 0 or not finite, or a volume that is not closed (an edge not shared by an even number of triangles): the marks
of another list's data parsed as triangles, which draws as stripes of shadow across the scene. Exit 0 otherwise.
Nearest existing: port/tools/hw_check.py compares scenes with an emulator's; this needs no emulator, one dump alone.
"""
import collections
import re
import struct
import sys

SLOT = 8
TRIANGLE = 16
PARA_END, PARA_POLY, PARA_VERTEX = 0, 4, 7
MOD_LISTS = (1, 3)


def read_dump(path):
    lists, cur = {}, None
    with open(path, encoding="ascii") as f:
        for line in f:
            m = re.match(r"pass (\d+) list (\d+)", line)
            if m:
                cur = lists.setdefault((int(m[1]), int(m[2])), [])
            else:
                cur += [int(x, 16) for x in line.split(":", 1)[1].split()]
    return lists


def floats(words):
    return struct.unpack(f"<{len(words)}f", struct.pack(f"<{len(words)}I", *words))


def volumes(words):
    """([volume: [triangle: three (x, y, z)]], [offsets of words that are no volume parameter])"""
    out, cur, odd, i, closing = [], [], [], 0, False
    while i + SLOT <= len(words):
        kind = words[i] >> 29
        if kind == PARA_POLY:
            if closing:
                out.append(cur)
                cur = []
            closing = words[i + 1] >> 29 != 0
            i += SLOT
        elif kind == PARA_VERTEX and i + TRIANGLE <= len(words):
            v = floats(words[i + 1:i + 10])
            cur.append((v[0:3], v[3:6], v[6:9]))
            i += TRIANGLE
        else:
            if kind != PARA_END:
                odd.append(i * 4)
            i += SLOT
    if cur:
        out.append(cur)
    return out, odd


def open_edges(triangles):
    edges = collections.Counter()
    for t in triangles:
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edges[tuple(sorted((t[a], t[b])))] += 1
    return sum(n % 2 for n in edges.values())


def check(path, verbose=False):
    """The faults found in a dump, one line each."""
    faults = []
    for (p, l), words in sorted(read_dump(path).items()):
        if l not in MOD_LISTS or not words:
            continue
        vols, odd = volumes(words)
        if odd:
            faults.append(f"pass {p} list {l}: {len(odd)} words are no volume parameter, the first at byte {odd[0]:#x}")
        for n, tris in enumerate(vols):
            bad = sum(not 0.0 < v[2] < 3.0e38 for t in tris for v in t)
            gaps = open_edges(tris)
            if bad or gaps:
                faults.append(f"pass {p} list {l} volume {n}: {len(tris)} triangles, {bad} bad 1/w, {gaps} open edges")
            elif verbose:
                print(f"pass {p} list {l} volume {n}: {len(tris)} triangles, closed")
        print(f"pass {p} list {l}: {len(words) * 4} bytes, {len(vols)} volumes")
    return faults


def main():
    faults = check(sys.argv[1], "-v" in sys.argv[2:])
    for line in faults[:20]:
        print(line)
    print(f"{len(faults)} fault(s)")
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())

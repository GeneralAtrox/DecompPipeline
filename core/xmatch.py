"""Match functions of one project against an earlier project of the same CPU by their machine code.

Addresses are ignored: call/branch displacements leaving the function are masked, and literal-pool words that hold
guest addresses become address pairs (earlier project -> this one). A match is exact when the masked code and every
non-address constant are equal and the address pairs are consistent; the pairs then relocate the earlier project's C
and carry its names over. Otherwise the most similar earlier function (word 4-grams, then difflib) is a near match.

SH-4 only for now (16-bit instructions, PC-relative literal pools).
"""
import collections
import difflib
import json
import pathlib
import struct

from . import scope

IMAGE_BASE = 0x8C010000


def is_guest_addr(v):
    return (v >> 24) in (0x8C, 0x0C, 0xAC) and (v & 0xFFFFFF) < 0x1000000


class Image:
    def __init__(self, workspace):
        ws = pathlib.Path(workspace)
        proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        self.ws = ws
        self.ranges = scope.game_ranges(proj)  # the game's code ranges (is_game)
        self.scope = scope.Scope(proj, ws)  # the functions it decompiles: ranges + extra_functions (in_scope)
        self.data = (ws / proj["binary"]).resolve().read_bytes()
        cat = json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]
        self.functions = {f["addr"]: f for f in cat}
        self.code = []
        for line in (ws / "state" / "code_ranges.txt").read_text(encoding="utf-8").splitlines():
            if line.strip():
                lo, hi = line.split()[:2]
                self.code.append((int(lo, 16), int(hi, 16)))
        self.code.sort()

    def u16(self, a):
        return struct.unpack_from("<H", self.data, a - IMAGE_BASE)[0]

    def u32(self, a):
        return struct.unpack_from("<I", self.data, a - IMAGE_BASE)[0]

    def in_image(self, a, n=2):
        return IMAGE_BASE <= a and a + n <= IMAGE_BASE + len(self.data)

    def is_game(self, addr):
        """Inside the game's code ranges."""
        return any(lo <= int(addr, 16) < hi for lo, hi in self.ranges)

    def in_scope(self, addr):
        """A function the project decompiles: the game ranges or its extra_functions (xproject.py stages C for these
        and reports them apart)."""
        return addr in self.scope


def normalize(img, addr):
    """-> (masked instruction words, constants, address slots) of one function, or None if it can't be read.

    constants: [(kind, value)] for non-address pool words, in reference order; address slots: [guest address] for
    pool words holding addresses and for call/branch targets outside the function, in reference order."""
    f = img.functions.get(addr)
    if not f:
        return None
    lo = int(addr, 16)
    hi = lo + int(f["bytes"])
    if not img.in_image(lo, hi - lo):
        return None
    words, consts, slots = [], [], []
    for pc in range(lo, hi, 2):
        w = img.u16(pc)
        top = w >> 12
        if top in (0xA, 0xB):  # bra / bsr disp12
            d = w & 0xFFF
            target = pc + 4 + 2 * (d - 0x1000 if d & 0x800 else d)
            if not lo <= target < hi:
                slots.append(target)
                w &= 0xF000
        elif top == 0x9 or top == 0xD or (w & 0xFF00) == 0xC700:  # mov.w / mov.l / mova @(disp,PC)
            d = w & 0xFF
            if top == 0x9:
                ref, size = pc + 4 + 2 * d, 2
            else:
                ref, size = (pc & ~3) + 4 + 4 * d, 4
            if (w & 0xFF00) == 0xC700:
                if not lo <= ref < hi + 0x400:
                    slots.append(ref)
            elif img.in_image(ref, size):
                v = img.u16(ref) if size == 2 else img.u32(ref)
                if size == 4 and is_guest_addr(v):
                    slots.append(v)
                else:
                    consts.append((size, v))
        words.append(w)
    return tuple(words), tuple(consts), tuple(slots)


def pair_slots(a_slots, b_slots):
    """Consistent pairing of two slot lists (earlier -> this project), or None."""
    if len(a_slots) != len(b_slots):
        return None
    fwd, back = {}, {}
    for x, y in zip(a_slots, b_slots):
        if fwd.setdefault(x, y) != y or back.setdefault(y, x) != x:
            return None
    return fwd


def shingles(words, n=4):
    return {words[i:i + n] for i in range(max(1, len(words) - n + 1))}


def match(earlier, current, near_min=0.6):
    """-> {current addr: {"from": addr, "kind": "exact"|"near", "ratio": r, "pairs": {hex: hex}}}."""
    prior = {}
    by_key = collections.defaultdict(list)
    index = collections.defaultdict(set)
    for a in earlier.functions:
        n = normalize(earlier, a)
        if n is None or not n[0]:
            continue
        prior[a] = n
        by_key[(n[0], n[1])].append(a)
        for s in shingles(n[0]):
            index[s].add(a)
    out = {}
    for a in current.functions:
        n = normalize(current, a)
        if n is None or not n[0]:
            continue
        exact = None
        for cand in by_key.get((n[0], n[1]), []):
            pairs = pair_slots(prior[cand][2], n[2])
            if pairs is not None:
                exact = (cand, pairs)
                break
        if exact:
            out[a] = {"from": exact[0], "kind": "exact", "ratio": 1.0,
                      "pairs": {f"{x:08x}": f"{y:08x}" for x, y in exact[1].items()}}
            continue
        mine = shingles(n[0])
        votes = collections.Counter()
        for s in mine:
            for c in index.get(s, ()):
                votes[c] += 1
        best = None
        for c, _ in votes.most_common(5):
            r = difflib.SequenceMatcher(None, prior[c][0], n[0], autojunk=False).ratio()
            if best is None or r > best[1]:
                best = (c, r)
        if best and best[1] >= near_min:
            out[a] = {"from": best[0], "kind": "near", "ratio": round(best[1], 3), "pairs": {}}
    return out


def address_map(matches):
    """Address pairs agreed by every exact match: {earlier hex: current hex}; conflicts are dropped."""
    seen = collections.defaultdict(set)
    for cur, m in matches.items():
        if m["kind"] == "exact":
            seen[m["from"]].add(cur)
            for x, y in m["pairs"].items():
                seen[x].add(y)
    return {x: next(iter(ys)) for x, ys in seen.items() if len(ys) == 1}

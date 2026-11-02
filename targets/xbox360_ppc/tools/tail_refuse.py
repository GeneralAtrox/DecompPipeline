"""Functions every run of which leaves the function's body by a branch into the middle of another function: the oracle
refuses them ("executes code outside its body"), so preparing them costs minutes of Ghidra for nothing.

usage: python tail_refuse.py WORKSPACE OUT.json [--check]
Walks each function's instructions from its entry. A path ends GOOD (a blr, a trap, a branch to a frame helper, to a
function entry or to code in no function, and bcctr without link: the oracle mocks that tail call and the run ends,
TcRun.tailJump), BAD (a branch that is not a call, into the middle of another function), or UNKNOWN (a fall-through past
the body's end; code outside the image). A function is listed only when at least one BAD exit is reachable and no GOOD
or UNKNOWN end is: then no input can run to the end. OUT.json is {"certain": {addr: [the BAD targets]}}, applied by
catalog_refuse.py --tails (refuse "unsupported: tail call out of the body").
--check compares the list with the prepared harnesses (harness/<addr>/meta.json): a listed function that has vectors is
a false positive (exit code 1); a prepared function refused with "executes code outside its body" and not listed is
counted as missed.
Nearest existing: catalog_refuse.py (applies the result), vector_abi.py (the same kind of per-function static scan).
"""
import bisect
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "translator"))
from xenontranslate.decode import decode  # noqa: E402

REASON = "unsupported: tail call out of the body"


class Scan:
    def __init__(self, ws):
        self.ws = pathlib.Path(ws)
        ij = json.loads((self.ws / "image.json").read_text())
        self.base = ij.get("load_address", 0x82000000)
        self.data = (self.ws / "image.bin").read_bytes()
        cat = json.loads((self.ws / "catalog_inputs.json").read_text())
        self.helpers = [(lo, hi) for _, lo, hi in cat["helper_ranges"]]
        self.funcs = {int(f["addr"], 16): f for f in cat["functions"]}
        self.entries = set(self.funcs) | {e["thunk"] for e in cat["imports"] if e.get("thunk")}   # Ghidra's functions
        self.bodies = self.read_bodies()
        self.ranges = sorted(r for rs in self.bodies.values() for r in rs)

    def read_bodies(self):
        """{addr: [(lo, hi)]}: Ghidra's bodies (state/function_bodies.txt) when the workspace has them, else the
        catalog's entry and size."""
        out = {}
        p = self.ws / "state" / "function_bodies.txt"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                t = line.split()
                if len(t) >= 3 and not t[0].startswith("#"):
                    out.setdefault(int(t[0], 16), []).append((int(t[1], 16), int(t[2], 16)))
        for a, f in self.funcs.items():
            if f.get("kind") == "function":
                out.setdefault(a, [(a, a + f["size"])])
        return out

    def word(self, a):
        o = a - self.base
        if 0 <= o <= len(self.data) - 4:
            return int.from_bytes(self.data[o:o + 4], "big")
        return None

    def helper(self, a):
        return any(lo <= a < hi for lo, hi in self.helpers)

    def in_function(self, a):
        i = bisect.bisect_right(self.ranges, (a, float("inf"))) - 1
        return i >= 0 and self.ranges[i][0] <= a < self.ranges[i][1]

    def walk(self, entry):
        """(bad targets, whether a GOOD or UNKNOWN end is reachable)."""
        body = self.bodies[entry]
        inb = lambda a: any(lo <= a < hi for lo, hi in body)  # noqa: E731
        bad, ok, seen, todo = set(), False, set(), [entry]
        while todo and not ok:
            a = todo.pop()
            while True:
                if a in seen:
                    break
                if not inb(a):
                    ok = True                      # a fall-through past the body
                    break
                seen.add(a)
                w = self.word(a)
                if w is None:
                    ok = True
                    break
                i = decode(w, a)
                o = i.op
                if o in ("b", "bc"):
                    always = o == "b" or (i.bo & 0x14) == 0x14
                    t = i.target
                    if i.lk:                       # a call: the mocked callee returns here
                        if always and t == a + 4:
                            a += 4
                            continue
                        a += 4
                        continue
                    if inb(t):
                        todo.append(t)
                    elif self.helper(t) or t in self.entries or not self.in_function(t):
                        ok = True
                    else:
                        bad.add(t)
                    if always:
                        break
                    a += 4
                    continue
                if o == "bclr":
                    always = (i.bo & 0x14) == 0x14
                    if i.lk:
                        a += 4
                        continue
                    ok = True                      # blr
                    if always:
                        break
                    a += 4
                    continue
                if o == "bcctr":
                    if i.lk:
                        a += 4
                        continue
                    ok = True                      # a jump table or a computed tail call: mocked, the run ends
                    break
                if o in ("rfid", "hrfid") or (o in ("tw", "twi", "td", "tdi") and i.to == 31):
                    ok = True
                    break
                a += 4
        return bad, ok

    def scan(self):
        """{addr: BAD targets}."""
        out = {}
        for entry in sorted(self.bodies):
            f = self.funcs.get(entry)
            if f is None or f.get("kind") != "function" or f.get("refuse"):
                continue
            bad, ok = self.walk(entry)
            if not ok and bad:
                out[f"{entry:08x}"] = sorted(f"{t:08x}" for t in bad)
        return out


def check(ws, found):
    """(listed with vectors, prepared and refused outside the body, of those listed, unsupported-for-another-reason)."""
    false_pos, outside, missed, other = [], 0, [], []
    for p in sorted((pathlib.Path(ws) / "harness").glob("*/meta.json")):
        addr = p.parent.name
        meta = json.loads(p.read_text(encoding="utf-8"))
        has_vectors = (p.parent / "vectors.txt").exists() and not meta.get("unsupported")
        why = meta.get("unsupported") or ""
        if addr in found:
            if has_vectors:
                false_pos.append(addr)
            elif "outside its body" not in why:
                other.append((addr, why[:70]))
        if "outside its body" in why:
            outside += 1
            if addr not in found:
                missed.append(addr)
    return false_pos, outside, missed, other


def main():
    ws, out = sys.argv[1], sys.argv[2]
    found = Scan(ws).scan()
    pathlib.Path(out).write_text(json.dumps({"certain": found}, indent=0), encoding="utf-8")
    print(f"{len(found)} functions leave their body into another function on every path")
    if "--check" in sys.argv:
        fp, outside, missed, other = check(ws, found)
        print(f"prepared harnesses refused 'outside its body': {outside}; listed: {outside - len(missed)}, "
              f"not listed: {len(missed)}")
        print(f"listed with vectors (FALSE POSITIVES): {len(fp)} {fp[:10]}")
        print(f"listed, prepared, refused for another reason: {len(other)} {other[:5]}")
        return 1 if fp else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

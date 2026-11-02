"""Finds the boundary of the statically linked XDK Direct3D library in an Xbox 360 image, so the kit can cut there instead of
translating it (DESIGN_PORT.md section 2). The library is the set of functions that talk to the GPU, plus the functions only
they call. Seeds: a call to a Vd* kernel import (GPU set-up), an instruction that builds an address in the GPU window
(0x7FC8xxxx), a string that names the library. Growth: a callee joins when every direct caller is already in the set. The
entry points are the members the rest of the game calls. Direct calls only: a function reached through a table of function
pointers shows as having a caller outside the set only if a direct call exists, and is listed under "pointer_refs" for a look.

Nearest existing: targets/xbox360_ppc/port/tools/survey.py - it counts imports and GPU-window instructions; this builds the
call graph and the set.

  python boundary.py --ws WORKSPACE --out boundary_candidates.json [--run-touched run.log.touched]
"""
import argparse
import bisect
import collections
import json
import pathlib
import re

KIT = pathlib.Path(__file__).resolve().parent.parent
SEED_STRING = re.compile(r"d3d|\bgpu\b|xenos|vertex shader|pixel shader|ERR\[", re.I)


def words(data, base, lo, size):
    for a in range(lo, lo + size, 4):
        o = a - base
        if 0 <= o and o + 4 <= len(data):
            yield a, int.from_bytes(data[o:o + 4], "big")


def build(ws):
    cat = json.loads((ws / "catalog_inputs.json").read_text())
    img = json.loads((ws / "image.json").read_text())
    data = (ws / "image.bin").read_bytes()
    base = img.get("load_address", 0x82000000)
    names = json.loads((KIT / "data" / "export_names.json").read_text())
    funcs = sorted((int(f["addr"], 16), f["size"]) for f in cat["functions"])
    starts = [f[0] for f in funcs]
    start_set = set(starts)
    imps = {i["thunk"]: i for i in cat["imports"] if i.get("thunk")}
    try:
        strings = {int(a, 16): f.get("strings") or [] for a, f in
                   ((x["addr"], x) for x in json.loads((ws / "state" / "catalog.json").read_text())["functions"])}
    except (OSError, ValueError, KeyError):
        strings = {}
    callees = {a: set() for a in starts}
    callers = collections.defaultdict(set)
    vd = collections.defaultdict(set)
    gpu = set()
    for lo, size in funcs:
        for a, w in words(data, base, lo, size):
            op = w >> 26
            if op == 18 and not (w & 2):
                li = w & 0x03FFFFFC
                if li & 0x02000000:
                    li -= 0x04000000
                t = (a + li) & 0xFFFFFFFF
                if t in imps:
                    i = imps[t]
                    nm = names.get(i["library"].split(".")[0], {}).get(str(i["ordinal"]), ["?"])[0]
                    if nm.startswith("Vd"):
                        vd[lo].add(nm)
                elif t in start_set and t != lo:
                    callees[lo].add(t)
                    callers[t].add(lo)
            elif op == 15 and ((w >> 16) & 31) == 0 and 0x7FC8 <= (w & 0xFFFF) <= 0x7FD0:
                gpu.add(lo)
    return funcs, starts, callees, callers, vd, gpu, strings


def library(funcs, callees, callers, seeds):
    members = set(seeds)
    changed = True
    while changed:
        changed = False
        for m in list(members):
            for c in callees.get(m, ()):
                if c not in members and callers[c] and callers[c] <= members:
                    members.add(c)
                    changed = True
    return members


def best_ranges(starts, callees, callers, seeds, reach=150, top=5):
    """The contiguous span of functions (a link unit sits together in the image) that holds every seed and has the fewest
    functions called from outside it plus the fewest distinct callees it leaves for, with a small price per member so the span
    does not grow to the whole image. Slides both edges `reach` functions past the seeds."""
    idx = {a: i for i, a in enumerate(starts)}
    si = sorted(idx[x] for x in seeds)
    lo0, hi0 = si[0], si[-1]
    results = []
    for lo in range(max(0, lo0 - reach), lo0 + 1):
        inside = set()
        ext = {}
        entries = 0
        leave = collections.Counter()
        members = 0
        for hi in range(lo, min(len(starts), hi0 + reach) + 1):
            if hi == len(starts):
                break
            g = starts[hi]
            inside.add(g)
            members += 1
            ext[g] = len(callers[g] - inside)
            if ext[g] > 0:
                entries += 1
            for c in callees[g]:
                if c in inside and c != g:
                    if ext[c] > 0 and ext[c] - 1 == 0:
                        entries -= 1
                    ext[c] -= 1
            for p in callers[g]:
                pass
            for c in callees[g]:
                if c not in inside:
                    leave[c] += 1
            for c in [c for c in leave if c in inside]:
                del leave[c]
            if hi >= hi0:
                cost = entries + len(leave) + 0.3 * members
                results.append((cost, lo, hi, entries, len(leave), members))
    results.sort()
    return [(starts[lo], starts[hi], e, lv, m) for _, lo, hi, e, lv, m in results[:top]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-touched")
    ap.add_argument("--write-boundary", help="write the best span as the kit's boundary.json (library ranges not to translate)")
    a = ap.parse_args()
    funcs, starts, callees, callers, vd, gpu, strings = build(pathlib.Path(a.ws))
    by_string = {f for f, ss in strings.items() if any(SEED_STRING.search(s) for s in ss)}
    seeds = set(vd) | gpu     # a function that merely names the library in a string (a shader-text writer) is game code
    members = library(funcs, callees, callers, seeds)
    size = dict(funcs)
    entries = sorted(m for m in members if callers[m] - members)
    ranges = []
    for m in sorted(members):
        if ranges and m <= ranges[-1][1] + 16:
            ranges[-1][1] = max(ranges[-1][1], m + size[m])
        else:
            ranges.append([m, m + size[m]])
    out = {"seeds": {"vd_import": len(vd), "gpu_window": len(gpu), "string": len(by_string)},
           "members": len(members), "bytes": sum(size[m] for m in members),
           "ranges": [[f"{lo:08x}", f"{hi:08x}"] for lo, hi in ranges],
           "entry_points": [{"addr": f"{e:08x}", "callers_outside": len(callers[e] - members),
                             "first_callers": sorted(f"{c:08x}" for c in callers[e] - members)[:4]} for e in entries],
           "vd_calls": {f"{k:08x}": sorted(v) for k, v in sorted(vd.items())}}
    if a.run_touched:
        touched = {int(x, 16) for x in pathlib.Path(a.run_touched).read_text().split()}
        out["run_touched_in_set"] = sorted(f"{t:08x}" for t in touched & members)
    # the link unit sits together: members far from the seeds (a thread-id helper only the library calls) do not stretch the span
    core = {m for m in members | seeds if min(seeds) - 0x10000 <= m <= max(seeds) + 0x10000}
    out["span_candidates"] = [{"lo": f"{lo:08x}", "hi_function": f"{hi:08x}", "called_from_outside": e, "leaves_for": lv, "members": m}
                              for lo, hi, e, lv, m in best_ranges(starts, callees, callers, core)]
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1))
    if a.write_boundary:
        lo, hi, e, lv, m = best_ranges(starts, callees, callers, core)[0]
        why = (f"XDK Direct3D: {m} functions, {e} called from outside, leaves for {lv} callees; found by tools/boundary.py "
               f"(seeds: {len(vd)} functions calling Vd* imports, {len(gpu)} building GPU-window addresses)")
        pathlib.Path(a.write_boundary).write_text(json.dumps([[f"{lo:08x}", f"{hi + size[hi]:08x}", why]], indent=1) + "\n")
    print("span candidates:", out["span_candidates"][:3])
    print(f"seeds {out['seeds']}; members {out['members']} ({out['bytes']} bytes) in {len(ranges)} ranges; "
          f"{len(entries)} entry points called from outside")


if __name__ == "__main__":
    main()

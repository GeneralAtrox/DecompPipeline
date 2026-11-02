"""Recompute every catalog_inputs.json function's "refuse" from its instruction-class counts with ppcscan.REFUSE.

usage: python catalog_refuse.py WORKSPACE_OR_CATALOG_JSON [--dry] [--vabi vabi.json] [--gaps decode_gaps.json]
       [--tails tails.json]
For a catalog scanned before ppcscan.REFUSE changed (the vector classes are no longer refused: TcVmx runs them): the
counts do not change, only which class refuses. With --vabi (vector_abi.py's output) a function the counts leave
judgeable is also refused when a vector value crosses its boundary ("unsupported: vector ABI": the judge compares no
vector arguments or results) or it uses a vector estimate op ("unsupported: vector estimate"); a function listed in decode_gaps.json (beside the catalog, or
--gaps; x360setup.py ghidra writes it) is refused as "unsupported: embedded data". With --tails (tail_refuse.py's
output) a function every run of which leaves its body into another function is refused as "unsupported: tail call out
of the body" (the oracle refuses it: no prepare). Prints how many functions changed refusal. Rewrites the file unless --dry.
Nearest existing: ppcscan.py scan - it derives "refuse" while scanning the executable, which needs the XEX; this
works from the counts a finished catalog already holds.
"""
import json
import pathlib
import sys

import ppcscan

TAILS = ("unsupported: tail call out of the body",)


def rerefuse(cat, vabi=None, gaps=None, tails=None):
    """Set each function's refuse from its counts (and vabi, {addr: reasons}; gaps, the addresses of functions with a body
    word Ghidra holds as data); return {(old, new): n} for the changed ones."""
    changed = {}
    for f in cat["functions"]:
        if f.get("kind") != "function":
            continue
        counts = f.get("counts") or {}
        new = next((why for c, why in ppcscan.REFUSE if counts.get(c)), None)
        if new is None and vabi and f["addr"] in vabi:
            new = "unsupported: vector estimate" if vabi[f["addr"]] == ["E"] else "unsupported: vector ABI"
        if new is None and gaps and f["addr"] in gaps:
            new = "unsupported: embedded data"
        if new is None and tails:
            new = TAILS[0] if f["addr"] in tails.get("certain", {}) else None
        if new != f.get("refuse"):
            changed[(f.get("refuse"), new)] = changed.get((f.get("refuse"), new), 0) + 1
            f["refuse"] = new
    return changed


def main():
    path = pathlib.Path(sys.argv[1])
    if path.is_dir():
        path = path / "catalog_inputs.json"
    cat = json.loads(path.read_text(encoding="utf-8"))
    vabi = json.loads(pathlib.Path(sys.argv[sys.argv.index("--vabi") + 1]).read_text(encoding="utf-8")) if "--vabi" in sys.argv else None
    gaps = None
    if "--gaps" in sys.argv:
        gaps = set(json.loads(pathlib.Path(sys.argv[sys.argv.index("--gaps") + 1]).read_text(encoding="utf-8")))
    elif (path.parent / "decode_gaps.json").exists():
        gaps = set(json.loads((path.parent / "decode_gaps.json").read_text(encoding="utf-8")))
    tails = None
    if "--tails" in sys.argv:
        tails = json.loads(pathlib.Path(sys.argv[sys.argv.index("--tails") + 1]).read_text(encoding="utf-8"))
    changed = rerefuse(cat, vabi, gaps, tails)
    for (old, new), n in sorted(changed.items(), key=lambda kv: -kv[1]):
        print(f"{n:6d}  {old} -> {new}")
    if "--dry" not in sys.argv and changed:
        path.write_text(json.dumps(cat, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

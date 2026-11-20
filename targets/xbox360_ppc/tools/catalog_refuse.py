"""Recompute every catalog_inputs.json function's "refuse" from its instruction-class counts with ppcscan.REFUSE.

usage: python catalog_refuse.py WORKSPACE_OR_CATALOG_JSON [--dry] [--vabi vabi.json] [--gaps decode_gaps.json]
       [--tails tails.json] [--out FILE] [--image DIR]
For a catalog scanned before ppcscan.REFUSE changed (the vector classes are no longer refused: TcVmx runs them): the
counts do not change, only which class refuses. With --vabi (vector_abi.py's output) a function the counts leave
judgeable is also refused when a vector value crosses its boundary ("unsupported: vector ABI": the judge compares no
vector arguments or results) or it uses a vector estimate op ("unsupported: vector estimate"); a function listed in decode_gaps.json (beside the catalog, or
--gaps; x360setup.py ghidra writes it) is refused as "unsupported: embedded data". With --tails (tail_refuse.py's
output) a function every run of which leaves its body into another function is refused as "unsupported: tail call out
of the body" (the oracle refuses it: no prepare). Prints how many functions changed refusal. Rewrites the file unless --dry, or writes the result to --out and leaves
the input alone.
The per-op gate: a function with a vector op not on the confirmed list (ghidra_scripts/TcVmxConfirmed.java, the one list
the oracle's TcFixups.refuseVector reads too) is refused as "unsupported: VMX unconfirmed <op>", the first such op in
address order. It reads image.bin and image.json (load_address) from --image, default the catalog's folder.
Nearest existing: ppcscan.py scan - it derives "refuse" while scanning the executable, which needs the XEX; this
works from the counts a finished catalog already holds.
"""
import json
import pathlib
import sys

import re
import struct

import ppcscan

HERE = pathlib.Path(__file__).resolve().parent
CONFIRMED_JAVA = HERE.parent / "ghidra_scripts" / "TcVmxConfirmed.java"
TAILS = ("unsupported: tail call out of the body",)


def confirmed_ops(path=CONFIRMED_JAVA):
    """{op: source} of the rows `row("op", "source");` in TcVmxConfirmed.java, comments aside."""
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", pathlib.Path(path).read_text(encoding="utf-8"), flags=re.S)
    pat = r'\brow\(\s*"([a-z0-9_]+)"\s*,\s*"([^"]+)"\s*\)'
    return {m.group(1): m.group(2) for m in re.finditer(pat, text)}


def vector_ops(word, addr=0):
    """The vector op a word is, or None (the oracle gates every op decode.py names as a vector op, plus the whole of
    opcodes 4, 5 and 6, which ppcscan counts as vector words)."""
    sys.path.insert(0, str(HERE.parent / "translator"))
    from xenontranslate.decode import decode, VECTOR_OPS
    prim = word >> 26
    if prim not in (4, 5, 6, 31):
        return None
    op = decode(word, addr).op
    return op if op in VECTOR_OPS or prim in (4, 5, 6) else None


def function_ops(f, image, base):
    """The vector ops of one catalog function, in address order of first use."""
    a = int(f["addr"], 16)
    seen = {}
    for p in range(a, a + f["size"], 4):
        w = struct.unpack_from(">I", image, p - base)[0]
        op = vector_ops(w, p)
        if op:
            seen.setdefault(op, p)
    return list(seen)


def rerefuse(cat, vabi=None, gaps=None, tails=None, ops=None, confirmed=None):
    """Set each function's refuse from its counts (and vabi, {addr: reasons}; gaps, the addresses of functions with a body
    word Ghidra holds as data; ops, {addr: [vector ops in address order]} with confirmed, the ops the gate admits: a
    function with an op not in it is refused "unsupported: VMX unconfirmed <op>", after the class refusals and before
    the vector ABI ones); return {(old, new): n} for the changed ones."""
    changed = {}
    for f in cat["functions"]:
        if f.get("kind") != "function":
            continue
        counts = f.get("counts") or {}
        new = next((why for c, why in ppcscan.REFUSE if counts.get(c)), None)
        if new is None and ops and confirmed is not None:
            bad = next((o for o in ops.get(f["addr"], ()) if o not in confirmed), None)
            new = "unsupported: VMX unconfirmed " + bad if bad else None
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
    imgdir = pathlib.Path(sys.argv[sys.argv.index("--image") + 1]) if "--image" in sys.argv else path.parent
    ij = json.loads((imgdir / "image.json").read_text(encoding="utf-8"))
    image = (imgdir / "image.bin").read_bytes()
    base = int(str(ij["load_address"]), 0)
    ops = {f["addr"]: function_ops(f, image, base) for f in cat["functions"]
           if f.get("kind") == "function" and (f.get("counts") or {}).get("vmx")}
    changed = rerefuse(cat, vabi, gaps, tails, ops, confirmed_ops())
    for (old, new), n in sorted(changed.items(), key=lambda kv: -kv[1]):
        print(f"{n:6d}  {old} -> {new}")
    out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else path
    if "--dry" not in sys.argv and (changed or out != path):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cat, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

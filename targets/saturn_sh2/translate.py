"""DP's translator contract (docs/TRANSLATOR_STAGE.md) for sh2-translate, the Saturn SH-2 translator.

core/translate_stage runs this file with target.json "translator" "command": the translator itself is in
targets/saturn_sh2/translator/ (unless config.local.json "translators" names another folder); this file only maps what DP has
for one function onto its API: the binary, the harness's meta.json (signature, parameter storage, callees, body
blocks), target.json (memory and ABI facts) and state/function_bodies.txt (every function's entry, so a jump to
another entry is a call).

usage: python translate.py --repo DIR --binary FILE --addr HEX --meta FILE --target-json FILE --bodies FILE
           --out FILE
Exit 0: --out holds the C. Exit 3: no translation (the reason on stderr). Anything else: the translator crashed.
"""
import argparse
import json
import pathlib
import sys

NO_TRANSLATION = 3


def entries_of(bodies):
    p = pathlib.Path(bodies) if bodies else None
    if not p or not p.is_file():
        return set()
    return {int(ln.split()[0], 16) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


def body_ranges(bodies):
    """{entry: [(start, end past the last byte)]} from state/function_bodies.txt."""
    p = pathlib.Path(bodies) if bodies else None
    out = {}
    for ln in p.read_text(encoding="utf-8").splitlines() if p and p.is_file() else ():
        t = ln.split()
        if len(t) == 3:
            out.setdefault(int(t[0], 16), []).append((int(t[1], 16), int(t[2], 16)))
    return out


def body_of(meta, addr, ranges):
    """The function's body, inclusive: the harness's blocks, and the rest of any range they start in. Ghidra's block
    list can stop short of code the function runs through: a branch into a delay slot (Robo Pit 0601dfe8), and the
    oracle runs that as the function's own code. A range with no block is a far chunk the oracle enters as a call
    (Bomberman 0600e960), bytes before a range's first block are not reached from it (Bomberman 060265b4), and the fill
    stops at a callee the harness names inside the range (Dragon Force 2 06060f9c branches to its 06060fa2)."""
    body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks") or []]
    callees = [int(c["addr"], 16) for c in meta.get("callees") or []]
    for lo, hi in ranges.get(addr, ()):
        first = [b for b, e in body if lo <= b < hi]
        fill = (min(first), min([c for c in callees if min(first) < c < hi], default=hi) - 1) if first else None
        if fill and not any(b <= fill[0] and fill[1] <= e for b, e in body):  # a block that holds it already: no copy
            body.append(fill)
    return body


def main():
    ap = argparse.ArgumentParser()
    for k in ("--repo", "--binary", "--addr", "--meta", "--target-json", "--out"):
        ap.add_argument(k, required=True)
    ap.add_argument("--bodies", default="")
    a = ap.parse_args()
    sys.path.insert(0, a.repo)
    from sh2translate.translate import Image, Signature, TargetFacts, TranslateError, translate_function
    meta = json.loads(pathlib.Path(a.meta).read_text(encoding="utf-8"))
    facts = TargetFacts.from_target_json(json.loads(pathlib.Path(a.target_json).read_text(encoding="utf-8")))
    img = Image(pathlib.Path(a.binary).read_bytes(), facts.image_base)
    body = body_of(meta, int(a.addr, 16), body_ranges(a.bodies))
    try:
        src = translate_function(img, int(a.addr, 16), Signature.from_meta(meta), facts, body or None,
                                 entries=entries_of(a.bodies))
    except TranslateError as e:
        print(f"sh2-translate: {e}", file=sys.stderr)
        return NO_TRANSLATION
    pathlib.Path(a.out).write_text(src, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

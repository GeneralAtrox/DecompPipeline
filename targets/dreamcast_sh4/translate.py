"""DP's translator contract (docs/TRANSLATOR_STAGE.md) for sh4-translate, the Dreamcast SH-4 translator.

core/translate_stage runs this file with target.json "translator" "command": the translator itself is in
targets/dreamcast_sh4/translator/ (unless config.local.json "translators" names another folder); this file only maps
what DP has for one function onto its API, as its tools/dp_judge.py does: the binary, the harness's meta.json as the
judge reads it (core/ret_reg.judged: fr0_results.txt), target.json, and from the workspace's state the catalog's
ranges (function_bodies.txt: every entry, so a jump to another entry is a call) and project.json's wait loops.

usage: python translate.py --repo DIR --tool DIR --binary FILE --addr HEX --meta FILE --target-json FILE --bodies FILE
           --out FILE
Exit 0: --out holds the C. Exit 3: no translation (the reason on stderr). Anything else: the translator crashed.
"""
import argparse
import json
import pathlib
import sys

NO_TRANSLATION = 3


def bodies_of(bodies):
    """(entries, {entry: [(lo, hi)]}) of state/function_bodies.txt ("entry lo hi" per range)."""
    p = pathlib.Path(bodies) if bodies else None
    entries, ranges = set(), {}
    if p and p.is_file():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if len(ln.split()) == 3:
                e, lo, hi = (int(x, 16) for x in ln.split())
                entries.add(e)
                ranges.setdefault(e, []).append((lo, hi))
    return entries, ranges


def main():
    ap = argparse.ArgumentParser()
    for k in ("--repo", "--tool", "--binary", "--addr", "--meta", "--target-json", "--out"):
        ap.add_argument(k, required=True)
    ap.add_argument("--bodies", default="")
    a = ap.parse_args()
    sys.path[:0] = [a.repo, a.tool]
    from core import ret_reg
    from sh4translate.translate import Image, Signature, TargetFacts, TranslateError, spin_ranges, translate_function
    state = pathlib.Path(a.bodies).parent if a.bodies else None
    proj_f = state.parent / "project.json" if state else None
    project = json.loads(proj_f.read_text(encoding="utf-8")) if proj_f and proj_f.is_file() else {}
    addr = int(a.addr, 16)
    meta = json.loads(pathlib.Path(a.meta).read_text(encoding="utf-8"))
    meta = ret_reg.judged(meta, ret_reg.listed(state) if state else set(), a.addr)
    facts = TargetFacts.from_target_json(json.loads(pathlib.Path(a.target_json).read_text(encoding="utf-8")))
    img = Image(pathlib.Path(a.binary).read_bytes(), facts.image_base, facts.little)
    entries, ranges = bodies_of(a.bodies)
    body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks") or []]
    try:
        src = translate_function(img, addr, Signature.from_meta(meta), facts, body or None, entries=entries,
                                 ranges=ranges.get(addr), spin=spin_ranges(project).get(addr))
    except TranslateError as e:
        print(f"sh4-translate: {e}", file=sys.stderr)
        return NO_TRANSLATION
    pathlib.Path(a.out).write_text(src, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

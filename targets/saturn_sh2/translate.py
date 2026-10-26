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
    body = [(int(lo, 16), int(hi, 16)) for lo, hi in meta.get("blocks") or []]
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

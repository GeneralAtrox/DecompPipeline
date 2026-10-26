"""Decode and vector coverage of an executable, without translating it.

- decode: of the words in the code ranges and in the catalog's function bodies, how many decode to an instruction
  (the rest are 'illegal': data or padding inside code, or words no Xenon form matches);
- vector: every AltiVec / VMX128 instruction in the function bodies, by op, and whether vmx.lift turns it into C.

    python tools/coverage.py [--ws DIR] [--out FILE.json]
"""
import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from xenontranslate import vmx  # noqa: E402
from xenontranslate.decode import VECTOR_OPS, decode  # noqa: E402
from xenontranslate.translate import TranslateError  # noqa: E402


class Probe:
    def __init__(self):
        self.notes, self.vhelpers = [], set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    ws = pathlib.Path(a.ws)
    img = (ws / "image.bin").read_bytes()
    base = 0x82000000

    def word(ad):
        return int.from_bytes(img[ad - base:ad - base + 4], "big")
    ranges = [tuple(int(x, 16) for x in ln.split()) for ln in (ws / "code_ranges.txt").read_text().splitlines() if ln]
    code_words = sum((hi - lo) // 4 for lo, hi in ranges)
    code_illegal = sum(1 for lo, hi in ranges for ad in range(lo, hi, 4) if decode(word(ad), ad).op == "illegal")
    cat = json.loads((ws / "catalog_inputs.json").read_text())
    fns = [f for f in cat["functions"] if f["kind"] == "function"]
    body_words = body_illegal = 0
    ill_fns = 0
    vins, vfns, vok, vwhy = collections.Counter(), collections.Counter(), collections.Counter(), {}
    vector_fns, vector_fns_ok = 0, 0
    for f in fns:
        lo = int(f["addr"], 16)
        seen, bad, ill = set(), False, False
        has_v = False
        for ad in range(lo, lo + f["size"], 4):
            i = decode(word(ad), ad)
            body_words += 1
            if i.op == "illegal":
                body_illegal += 1
                ill = True
                continue
            if i.op in VECTOR_OPS or i.op in ("mfvscr", "mtvscr"):
                has_v = True
                vins[i.op] += 1
                if i.op not in seen:
                    vfns[i.op] += 1
                    seen.add(i.op)
                try:
                    vmx.lift(Probe(), i)
                    vok[i.op] += 1
                except TranslateError as e:
                    bad = True
                    vwhy.setdefault(i.op, str(e))
        ill_fns += ill
        if has_v:
            vector_fns += 1
            vector_fns_ok += not bad
    tot, ok = sum(vins.values()), sum(vok.values())
    res = {
        "decode": {"code_words": code_words, "code_illegal": code_illegal, "body_words": body_words,
                   "body_illegal": body_illegal, "functions": len(fns), "functions_with_illegal": ill_fns},
        "vector": {"ops_seen": len(vins), "ops_lifted": sum(1 for o in vins if vok[o] == vins[o]),
                   "instructions": tot, "instructions_lifted": ok, "functions": vector_fns,
                   "functions_all_lifted": vector_fns_ok,
                   "unsupported": {o: {"insns": vins[o], "functions": vfns[o], "why": vwhy[o]}
                                   for o in sorted(vwhy, key=lambda o: -vins[o])},
                   "by_op": {o: [vins[o], vfns[o]] for o, _ in vins.most_common()}},
    }
    d, v = res["decode"], res["vector"]
    print(f"code words {code_words}: {code_words - code_illegal} decode ({100 - 100 * code_illegal / code_words:.3f}%)")
    print(f"function bodies: {body_words} words, {body_illegal} illegal in {ill_fns} of {len(fns)} functions")
    print(f"vector: {v['ops_lifted']} of {v['ops_seen']} ops lifted; {ok} of {tot} instructions "
          f"({100 * ok / max(tot, 1):.2f}%); functions {vector_fns_ok} of {vector_fns} fully lifted")
    for o, e in v["unsupported"].items():
        print(f"  unsupported {o}: {e['insns']} instructions in {e['functions']} functions")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

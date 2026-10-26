"""Regression check: translate a fixed list of functions again, every signature generic (no harness meta, so the C
depends only on the translator and the executable), and show that every byte of C is the same as the recorded
baseline (regress/<name>.json, which tests/test_own_translators.py checks too). Run after any translator change;
--update records a new baseline (only after the differences it prints are understood).

usage: python tools/regress.py --ws <workspace> [--set <translator_set.json>] [--update] [--name toohuman_set]
  --set: the functions, {"functions": [{"addr": ...}]} (default: the workspace's translator_set.json); without
         --update the baseline's own list is used
"""
import argparse
import hashlib
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--set")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--name", default="toohuman_set")
    args = ap.parse_args()
    from xenontranslate import returns, workspace
    from xenontranslate.translate import Signature, TranslateError, translate_function
    ws = pathlib.Path(args.ws)
    base_p = HERE / "regress" / f"{args.name}.json"
    base = json.loads(base_p.read_text(encoding="utf-8")) if base_p.exists() else {}
    if args.update or not base:
        sp = pathlib.Path(args.set) if args.set else ws / "translator_set.json"
        addrs = sorted(f["addr"].lower() for f in json.loads(sp.read_text(encoding="utf-8"))["functions"])
    else:
        addrs = sorted(base)
    w = workspace.Workspace(ws)
    w.meta = lambda addr: None
    with tempfile.TemporaryDirectory() as cache:  # built fresh, as the test does
        kinds = returns.analyse(w, cache)
    rows = {}
    for a in addrs:
        ad = int(a, 16)
        try:
            src, _ = translate_function(w.dol, ad, Signature.generic_for(ad), w.facts, w.bodies[ad], returns=kinds)
            rows[a] = {"c_sha256": hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]}
        except TranslateError as e:
            rows[a] = {"c_sha256": None, "why": str(e)}
    if args.update or not base:
        base_p.parent.mkdir(exist_ok=True)
        base_p.write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"baseline recorded: {base_p} ({len(rows)} functions)")
        return
    diff = [f"  {a}: baseline {base.get(a)}  now {rows.get(a)}" for a in sorted(set(base) | set(rows))
            if base.get(a) != rows.get(a)]
    print(f"{len(rows)} functions: {len(rows) - len(diff)} same C, {len(diff)} differ")
    print("\n".join(diff) if diff else "identical to the baseline")
    sys.exit(1 if diff else 0)


if __name__ == "__main__":
    main()

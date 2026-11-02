"""The functions the kit's run has reached, each with its raw C verdict: OUT/reached.tsv, kept over every bring-up round (a function stays once any run has called it), so the
judging can go to what the front end really uses first.

Nearest existing: core/coverage.py - it reports what a judge's vectors covered inside one function; nothing records what a real run called.

usage: py -3.12 reached.py --ws WS --out OUT     (gen_xbox.py's bring-up calls update after every run)
Columns: address, verdict (PASS, FAIL, NOVECTORS, unjudged, or not catalog when the address has no catalog entry and so cannot be judged by itself), whether a harness exists,
the time it was first reached.
"""
import argparse
import collections
import json
import pathlib
import time


def verdict_of(row):
    return (row.get("verdict") if isinstance(row, dict) else row) or None


def update(out, ws, catalog):
    """Merge OUT/run.log.touched into OUT/reached.tsv with today's verdicts; returns {column value: count}. catalog: the addresses (ints) that are catalog functions."""
    out, ws = pathlib.Path(out), pathlib.Path(ws)
    path = out / "reached.tsv"
    first = {}
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines()[1:]:
            c = ln.split("\t")
            first[c[0]] = c[3]
    touched = out / "run.log.touched"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if touched.exists():
        for a in touched.read_text(encoding="utf-8").split():
            first.setdefault(a, now)
    try:
        verdicts = json.loads((ws / "state" / "translator_verdicts.json").read_text(encoding="utf-8"))["functions"]
    except (OSError, ValueError, KeyError):
        verdicts = {}
    rows, count = ["address\tverdict\tharness\tfirst reached"], collections.Counter()
    for a in sorted(first):
        v = verdict_of(verdicts.get(a))
        if not v:
            v = "unjudged" if int(a, 16) in catalog else "not catalog"
        count[v] += 1
        rows.append(f"{a}\t{v}\t{'yes' if (ws / 'harness' / a / 'meta.json').exists() else 'no'}\t{first[a]}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)
    return dict(count)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cat = json.loads((pathlib.Path(a.ws) / "catalog_inputs.json").read_text(encoding="utf-8"))
    print(update(a.out, a.ws, {int(f["addr"], 16) for f in cat["functions"] if f["kind"] == "function"}))


if __name__ == "__main__":
    main()

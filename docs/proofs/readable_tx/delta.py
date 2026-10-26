"""Merge judge runs onto a final lift-only run: a judged row counts only when its machine file is the final text.
usage: delta.py LIFT_OUT NAME MERGED.json TODO.txt JUDGED_OUT...   (each *_OUT is a readable_tx.py --out folder)
Writes MERGED.json (one row per function) and TODO.txt (comma list: gap-free final files with no judged row)."""
import json
import pathlib
import sys

lift, name, merged, todo = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]


def text(out, addr):
    p = out / "readable" / name / f"{addr}.c"
    return p.read_text(encoding="utf-8") if p.exists() else None


judged = []  # newest last
for d in map(pathlib.Path, sys.argv[5:]):
    res = d / "results" / f"{name}.json"
    if res.exists():
        judged.append((d, {r["addr"]: r for r in json.load(open(res, encoding="utf-8"))}))
rows, left = [], []
for r in json.load(open(lift / "results" / f"{name}_lift.json", encoding="utf-8")):
    if r["stage"] == "lifted" and not r.get("gaps"):
        final = text(lift, r["addr"])
        for d, got in reversed(judged):
            j = got.get(r["addr"])
            if j and j["stage"] not in ("lift", "lifted", "gaps") and text(d, r["addr"]) == final:
                r = j
                break
        else:
            left.append(r["addr"])
    rows.append(r)
json.dump(rows, open(merged, "w", encoding="utf-8"), indent=1)
open(todo, "w").write(",".join(left))
print(len(rows), "functions;", len(left), "to judge")

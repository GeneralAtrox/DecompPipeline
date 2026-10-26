"""Pick the Toy Commander sample: 34 matched (evenly spaced by address), 6 failed attempts that reached the compare
stage (their work.c still on disk), and 20 readable/src files for the readable (x86) judge. Writes sample_tc.json.
    python pick_tc.py WORKSPACE    (Toy Commander's decomp workspace)"""
import json, pathlib, sys
WS = pathlib.Path(sys.argv[1])
L = json.loads((WS / "state/ledger.json").read_text(encoding="utf-8"))["functions"]

def spaced(xs, n):
    xs = sorted(xs)
    return [xs[int(i * len(xs) / n)] for i in range(n)] if len(xs) > n else xs

def local(p):  # ledger paths name the workspace's old home
    p = str(p).replace("\\", "/")
    i = p.find("/runs/")
    return WS / p[i + 1:] if i >= 0 else pathlib.Path(p)

matched = [a for a, v in L.items() if v.get("status") == "matched" and (WS / "matched" / f"{a}.c").exists()]
items = [{"addr": a, "kind": "matched", "src": str(WS / "matched" / f"{a}.c")} for a in spaced(matched, 34)]
fails = []
for a, v in L.items():
    for t in v.get("attempts") or []:
        if t.get("verdict") == "FAIL" and t.get("stage") == "compare" and t.get("work_file"):
            f = local(t["work_file"])
            if f.exists() and (WS / "harness" / a / "meta.json").exists():
                fails.append((a, t["n"], str(f), v.get("status")))
cur = [f for f in fails if f[3] == "failed"]
picked = cur[-1:] + spaced([f for f in fails if f not in cur], 6 - len(cur[-1:]))
taken = {i["addr"] for i in items}
for a, n, f, st in picked:
    if a not in taken:
        items.append({"addr": a, "kind": f"fail-attempt-{n:02d} ({st})", "src": f})
        taken.add(a)
rd = [p.stem for p in (WS / "readable/src").glob("*.c") if (WS / "matched" / p.name).exists()]
readable = [{"addr": a, "kind": "readable", "src": str(WS / "readable/src" / f"{a}.c")} for a in spaced(rd, 20)]
print(len(fails), "compare-stage failed attempts on disk;", len(items), "raw,", len(readable), "readable")
json.dump({"raw": items, "readable": readable}, open("sample_tc.json", "w"), indent=1)

"""hdiff.py WS_A WS_B LIST OUT: per function, which harness files differ (meta.json without oracle_stamp)."""
import hashlib, json, pathlib, sys
a, b, lst, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4]
def h(p):
    if not p.exists(): return "-"
    d = p.read_bytes()
    if p.name == "meta.json":
        m = json.loads(d); m.pop("oracle_stamp", None); d = json.dumps(m, sort_keys=True).encode()
    return hashlib.sha256(d).hexdigest()
rows = []
for f in open(lst).read().split():
    f = f.lstrip("@")
    diff = [n for n in ("vectors.txt", "expected.txt", "meta.json") if h((a / f / n if (a / f).exists() else a / "harness" / f / n)) != h(b / "harness" / f / n)]
    rows.append(f"{f}\t{','.join(diff) or '-'}")
open(out, "w").write("\n".join(rows) + "\n")
print(sum(1 for r in rows if not r.endswith("\t-")), "of", len(rows), "differ")

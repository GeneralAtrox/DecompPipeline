"""vcmp.py BASE.json [BASE2.json] -- BRANCH.json [BRANCH2.json]: per-function verdict changes, same-C or not."""
import collections
import json
import sys

i = sys.argv.index("--")
def load(files):
    out = {}
    for f in files:
        for r in json.load(open(f)):
            out[r["addr"]] = r
    return out
a, b = load(sys.argv[1:i]), load(sys.argv[i + 1:])
def v(r):
    return "-" if r is None else f"{r['verdict']}" + (f" {r['failing']}/{r['total']}" if r.get("total") else "")
cnt = collections.Counter()
for addr in sorted(set(a) | set(b)):
    x, y = a.get(addr), b.get(addr)
    same_c = x is not None and y is not None and x.get("c_sha256") == y.get("c_sha256")
    cnt[(x and x["verdict"], y and y["verdict"], same_c)] += 1
    if (x and x["verdict"]) != (y and y["verdict"]) or (x and y and (x.get("failing"), x.get("total")) != (y.get("failing"), y.get("total"))):
        note = (y or {}).get("notes") or (y or {}).get("diff") or (y or {}).get("run_error") or ""
        print(f"{addr}  {v(x):>14} -> {v(y):<14} {'same C' if same_c else 'C differs'}  {str(note)[:160]}")
print("--- (base, branch, same C): count")
for k, n in sorted(cnt.items(), key=lambda kv: -kv[1]):
    print(k, n)

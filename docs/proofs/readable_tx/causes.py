"""Why functions are left for agents. usage: causes.py MERGED.json"""
import collections, json, re, sys
rows = json.load(open(sys.argv[1]))
st = collections.Counter(r["stage"] for r in rows); print(dict(st), len(rows))
def norm(w):
    if w.startswith("runtime state"): return w[:90]
    w = re.sub(r"`[^`]*`|'[^']*'", "#", w)
    w = re.sub(r"\b(?:0x)?[0-9a-f]{6,8}\b|\b\d+\b|0x[0-9a-f]+", "#", w)
    return w[:70]
for stage in ("lift", "lifted", "judge"):
    c = collections.Counter()
    for r in rows:
        if r["stage"] != stage: continue
        if stage in ("gaps", "lifted"):
            ks = set()
            for g in r["gaps"]:
                g = str(g)
                ks.add("fn_8c… callee name" if "fn_" in g else "param_N name" if "param_" in g else "register name" if re.search(r"\b(r\d+|fr\d+)\b", g) else norm(g)[:40])
            c[" + ".join(sorted(ks))] += 1
        else:
            c[norm(r.get("why", ""))] += 1
    print("--", stage)
    for k, n in c.most_common(14): print(f"{n:5} {k}")

"""Compare the old/new junk-compare A/B runs (l/jrab_{old,new}_LIST.json, tools/mutation_check.py --report) with the audit's own
compare (mutation_audit.compare): lost kills, gained, flips per list. Also: functions whose old baseline failed and now pass.
Usage: jr_cmp.py LIST... (default jrsq jrs0 jrs1 jrs2 jrs3 jr4; pl_KEY compares jrpl_{old,new}_KEY.json, the planted wrong reads)
Nearest existing: tools/mutation_audit.py compare/report - this only turns two --report jsons into its base/cur shape."""
import os
import json, pathlib, sys
K = os.environ["DPWORK"] + r"\land\tcdone\kitjr"
sys.path[:0] = [K, K + r"\tools"]
import mutation_audit as ma
L = pathlib.Path(os.environ["DPWORK"] + r"\land\tcdone\l")


def shape(p):
    recs = json.loads(p.read_text(encoding="utf-8"))["functions"]
    return {a: ({"error": r["error"][:160]} if r.get("error") else {"sha": r["src_sha256"], "outcomes": r.get("outcomes", "")})
            for a, r in recs.items()}


lists = sys.argv[1:] or ["jrsq", "jrs0", "jrs1", "jrs2", "jrs3", "jr4"]
base, cur, old_err, new_err = {}, {}, [], []
for lst in lists:
    pre = "jrpl" if lst.startswith("pl_") else "jrab"
    key = lst[3:] if pre == "jrpl" else lst
    o, n = L / f"{pre}_old_{key}.json", L / f"{pre}_new_{key}.json"
    if not (o.exists() and n.exists()):
        print(f"{lst}: missing {'old' if not o.exists() else ''} {'new' if not n.exists() else ''}")
        continue
    so, sn = shape(o), shape(n)
    base[lst], cur[lst] = so, sn
    old_err += [(lst, a, r["error"]) for a, r in so.items() if r.get("error")]
    new_err += [(lst, a, r["error"]) for a, r in sn.items() if r.get("error")]
res = ma.compare(base, cur)
print(ma.report(res))
print("old baseline fails:", [(l, a) for l, a, e in old_err])
print("new baseline fails:", [(l, a, e[:80]) for l, a, e in new_err])
print("lost kills:", res["lost"])
print("total mutants compared", sum(k["mutants"] for k in res["keys"].values()),
      "killed old", sum(k["killed_base"] for k in res["keys"].values()),
      "killed new", sum(k["killed_run"] for k in res["keys"].values()))

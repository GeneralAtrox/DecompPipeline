"""Pick the Dragon Force 2 (ws-btl) and Skies (decomp-gekko) samples: sample_df2.json, sample_skies.json.
    python pick_df2_skies.py DF2_RESEARCH SKIES_WORKSPACE
(Dragon Force 2's research folder, holding judge-head/ and ws-btl/; Skies' decomp-gekko workspace)"""
import json, pathlib, sys

def spaced(xs, n):
    xs = sorted(xs)
    return [xs[int(i * len(xs) / n)] for i in range(n)] if len(xs) > n else xs

# DF2: sh2-translate's C from the latest judging run; 8 that the judge failed, 32 that passed
J = pathlib.Path(sys.argv[1]) / "judge-head"
WS = pathlib.Path(sys.argv[1]) / "ws-btl"
res = {r["addr"]: r for r in json.loads((J / "results_translated_ws-btl_all.json").read_text(encoding="utf-8"))}
ok = lambda a: (J / "translated/ws-btl" / f"{a}.c").exists() and (WS / "harness" / a / "meta.json").exists()
fails = [a for a, r in res.items() if r["verdict"] == "FAIL" and ok(a)]
passes = [a for a, r in res.items() if r["verdict"] == "PASS" and ok(a)]
pick = [(a, "translated, judged FAIL") for a in spaced(fails, 8)] + [(a, "translated, judged PASS") for a in spaced(passes, 32)]
json.dump({"raw": [{"addr": a, "kind": k, "src": str(J / "translated/ws-btl" / f"{a}.c")} for a, k in sorted(pick)]},
          open("sample_df2.json", "w"), indent=1)
print("df2", len(fails), "FAIL,", len(passes), "PASS available")

# Skies: the failed and stopped functions' last attempts, one earlier failed attempt, matched evenly spaced
WS = pathlib.Path(sys.argv[2])
L = json.loads((WS / "state/ledger.json").read_text(encoding="utf-8"))["functions"]
def local(p):
    p = str(p).replace("\\", "/"); i = p.find("/runs/")
    return WS / p[i + 1:] if i >= 0 else pathlib.Path(p)
items = []
for a, v in sorted(L.items()):
    if v.get("status") in ("failed", "stopped"):
        att = [t for t in v.get("attempts") or [] if t.get("work_file") and local(t["work_file"]).exists()]
        if att and (WS / "harness" / a / "meta.json").exists():
            t = att[-1]
            items.append({"addr": a, "kind": f"{v['status']}, attempt-{t['n']:02d} ({t.get('verdict')}, {t.get('stage')})",
                          "src": str(local(t["work_file"]))})
taken = {i["addr"] for i in items}
earlier = [(a, t) for a, v in sorted(L.items()) if v.get("status") == "matched" and a not in taken
           for t in v.get("attempts") or [] if t.get("verdict") == "FAIL" and t.get("stage") == "compare"
           and t.get("work_file") and local(t["work_file"]).exists()]
for a, t in spaced([e for e in earlier], 1) if earlier else []:
    items.append({"addr": a, "kind": f"matched later; failed attempt-{t['n']:02d}", "src": str(local(t["work_file"]))})
    taken.add(a)
matched = [a for a, v in L.items() if v.get("status") == "matched" and a not in taken and (WS / "matched" / f"{a}.c").exists()]
items += [{"addr": a, "kind": "matched", "src": str(WS / "matched" / f"{a}.c")} for a in spaced(matched, 40 - len(items))]
json.dump({"raw": sorted(items, key=lambda i: i["addr"])}, open("sample_skies.json", "w"), indent=1)
print("skies", len(items), [(i["addr"], i["kind"]) for i in items if i["kind"] != "matched"])

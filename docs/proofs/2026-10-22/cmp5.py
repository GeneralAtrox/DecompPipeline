import json, sys, collections
g = sys.argv[1]
runs = {n: json.load(open(f"verify/{g}-{n}.json")) for n in ("base", "judge_cold", "judge_warm")}
fns = {n: r["fns"] for n, r in runs.items()}
assert fns["base"].keys() == fns["judge_cold"].keys() == fns["judge_warm"].keys()
diff = [a for a in fns["base"] if not (fns["base"][a] == fns["judge_cold"][a] == fns["judge_warm"][a])]
print(g, "fns", len(fns["base"]), "differ", len(diff), diff[:10])
for n, r in runs.items():
    print(n, r["wall_s"], r["bad"], collections.Counter(x.get("verdict") for x in r["fns"].values()))

"""Too Human sample. `screen`: 80 of the translated judge set, evenly spaced (sample_th_screen.json). `final`: from the
screening verdicts (screen_th.json), up to 8 FAILs evenly spaced plus PASSes evenly spaced, 40 in all
(sample_th.json). The screening copy is thrown away; the baseline judges a fresh copy."""
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
def spaced(xs, n):
    xs = sorted(xs)
    return [xs[int(i * len(xs) / n)] for i in range(n)] if len(xs) > n else xs
item = lambda a, k: {"addr": a, "kind": k, "src": str(here / "th_c" / f"{a}.c")}
if sys.argv[1] == "screen":
    ok = [a for a, v in json.load(open(here / "th_translate.json")).items() if v == "ok"]
    json.dump({"raw": [item(a, "translated") for a in spaced(ok, 80)]}, open(here / "sample_th_screen.json", "w"), indent=1)
else:
    r = json.load(open(here / "screen_th.json"))["raw"]
    fails = [a for a, v in r.items() if v["verdict"] != "PASS"]
    f = spaced(fails, 8)
    p = spaced([a for a, v in r.items() if v["verdict"] == "PASS"], 40 - len(f))
    items = [item(a, "translated, screened FAIL") for a in f] + [item(a, "translated, screened PASS") for a in p]
    json.dump({"raw": sorted(items, key=lambda i: i["addr"])}, open(here / "sample_th.json", "w"), indent=1)
    print(len(fails), "FAIL in screening;", len(f), "taken")

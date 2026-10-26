"""Compare A/B sides of gc_rd_ab.py: verdicts and actual.txt hashes per address, medians and sums.
usage: rdab_cmp.py A.json B.json [C.json ...]"""
import json
import statistics
import sys

sides = [json.load(open(p, encoding="utf-8")) for p in sys.argv[1:]]
base = sides[0]["rows"]
for name, s in zip(sys.argv[1:], sides):
    rows = s["rows"]
    same_v = sum(rows[a]["result"].get("verdict") == base[a]["result"].get("verdict") for a in base if a in rows)
    same_h = sum(rows[a]["actual_sha256"] == base[a]["actual_sha256"] for a in base if a in rows)
    diff = [a for a in base if a in rows and (rows[a]["actual_sha256"] != base[a]["actual_sha256"]
                                               or rows[a]["result"].get("verdict") != base[a]["result"].get("verdict"))]
    t = [r["s"] for r in rows.values()]
    verdicts = {}
    for r in rows.values():
        verdicts[r["result"].get("verdict")] = verdicts.get(r["result"].get("verdict"), 0) + 1
    print(f"{name}: n={len(rows)} wall={s['wall_s']} sum={sum(t):.1f} median={statistics.median(t):.2f} "
          f"verdicts={verdicts} same_verdict={same_v} same_actual={same_h} diff={diff[:10]}")

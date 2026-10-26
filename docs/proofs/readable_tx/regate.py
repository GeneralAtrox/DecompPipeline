"""Re-run the gates, unchanged, on every adopted machine file. usage: regate.py DP WS MERGED.json LIFT_OUT NAME"""
import json, os, pathlib, sys
dp, ws, merged, lift, name = sys.argv[1:6]
os.environ["DECOMP_PROJECT"] = ws
sys.path.insert(0, dp)
from core import draft_gate, draft_run, readable
vals = draft_gate.named_values(readable.INCLUDE)
rows = [r for r in json.load(open(merged)) if r["stage"] == "pass"]
bad = []
for r in rows:
    t = (pathlib.Path(lift) / "readable" / name / f"{r['addr']}.c").read_text(encoding="utf-8")
    if draft_gate.reasons(t, vals, max_lines=None) or draft_run.readable_gaps(t):
        bad.append(r["addr"])
print(f"{name}: {len(rows) - len(bad)}/{len(rows)} adopted files pass the literal check and the readability gate", bad[:5])

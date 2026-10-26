"""The literal check (draft_gate.reasons, unchanged) on the agents' own readable files. usage: agentgate.py DP WS"""
import os, sys, pathlib, json
dp, ws = sys.argv[1:3]
os.environ["DECOMP_PROJECT"] = ws
sys.path.insert(0, dp)
from core import draft_gate, readable
from core.draft import strip_comments
vals = draft_gate.named_values(readable.INCLUDE)
led = json.load(open(pathlib.Path(ws) / "state" / "readable.json", encoding="utf-8"))
n = ok = 0
for a, r in led.items():
    p = readable.SRC / f"{a}.c"
    if r.get("status") != "readable" or r.get("readable_by") == "machine" or not p.exists():
        continue
    n += 1
    ok += not draft_gate.reasons(strip_comments(p.read_text(encoding="utf-8", errors="replace")), vals, max_lines=None)
print(f"{ws}: {ok}/{n} agent readable files pass the literal check ({ok / max(n, 1):.1%})")

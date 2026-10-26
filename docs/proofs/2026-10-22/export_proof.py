"""Proof 4 (export copy reuse): 20 TC functions exported in calls of 5 (port/tools/sdk_ref.py's CHUNK) by one DP tree
into one private workspace. Writes timings to WS/proof_timing.json; outputs land in WS/out/<addr>/.

usage: python export_proof.py DP_TREE PRIVATE_WS
The broker pass is read from the HOLD into this process's environment only (never printed or written).
"""
import json
import os
import pathlib
import sys
import time

tree, ws = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
hold = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker" / "HOLD"
if hold.exists():
    key = (json.loads(hold.read_text(encoding="utf-8") or "{}") or {}).get("pass")
    if key:
        os.environ["DP_BROKER_PASS"] = key
    del key
os.environ["DECOMP_PROJECT"] = str(ws)
sys.path.insert(0, str(tree))
from core import ghidra, paths  # noqa: E402

assert paths.ROOT == ws.resolve(), paths.ROOT
assert str(paths.GHIDRA_SLOTS).startswith(str(ws.resolve())), paths.GHIDRA_SLOTS
cfg = paths.config()
addrs = (ws / "addrs.txt").read_text(encoding="utf-8").split()
out = ws / "out"
times = []
for i in range(0, len(addrs), 5):
    t = time.perf_counter()
    ghidra.export(cfg, addrs[i:i + 5], out)
    times.append(round(time.perf_counter() - t, 2))
    print(f"call {i // 5 + 1}: {times[-1]} s", flush=True)
(ws / "proof_timing.json").write_text(json.dumps({"tree": str(tree), "calls_s": times}), encoding="utf-8")

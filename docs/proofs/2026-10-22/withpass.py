"""Run CMD with the broker HOLD's pass in its environment only (never printed or written). usage: withpass.py CMD..."""
import json
import os
import pathlib
import subprocess
import sys

hold = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker" / "HOLD"
env = dict(os.environ)
if hold.exists():
    key = (json.loads(hold.read_text(encoding="utf-8") or "{}") or {}).get("pass")
    if key:
        env["DP_BROKER_PASS"] = key
sys.exit(subprocess.run(sys.argv[1:], env=env).returncode)

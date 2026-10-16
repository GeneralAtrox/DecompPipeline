# Token-free stand-in for the watcher agent (dry runs): set
#   TCD_WATCHER_COMMAND=["{python}", "{root}/tools/fake_watcher.py"]   ({root} = DecompPipeline/)
# It runs in watcher/ws/ like the real agent, optionally adds a hint (TC_FAKE_HINT), then runs
# `python wtool.py try` and `python wtool.py gate`, printing stream-json events like the CLI.
import json
import os
import subprocess
import sys


def say(text):
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}), flush=True)


def tool(kind):
    out = subprocess.run([sys.executable, "wtool.py", kind], capture_output=True, text=True, timeout=600).stdout
    while out.startswith("STILL RUNNING"):
        out = subprocess.run([sys.executable, "wtool.py", "wait"], capture_output=True, text=True, timeout=600).stdout
    say(f"wtool {kind}: " + out.strip().replace("\n", " | ")[:400])
    return out


sys.stdin.read()
hint = os.environ.get("TC_FAKE_HINT")
if hint:
    name = next(n for n in os.listdir("hints") if n.endswith(".txt"))
    with open(os.path.join("hints", name), "a", encoding="utf-8") as f:
        f.write(hint + "\n")
tool("try")
res = tool("gate")
final = "RESULT: PROMOTED" if res.startswith("GATE PASSED") else "RESULT: GAVE UP fake agent"
print(json.dumps({"type": "result", "subtype": "success", "result": final, "total_cost_usd": 0, "num_turns": 2}),
      flush=True)

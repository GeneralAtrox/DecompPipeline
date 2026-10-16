# The watcher agent's only tool. It asks the monitor server to run a check; all checks run server-side.
#   python wtool.py try    lint + run the oracle on the target with the current TcInputs.java and hints
#   python wtool.py gate   the full strict gate; promotes the change if every check passes
#   python wtool.py wait   keep waiting for the request that is still running
import json
import sys
import time
import urllib.request

LIMIT_S = 540  # stay under the agent's 10-minute Bash limit; "wait" picks up where this left off


def call(cfg, kind):
    req = urllib.request.Request(f"http://127.0.0.1:{cfg['port']}/api/watcher/tool",
                                 data=json.dumps({"token": cfg["token"], "kind": kind}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    kind = sys.argv[1] if len(sys.argv) == 2 else ""
    if kind not in ("try", "gate", "wait"):
        print("usage: python wtool.py try|gate|wait")
        return 2
    with open(".watch", encoding="utf-8") as f:
        cfg = json.load(f)
    t0 = time.time()
    res = call(cfg, kind)
    while not res.get("done") and time.time() - t0 < LIMIT_S:
        res = call(cfg, "wait")
    print(res.get("text", "") if res.get("done") else
          "STILL RUNNING - run exactly: python wtool.py wait\n" + res.get("text", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

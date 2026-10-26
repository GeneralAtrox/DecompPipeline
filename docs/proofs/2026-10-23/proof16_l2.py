"""Row 16 rerun on laptop2: proofs B (Skies, p1ws) and C (Toy Commander, p2ws), serving off (cold) and on (live),
on a private broker queue with the shipped setting (by_ram; the broker counts every Ghidra JVM on the machine,
other sessions' too). Each phase runs the two workspaces at once, so one is the other's "other session" in the queue:
  1: p1 cold + p2 cold   (the cost to others with serving off)
  2: p1 live + p2 cold   (Skies served; Toy Commander waits behind it)
  3: p1 cold + p2 live   (Toy Commander served; Skies waits behind it)
waitwatch.py samples the queue throughout; phases.json records each runner's PID, start and end. Outputs are kept
per phase (out_MODE_phN, timing_MODE_phN.json).
usage: python proof16_l2.py WORK   (WORK holds tree16, the DP checkout to run, ws/p1ws, ws/p2ws and appdata)"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

W = pathlib.Path(sys.argv[1])
TREE = W / "tree16"
P = TREE / "docs" / "proofs"
OUT = W / "r16"
OUT.mkdir(exist_ok=True)
env = dict(os.environ, LOCALAPPDATA=str(W / "appdata"), DP_TREE=str(TREE))
env.pop("DP_BROKER_PASS", None)
ww = subprocess.Popen([sys.executable, str(P / "2026-10-23" / "waitwatch.py"), str(OUT / "ww.jsonl"), "9000"], env=env)
phases = []


def start(ws, mode, n):
    pid = W / "ws" / ws / "runner.pid"
    pid.unlink(missing_ok=True)
    log = open(OUT / f"{ws}_{mode}_ph{n}.log", "w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(P / "2026-10-22" / "ramguard.py"), "2400", "--", sys.executable,
                          str(P / "2026-10-23" / "lease_proof.py"), "run", str(W / "ws" / ws), mode],
                         env=env, stdout=log, stderr=subprocess.STDOUT)
    return {"ws": ws, "mode": mode, "phase": n, "proc": p, "pidfile": pid, "start": time.time(), "log": log}


for n, modes in ((1, ("cold", "cold")), (2, ("live", "cold")), (3, ("cold", "live"))):
    runs = [start("p1ws", modes[0], n), start("p2ws", modes[1], n)]
    for r in runs:
        r["rc"] = r["proc"].wait(timeout=2700)
        r["end"] = time.time()
        r["log"].close()
        r["pid"] = int(r["pidfile"].read_text()) if r["pidfile"].exists() else None
        ws = W / "ws" / r["ws"]
        for src, dst in ((ws / f"out_{r['mode']}", ws / f"out_{r['mode']}_ph{n}"),
                         (ws / f"timing_{r['mode']}.json", ws / f"timing_{r['mode']}_ph{n}.json")):
            if src.exists():
                shutil.rmtree(dst, ignore_errors=True) if dst.is_dir() else dst.unlink(missing_ok=True)
                src.rename(dst)
        phases.append({k: r[k] for k in ("ws", "mode", "phase", "pid", "start", "end", "rc")})
        print(json.dumps(phases[-1]), flush=True)
    (OUT / "phases.json").write_text(json.dumps(phases, indent=1), encoding="utf-8")
(OUT / "ww.jsonl.stop").touch()
ww.wait(timeout=60)
print("done", flush=True)

"""Proof H (row 18): Xbox 360 prepares with TcFixups' mnemonic cache and TcFacts' compiled split (xbox360-speed
1481dae) write the same harnesses and packets as before (6505588). A private copy of the Too Human workspace is made
per tree, without the harness and packet of the functions to prepare; tools/prepare_list.py of the tree in DP_TREE
then prepares them on one Ghidra slot, through the broker, cold.

usage: python x360_prep_proof.py build LIVE_WS WS ADDRS PORT   copy (Ghidra project included), own server port
       python x360_prep_proof.py run WS ADDRS                  prepare; WS/prep.tsv, WS/prep_time.json
       python x360_prep_proof.py compare WS_A WS_B ADDRS       every harness and packet file, meta.json's oracle_stamp
                                                               aside
DP_TREE names the DP tree to run. The broker pass is read from the HOLD into this process's environment only (never
printed, logged or written); the child prepare inherits it.
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

SKIP = {"ghidra_slots", "build", "runs", "matched"}


def addrs_of(path):
    return [a for a in (ln.split("#")[0].strip().rsplit("/", 1)[-1] for ln in open(path, encoding="utf-8")) if a]


def build(live, ws, addrs, port):
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    for f in live.iterdir():
        if f.name in SKIP:
            continue
        if f.is_dir():
            shutil.copytree(f, ws / f.name, ignore=shutil.ignore_patterns("*.log", "*.lock", "*.lock~", "ledger*",
                                                                          "prepare_timing*"))
        else:
            shutil.copy2(f, ws / f.name)
    for a in addrs:
        shutil.rmtree(ws / "harness" / a)
        shutil.rmtree(ws / "packets" / a, ignore_errors=True)
    p = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    p["port"] = port
    (ws / "project.json").write_text(json.dumps(p, indent=2), encoding="utf-8", newline="\n")


def run(ws, addrs_file):
    hold = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker" / "HOLD"
    env = dict(os.environ)
    if hold.exists():
        key = (json.loads(hold.read_text(encoding="utf-8") or "{}") or {}).get("pass")
        if key:
            env["DP_BROKER_PASS"] = key
        del key
    tree = pathlib.Path(os.environ["DP_TREE"])
    t = time.perf_counter()
    rc = subprocess.run([sys.executable, str(tree / "tools" / "prepare_list.py"), "--project", str(ws), "--slots", "1",
                         f"@{addrs_file}", "--out", str(ws / "prep.tsv")], env=env, timeout=3000).returncode
    rec = {"tree": str(tree), "rc": rc, "seconds": round(time.perf_counter() - t, 1)}
    (ws / "prep_time.json").write_text(json.dumps(rec), encoding="utf-8", newline="\n")
    print(json.dumps(rec), flush=True)


def files(ws, addrs):
    out = {}
    for a in addrs:
        for sub in ("harness", "packets"):
            for f in sorted((ws / sub / a).rglob("*")):
                if f.is_file():
                    b = f.read_bytes()
                    if f.name == "meta.json":
                        m = json.loads(b)
                        m.pop("oracle_stamp", None)
                        b = json.dumps(m, sort_keys=True).encode()
                    out[f"{sub}/{a}/{f.relative_to(ws / sub / a).as_posix()}"] = hashlib.sha256(b).hexdigest()
    return out


def compare(a, b, addrs):
    fa, fb = files(a, addrs), files(b, addrs)
    diff = sorted(k for k in fa.keys() | fb.keys() if fa.get(k) != fb.get(k))
    print(json.dumps({"files": len(fa), "files_b": len(fb), "differ": diff}, indent=1))


if __name__ == "__main__":
    cmd, ws = sys.argv[1], pathlib.Path(sys.argv[2]).resolve()
    if cmd == "build":
        build(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]).resolve(), addrs_of(sys.argv[4]), int(sys.argv[5]))
    elif cmd == "run":
        run(ws, sys.argv[3])
    else:
        compare(ws, pathlib.Path(sys.argv[3]).resolve(), addrs_of(sys.argv[4]))

"""Proofs 1 and 2 of the second waste audit (TcServe facts argument 39dae1b, the slot server Lease 162714e): N matched
functions prepared in a private workspace copy, one at a time on one Ghidra slot, either cold (no slot server: the
master project, a fresh Ghidra and a broker token per prepare) or served (read-only copy 1 through its long-lived
Ghidra, which holds one Lease for the run). The harness and packet of each go to WS/out_MODE/.

usage: python lease_proof.py build LIVE_WS PRIVATE_WS N    make the private workspace (copies; nothing live written)
       python lease_proof.py run PRIVATE_WS cold|served|live
                                                         prepare the N; timings in WS/timing_MODE.json. live: the
                                                         DP tree's config as it is (ghidra_serve, ghidra_parallel) and
                                                         slot() as live runs take it (row 16, serving on by default)
DP_TREE names the DP checkout to run. The broker pass is read from the HOLD into this process's environment only
(never printed, logged or written). This process's PID is written to WS/runner.pid for waitwatch.py.
"""
import json
import os
import pathlib
import shutil
import sys
import time


def build(live, ws, n):
    shutil.rmtree(ws, ignore_errors=True)
    (ws / "state").mkdir(parents=True)
    p = json.loads((live / "project.json").read_text(encoding="utf-8"))
    p["binary"] = str((live / p["binary"]).resolve())
    p.pop("ghidra_dir", None)
    (ws / "project.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
    shutil.copytree(live / "oracle", ws / "oracle")
    if (live / "readable" / "names").is_dir():
        shutil.copytree(live / "readable" / "names", ws / "readable" / "names")
    shutil.copytree(live / "ghidra", ws / "ghidra", ignore=shutil.ignore_patterns("*.lock", "*.lock~"))
    for f in (live / "state").iterdir():
        skip = (".before" in f.name or f.suffix in (".log", ".lock") or f.name.startswith(("ghidra_marks",
                "prepare_timing", "ledger", "final_audit", "usage")) or (f.is_file() and f.stat().st_size > 5 << 20))
        if f.is_file() and not skip:
            shutil.copy2(f, ws / "state" / f.name)
    for sub in ("fsca", "ghidra_scripts"):
        if (live / "state" / sub).is_dir():
            shutil.copytree(live / "state" / sub, ws / "state" / sub)
    harness = [d for d in (live / "harness").iterdir() if (d / "meta.json").exists()]
    for d in harness:  # callee records read the callees' signatures from harness/*/meta.json
        (ws / "harness" / d.name).mkdir(parents=True)
        shutil.copy2(d / "meta.json", ws / "harness" / d.name / "meta.json")
    have = {d.name for d in harness}
    matched = sorted(f.stem for f in (live / "matched").glob("*.c") if f.stem in have)
    step = max(1, len(matched) // n)
    (ws / "addrs.txt").write_text("\n".join(matched[i * step] for i in range(min(n, len(matched)))) + "\n",
                                  encoding="utf-8")


def run(ws, mode):
    hold = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker" / "HOLD"
    if hold.exists():
        key = (json.loads(hold.read_text(encoding="utf-8") or "{}") or {}).get("pass")
        if key:
            os.environ["DP_BROKER_PASS"] = key
        del key
    os.environ["DECOMP_PROJECT"] = str(ws)
    (ws / "runner.pid").write_text(str(os.getpid()), encoding="utf-8")
    sys.path.insert(0, str(pathlib.Path(os.environ["DP_TREE"])))
    from core import ghidra, ghidra_server, paths
    assert paths.ROOT == ws.resolve() and str(paths.GHIDRA_PROJECT).startswith(str(ws.resolve()))
    cfg = paths.config()
    served = mode == "served"
    if mode != "live":
        cfg["ghidra_parallel"] = 2 if served else 1  # served: copy 1 only (first=1); cold: the master only
        cfg["ghidra_serve"] = {**(cfg.get("ghidra_serve") or {}), "enabled": served}
    print(f"{mode}: ghidra_serve {cfg.get('ghidra_serve')}, ghidra_parallel {cfg.get('ghidra_parallel')}", flush=True)
    addrs = (ws / "addrs.txt").read_text(encoding="utf-8").split()
    recs = {}
    t0 = time.perf_counter()
    try:
        for addr in addrs:
            t = time.perf_counter()
            with ghidra.slot(cfg, first=1 if served else 0) as i:
                w = time.perf_counter()
                ghidra.prepare(cfg, addr, slot_index=i, timing={})
            recs[addr] = {"slot": i, "wait_s": round(w - t, 2), "prepare_s": round(time.perf_counter() - w, 2)}
            print(f"{addr} slot {i}: wait {recs[addr]['wait_s']} s, prepare {recs[addr]['prepare_s']} s", flush=True)
    finally:
        ghidra_server.stop_all()
    wall = round(time.perf_counter() - t0, 1)
    out = ws / f"out_{mode}"
    shutil.rmtree(out, ignore_errors=True)
    for a in addrs:
        shutil.copytree(paths.HARNESS / a, out / "harness" / a)
        shutil.copytree(paths.PACKETS / a, out / "packets" / a)
    (ws / f"timing_{mode}.json").write_text(json.dumps({"mode": mode, "wall_s": wall, "fns": recs}, indent=1),
                                            encoding="utf-8")
    print(f"{mode}: {len(addrs)} prepares in {wall} s", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), int(sys.argv[4]))
    else:
        run(pathlib.Path(sys.argv[2]).resolve(), sys.argv[3])

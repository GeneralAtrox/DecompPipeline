"""Proof 2 (served slots, 7735605): 50 matched TC functions prepared in a private workspace, 2 at a time over 3 slots
(the master and 2 read-only copies), with the slot servers off (the order before the fix: the master first, a fresh
Ghidra per prepare) or on (the new order: a served copy first). The harness and packet of each go to WS/out/.

usage: python serve_proof.py build LIVE_WS PRIVATE_WS     make the private workspace (copies; nothing live written)
       python serve_proof.py run PRIVATE_WS on|off         prepare the 50; timings in WS/proof_timing.json
The broker pass is read from the HOLD into this process's environment only (never printed or written).
"""
import concurrent.futures
import json
import os
import pathlib
import shutil
import sys
import threading
import time


def build(live, ws):
    shutil.rmtree(ws, ignore_errors=True)
    (ws / "state").mkdir(parents=True)
    p = json.loads((live / "project.json").read_text(encoding="utf-8"))
    p["binary"] = str((live / p["binary"]).resolve())
    p.pop("ghidra_dir", None)
    (ws / "project.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
    shutil.copytree(live / "oracle", ws / "oracle")
    shutil.copytree(live / "readable" / "names", ws / "readable" / "names")
    shutil.copytree(live / "ghidra", ws / "ghidra", ignore=shutil.ignore_patterns("*.lock", "*.lock~"))
    for f in (live / "state").iterdir():
        skip = (".before" in f.name or f.suffix == ".log" or f.name.startswith(("ghidra_marks", "prepare_timing",
                "ledger", "final_audit")) or f.stat().st_size > 5 << 20)
        if f.is_file() and not skip:
            shutil.copy2(f, ws / "state" / f.name)
    shutil.copytree(live / "state" / "fsca", ws / "state" / "fsca")
    harness = [d for d in (live / "harness").iterdir() if (d / "meta.json").exists()]
    for d in harness:  # callee records read the callees' signatures from harness/*/meta.json
        (ws / "harness" / d.name).mkdir(parents=True)
        shutil.copy2(d / "meta.json", ws / "harness" / d.name / "meta.json")
    have = {d.name for d in harness}
    matched = sorted(f.stem for f in (live / "matched").glob("*.c") if f.stem in have)
    step = len(matched) // 50
    (ws / "addrs.txt").write_text("\n".join(matched[i * step] for i in range(50)) + "\n", encoding="utf-8")


def run(ws, mode):
    hold = pathlib.Path(os.environ["LOCALAPPDATA"]) / "DecompPipeline" / "ghidra_broker" / "HOLD"
    if hold.exists():
        key = (json.loads(hold.read_text(encoding="utf-8") or "{}") or {}).get("pass")
        if key:
            os.environ["DP_BROKER_PASS"] = key
        del key
    os.environ["DECOMP_PROJECT"] = str(ws)
    sys.path.insert(0, str(pathlib.Path(os.environ["DP_TREE"])))
    from core import ghidra, ghidra_server, paths
    assert paths.ROOT == ws.resolve() and str(paths.GHIDRA_PROJECT).startswith(str(ws.resolve()))
    cfg = paths.config()
    cfg["ghidra_parallel"] = 3
    cfg["ghidra_serve"] = {**(cfg.get("ghidra_serve") or {}), "enabled": mode == "on"}
    addrs = (ws / "addrs.txt").read_text(encoding="utf-8").split()
    recs, lock = {}, threading.Lock()

    def one(addr):
        t = time.perf_counter()
        with ghidra.slot(cfg) as i:
            w = time.perf_counter()
            rec = {}
            ghidra.prepare(cfg, addr, slot_index=i, timing=rec)
        with lock:
            recs[addr] = {"slot": i, "wait_s": round(w - t, 2), "prepare_s": round(time.perf_counter() - w, 2)}
        print(f"{addr} slot {i}: {recs[addr]['prepare_s']} s", flush=True)

    t0 = time.perf_counter()
    try:
        with concurrent.futures.ThreadPoolExecutor(2) as ex:
            for f in [ex.submit(one, a) for a in addrs]:
                f.result()
    finally:
        ghidra_server.stop_all()
    wall = round(time.perf_counter() - t0, 1)
    out = ws / "out"
    shutil.rmtree(out, ignore_errors=True)
    for a in addrs:
        shutil.copytree(paths.HARNESS / a, out / "harness" / a)
        shutil.copytree(paths.PACKETS / a, out / "packets" / a)
    (ws / "proof_timing.json").write_text(json.dumps({"mode": mode, "wall_s": wall, "fns": recs}, indent=1),
                                          encoding="utf-8")
    print(f"{mode}: {len(addrs)} prepares in {wall} s", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    else:
        run(pathlib.Path(sys.argv[2]).resolve(), sys.argv[3])

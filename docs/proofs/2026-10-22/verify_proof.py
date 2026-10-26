"""Proof 5 (judge branch: F1 runner reuse, F5 raw runs per function, F6 runtime once): `refine.py verify --no-sync`
over every readable file of a private workspace, as refine.py runs it (the current headers' snapshot, then
core/readable_verify.verify), with one DP tree; every function's final judgement goes to OUT.json.

usage: python verify_proof.py build LIVE_WS PRIVATE_WS     private copy (nothing live is written by any run)
       python verify_proof.py run DP_TREE PRIVATE_WS OUT.json WORKERS
A run resets the private ledger (state/readable.json) first; build/ is left as the caller leaves it. build_prune
is off in the copy, so a runner kept by one run is there for the next (what the agent's check leaves the official
judge in a refine cycle).
"""
import json
import os
import pathlib
import shutil
import sys
import time

KEYS = ("verdict", "stage", "failing", "total", "first_diffs", "note", "out_pointers")
SKIP_DIRS = {"ghidra_scripts", "readable_snapshots", "stamp_refresh", "sig_override_backup", "usage_probe", "fnlocks"}


def build(live, ws):
    shutil.rmtree(ws, ignore_errors=True)
    (ws / "state").mkdir(parents=True)
    p = json.loads((live / "project.json").read_text(encoding="utf-8"))
    p["binary"] = str((live / p["binary"]).resolve())
    p.pop("ghidra_dir", None)
    p["build_prune"] = {"enabled": False}
    (ws / "project.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
    for d in ("oracle", "matched", "readable"):
        shutil.copytree(live / d, ws / d)
    for f in (live / "state").iterdir():
        if f.is_dir():
            if f.name not in SKIP_DIRS and not f.name.startswith("."):
                shutil.copytree(f, ws / "state" / f.name)
        elif not (".before" in f.name or f.suffix in (".log", ".lock") or f.name.startswith(("ghidra_marks",
                  "prepare_timing", "events", "final_audit"))):
            shutil.copy2(f, ws / "state" / f.name)
    shutil.copy2(ws / "state" / "readable.json", ws / "readable.json.pristine")
    src = {q.stem for q in (live / "readable" / "src").glob("8c*.c")}
    for d in (live / "harness").iterdir():
        if d.name in src:
            shutil.copytree(d, ws / "harness" / d.name)
        elif (d / "meta.json").exists():
            (ws / "harness" / d.name).mkdir(parents=True)
            shutil.copy2(d / "meta.json", ws / "harness" / d.name / "meta.json")
    for a in sorted(src):
        if (live / "packets" / a).is_dir():
            shutil.copytree(live / "packets" / a, ws / "packets" / a)


def run(tree, ws, out, workers):
    os.environ["DECOMP_PROJECT"] = str(ws)
    sys.path.insert(0, str(tree))
    shutil.copy2(ws / "readable.json.pristine", ws / "state" / "readable.json")
    from core import header_snapshot, paths, readable, readable_verify
    assert paths.ROOT == ws.resolve()
    cfg = paths.config()
    snap = header_snapshot.acquire(cfg, log=lambda m: print(m, flush=True), wait_s=0, fallback=False)
    header_snapshot.use(snap)
    files = sorted(p.stem for p in readable.SRC.glob("8c*.c"))
    if os.environ.get("PROOF_SAMPLE"):  # a seeded sample: the same files for every tree
        import random
        files = sorted(random.Random(int(os.environ.get("PROOF_SEED", "1"))).sample(
            files, min(len(files), int(os.environ["PROOF_SAMPLE"]))))
    t = time.perf_counter()
    try:
        final, bad = readable_verify.verify(cfg, files, workers=workers, sync=False,
                                            log=lambda m: print(m, flush=True))
    finally:
        snap.release()
    wall = round(time.perf_counter() - t, 1)
    rows = {a: {k: r.get(k) for k in KEYS if k in r} for a, r in final.items()}
    pathlib.Path(out).write_text(json.dumps({"tree": str(tree), "wall_s": wall, "bad": bad, "fns": rows}, indent=1),
                                 encoding="utf-8")
    print(f"verify: {len(files)} files in {wall} s; {bad} not passing", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    else:
        run(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]).resolve(), sys.argv[4], int(sys.argv[5]))

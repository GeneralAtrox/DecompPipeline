"""Proof G (row 19): the Xbox 360 translator with its whole-program return analysis kept (xenon-tx b9fadab) gives
the same C, and the same verdicts, as before (21b7a39). A private copy of the Too Human workspace is made once per
tree; core/translate_stage.attempt (the job's attempt 0, the official judge) then runs for the same functions in
each, in the order given.

usage: python x360_tx_proof.py build LIVE_WS WS     copy the workspace's own files (no Ghidra project, no builds)
       python x360_tx_proof.py run WS ADDRS OUT.json  ADDRS: one address per line
DP_TREE names the DP tree to run. No Ghidra.
"""
import hashlib
import json
import os
import pathlib
import shutil
import sys
import time

FIELDS = ("verdict", "stage", "score", "summary", "first_diffs")
SKIP = {"ghidra", "ghidra_slots", "build", "runs", "matched"}


def build(live, ws):
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    for f in live.iterdir():
        if f.name in SKIP:
            continue
        if f.is_dir():
            shutil.copytree(f, ws / f.name, ignore=shutil.ignore_patterns("*.log", "*.lock", "ledger*", "translator"))
        else:
            shutil.copy2(f, ws / f.name)


def run(ws, addrs, out):
    os.environ["DECOMP_PROJECT"] = str(ws)
    tree = pathlib.Path(os.environ["DP_TREE"])
    sys.path[:0] = [str(tree), str(tree / "tools")]
    from core import filelock, job, paths, translate_stage
    from translate_backlog import O, W
    assert paths.ROOT == ws.resolve()
    o = O(paths, filelock, paths.config())
    rows, t_all = {}, time.perf_counter()
    for a in addrs:
        t = time.perf_counter()
        att = translate_stage.attempt(o, W(), a, job.judge)
        work = paths.RUNS / a / "translated" / "work.c"
        rows[a] = {"s": round(time.perf_counter() - t, 3),
                   "translate_s": (o.rec(a).get("translator") or {}).get("translate_s"),
                   "c_sha256": hashlib.sha256(work.read_bytes()).hexdigest() if work.is_file() else None,
                   **({k: att.get(k) for k in FIELDS} if att else {"verdict": None})}
        print(a, rows[a]["verdict"], rows[a]["translate_s"], flush=True)
    rec = {"tree": os.environ["DP_TREE"], "wall_s": round(time.perf_counter() - t_all, 1), "rows": rows}
    pathlib.Path(out).write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    else:
        addrs = [ln.split("#")[0].strip().rsplit("/", 1)[-1] for ln in open(sys.argv[3], encoding="utf-8")]
        run(pathlib.Path(sys.argv[2]).resolve(), [a for a in addrs if a], sys.argv[4])

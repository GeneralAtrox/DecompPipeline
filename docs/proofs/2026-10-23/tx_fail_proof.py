"""Proof F (row 23): a kept translator FAIL (docs/proposals/translated_fail.patch) is the same attempt as translating
and judging again. On a private workspace copy, core/translate_stage.attempt (the job's attempt 0) runs for every
copied function: with the tree in DP_TREE as it is ("plain"), then with the patch twice, each in a new process
("kept1" translates, judges and keeps its FAILs in the ledger; "kept2" is a later job on unchanged inputs).

usage: python tx_fail_proof.py build LIVE_WS WS     copy every function with a full harness (harness, packet)
       python tx_fail_proof.py run WS plain|kept OUT.json
DP_TREE names the DP checkout. No Ghidra.
"""
import json
import os
import pathlib
import shutil
import sys
import time

FIELDS = ("verdict", "stage", "score", "summary", "first_diffs", "translator", "translator_rev")


def build(live, ws):
    shutil.rmtree(ws, ignore_errors=True)
    (ws / "state").mkdir(parents=True)
    p = json.loads((live / "project.json").read_text(encoding="utf-8"))
    p["binary"] = str((live / p["binary"]).resolve())
    p.pop("ghidra_dir", None)
    (ws / "project.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
    for f in (live / "state").iterdir():
        if (f.is_file() and f.suffix not in (".log", ".lock") and f.stat().st_size <= 5 << 20
                and ".before" not in f.name and not f.name.startswith("ledger")):
            shutil.copy2(f, ws / "state" / f.name)
    for d in sorted((live / "harness").iterdir()):
        if (d / "vectors.txt").is_file() and (live / "packets" / d.name).is_dir():
            shutil.copytree(d, ws / "harness" / d.name)
            shutil.copytree(live / "packets" / d.name, ws / "packets" / d.name)


def run(ws, mode, out):
    os.environ["DECOMP_PROJECT"] = str(ws)
    tree = pathlib.Path(os.environ["DP_TREE"])
    sys.path[:0] = [str(tree), str(tree / "tools")]
    from core import filelock, job, paths, translate_stage
    from translate_backlog import O, W
    assert paths.ROOT == ws.resolve()
    if mode == "plain":
        paths.RUNS = ws / "runs_plain"  # the kept runs' work.c stay apart
    o = O(paths, filelock, paths.config())
    rows, t_all = {}, time.perf_counter()
    for a in sorted(d.name for d in paths.HARNESS.iterdir()):
        t = time.perf_counter()
        att = translate_stage.attempt(o, W(), a, job.judge)
        rows[a] = {"s": round(time.perf_counter() - t, 3), "reused": bool(att and att.get("reused")),
                   **({k: att.get(k) for k in FIELDS} if att else {"verdict": None})}
    if mode == "kept":
        o.save()
    rec = {"mode": mode, "wall_s": round(time.perf_counter() - t_all, 1), "rows": rows}
    pathlib.Path(out).write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    v = [r["verdict"] for r in rows.values()]
    print(f"{mode}: {len(rows)} functions, {v.count('PASS')} PASS, {v.count('FAIL')} FAIL, "
          f"{sum(r['reused'] for r in rows.values())} kept FAILs taken, {rec['wall_s']} s", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    else:
        run(pathlib.Path(sys.argv[2]).resolve(), sys.argv[3], sys.argv[4])

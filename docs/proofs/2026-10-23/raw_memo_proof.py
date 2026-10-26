"""Proof E (row 22): the readable stage's kept raw PASSes (docs/proposals/raw_pass_memo.patch) give the same
harness_stale answer as judging every time. On a private workspace copy, refine.harness_stale runs over every copied
function three times: with the tree in DP_TREE as it is ("plain", no memo), then twice with the patch's memo, each
time in a new process ("memo1" judges and keeps the PASSes, "memo2" is a later run on unchanged inputs).

usage: python raw_memo_proof.py build LIVE_WS WS N    copy N evenly spaced matched functions (C, harness, packet)
       python raw_memo_proof.py run WS plain|memo OUT.json
DP_TREE names the DP checkout. No Ghidra: the raw judge only.
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
    for f in (live / "state").iterdir():
        if (f.is_file() and f.suffix not in (".log", ".lock") and f.stat().st_size <= 5 << 20
                and ".before" not in f.name):
            shutil.copy2(f, ws / "state" / f.name)
    have = sorted(f.stem for f in (live / "matched").glob("*.c")
                  if (live / "harness" / f.stem / "vectors.txt").is_file() and (live / "packets" / f.stem).is_dir())
    step = max(1, len(have) // n)
    for a in have[::step][:n]:
        shutil.copytree(live / "harness" / a, ws / "harness" / a)
        shutil.copytree(live / "packets" / a, ws / "packets" / a)
        (ws / "matched").mkdir(exist_ok=True)
        shutil.copy2(live / "matched" / f"{a}.c", ws / "matched" / f"{a}.c")
    if (live / "include").is_dir():  # headers a raw build may include
        shutil.copytree(live / "include", ws / "include")


def run(ws, mode, out):
    os.environ["DECOMP_PROJECT"] = str(ws)
    sys.path.insert(0, str(pathlib.Path(os.environ["DP_TREE"])))
    from core import paths, refine
    assert paths.ROOT == ws.resolve()
    cfg = paths.config()
    memo = refine.raw_memo() if mode == "memo" else None
    rows, t_all = {}, time.perf_counter()
    for a in sorted(p.stem for p in paths.MATCHED.glob("*.c")):
        t = time.perf_counter()
        stale = refine.harness_stale(cfg, a, memo=memo) if memo is not None else refine.harness_stale(cfg, a)
        rows[a] = {"stale": stale, "s": round(time.perf_counter() - t, 3)}
    rec = {"mode": mode, "wall_s": round(time.perf_counter() - t_all, 1), "hits": memo.hits if memo else 0,
           "rows": rows}
    pathlib.Path(out).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(f"{mode}: {len(rows)} functions, {sum(r['stale'] is None for r in rows.values())} pass, "
          f"{rec['hits']} from the memo, {rec['wall_s']} s", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), int(sys.argv[4]))
    else:
        run(pathlib.Path(sys.argv[2]).resolve(), sys.argv[3], sys.argv[4])

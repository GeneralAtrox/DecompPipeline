"""One side of the GameCube readable-runtime A/B: every matched raw C of a workspace through ppc_readable.check_raw
(its build, rd_runner and compare), with the DP tree given, builds and caches in a scratch folder (nothing written in
the workspace). Writes {addr: {result, actual_sha256, build_s, run_s}} as JSON.
usage: python gc_rd_ab.py TREE WS SCRATCH OUT.json [N]"""
import hashlib
import json
import os
import pathlib
import sys
import time

tree, ws, scratch, out = map(pathlib.Path, sys.argv[1:5])
n = int(sys.argv[5]) if len(sys.argv) > 5 else 10 ** 9
os.environ["DECOMP_PROJECT"] = str(ws)
sys.path[:0] = [str(tree), str(tree / "targets" / "gamecube_ppc")]
from core import paths, readable_check as rc  # noqa: E402

scratch.mkdir(parents=True, exist_ok=True)
paths.BUILD = scratch / "build"
rc.MSVC_ENV_X86 = scratch / "msvc_env_x86.json"
import ppc_readable  # noqa: E402

cfg = paths.config()
addrs = sorted(p.stem for p in (ws / "matched").glob("*.c") if (paths.HARNESS / p.stem / "meta.json").is_file())[:n]
if os.environ.get("ONLY"):
    addrs = os.environ["ONLY"].split(",")
res, t_all = {}, time.perf_counter()
for a in addrs:
    bd = paths.BUILD / a / "rd_raw"
    t0 = time.perf_counter()
    r = ppc_readable.check_raw(cfg, a, ws / "matched" / f"{a}.c", bd)
    dt = time.perf_counter() - t0
    act = bd / "actual.txt"
    res[a] = {"result": r, "actual_sha256": hashlib.sha256(act.read_bytes()).hexdigest() if act.is_file() else None,
              "s": round(dt, 3)}
    print(a, r.get("verdict"), r.get("summary", "")[:60], f"{dt:.2f} s", flush=True)
json.dump({"tree": str(tree), "wall_s": round(time.perf_counter() - t_all, 1), "rows": res},
          open(out, "w", encoding="utf-8"), indent=1, default=str)

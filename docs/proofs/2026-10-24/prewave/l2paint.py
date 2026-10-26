"""laptop2: the stack paint's verdict check. Every matched C that declares a TcArgs without an initialiser (the only C
whose result the paint can change), judged flags off on its workspace harness (paint_h/KEY/ADDR, copied from the main
laptop with its matched C, paint_m/KEY/ADDR.c) by one repo's official check.
usage: py -3.12 l2paint.py REPO(land|rb) WORKERS KEY ...   writes proof/paint_REPO_KEY.json {addr: [verdict, failing,
total, stage, first diff]}; a key whose JSON exists is skipped (resume)."""
import concurrent.futures
import json
import os
import pathlib
import shutil
import sys
import time

W = pathlib.Path(os.environ["DP_PREWAVE_DIR"])  # the work folder (on laptop2: its dpwork\prewave)
WS = {"grandia": W / r"ws\grandia\ws-1pct8", "tc": W / r"ws\tc\decomp", "ps2": W / r"ws\ps2\decomp\main",
      "spawn": W / r"ws\spawn\decomp\main"}
repo, workers, keys = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
if len(keys) != 1:  # one workspace per process: core.paths reads DECOMP_PROJECT once
    import subprocess
    for k in keys:
        r = subprocess.run([sys.executable, __file__, repo, str(workers), k])
        print(time.strftime("%H:%M:%S"), repo, k, "exit", r.returncode, flush=True)
    sys.exit(0)
key = keys[0]
out = W / "proof" / f"paint_{repo}_{key}.json"
if out.exists():
    sys.exit(print(key, "done before, skipped"))
os.environ["DECOMP_PROJECT"] = str(WS[key])
sys.path.insert(0, str(W / f"repo-{repo}"))
from core import check, paths  # noqa: E402

cfg = paths.config()
root = W / "paint_build" / f"{repo}_{key}"
shutil.rmtree(root, ignore_errors=True)
addrs = sorted(p.name for p in (W / "paint_h" / key).iterdir())


def one(a):
    try:
        r = check.run_check(cfg, a, str(W / "paint_m" / key / f"{a}.c"), build_dir=root / a,
                            harness_dir=W / "paint_h" / key / a)
    except Exception as e:  # reported, not fatal: the same error on both sides is no verdict change
        return a, ["ERROR", None, None, type(e).__name__, str(e)[:300]]
    finally:
        shutil.rmtree(root / a, ignore_errors=True)
    d = (r.get("first_diffs") or [{}])[0]
    return a, [r["verdict"], r.get("failing"), r.get("total"), r.get("stage"), json.dumps(d)[:300]]


t = time.time()
res = {}
with concurrent.futures.ThreadPoolExecutor(workers) as ex:
    for a, v in ex.map(one, addrs):
        res[a] = v
out.write_text(json.dumps(res, indent=0, sort_keys=True), encoding="utf-8")
shutil.rmtree(root, ignore_errors=True)
n = sum(v[0] == "PASS" for v in res.values())
print(time.strftime("%H:%M:%S"), repo, key, f"{n}/{len(res)} PASS", f"{time.time() - t:.0f}s", flush=True)

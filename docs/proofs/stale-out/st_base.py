"""st_base.py LIST OUT.json [BUDGET_S]: judge each listed function's accepted readable C once with the stale-out-word rule on and
count what it masks (words changed in the act writes, vectors touched), resumable (skips functions already in OUT).
Nearest existing: l/st_ed5c.py (one function with the v4e harness); rd_mut_ab2.py (mutants). This is the base run only: where the
rule masks nothing it cannot change any mutant's outcome (the parked bytes come from the original's raw runs, not from the C)."""
import json, os, pathlib, shutil, sys, time
W = os.environ.get("KIT", os.environ["DPWORK"] + r"\land\tcdone\kitst")
sys.path[:0] = [W, W + r"\tools"]
from common import use_project
use_project(os.environ["DPWORK"] + r"\recheck\ws\tc\decomp")
from core import paths, readable, readable_check as rc, readable_stack
lst = [x.split()[0] for x in open(sys.argv[1]) if x.strip()]
outp = pathlib.Path(sys.argv[2]); budget = float(sys.argv[3]) if len(sys.argv) > 3 else 2400
res = json.loads(outp.read_text(encoding="utf-8")) if outp.exists() else {}
cfg = paths.config(); reg = readable.Registry()
cnt = {"w": 0, "v": 0}
orig = readable_stack.stale_out_words


def counted(exp, act, dlines, frame_lo=readable_stack.FRAME_LO):
    before = dict(act["writes"]) if act and dlines else None
    orig(exp, act, dlines, frame_lo)
    if before is not None:
        ch = sum(1 for a, b in act["writes"].items() if before.get(a) != b)
        if ch:
            cnt["w"] += ch; cnt["v"] += 1


readable_stack.stale_out_words = counted
if os.environ.get('STALE_OFF'):  # STALE_OFF: same run with the rule disabled
    readable_stack.STALE_OUT = False
t0 = time.time()
for a in lst:
    if a in res or time.time() - t0 > budget:
        continue
    cnt["w"] = cnt["v"] = 0
    d = pathlib.Path(os.environ["DPWORK"] + r"\land\tcdone\l\mbuild_stb") / (a + "_" + str(os.getpid()))
    shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    try:
        r = rc.check_readable(cfg, a, str(paths.ROOT / "readable" / "src" / f"{a}.c"), d / "b", reg=reg)
        res[a] = {"verdict": r["verdict"], "failing": r.get("failing"), "masked_bytes": cnt["w"], "masked_vectors": cnt["v"]}
    except Exception as e:  # noqa: BLE001
        res[a] = {"error": str(e)[:150]}
    shutil.rmtree(d, ignore_errors=True)
    outp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(a, res[a], flush=True)
print("DONE" if all(a in res for a in lst) else "PARTIAL", len(res), flush=True)

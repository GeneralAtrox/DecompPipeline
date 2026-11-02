"""st_elig.py LIST OUT.json [BUDGET_S]: judge each listed function's accepted readable C once and count where the stale-out-word rule
COULD fire: the words of the original's output (outside the frame) that equal a non-zero whole word parked in the callee's out buffer
(eligible), whether or not the C's word already equals it. A mutant changes the C's words, so any function with an eligible word needs
the mutation A/B; one with none cannot be changed by the rule. Resumable.
Nearest existing: l/st_base.py - it counts only words the rule CHANGED on the unmutated C (0 on 313 of 327 functions), which does not bound
where a mutant could be masked; this counts eligibility."""
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
cnt = {"e": 0, "v": 0, "s": 0}
orig = readable_stack.stale_out_words


def counted(exp, act, dlines, frame_lo=readable_stack.FRAME_LO):
    if act and dlines:
        cnt["s"] += 1
        ew, held = exp["writes"], set()
        for _, _, data in dlines:
            for j in range(0, len(data) // 2 - 3, 4):
                if "--" not in data[2 * j:2 * j + 8]:
                    held.add(int.from_bytes(bytes.fromhex(data[2 * j:2 * j + 8]), "little"))
        held.discard(0)
        n = sum(1 for w in {a & ~3 for a in ew if a < frame_lo}
                if all(w + i in ew for i in range(4)) and int.from_bytes(bytes(ew[w + i] for i in range(4)), "little") in held)
        if n:
            cnt["e"] += n; cnt["v"] += 1
    orig(exp, act, dlines, frame_lo)


readable_stack.stale_out_words = counted
t0 = time.time()
for a in lst:
    if a in res or time.time() - t0 > budget:
        continue
    cnt.update(e=0, v=0, s=0)
    d = pathlib.Path(os.environ["DPWORK"] + r"\land\tcdone\l\mbuild_elig") / (a + "_" + str(os.getpid()))
    shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    try:
        r = rc.check_readable(cfg, a, str(paths.ROOT / "readable" / "src" / f"{a}.c"), d / "b", reg=reg)
        res[a] = {"verdict": r["verdict"], "eligible_words": cnt["e"], "eligible_vectors": cnt["v"], "vectors_with_parked": cnt["s"]}
    except Exception as e:  # noqa: BLE001
        res[a] = {"error": str(e)[:150]}
    shutil.rmtree(d, ignore_errors=True)
    outp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(a, res[a], flush=True)
print("DONE" if all(a in res for a in lst) else "PARTIAL", len(res), flush=True)

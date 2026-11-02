"""outspec_scan.py WS OUT.json: the TC functions whose readable judge gives a callee an out buffer at a guest stack address (the
vectors the out-pointer delivery D/S lines are written for), per function: vectors with such a call.
Nearest existing: core/readable_outptr.out_params/lines_for (the judge's own selection); this only runs them over the workspace,
without any build, to name the functions the stale-out-word rule can touch."""
import json, os, pathlib, sys
W = os.environ.get("KIT", os.environ["DPWORK"] + r"\land\tcdone\kitst")
sys.path[:0] = [W, W + r"\tools"]
from common import use_project
use_project(sys.argv[1])
from core import check, paths, readable, readable_check as rc, readable_outptr as op, readable_stack
reg = readable.Registry()
lo = readable_stack.frame_lo()
res = {}
for src in sorted((paths.ROOT / "readable" / "src").glob("8c*.c")):
    a = src.stem
    h = paths.HARNESS / a
    if not (h / "meta.json").exists() or not (h / "expected.txt").exists():
        continue
    try:
        meta = paths.read_json(h / "meta.json")
        specs = op.out_params(reg, meta, lambda c: rc.callee_storage(h, c, ()))
        if not specs:
            continue
        exp = check.parse_results(h / "expected.txt")
        specs, _ = op.paired(meta, exp, specs, lo)
        lines = op.lines_for(exp, specs, lambda b: 1, None, lo)
        if lines:
            res[a] = {"vectors": len(lines), "lines": sum(len(v) for v in lines.values()), "specs": len(specs)}
    except Exception as e:  # noqa: BLE001
        res[a] = {"error": str(e)[:100]}
    pathlib.Path(sys.argv[2]).write_text(json.dumps(res, indent=1), encoding="utf-8")
print(len(res), sum(1 for r in res.values() if "error" in r))

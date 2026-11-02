"""rd_mut_ab6.py LIST OUT.json [BUDGET_S]: mutation A/B of the stale-out-word rule over a list of functions, resumable.
OLD = readable_stack.STALE_OUT False (the readable judge as it is), NEW = True (rule on), same harness and config. Same draw
(core/mutation.generate seed 1, mutation_audit.MUTANTS) and gate as rd_mut_ab5/mutation_audit.compare: lost kill = OLD saw the
mutant (compare-stage difference list differs from its base) and NEW passes it.
Nearest existing: l/rd_mut_ab5.py - same A/B for one function with a swapped harness and opt-ins; this takes a list and swaps nothing."""
import os
import hashlib, json, pathlib, shutil, sys, time
W = os.environ["DPWORK"] + r"\land\tcdone\kitst"
sys.path[:0] = [W, W + r"\tools"]
from common import use_project
use_project(os.environ["DPWORK"] + r"\recheck\ws\tc\decomp")
from core import mutation, paths, readable, readable_check as rc, readable_stack
import mutation_audit

lst = [x.split()[0] for x in open(sys.argv[1]) if x.strip()]
outp = pathlib.Path(sys.argv[2]); budget = float(sys.argv[3]) if len(sys.argv) > 3 else 2400
res = json.loads(outp.read_text(encoding="utf-8")) if outp.exists() else {}
cfg = paths.config(); reg = readable.Registry()
ROOT = pathlib.Path(os.environ["DPWORK"] + r"\land\tcdone\l\mbuild_ab6")
orig_cv = rc.check.compare_vector
mode, log = {"k": None}, {"old": [], "new": []}


def cv(*a, **k):
    d = orig_cv(*a, **k)
    if mode["k"]:
        log[mode["k"]].append(list(d))
    return d


rc.check.compare_vector = cv


def judge(addr, text, tag):
    d = ROOT / addr / tag
    shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    f = d / f"{addr}.c"; f.write_text(text, encoding="utf-8")
    out = {}
    try:
        for k in ("old", "new"):
            mode["k"] = k; log[k].clear()
            readable_stack.STALE_OUT = k == "new"
            try:
                r = rc.check_readable(cfg, addr, str(f), d / f"b{k}", reg=reg)
            finally:
                mode["k"] = None
            out[k] = {"verdict": r["verdict"], "stage": r["stage"], "failing": r.get("failing"), "full": list(log[k])}
            if r.get("run_error"):
                out[k]["verdict"] = "FAIL"
        return out
    finally:
        readable_stack.STALE_OUT = True
        shutil.rmtree(d, ignore_errors=True)


t0 = time.time()
for addr in lst:
    if addr in res or time.time() - t0 > budget:
        continue
    src = (paths.ROOT / "readable" / "src" / f"{addr}.c").read_text(encoding="utf-8")
    base = judge(addr, src, "base")
    det = {"sha": hashlib.sha256(src.encode()).hexdigest(), "base": {k: [v["verdict"], v["failing"]] for k, v in base.items()}, "mutants": []}
    oc = {"old": "", "new": ""}
    for m in mutation.generate(src, addr, mutation_audit.MUTANTS, 1):
        j = judge(addr, m["src"], f"m{m['id']:02d}")
        for k in ("old", "new"):
            oc[k] += {"killed": "K", "survived": "S", "dropped_lint": "L", "dropped_build": "B"}[mutation.outcome(j[k])]
        sub = j["old"]["stage"] == "compare" and (j["old"]["verdict"] != base["old"]["verdict"] or j["old"]["full"] != base["old"]["full"])
        lost = sub and j["new"]["verdict"] == "PASS"
        det["mutants"].append({"id": m["id"], "op": m["op"], "old": [j["old"]["verdict"], j["old"]["stage"]], "new": [j["new"]["verdict"], j["new"]["stage"]],
                               "sub": sub, "lost": lost})
    det["old"], det["new"] = oc["old"], oc["new"]
    det["lost"] = [m["id"] for m in det["mutants"] if m["lost"]]
    res[addr] = det
    outp.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(addr, "base", det["base"], "old", oc["old"], "new", oc["new"], "LOST", det["lost"], flush=True)
print("DONE" if all(a in res for a in lst) else "PARTIAL", len(res), flush=True)

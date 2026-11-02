"""rd_mut_ab3.py WS LISTFILE OUT.json: rd_mut_ab2 for a change of the raw-run step (readable_outrun.SALVAGE): OLD = the
judge with SALVAGE False (a timed-out raw run gives no D lines), NEW = as built. Same draw (core/mutation.generate seed 1,
tools/mutation_audit.MUTANTS), same gate. The vectors file of each side is made once (the raw runs do not depend on the
mutant) and reused. Lost kill = a mutant OLD saw (its verdict or full difference list differs from OLD's base) and NEW
passes. Nearest existing: rd_mut_ab2.py - it toggles readable_optin.ENABLED at the compare step; this toggles the vectors."""
import os
import hashlib, json, pathlib, shutil, sys
W = os.environ["DPWORK"] + r"\land\tcdone\wt"
sys.path[:0] = [W, W + r"\tools"]
from common import use_project
use_project(sys.argv[1])
from core import mutation, paths, readable, readable_check as rc, readable_outrun
import mutation_audit

cfg = paths.config()
reg = readable.Registry()
todo = [x.split() for x in open(sys.argv[2]) if x.strip()]
ROOT = pathlib.Path(os.environ["DPWORK"] + r"\land\tcdone\l\mbuild3")
KEEP = ROOT / "_vec"
KEEP.mkdir(parents=True, exist_ok=True)
orig_cv, orig_vf = rc.check.compare_vector, readable_outrun.vectors_file
mode, log, memo, cur = {"k": None}, {"old": [], "new": []}, {}, {"addr": None}


def cv(*a, **k):
    d = orig_cv(*a, **k)
    if mode["k"]:
        log[mode["k"]].append(list(d))
    return d


def vf(cfg_, meta, harness, specs, build_dir, include_dirs, addr=None, sets=("dev", "hidden")):
    key = (addr, readable_outrun.SALVAGE)
    if key not in memo:
        p, n = orig_vf(cfg_, meta, harness, specs, build_dir, include_dirs, addr, sets)
        keep = KEEP / f"{addr}_{int(readable_outrun.SALVAGE)}.txt"
        shutil.copyfile(p, keep)
        memo[key] = (keep, n)
    return memo[key]


rc.check.compare_vector, readable_outrun.vectors_file = cv, vf


def judge(addr, text, tag):
    d = ROOT / addr / tag
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    f = d / f"{addr}.c"
    f.write_text(text, encoding="utf-8")
    out = {}
    try:
        for k, on in (("old", False), ("new", True)):
            readable_outrun.SALVAGE, mode["k"] = on, k
            log[k].clear()
            try:
                r = rc.check_readable(cfg, addr, str(f), d / f"b{k}", reg=reg)
            finally:
                readable_outrun.SALVAGE, mode["k"] = True, None
            out[k] = {"verdict": r["verdict"], "stage": r["stage"], "failing": r.get("failing"),
                      "ids": {s: v.get("failing_ids") for s, v in (r.get("vectors") or {}).items()},
                      "diff": (r.get("first_diffs") or [{}])[0].get("diffs", [])[:2], "full": list(log[k])}
            if r.get("run_error"):
                out[k]["verdict"] = "FAIL"
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


res = {"old": {"tc": {}}, "new": {"tc": {}}, "detail": {}}
outp = pathlib.Path(sys.argv[3])
for row in todo:
    addr = row[0]
    p = pathlib.Path(row[1]) if len(row) > 1 else paths.ROOT / "readable" / "src" / f"{addr}.c"
    src = p.read_text(encoding="utf-8")
    sha = hashlib.sha256(src.encode()).hexdigest()
    base = judge(addr, src, "base")
    det = {"src": str(p), "sha": sha, "base": {k: {kk: vv for kk, vv in v.items() if kk != "full"} for k, v in base.items()},
           "mutants": []}
    print(addr, "base old", base["old"]["verdict"], base["old"]["failing"], "new", base["new"]["verdict"],
          base["new"]["failing"], flush=True)
    oc = {"old": "", "new": ""}
    for m in mutation.generate(src, addr, mutation_audit.MUTANTS, 1):
        j = judge(addr, m["src"], f"m{m['id']:02d}")
        for k in ("old", "new"):
            oc[k] += {"killed": "K", "survived": "S", "dropped_lint": "L", "dropped_build": "B"}[mutation.outcome(j[k])]
        sub = j["old"]["stage"] == "compare" and (j["old"]["verdict"] != base["old"]["verdict"]
                                                  or j["old"]["full"] != base["old"]["full"])
        lost = sub and j["new"]["verdict"] == "PASS"
        det["mutants"].append({"id": m["id"], "op": m["op"], "line": m["line"], "what": m["what"],
                               **{k: {kk: vv for kk, vv in j[k].items() if kk != "full"} for k in ("old", "new")},
                               "old_substantive_kill": sub, "lost": lost})
        print(addr, f"m{m['id']:02d}", m["op"], oc["old"][-1], oc["new"][-1], "SUB" if sub else "", "LOST" if lost else "",
              flush=True)
    for k in ("old", "new"):
        res[k]["tc"][addr] = ({"sha": sha, "outcomes": oc[k]} if base[k]["verdict"] == "PASS" else
                              {"error": f"base {base[k]['verdict']} {base[k]['failing']}"})
    res["detail"][addr] = det
    print(addr, "old", oc["old"], "new", oc["new"], flush=True)
    outp.write_text(json.dumps(res, indent=1), encoding="utf-8")
res["substantive_lost"] = [(a, m["id"]) for a, d in res["detail"].items() for m in d.get("mutants", []) if m["lost"]]
outp.write_text(json.dumps(res, indent=1, default=list), encoding="utf-8")
print("substantive lost:", res["substantive_lost"])

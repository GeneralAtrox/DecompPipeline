"""rd_mut_ab5.py WS ADDR OUT.json: rd_mut_ab4 for 8c05ed5c with the stale-out-word rule. OLD = the workspace's harness, no opt-in,
rule off (what is judged today); NEW = the round-2 v4e harness (aside/harness_8c05ed5c.attempt_v4e: primed candidate, table pinned,
two-box vectors), its opt-ins (out_targets, drop_vectors 5/56/62) and the rule on. Same draw (core/mutation.generate seed 1,
mutation_audit.MUTANTS), same gate (mutation_audit.compare); lost kill = OLD's complete difference list differs from its base's
(it saw the mutant) and NEW passes it.
Nearest existing: l/rd_mut_ab4.py - it swaps the OLD harness under ENABLED; this swaps the NEW harness and opt-ins in and
toggles readable_stack.STALE_OUT."""
import os
import hashlib, json, pathlib, shutil, sys
W = os.environ["DPWORK"] + r"\land\tcdone\kitst"
sys.path[:0] = [W, W + r"\tools"]
from common import use_project
use_project(sys.argv[1])
from core import mutation, paths, readable, readable_check as rc, readable_stack
import mutation_audit

addr = sys.argv[2]
V4E = pathlib.Path(os.environ["DPWORK"] + r"\land\tcdone\aside\harness_8c05ed5c.attempt_v4e")
base_cfg = paths.config()
optin = json.load(open(os.environ["DPWORK"] + r"\land\tcdone\out\optionB_8c05ed5c\project.json.v2_counts", encoding="utf-8"))["readable_judge"][addr]
optin["drop_vectors"] = {"ids": [5, 56, 62], "known_callees": True, "why": "tcdone round 3 A/B"}
cfg_new = dict(base_cfg, readable_judge=dict(base_cfg.get("readable_judge") or {}, **{addr: optin}))
reg = readable.Registry()
ROOT = pathlib.Path(os.environ["DPWORK"] + r"\land\tcdone\l\mbuild_ab5")
orig_cv = rc.check.compare_vector
mode, log = {"k": None}, {"old": [], "new": []}
REAL = paths.HARNESS


class Swapped(type(REAL)):
    def __truediv__(self, key):
        return V4E if key == addr else super().__truediv__(key)


def cv(*a, **k):
    d = orig_cv(*a, **k)
    if mode["k"]:
        log[mode["k"]].append(list(d))
    return d


rc.check.compare_vector = cv


def judge(text, tag):
    d = ROOT / addr / tag
    shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    f = d / f"{addr}.c"; f.write_text(text, encoding="utf-8")
    out = {}
    try:
        for k in ("old", "new"):
            mode["k"] = k; log[k].clear()
            readable_stack.STALE_OUT = k == "new"
            paths.HARNESS = Swapped(REAL) if k == "new" else REAL
            try:
                r = rc.check_readable(cfg_new if k == "new" else base_cfg, addr, str(f), d / f"b{k}", reg=reg)
            finally:
                mode["k"], paths.HARNESS = None, REAL
            out[k] = {"verdict": r["verdict"], "stage": r["stage"], "failing": r.get("failing"),
                      "ids": {s: v.get("failing_ids") for s, v in (r.get("vectors") or {}).items()},
                      "diff": (r.get("first_diffs") or [{}])[0].get("diffs", [])[:2], "full": list(log[k])}
            if r.get("run_error"):
                out[k]["verdict"] = "FAIL"
        return out
    finally:
        readable_stack.STALE_OUT = True
        shutil.rmtree(d, ignore_errors=True)


res = {"old": {"tc": {}}, "new": {"tc": {}}, "detail": {}}
outp = pathlib.Path(sys.argv[3])
src = (paths.ROOT / "readable" / "src" / f"{addr}.c").read_text(encoding="utf-8")
sha = hashlib.sha256(src.encode()).hexdigest()
base = judge(src, "base")
det = {"sha": sha, "base": {k: {kk: vv for kk, vv in v.items() if kk != "full"} for k, v in base.items()}, "mutants": []}
print(addr, "base old", base["old"]["verdict"], base["old"]["failing"], "new", base["new"]["verdict"], base["new"]["failing"], flush=True)
oc = {"old": "", "new": ""}
for m in mutation.generate(src, addr, mutation_audit.MUTANTS, 1):
    j = judge(m["src"], f"m{m['id']:02d}")
    for k in ("old", "new"):
        oc[k] += {"killed": "K", "survived": "S", "dropped_lint": "L", "dropped_build": "B"}[mutation.outcome(j[k])]
    sub = j["old"]["stage"] == "compare" and (j["old"]["verdict"] != base["old"]["verdict"] or j["old"]["full"] != base["old"]["full"])
    lost = sub and j["new"]["verdict"] == "PASS"
    det["mutants"].append({"id": m["id"], "op": m["op"], "line": m["line"], "what": m["what"],
                           **{k: {kk: vv for kk, vv in j[k].items() if kk != "full"} for k in ("old", "new")},
                           "old_substantive_kill": sub, "lost": lost})
    print(addr, f"m{m['id']:02d}", m["op"], m["what"], oc["old"][-1], oc["new"][-1], "SUB" if sub else "", "LOST" if lost else "", flush=True)
    outp.write_text(json.dumps(dict(res, detail={addr: det}), indent=1), encoding="utf-8")
for k in ("old", "new"):
    res[k]["tc"][addr] = ({"sha": sha, "outcomes": oc[k]} if base[k]["verdict"] == "PASS" else {"error": f"base {base[k]['verdict']} {base[k]['failing']}"})
res["detail"][addr] = det
print(addr, "old", oc["old"], "new", oc["new"], flush=True)
res["substantive_lost"] = [m["id"] for m in det["mutants"] if m["lost"]]
res["new_killed"] = [m["id"] for m in det["mutants"] if mutation.outcome({"verdict": m["new"]["verdict"], "stage": m["new"]["stage"]}) == "killed"] if False else None
outp.write_text(json.dumps(res, indent=1, default=list), encoding="utf-8")
print("substantive lost:", res["substantive_lost"])

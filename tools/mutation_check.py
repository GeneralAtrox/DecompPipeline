"""Per-function mutation check (core/mutation.py, docs/PROOF_GATES.md): seeded mutants of the matched C, judged on the
function's own harness with the official check; survivors mark a weak harness. Host only: no agent, no Ghidra.

usage: python tools/bounded.py 7200 python tools/mutation_check.py --project DIR [ADDR... | @FILE | --references]
           [--sample N] [--seed S] [--report] [--gate] [--json OUT] [--workers N] [--max-mutants N] [--time-cap S]
           [--build-dir DIR] [-v]

  ADDR/@FILE     the functions (matched ones only); else every matched function, or --sample N of them (seeded)
  --references   the project's references (project.json "references", the regress suite's reference functions)
  --gate         the gate fixture (docs/PROOF_GATES.md): exit 1 unless every listed function's matched C passes its
                 harness here (baseline) and, if it has any mutant (an empty body has none), one of them is killed
  --report       read-only: nothing in the workspace is written (state/mutation.json untouched, builds in a temporary
                 folder or --build-dir, the MSVC environment cache not created); print, and --json OUT
  --max-mutants  mutants per function (default config mutation_check.max_mutants, 16)
  --time-cap     seconds per function (default mutation_check.time_cap_s, 240): the rest is not tried
  --workers      functions judged at once (default 4)
Without --report the records are merged into state/mutation.json (the proof state reads them: core/proof.py).
Prints per function: tried, killed, survived (and the logic ones), dropped (refused by lint or the /W4 /WX build),
score (logic survivors only) and score_raw (every survivor); then the project figure: score distribution, survivors
by class and by operator, and the first logic survivor of each operator. Exit 0 (report tool), 1 when
a baseline (the matched C itself) does not pass its harness here, or with --gate a function had no mutant killed.
"""
import argparse
import collections
import concurrent.futures
import json
import pathlib
import random
import shutil
import statistics
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import expand_addrs, use_project  # noqa: E402


def figure(recs, mset):
    ok = [r for r in recs.values() if not r.get("error")]
    scores = [r["score"] for r in ok if r.get("score") is not None]
    raw = [r["score_raw"] for r in ok if r.get("score_raw") is not None]
    classes = collections.Counter()
    for r in ok:
        classes.update(r.get("survived_by_class") or {})
    by_op = collections.defaultdict(lambda: collections.Counter())
    for r in ok:
        for op, c in r["by_op"].items():
            by_op[op].update(c)
    buckets = collections.Counter("100" if s >= 100 else "90-99" if s >= 90 else "80-89" if s >= 80 else
                                  "60-79" if s >= 60 else "<60" for s in scores)
    return {"functions": len(recs), "errors": len(recs) - len(ok), "scored": len(scores),
            "mean_score": round(statistics.mean(scores), 1) if scores else None,
            "median_score": statistics.median(scores) if scores else None,
            "mean_score_raw": round(statistics.mean(raw), 1) if raw else None,
            "survived_by_class": dict(classes),
            "with_logic_survivors": sum(1 for r in ok if (r.get("survived_by_class") or {}).get("logic")),
            "below_min_score": sum(1 for s in scores if s < mset["min_score"]),
            "with_survivors": sum(1 for r in ok if r["survived"]),
            "tried": sum(r["tried"] for r in ok), "killed": sum(r["killed"] for r in ok),
            "survived": sum(r["survived"] for r in ok), "dropped": sum(r["dropped"] for r in ok),
            "time_capped": sum(1 for r in ok if r.get("time_capped")),
            "no_mutants": sum(1 for r in ok if not r.get("generated")),
            "buckets": {k: buckets[k] for k in ("100", "90-99", "80-89", "60-79", "<60")},
            "by_op": {op: dict(c) for op, c in sorted(by_op.items())},
            "seconds": round(sum(r.get("seconds", 0) for r in recs.values()), 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("addrs", nargs="*")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", type=int, default=1, help="sample seed (the mutant seed is mutation_check.seed)")
    ap.add_argument("--references", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-mutants", type=int)
    ap.add_argument("--time-cap", type=float)
    ap.add_argument("--build-dir")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    ws = use_project(a.project)
    from core import mutation, paths, proof
    cfg = paths.config()
    mset = proof.mutation_settings(cfg)
    if a.max_mutants:
        mset["max_mutants"] = a.max_mutants
    if a.time_cap:
        mset["time_cap_s"] = a.time_cap
    led = (paths.read_json(paths.LEDGER) or {}).get("functions") or {}
    matched = sorted(x for x, r in led.items() if r.get("status") == "matched" and (paths.MATCHED / f"{x}.c").exists()
                     and (paths.HARNESS / x / "expected.txt").exists())
    if a.addrs or a.references:
        want = expand_addrs(a.addrs) + [x for x in (paths.PROJECT.get("references") or []) if a.references]
        want = list(dict.fromkeys(want))
        addrs = [x for x in want if x in set(matched)]
        if len(addrs) < len(want):
            print(f"not matched (or no matched C / harness), left out: {' '.join(x for x in want if x not in addrs)}")
    else:
        addrs = matched
    if a.sample is not None and a.sample < len(addrs):
        addrs = sorted(random.Random(a.seed).sample(addrs, a.sample))
    tmp = None
    if a.build_dir:
        root = pathlib.Path(a.build_dir)
    elif a.report:
        root = tmp = pathlib.Path(tempfile.mkdtemp(prefix="mutation_"))
    else:
        root = paths.BUILD / "mutation"
    if a.report and not paths.MSVC_ENV.exists():  # check.msvc_env caches it in state/: keep the workspace untouched
        paths.MSVC_ENV = root / "msvc_env.json"
    print(f"mutation check of {ws}: {len(addrs)} function(s), up to {mset['max_mutants']} mutants each, "
          f"{mset['time_cap_s']} s cap, seed {mset['seed']}, mode {mset['mode']}"
          + ("; report only (no writes)" if a.report else ""), flush=True)
    recs, lock, t0 = {}, threading.Lock(), time.time()

    def one(addr):
        src = (paths.MATCHED / f"{addr}.c").read_text(encoding="utf-8", errors="replace")
        judge = mutation.judge_with(cfg, addr, root / addr, mset["mutant_timeout_s"])
        try:
            rec = mutation.check_function(addr, src, judge, mset, log=print if a.verbose else None)
        except Exception as e:  # noqa: BLE001  (one function's crash is its own error, the others go on)
            rec = {"addr": addr, "error": f"{type(e).__name__}: {e}"[:300], "score": None, "tried": 0}
        finally:
            shutil.rmtree(root / addr, ignore_errors=True)
        rec["checked"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with lock:
            recs[addr] = rec
            if rec.get("error"):
                print(f"{addr}: ERROR {rec['error']}", flush=True)
            else:
                print(f"{addr}: score {rec['score']} (raw {rec['score_raw']})  tried {rec['tried']} killed "
                      f"{rec['killed']} survived {rec['survived']} (logic {rec['survived_by_class']['logic']}) "
                      f"dropped {rec['dropped']}" + (" (time cap)" if rec["time_capped"] else "")
                      + f"  {rec['seconds']} s  [{len(recs)}/{len(addrs)}]", flush=True)
        return rec

    try:
        with concurrent.futures.ThreadPoolExecutor(max(1, a.workers)) as ex:
            list(ex.map(one, addrs))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    f = figure(recs, mset)
    print(f"\n== {ws}: {f['functions']} functions, {f['errors']} errors; scored {f['scored']}: mean {f['mean_score']}, "
          f"median {f['median_score']} (raw, every survivor counted: mean {f['mean_score_raw']}); below min_score "
          f"{mset['min_score']}: {f['below_min_score']}; with logic survivors {f['with_logic_survivors']} "
          f"(weak_harness), with any survivor {f['with_survivors']}; mutants tried {f['tried']}, killed {f['killed']}, survived "
          f"{f['survived']}, dropped {f['dropped']} (lint/build); time-capped {f['time_capped']}; no mutants (empty) "
          f"{f['no_mutants']}; "
          f"{(time.time() - t0) / 60:.1f} min")
    print("score buckets: " + ", ".join(f"{k}: {n}" for k, n in f["buckets"].items()))
    print("survivors by class: " + ", ".join(f"{k} {n}" for k, n in sorted(f["survived_by_class"].items()))
          + " (arg_slot, stack: equivalent by the judge's design; logic: harness weakness)")
    print("by operator (killed/survived/dropped): " + "; ".join(
        f"{op} {c.get('killed', 0)}/{c.get('survived', 0)}/{c.get('dropped', 0)}" for op, c in f["by_op"].items()))
    surv = [(addr, s) for addr, r in sorted(recs.items()) for s in r.get("survivors") or []]
    if surv:
        print(f"logic survivors, first per operator (of {len(surv)} survivors):")
        seen = set()
        for addr, s in surv:
            if s.get("cls", "logic") != "logic" or s["op"] in seen:
                continue
            seen.add(s["op"])
            print(f"  {addr} {s['op']} line {s['line']}: {s['what']}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"project": str(ws), "settings": mset, "figure": f,
                                                    "functions": recs}, indent=1), encoding="utf-8")
    if not a.report and recs:
        mutation.save(paths.STATE / proof.MUTATION_FILE, recs, mset)
        print(f"records merged into {paths.STATE / proof.MUTATION_FILE}")
    if a.gate:
        dead = sorted(x for x, r in recs.items() if not r.get("error") and r.get("generated") and not r.get("killed"))
        print(f"gate: {len(recs) - f['errors'] - len(dead)}/{len(recs)} pass (baseline PASS, a mutant killed)"
              + (f"; no mutant killed: {' '.join(dead)}" if dead else "")
              + (f"; baseline errors: {' '.join(x for x, r in sorted(recs.items()) if r.get('error'))}"
                 if f["errors"] else ""))
        return 1 if f["errors"] or dead or not recs else 0
    return 1 if f["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

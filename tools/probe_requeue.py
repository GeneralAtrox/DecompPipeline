"""Seeded requeue on a private copy: agent attempts for functions whose harness tools/oracle_probe.py made in its work
dir, judged on that private harness. Proves an oracle change end to end (vectors -> a matching C) without touching
the live project: no ledger, harness, packet, run or matched file of the live project is read for writing.

usage: python tools/bounded.py 7200 python tools/probe_requeue.py --project DIR --tag T [--attempts N]
           [--seed ADDR=FILE ...] [--note TEXT] [--max-cost USD] [--json OUT] ADDR... | @FILE
  --tag       the probe's tag (tests/.work/probe-T): the harnesses must be there, made by the current working tree
  --seed      start the first attempt from FILE (e.g. the live best attempt's work.c), judged first like an import:
              a seed that passes the private harness is a match with no agent run
  --note      text added to the first attempt's task (why the function is requeued)
  --attempts  agent attempts per function (default 2); --max-cost stops starting new attempts past USD spent
The sandbox's check.py runs `probe_requeue.py check` (the dev set on the private harness), so the agent iterates on
the same vectors the official verdict uses. Prints one line per attempt and a total: matched / tried and cost.
"""
import argparse
import json
import os
import pathlib
import stat
import sys
import threading
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tools"))
import regress_project as rp  # noqa: E402

LAUNCHER = """# Runs the dev test set on work.c. The official verdict (dev + hidden) is computed after your session.
import subprocess, sys
sys.exit(subprocess.call([sys.executable, r"{me}", "--project", r"{project}", "--tag", "{tag}", "check", "{addr}",
                          "--work", r"{work}"]))
"""


def setup(project, tag):
    # catalog=True: keep the probe's catalog and code ranges (a probe --catalog rebuilt them; the C runner's rule R8
    # must read the same code_ranges.txt as the oracle that made the harness), never the live project's
    ns = argparse.Namespace(project=project, catalog=True, work=str(TOOL / "tests" / ".work" / f"probe-{tag}"),
                            ghidra_work=str(TOOL / "tests" / "ghidra_work" / f"probe_{tag}"))
    paths, ghidra, check, cfg, live = rp.setup(ns)
    work = pathlib.Path(ns.work).resolve()
    paths.SANDBOX, paths.RUNS = work / "sandbox", work / "runs"
    return paths, check, cfg, live, work


def judge(check, cfg, work, addr, cfile, sets=("dev", "hidden"), sub="official"):
    return check.run_check(cfg, addr, str(cfile), sets=sets, build_dir=work / "build" / addr / sub,
                           harness_dir=work / "harness" / addr)


def cmd_check(a):
    paths, check, cfg, live, work = setup(a.project, a.tag)
    res = judge(check, cfg, work, a.addr.lower(), a.work, sets=("dev",), sub="agent")
    print(check.format_for_agent(res))
    return 0 if res["verdict"] == "PASS" else 1


def main():
    if len(sys.argv) > 1 and "check" in sys.argv[1:]:
        ap = argparse.ArgumentParser()
        ap.add_argument("--project", required=True)
        ap.add_argument("--tag", required=True)
        ap.add_argument("cmd")
        ap.add_argument("addr")
        ap.add_argument("--work", required=True)
        return cmd_check(ap.parse_args())
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--attempts", type=int, default=2)
    ap.add_argument("--seed", action="append", default=[], metavar="ADDR=FILE")
    ap.add_argument("--note", default="")
    ap.add_argument("--max-cost", type=float, default=20.0)
    ap.add_argument("--json")
    ap.add_argument("addrs", nargs="+")
    a = ap.parse_args()
    paths, check, cfg, live, work = setup(a.project, a.tag)
    from core import agent
    todo = []
    for x in a.addrs:
        todo += pathlib.Path(x[1:]).read_text(encoding="utf-8").split() if x.startswith("@") else [x]
    todo = [x.lower() for x in dict.fromkeys(todo)]
    seeds = {k.lower(): pathlib.Path(v) for k, v in (s.split("=", 1) for s in a.seed)}
    spent, results, t0 = 0.0, [], time.time()

    def log(msg):
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    for addr in todo:
        h = work / "harness" / addr
        meta, summary = paths.read_json(h / "meta.json"), paths.read_json(h / "oracle_summary.json") or {}
        r = {"addr": addr, "attempts": []}
        results.append(r)
        if not meta or meta.get("unsupported") or not (h / "vectors.txt").exists():
            r["status"] = "no harness" if not meta else f"refused: {(meta.get('unsupported') or '')[:120]}"
            log(f"{addr}: {r['status']}")
            continue
        prev_work, previous = None, ""
        if addr in seeds:
            res = judge(check, cfg, work, addr, seeds[addr])
            r["seed"] = f"{res['verdict']} {res['failing']}/{res['total']}"
            log(f"{addr}: seed {seeds[addr].name} -> {r['seed']}")
            if res["verdict"] == "PASS":
                r["status"] = "matched (seed)"
                continue
            prev_work = seeds[addr]
            previous = agent.PREVIOUS.format(n=0, summary=f"{res['failing']} of {res['total']} vectors differ",
                                             details=check.format_for_agent({**res, "verdict": "FAIL"}))
        if a.note:
            previous += f"\nWhy this function is tried again: {a.note}\n"
        r["status"] = "failed"
        for n in range(1, a.attempts + 1):
            if spent >= a.max_cost:
                r["status"] = "not tried (cost cap)"
                break
            run_dir = paths.RUNS / addr / f"attempt-{n:02d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            box, task = agent.build_sandbox(cfg, addr, meta, summary, prev_work, previous)
            launcher = box / "check.py"
            os.chmod(launcher, stat.S_IWRITE)
            launcher.write_text(LAUNCHER.format(me=pathlib.Path(__file__).resolve(), project=a.project, tag=a.tag,
                                                addr=addr, work=box / "work.c"), encoding="utf-8")
            os.chmod(launcher, stat.S_IREAD)
            sess = agent.run_attempt(cfg, box, task, run_dir / "transcript.jsonl", lambda m: None, threading.Event(),
                                     system=agent.raw_system_prompt(cfg))
            spent += sess.get("cost_usd") or 0
            wf = run_dir / "work.c"
            wf.write_bytes((box / "work.c").read_bytes() if (box / "work.c").exists() else b"")
            res = judge(check, cfg, work, addr, wf)
            att = {"n": n, "verdict": res["verdict"], "failing": f"{res['failing']}/{res['total']}",
                   "stage": res["stage"], "cost_usd": sess.get("cost_usd"), "api_error": sess.get("api_error")}
            r["attempts"].append(att)
            log(f"{addr}: attempt {n} -> {res['verdict']} {att['failing']} ({res['stage']}) ${att['cost_usd'] or 0:.3f}")
            if res["verdict"] == "PASS":
                r["status"] = f"matched (attempt {n})"
                break
            prev_work = wf
            previous = agent.PREVIOUS.format(n=n, summary=f"{res['failing']} of {res['total']} vectors differ",
                                             details=check.format_for_agent({**res, "verdict": "FAIL"}))
    matched = sum(1 for r in results if r.get("status", "").startswith("matched"))
    log(f"done: {matched} of {len(results)} matched; ${spent:.2f} spent")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"results": results, "cost_usd": round(spent, 4)}, indent=1),
                                        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

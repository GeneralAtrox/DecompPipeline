"""Standing mutation audit (docs/PROOF_GATES.md section 2b): no judge or oracle change may lower the kill rate.

A frozen sample of matched functions per workspace (tools/mutation_audit/sample.json {key: [addr...]}, drawn once,
seeded), each with up to MUTANTS mutants of its matched C (core/mutation.generate, seed 1), judged by the official
check on that workspace's harnesses (tools/mutation_check.py --report: nothing in a workspace is written). The
baseline (tools/mutation_audit/baseline.json) keeps every mutant's outcome (core/mutation "outcomes") per function,
with the matched C's hash. The gate compares a run with it mutant by mutant:
- a lost kill (a mutant the baseline killed that the run did not) fails the gate;
- a flip (the matched C itself no longer passes its harness: a stricter judge or new vectors found it wrong) and a
  stale function (its matched C changed since the baseline: other mutants) are listed, not failed: flips are the
  judge change's own report (each one a decision), stale ones need a new baseline entry;
- gained kills are counted: the kill rate per workspace before and after.
A baseline only moves up: `baseline` refuses a run with a lost kill. Which heads need the audit: `needed` (a change
to the judge, a runtime or a target's locked oracle scripts).

usage:
  python tools/mutation_audit.py sample --ws KEY=DIR ... [--n 150] [--seed 1]       (re)draw sample.json
  python tools/bounded.py 21600 python tools/mutation_audit.py run --ws KEY=DIR ... --out RUN.json [--workers 4]
  python tools/mutation_audit.py gate RUN.json [--baseline FILE]                     exit 1 on a lost kill
  python tools/mutation_audit.py baseline RUN.json                                   RUN becomes the baseline
  python tools/mutation_audit.py needed BASE HEAD [--repo DIR]                       exit 0: the head needs the audit
KEY=DIR: a workspace (a project folder) under the sample's key; for a judge change's proof, a private copy of it with
the new harnesses. Keys not given are left out of a run (and reported as missing by the gate).
"""
import argparse
import json
import pathlib
import random
import subprocess
import sys
import tempfile

TOOL = pathlib.Path(__file__).resolve().parents[1]
HERE = TOOL / "tools" / "mutation_audit"
MUTANTS = 10       # mutants per function
TIME_CAP_S = 3600  # per function: the audit judges every mutant (a time cap would leave some untried)
JUDGE_PATHS = ("core/compare.py", "core/check.py", "core/readable_check.py", "core/mutation.py",
               "core/mutation_ops.py", "core/ghidra.py", "targets/gamecube_ppc/ppc_compare.py",
               "targets/gamecube_ppc/ppc_check.py")
RUNTIME_DIRS = ("runtime/", "runtime_rd/")


def load(p, default=None):
    p = pathlib.Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def workspaces(pairs):
    out = {}
    for x in pairs or []:
        k, _, d = x.partition("=")
        if not d:
            raise SystemExit(f"--ws {x!r}: KEY=DIR")
        out[k] = pathlib.Path(d)
    return out


def matched(ws):
    led = (load(ws / "state" / "ledger.json", {}) or {}).get("functions") or {}
    return sorted(a for a, r in led.items() if r.get("status") == "matched" and (ws / "matched" / f"{a}.c").exists()
                  and (ws / "harness" / a / "expected.txt").exists())


def draw(ws, n, seed):
    m = matched(ws)
    return m if len(m) <= n else sorted(random.Random(seed).sample(m, n))


def run(wss, sample, workers, timeout_s):
    """{key: {addr: {"sha", "outcomes"} or {"error"}}} for the sample's functions in each given workspace."""
    out = {}
    for key, ws in wss.items():
        addrs = sample.get(key) or []
        if not addrs:
            continue
        with tempfile.TemporaryDirectory(prefix="maudit_") as tmp:
            j = pathlib.Path(tmp) / "run.json"
            cmd = [sys.executable, "-B", str(TOOL / "tools" / "mutation_check.py"), "--project", str(ws), "--report",
                   "--json", str(j), "--max-mutants", str(MUTANTS), "--time-cap", str(TIME_CAP_S), "--workers",
                   str(workers), "--build-dir", str(pathlib.Path(tmp) / "b")] + addrs
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            recs = (load(j, {}) or {}).get("functions") or {}
            if not recs:
                raise SystemExit(f"{key}: mutation_check made no records (exit {r.returncode}): {r.stdout[-800:]}"
                                 f"{r.stderr[-800:]}")
        out[key] = {a: ({"error": rec["error"][:200]} if rec.get("error") else
                        {"sha": rec["src_sha256"], "outcomes": rec.get("outcomes", "")}) for a, rec in recs.items()}
    return out


def compare(base, cur):
    """The gate's figures per key and its verdict: {"keys": {key: {...}}, "lost": [(key, addr, id)], "ok"}."""
    keys, lost = {}, []
    for key, fs in sorted(base.items()):
        k = {"functions": len(fs), "missing": [], "stale": [], "flips": [], "mutants": 0, "killed_base": 0,
             "killed_run": 0, "gained": 0, "lost": 0}
        got = cur.get(key) or {}
        for addr, b in sorted(fs.items()):
            r = got.get(addr)
            if b.get("error"):  # no baseline outcomes for it (its own baseline did not pass): nothing to compare
                continue
            if r is None:
                k["missing"].append(addr)
                continue
            if r.get("error"):
                k["flips"].append(addr)
                continue
            if r["sha"] != b["sha"] or len(r["outcomes"]) != len(b["outcomes"]):
                k["stale"].append(addr)
                continue
            for i, (x, y) in enumerate(zip(b["outcomes"], r["outcomes"]), 1):
                if x in "LB":  # dropped by lint or build: no run to compare
                    continue
                k["mutants"] += 1
                k["killed_base"] += x == "K"
                k["killed_run"] += y == "K"
                if x == "K" and y != "K":
                    k["lost"] += 1
                    lost.append((key, addr, i))
                k["gained"] += x != "K" and y == "K"
        for name in ("base", "run"):
            k[f"rate_{name}"] = round(100.0 * k[f"killed_{name}"] / k["mutants"], 1) if k["mutants"] else None
        keys[key] = k
    missing = sum(len(k["missing"]) for k in keys.values())
    return {"keys": keys, "lost": lost, "ok": not lost and not missing}


def report(res):
    lines = []
    for key, k in res["keys"].items():
        lines.append(f"{key}: {k['functions']} functions, {k['mutants']} mutants compared; killed {k['killed_base']} "
                     f"-> {k['killed_run']} ({k['rate_base']}% -> {k['rate_run']}%), gained {k['gained']}, lost "
                     f"{k['lost']}; flips {len(k['flips'])}, stale {len(k['stale'])}, missing {len(k['missing'])}"
                     + (f"; flips: {' '.join(k['flips'][:20])}" if k["flips"] else ""))
    lines.append("gate: " + ("PASS" if res["ok"] else "FAIL") + (f" ({len(res['lost'])} lost kills: "
                 + ", ".join(f"{k} {a} m{i:02d}" for k, a, i in res["lost"][:20]) + ")" if res["lost"] else ""))
    return "\n".join(lines)


def at_stake(files, locked):
    """The files that put the kill rate at stake: the judge, a target's runtime or target.json, a locked script."""
    out = []
    for f in files:
        parts = f.split("/")
        if f in JUDGE_PATHS or f in locked or (len(parts) > 3 and parts[0] == "targets"
                                                and f"{parts[2]}/" in RUNTIME_DIRS):
            out.append(f)
        elif len(parts) == 3 and parts[0] == "targets" and parts[2] == "target.json":
            out.append(f)
    return out


def needed(base, head, repo):
    """at_stake over the files of base..head (empty: no audit needed)."""
    r = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", f"{base}..{head}"], capture_output=True,
                       text=True, timeout=120, check=True)
    locked = set()
    for t in (pathlib.Path(repo) / "targets").glob("*/target.json"):
        cfg = load(t, {}) or {}
        dirs = [f"targets/{t.parent.name}/ghidra_scripts"]  # and its shared folders (core/paths.target_scripts)
        dirs += [f"core/ghidra_shared/{d}" for d in cfg.get("shared_scripts") or []]
        for s in cfg.get("locked_scripts") or []:
            locked.update(f"{d}/{s}" for d in dirs)
    return at_stake(r.stdout.split(), locked)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--ws", action="append")
    s.add_argument("--n", type=int, default=150)
    s.add_argument("--seed", type=int, default=1)
    r = sub.add_parser("run")
    r.add_argument("--ws", action="append", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--timeout", type=int, default=6 * 3600, help="seconds per workspace")
    g = sub.add_parser("gate")
    g.add_argument("run")
    g.add_argument("--baseline", default=str(HERE / "baseline.json"))
    b = sub.add_parser("baseline")
    b.add_argument("run")
    n = sub.add_parser("needed")
    n.add_argument("base")
    n.add_argument("head")
    n.add_argument("--repo", default=str(TOOL))
    a = ap.parse_args()
    if a.cmd == "sample":
        sample = load(HERE / "sample.json", {}) or {}
        for key, ws in workspaces(a.ws).items():
            sample[key] = draw(ws, a.n, a.seed)
            print(f"{key}: {len(sample[key])} of {len(matched(ws))} matched")
        HERE.mkdir(exist_ok=True)
        (HERE / "sample.json").write_text(json.dumps(sample, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    if a.cmd == "run":
        res = run(workspaces(a.ws), load(HERE / "sample.json", {}) or {}, a.workers, a.timeout)
        pathlib.Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{a.out}: " + ", ".join(f"{k} {len(v)}" for k, v in res.items()))
        return 0
    if a.cmd == "gate":
        res = compare(load(a.baseline, {}) or {}, load(a.run, {}) or {})
        print(report(res))
        return 0 if res["ok"] else 1
    if a.cmd == "baseline":
        cur, old = load(a.run, {}) or {}, load(HERE / "baseline.json")
        if old is not None:
            res = compare(old, cur)
            print(report(res))
            if res["lost"]:
                print("refused: the baseline only moves up (a lost kill)")
                return 1
            cur = {k: dict(old.get(k) or {}, **v) for k, v in {**old, **cur}.items()}
        (HERE / "baseline.json").write_text(json.dumps(cur, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline: {sum(len(v) for v in cur.values())} functions")
        return 0
    files = needed(a.base, a.head, a.repo)
    print("\n".join(files) if files else "no judge, runtime or locked oracle change: the audit is not needed")
    return 0 if files else 1


if __name__ == "__main__":
    sys.exit(main())

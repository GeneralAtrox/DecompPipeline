"""The translator stage (core/translate_stage, docs/TRANSLATOR_STAGE.md) over the harnesses a workspace already has:
what the stage would have done for each function, and with --record what a worker does at a translated PASS. Never
starts Ghidra (its prepare and slot raise here): only functions with harness/ADDR/meta.json are tried, and one
whose oracle made too few valid vectors is reported NOVECTORS untried (a worker skips it before any attempt). Each
row says whether its harness is current (core/oracle_fresh.scan) or what a prepare now would change.

usage: python tools/bounded.py 3600 python tools/translate_backlog.py --project WS [--only a,b | @FILE] [--jobs N]
           [--record | --keep DIR] [--json OUT]
  Without --record the attempts' runs/ and build/ go to a folder of the tool's own (removed at the end; --keep DIR
  keeps them there), not the workspace's build/ADDR/official, which a live worker uses too: no private copy needed.
  --record  a PASS of a function not yet matched, on a current harness and not unsupported, is finished as a worker
            finishes it (core/job.finish: matched/ADDR.c, proof gates, ledger "source": "translated"; the row's
            earlier attempts kept); a FAIL is kept in the row's "translated_fail" (core/translate_stage), so the
            job does not translate and judge the same C again on the same inputs. Refused while the project's
            server answers (the server owns the ledger): run it on a private copy, or with the server stopped.
  --json    one row per function: verdict, translate_s, judge_s, the first differences or why there was no C, and the
            ledger's agent figures for it before this run (attempts, tokens, cost, agent seconds), for comparison.
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time

TOOL = pathlib.Path(__file__).resolve().parents[1]


class O:
    """Just what core/translate_stage.attempt, core/job.judge and core/job.finish use of an Orchestrator."""

    def __init__(self, paths, filelock, cfg):
        self.paths, self.filelock, self.cfg = paths, filelock, cfg
        self.ledger = paths.read_json(paths.LEDGER, {"loop": False, "functions": {}})
        self.lock, self.proof_states = threading.RLock(), {}

    def rec(self, addr):
        with self.lock:
            return self.ledger["functions"].setdefault(addr, {"addr": addr, "status": "queued", "attempts": []})

    def save(self):
        with self.lock, self.filelock.locked(self.paths.LEDGER):
            self.filelock.write_atomic(self.paths.LEDGER, self.filelock.dumps_rows(self.ledger))

    def log(self, msg):
        print(msg, flush=True)


class Probe:
    """An O whose rec is a scratch row: the attempt's own writes stay out of the ledger."""

    def __init__(self, o):
        self.cfg, self.log, self.row = o.cfg, o.log, {"status": "queued", "attempts": []}

    def rec(self, addr):
        return self.row

    def save(self):
        pass


class W:
    id, stop_reason = 0, "stopped"

    def __init__(self):
        self.stop_flag = threading.Event()

    def set_phase(self, *a):
        pass


def agent_figures(rec):
    """The agent's own attempts in a ledger row (attempt 0 imports, earlier C and translations left out)."""
    atts = [a for a in (rec or {}).get("attempts") or [] if a.get("model") not in ("import", "previous", "translated")]
    return {"status": (rec or {}).get("status"), "agent_attempts": len(atts),
            "agent_tokens_in": sum(a.get("tokens_in") or 0 for a in atts),
            "agent_tokens_out": sum(a.get("tokens_out") or 0 for a in atts),
            "agent_cost_usd": round(sum(a.get("cost_usd") or 0 for a in atts), 6),
            "agent_s": round(sum(a.get("duration_s") or 0 for a in atts), 1)}


def no_ghidra(*a, **k):
    raise RuntimeError("translate_backlog never starts Ghidra")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--keep", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    if a.record and a.keep:
        sys.exit("--keep is for a report: --record finishes PASSes from the workspace's own runs/")
    os.environ["DECOMP_PROJECT"] = str(pathlib.Path(a.project).resolve())
    sys.path[:0] = [str(TOOL), str(TOOL / "tools")]
    if a.record:
        from common import server_state
        if server_state(pathlib.Path(a.project).resolve(), timeout=3) is not None:  # the server owns the ledger
            sys.exit("the project's server is running: stop it first (--record writes the ledger)")
    from core import check, filelock, ghidra, job, oracle_fresh, paths, translate_stage
    ghidra.prepare = ghidra.slot = no_ghidra
    out = None
    if not a.record:  # a report's runs and builds: its own folder (core/job and check read paths.RUNS/BUILD per call)
        out = pathlib.Path(a.keep).resolve() if a.keep else pathlib.Path(tempfile.mkdtemp(prefix="dp_backlog_"))
        paths.RUNS, paths.BUILD = out / "runs", out / "build"
    try:
        report(a, paths, filelock, check, job, oracle_fresh, translate_stage)
    finally:
        if out is not None and not a.keep:
            shutil.rmtree(out, ignore_errors=True)


def report(a, paths, filelock, check, job, oracle_fresh, translate_stage):
    cfg = paths.config()
    st, why = translate_stage.settings(cfg)
    if st is None:
        sys.exit(f"translator stage is off for this workspace: {why}")
    addrs = sorted(p.parent.name for p in paths.HARNESS.glob("*/meta.json"))
    if a.only:
        want = (pathlib.Path(a.only[1:]).read_text(encoding="utf-8").split() if a.only.startswith("@")
                else a.only.split(","))
        addrs = [x for x in addrs if x in {paths.norm_addr(v) for v in want}]
    o = O(paths, filelock, cfg)
    before = {x: agent_figures(o.ledger["functions"].get(x)) for x in addrs}
    env = check.msvc_env(cfg)  # the shared runtime objects once, before parallel builds race to make them
    cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
    if cl:
        check.runtime_objs(cl, env)

    stale = oracle_fresh.scan(cfg, addrs)  # {addr: (kind, detail)} of harnesses a prepare now would change
    mv = cfg["min_valid_vectors"]

    def one(addr):
        w, t0 = W(), time.monotonic()
        summary = paths.read_json(paths.HARNESS / addr / "oracle_summary.json") or {}
        harness = " ".join(stale[addr]) if addr in stale else "current"
        if summary.get("dev_valid", 0) < mv or summary.get("hidden_valid", 0) < mv:  # job.investigate skips it
            return dict(addr=addr, **before[addr], harness=harness, verdict="NOVECTORS",
                        why=f"oracle made fewer than {mv} valid vectors per set: no attempt, re-prepare first")
        probe = Probe(o)  # the attempt writes its row here; only --record's PASS reaches the ledger
        att = translate_stage.attempt(probe, w, addr, job.judge)
        tx = probe.row.get("translator") or {}
        row = dict(addr=addr, **before[addr], harness=harness, translator=tx)
        if att is None:
            return dict(row, verdict="NOTRANSLATE", why=tx.get("why"))
        d = att.get("first_diffs") or []
        row.update(verdict=att["verdict"], stage=att["stage"], score=att["score"], summary=att["summary"],
                   translate_s=att["duration_s"], judge_s=att["judge_s"], lint=att["lint"][:3],
                   build=att["build"][:4], run_error=att.get("run_error"), diff=d[0]["diffs"][:4] if d else [],
                   coverage=att.get("coverage"), wall_s=round(time.monotonic() - t0, 2))
        meta = paths.read_json(paths.HARNESS / addr / "meta.json") or {}
        if (a.record and att["verdict"] == "PASS" and before[addr]["status"] != "matched"
                and addr not in stale and not meta.get("unsupported")):  # a worker judges on a current harness only
            rec = o.rec(addr)
            rec.update(attempts=(rec.get("attempts") or []) + [att], translator=tx, started=att["started"])
            job.finish(o, addr, "matched", f"PASS: translated by {att['translator']} {att['translator_rev']}, "
                                           f"no agent run" + job.weak_note(att))
            row["recorded"] = o.rec(addr).get("source")
        elif (a.record and probe.row.get("translated_fail") and before[addr]["status"] != "matched"
                and addr not in stale and not meta.get("unsupported")):  # the job takes it on these inputs
            with o.lock:
                o.rec(addr)["translated_fail"] = probe.row["translated_fail"]
            o.save()
            row["recorded"] = "translated_fail"
        return row

    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(max(1, a.jobs)) as ex:
        rows = list(ex.map(one, addrs))
    total = round(time.monotonic() - t0, 1)
    from collections import Counter
    c = Counter(r["verdict"] for r in rows)
    print(f"{paths.ROOT.name}: {len(rows)} functions with a harness, {dict(c)} in {total} s "
          f"({st[0]} {translate_stage.revision(st[2])}, {a.jobs} jobs)")
    for r in rows:
        if r["verdict"] != "PASS":
            print(" ", r["addr"], r["status"], r["verdict"], r["harness"], r.get("summary") or r.get("why") or "",
                  r.get("lint") or "", r.get("build") or "", r.get("run_error") or "", r.get("diff") or "")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"workspace": str(paths.ROOT), "translator": st[0],
                                                    "rev": translate_stage.revision(st[2]), "jobs": a.jobs,
                                                    "wall_s": total, "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

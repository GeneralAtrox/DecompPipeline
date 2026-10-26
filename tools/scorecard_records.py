"""The readers of the records tools/scorecard.py needs that DP keeps for it alone (docs/DEFINITION_OF_DONE.md): the
per-worker busy/idle log (core/busylog, gate 3), the manual operator steps (core/oplog, gate 2) and the job registry
(tools/jobreg.py: heavy jobs run, reused, refused, cross-checked, and started outside it). Read-only.
"""
import collections
import json
import pathlib


BUSY = {"p": "prepare", "t": "translate", "a": "agent", "j": "judge"}  # core/busylog's codes


def busy_totals(state):
    """Worker time from core/busylog's state/worker_busy/*.log: {"files", "lives", "alive_s", "busy_s", "by": {code:
    s}, "open"}, None when there is no log. Only lives that wrote their end ("-") count: a worker a killed process
    left open (no end, or started again without one) lost its idle time after its last event, so counting it would
    make idle look smaller; "open" says how many were left out."""
    d = pathlib.Path(state) / "worker_busy"
    files = sorted(d.glob("*.log")) if d.is_dir() else []
    if not files:
        return None
    t = {"files": len(files), "lives": 0, "alive_s": 0.0, "busy_s": 0.0, "by": {}, "open": 0}

    def work_end(w, at):
        if w[1] is not None:
            s = max(0.0, at - w[2])
            w[4][w[1]] = w[4].get(w[1], 0.0) + s
            w[1] = None

    for f in files:
        live = {}  # worker -> [up at, open work code, its start, last event at, {code: busy s}]

        def down(k, at, ended=True):
            w = live.pop(k)
            if not ended:
                t["open"] += 1
                return
            work_end(w, at)
            t["alive_s"] += max(0.0, at - w[0])
            t["lives"] += 1
            for c, s in w[4].items():
                t["busy_s"] += s
                t["by"][c] = t["by"].get(c, 0.0) + s
        with open(f, encoding="ascii", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                try:
                    at, k, c = float(parts[0]), parts[1], parts[2]
                except (ValueError, IndexError):
                    continue
                if c == "+":
                    if k in live:
                        down(k, at, ended=False)
                    live[k] = [at, None, None, at, {}]
                    continue
                w = live.get(k)
                if w is None:
                    continue
                w[3] = at
                if c == "-":
                    down(k, at)
                elif c == ".":
                    work_end(w, at)
                else:
                    work_end(w, at)
                    w[1], w[2] = c, at
        for k in list(live):
            down(k, live[k][3], ended=False)
    return t


OPLOG = "operator_steps.jsonl"  # core/oplog.FILE


def oplog_records(state):
    """core/oplog's records in state/operator_steps.jsonl, or None when the file does not exist."""
    p = pathlib.Path(state) / OPLOG
    if not p.is_file():
        return None
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("kind") in ("start", "stop", "act"):
            out.append(r)
    return out


def whole_run(logs):
    """A run id with records in every one of `logs` (each workspace of a game, and its port folder): an end-to-end
    run, as far as the logs show. False when a log is empty or no run reaches them all."""
    runs = [{r.get("run") for r in log if r.get("run")} for log in logs]
    return bool(runs) and bool(set.intersection(*runs))


def operator_steps(logs):
    """Manual steps from oplog_records() lists (a record a driver wrote into several of them counts once): every
    start after the first (each another hand-started driver), every stop and every act."""
    recs = {tuple(str(r.get(k)) for k in ("at", "kind", "tool", "step", "run")): r for log in logs for r in log}
    n = {k: sum(1 for r in recs.values() if r["kind"] == k) for k in ("start", "stop", "act")}
    n["steps"] = max(0, n["start"] - 1) + n["stop"] + n["act"]
    n["since"] = min((r.get("at") or "" for r in recs.values()), default=None)
    return n


def job_days(recs):
    """Per day, from tools/jobreg.py's records: {day: {"run", "run_h", "reused", "saved_h", "refused", "refused_h",
    "cross_checks", "outside", "duplicates"}}. saved_h: the run time of the finished job each reuse pointed to;
    refused_h: the run time of the failed or timed-out job a refusal pointed to (a refusal of a running duplicate
    counts, with no hours: how long the copy would have run is unknown); outside: heavy jobs the hook saw start
    outside the wrapper; duplicates (past the guard): a job started outside the wrapper whose command, limits left
    out, was already started (through the wrapper or outside it), and a wrapped start of inputs that had finished
    before (none unless two registries were in use)."""
    import jobreg
    days = collections.defaultdict(lambda: {"run": 0, "run_h": 0.0, "reused": 0, "saved_h": 0.0, "refused": 0,
                                            "refused_h": 0.0, "cross_checks": 0, "outside": 0, "duplicates": 0})
    js = jobreg.jobs(recs)
    seen, done = set(), set()

    def norm(cmd):
        return jobreg._split_limits(str(cmd).split())[0]

    for r in recs:
        day = str(r.get("started") or r.get("at") or "")[:10] or "unknown"
        d, ev = days[day], r.get("event")
        if ev == "start":
            j = js.get(r.get("id")) or {}
            d["run"] += 1
            d["run_h"] += ((j.get("end") or {}).get("seconds") or 0) / 3600
            d["cross_checks"] += bool(r.get("cross_check"))
            d["duplicates"] += r.get("inputs") in done and not r.get("cross_check")
            seen.update(norm(h[2]) for h in jobreg.classify(r.get("command") or "") or [(0, 0, r.get("command"))])
        elif ev == "end" and r.get("status") == "done":
            done.add((js.get(r.get("id")) or {}).get("inputs"))
        elif ev == "reuse":
            d["reused"] += 1
            d["saved_h"] += (r.get("saved_s") or 0) / 3600
        elif ev == "refuse":
            d["refused"] += 1
            d["refused_h"] += (((js.get(r.get("of")) or {}).get("end") or {}).get("seconds") or 0) / 3600
        elif ev == "outside" and not r.get("denied"):
            d["outside"] += 1
            for c in r.get("jobs") or []:
                d["duplicates"] += norm(c) in seen
                seen.add(norm(c))
    return {k: {f: round(v, 2) if isinstance(v, float) else v for f, v in d.items()} for k, d in sorted(days.items())}

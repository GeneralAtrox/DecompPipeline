"""Proof backlog (core/proof.py, docs/PROOF_GATES.md "Rollout"): the matched functions the proof gates flag, queued for
more test inputs, and the switch from report-only to enforce once a project's backlog is clear.

usage:
  python tools/proof_backlog.py queue   --project DIR [--project DIR ...] [--out DIR] [--dry] [--mutation FILE]
  python tools/proof_backlog.py status  --project DIR [--project DIR ...] [--mutation FILE] [--check]
  python tools/proof_backlog.py enforce --project DIR [--project DIR ...] (on | off) [--apply]

queue    Writes each project's backlog to state/proof_backlog/ (or --out DIR/<label>/; --dry: print only):
         backlog.json (every entry with its reason) and one @FILE queue per kind, closest to the gate first:
           reach.txt    below the coverage gate, with a call site no vector reaches and no reach pass tried yet:
                        tools/bounded.py 14400 python tools/call_coverage.py --project DIR reach @.../reach.txt
           vectors.txt  below the coverage gate otherwise: unreached blocks without an untried call site (more vectors,
                        the watcher's range/ptr hints, or a waive in project.json coverage_gate.waive with its reason)
           harness.txt  weak_harness: logic survivors of the mutation check (state/mutation.json or --mutation FILE,
                        a tools/mutation_check.py --json report): hints that separate the surviving mutant
           no_data.txt  no block counts in the oracle summary (re-prepare)
         The queued work is Ghidra work (the reach pass, re-prepares): run it only in free Ghidra slots, servers
         stopped, under the machine's RAM rules. This tool itself runs no Ghidra and builds nothing.
status   One line per project: the coverage gate's mode (enforce or report only), the backlog by kind, and CLEAR
         when no function is below the coverage gate: the signal to switch the project to enforce
         (`enforce --project DIR on --apply`). Weak harnesses and no-data functions are listed, not counted against
         CLEAR (the mutation check is report-only unless mutation_check.mode is enforce). --check: exit 1 unless
         every project is clear.
enforce  The project's coverage_gate.enforce in its project.json (key by key over config.json, whose default is
         true: a new game enforces from the start). off: report only while an existing game works down its backlog
         (its final audit's proof check is INFO; the functions stay matched_low_coverage and in the backlog). Prints
         the change; --apply writes project.json (only that key; the rest of the file as it was).
Exit 0 (status --check: 1 while a project is not clear; enforce: 1 when a project.json cannot be read).
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import workspace  # noqa: E402
from core import proof  # noqa: E402  (pure: no core.paths)

KINDS = ("reach", "vectors", "harness", "no_data")


def _untried_sites(summary):
    """Call sites no valid vector reaches, when no reach pass has run on this harness yet (core/call_coverage)."""
    if not summary or ((summary.get("reach") or {}).get("runs") or 0) > 0:
        return []
    return sorted(s for s, n in (summary.get("call_sites") or {}).items() if not n)


def backlog(ws, mutation_file=None):
    """[{"addr", "kind", "deficit", "reason", ...}] of one workspace, closest to the gate first per kind."""
    ws = pathlib.Path(ws)
    cfg = proof.project_cfg(ws)
    rows = proof.scan(ws, cfg=cfg)
    mset = proof.mutation_settings(cfg)
    muts = (json.loads(pathlib.Path(mutation_file).read_text(encoding="utf-8")).get("functions") or {}
            if mutation_file else {})
    out = []
    for a, r in sorted(rows.items()):
        c = r["coverage"]
        if c.get("ok") is None:
            out.append({"addr": a, "kind": "no_data", "deficit": 0, "reason": c.get("why", "no block data")})
        elif c["ok"] is False:
            sites = _untried_sites(proof.read_summary(ws / "harness", a))
            out.append({"addr": a, "kind": "reach" if sites else "vectors", "deficit": c["uncovered"] - c["allowed"],
                        "blocks_total": c["blocks_total"], "uncovered": c["uncovered"], "allowed": c["allowed"],
                        "untried_call_sites": len(sites), "unhit_blocks": c.get("unhit") or [],
                        "reason": proof.describe(r) + (f"; {len(sites)} call site(s) never reached, reach pass "
                                                       "not tried" if sites else "")})
        m = muts.get(a) or r["mutation"]
        if proof.mutation_flag(m, mset) == "weak":
            logic = (m.get("survived_by_class") or {}).get("logic", m.get("survived", 0))
            out.append({"addr": a, "kind": "harness", "deficit": logic, "score": m.get("score"),
                        "reason": f"weak_harness: {logic} logic survivor(s), mutation score {m.get('score')}"})
    out.sort(key=lambda e: (KINDS.index(e["kind"]), e["deficit"], e["addr"]))
    return out, rows, cfg


def label(ws):
    """A short, unique name for a workspace: its project.json name (+ the folder, unless it is decomp/)."""
    name = json.loads((ws / "project.json").read_text(encoding="utf-8")).get("name") or ws.parent.name
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return slug if ws.name == "decomp" else f"{slug}_{ws.name}"


def counts(entries):
    return {k: sum(1 for e in entries if e["kind"] == k) for k in KINDS}


def status_line(ws, entries, cfg):
    gate = proof.coverage_settings(cfg)
    n = counts(entries)
    low = n["reach"] + n["vectors"]
    mode = "enforce" if gate["enforce"] else "report only"
    head = ("CLEAR: no function below the coverage gate" + (" (enforcing)" if gate["enforce"] else
            f"; switch it to enforce: python tools/proof_backlog.py enforce --project \"{ws}\" on --apply")
            if not low else f"{low} below the coverage gate (reach {n['reach']}, vectors {n['vectors']})")
    line = f"{label(ws)}: coverage gate {mode}; {head}; weak harnesses {n['harness']}, no block data {n['no_data']}"
    return line, not low


def cmd_queue(a):
    for p in a.project:
        ws = workspace(p)
        entries, rows, cfg = backlog(ws, a.mutation)
        line, _ = status_line(ws, entries, cfg)
        print(line)
        if a.dry:
            for e in entries[:a.top]:
                print(f"  {e['kind']:8s} {e['addr']}  {e['reason']}")
            continue
        d = pathlib.Path(a.out) / label(ws) if a.out else ws / "state" / "proof_backlog"
        d.mkdir(parents=True, exist_ok=True)
        (d / "backlog.json").write_text(json.dumps({"project": str(ws), "gate": proof.coverage_settings(cfg),
                                                    "counts": counts(entries), "matched": len(rows),
                                                    "entries": entries}, indent=1), encoding="utf-8")
        for k in KINDS:
            (d / f"{k}.txt").write_text("".join(f"{e['addr']}  # {e['reason']}\n" for e in entries if e["kind"] == k),
                                        encoding="utf-8")
        print(f"  queues in {d}: " + ", ".join(f"{k}.txt {n}" for k, n in counts(entries).items()))
    return 0


def cmd_status(a):
    clear = True
    for p in a.project:
        ws = workspace(p)
        entries, _, cfg = backlog(ws, a.mutation)
        line, ok = status_line(ws, entries, cfg)
        clear &= ok
        print(line)
    return 1 if a.check and not clear else 0


def set_enforce(text, on):
    """project.json text with coverage_gate.enforce set (the rest kept): (new text, old value or None)."""
    data = json.loads(text)
    gate = dict(data.get("coverage_gate") or {})
    old = gate.get("enforce")
    gate["enforce"] = on
    data["coverage_gate"] = gate
    indent = 2 if "\n  \"" in text else None
    return json.dumps(data, indent=indent, ensure_ascii=False) + ("\n" if text.endswith("\n") else ""), old


def cmd_enforce(a):
    on, rc = a.mode == "on", 0
    for p in a.project:
        ws = workspace(p)
        pj = ws / "project.json"
        try:
            text = pj.read_text(encoding="utf-8")
            new, old = set_enforce(text, on)
        except (OSError, ValueError) as e:
            print(f"{pj}: cannot read: {e}")
            rc = 1
            continue
        snippet = json.dumps({"coverage_gate": {"enforce": on}})
        if old == on:
            print(f"{pj}: coverage_gate.enforce is already {json.dumps(on)}")
        elif a.apply:
            pj.write_text(new, encoding="utf-8")
            print(f"{pj}: coverage_gate.enforce {json.dumps(old)} -> {json.dumps(on)} (written)")
        else:
            print(f"{pj}: would set {snippet} (now {json.dumps(old)}; config.json default "
                  f"{json.dumps(proof.coverage_settings()['enforce'])}); --apply writes it")
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    s = sub.add_parser("status")
    e = sub.add_parser("enforce")
    for x in (q, s, e):
        x.add_argument("--project", action="append", required=True)
    for x in (q, s):
        x.add_argument("--mutation")
    q.add_argument("--out")
    q.add_argument("--dry", action="store_true")
    q.add_argument("--top", type=int, default=20)
    s.add_argument("--check", action="store_true")
    e.add_argument("mode", choices=("on", "off"))
    e.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"queue": cmd_queue, "status": cmd_status, "enforce": cmd_enforce}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

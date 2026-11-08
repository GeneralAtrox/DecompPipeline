"""Proof backlog (core/proof.py, docs/PROOF_GATES.md "Rollout"): the matched functions the proof gates flag, queued for
more test inputs, and the switch from report-only to enforce once a project's backlog is clear.

usage:
  python tools/proof_backlog.py queue   --project DIR [--project DIR ...] [--out DIR] [--dry] [--mutation FILE]
  python tools/proof_backlog.py status  --project DIR [--project DIR ...] [--mutation FILE] [--check]
  python tools/proof_backlog.py enforce --project DIR [--project DIR ...] (on | off) [--apply]
  python tools/proof_backlog.py restatus --project DIR [--project DIR ...] [--apply]
  python tools/proof_backlog.py truth   --project DIR [--project DIR ...] [--no-stale] [--list FILE] [--apply]

queue    Writes each project's backlog to state/proof_backlog/ (or --out DIR/<label>/; --dry: print only):
         backlog.json (every entry with its reason) and one @FILE queue per kind, closest to the gate first:
           reach.txt    below the coverage gate, with a call site no vector reaches and no reach pass tried yet:
                        tools/bounded.py 14400 python tools/call_coverage.py --project DIR reach @.../reach.txt
           vectors.txt  below the coverage gate otherwise: unreached blocks without an untried call site (more vectors,
                        the watcher's range/ptr hints, or a waive in project.json coverage_gate.waive with its reason)
           harness.txt  weak_harness: logic survivors of the mutation check (state/mutation.json or --mutation FILE,
                        a tools/mutation_check.py --json report): hints that separate the surviving mutant
           no_data.txt  no block counts in the oracle summary (re-prepare)
           stores.txt   readable files `unproven` (state/readable.json, core/proof.readable_state): a store of the
                        original no valid vector runs. "untried" ones (no TcBound or reach pass on the harness) are the
                        oracle's to try: tools/call_coverage.py reach @FILE re-prepares them with the current flags;
                        "tried" ones need the watcher's hints or a waive. Then `refine.py verify` records them again
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
restatus Records each readable file's status again (core/proof.readable_state: readable, unproven, needs_review) from
         the harness, listing and attempts already in the workspace: no rejudge, no Ghidra, no compiler. Rows with
         another status or no readable/src file are left alone. Prints old -> new counts; --apply writes
         state/readable.json under its lock, one line per function (as core/refine writes it).
truth    Ledger truth: a raw `matched` row counts only while its latest recorded harness verdict is a PASS on a
         current harness. From the records already in the workspace (no rejudge, no Ghidra, no compiler), a matched row
         fails it when it is
           marked         already `unverified` (this check, a triage) or `unproven` (another gate's mark);
           verdict        its own verdict_detail is no PASS;
           harness_stale  state/readable.json holds the readable stage's raw judge FAIL of its matched C on the
                          harness, with no later re-judge PASS on the row (`rejudged`);
           stale          its harness is not current (tools/fresh_oracle.py --list: core/oracle_fresh.scan, an old
                          or no stamp, no harness, callee records), so its PASS was on an older oracle
                          (--no-stale: skip this one).
         Prints the totals by reason; --list FILE writes the rows; --apply backs the ledger up to
         state/ledger.before-truth-STAMP.json and marks each unmarked row `unverified` {reason, since, source}
         through core/filelock's row merge (other writers' rows and marks kept). The scorecard (scorecard_data) leaves
         a marked row out of the matched count. A row is lifted by a re-judge PASS on a current harness.
Exit 0 (status --check: 1 while a project is not clear; enforce: 1 when a project.json cannot be read).
"""
import argparse
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # also under python -I
from common import workspace  # noqa: E402
from core import filelock, proof  # noqa: E402  (pure: no core.paths)

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


def unproven_readable(ws):
    """[(addr, reason)] of the readable rows `unproven` (core/proof.readable_state), the oracle's untried ones first."""
    p = pathlib.Path(ws) / "state" / "readable.json"
    rows = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    out = [(a, r.get("unproven") or {}) for a, r in sorted(rows.items()) if r.get("status") == proof.UNPROVEN]
    out.sort(key=lambda x: x[1].get("oracle") != "untried")
    return [(a, f"oracle {u.get('oracle', '?')}: " + proof.state_note({"unproven": u})) if u else (a, "unproven")
            for a, u in out]


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
        st = unproven_readable(ws)
        (d / "stores.txt").write_text("".join(f"{a}  # {r}\n" for a, r in st), encoding="utf-8")
        print(f"  queues in {d}: " + ", ".join(f"{k}.txt {n}" for k, n in counts(entries).items())
              + f", stores.txt {len(st)}")
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


def restatus(ws, apply=False):
    """{(old status, new status): rows} over the rows readable_state applies to; apply: written under the lock."""
    ws = pathlib.Path(ws)
    path, moves = ws / "state" / "readable.json", {}

    def change(rows):
        moves.clear()
        for addr, row in rows.items():
            old = row.get("status")
            if old not in ("readable", proof.UNPROVEN, proof.REVIEW) or not (ws / "readable" / "src" / f"{addr}.c").exists():
                continue
            st, fields = proof.readable_state(ws, addr, row)
            moves[(old, st)] = moves.get((old, st), 0) + 1
            if old == "readable" and not fields:
                continue  # unchanged, its own note kept
            for k in ("unproven", "review", "note"):
                row.pop(k, None)
            row.update(fields, status=st)
    if not path.exists():
        return moves
    if apply:
        filelock.update_json(path, change, dump=lambda d: filelock.dumps_rows(d, None))
    else:
        change(json.loads(path.read_text(encoding="utf-8")))
    return moves


def cmd_restatus(a):
    for p in a.project:
        ws = workspace(p)
        moves = restatus(ws, a.apply)
        print(f"{label(ws)}: " + (", ".join(f"{o} -> {n} {c}" for (o, n), c in sorted(moves.items())) or "no rows")
              + ("" if a.apply else "  (dry run: --apply writes state/readable.json)"))
    return 0


TRUTH = ("marked", "verdict", "harness_stale", "stale")


def stale_harnesses(ws, timeout=3600):
    """{addr: kind} of the matched harnesses that are not current (tools/fresh_oracle.py --list, in its own process:
    core.paths binds one workspace). No Ghidra, no judge."""
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "stale.txt"
        subprocess.run([sys.executable, "-B", str(pathlib.Path(__file__).resolve().parent / "fresh_oracle.py"),
                        "--project", str(ws), "--list", str(f)], capture_output=True, timeout=timeout)
        if not f.exists():
            raise SystemExit(f"{ws}: tools/fresh_oracle.py wrote no list")
        return {x.split("#")[0].strip(): x.split("#", 1)[1].strip() for x in f.read_text(encoding="utf-8").splitlines()
                if "#" in x}


def truth(ws, stale=None):
    """{addr: [reason, ...]} of the matched rows whose latest recorded harness verdict is no PASS on a current harness
    (TRUTH; the `truth` command's docstring). stale: stale_harnesses(ws), or None to skip that reason."""
    ws = pathlib.Path(ws)
    led = (filelock.read_json(ws / "state" / "ledger.json", {}) or {}).get("functions") or {}
    rd = filelock.read_json(ws / "state" / "readable.json", {}) or {}
    out = {}
    for a, r in led.items():
        if r.get("status") != "matched":
            continue
        why, x = [], rd.get(a) if isinstance(rd.get(a), dict) else {}
        if r.get("unverified") or r.get("unproven"):
            why.append("marked")
        if not str(r.get("verdict_detail") or "").startswith("PASS"):
            why.append("verdict")
        if x.get("status") == "harness_stale" and \
                str((r.get("rejudged") or {}).get("at") or "") <= str(x.get("verified") or x.get("finished") or ""):
            why.append("harness_stale")
        if stale and a in stale:
            why.append("stale")
        if why:
            out[a] = why
    return out


def mark_unverified(ws, found, stale):
    """Back the ledger up, then mark each found row not marked yet `unverified` under the row merge. Returns the count."""
    ws = pathlib.Path(ws)
    path = ws / "state" / "ledger.json"
    now = datetime.datetime.now()
    shutil.copy2(path, ws / "state" / f"ledger.before-truth-{now:%Y%m%d-%H%M%S}.json")
    data, base = filelock.load_rows(path, {"functions": {}})
    rows, n = data["functions"], 0
    for a, why in found.items():
        r = rows.get(a)
        if not r or r.get("status") != "matched" or r.get("unverified") or r.get("unproven"):
            continue
        r["unverified"] = {"reason": ", ".join(w if w != "stale" else f"stale ({stale.get(a)})" for w in why),
                           "since": now.isoformat(timespec="seconds"), "source": "tools/proof_backlog.py truth"}
        n += 1
    filelock.save_rows(path, data, base)
    return n


def cmd_truth(a):
    for p in a.project:
        ws = workspace(p)
        stale = None if a.no_stale else stale_harnesses(ws)
        found = truth(ws, stale)
        by = {k: sum(1 for w in found.values() if k in w) for k in TRUTH}
        matched = len(proof.matched(ws))
        print(f"{label(ws)}: {len(found)}/{matched} matched rows fail ledger truth; " + ", ".join(
            f"{k} {n}" for k, n in by.items()) + ("" if stale is not None else " (stale not checked)"))
        if a.list:
            with open(a.list, "a", encoding="utf-8") as f:
                f.writelines(f"{label(ws)} {x}  # {', '.join(w)}\n" for x, w in sorted(found.items()))
        if a.apply and found:
            print(f"  marked {mark_unverified(ws, found, stale or {})} row(s) unverified; backup in state/")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    s = sub.add_parser("status")
    e = sub.add_parser("enforce")
    r = sub.add_parser("restatus")
    t = sub.add_parser("truth")
    t.add_argument("--no-stale", action="store_true")
    t.add_argument("--list")
    t.add_argument("--apply", action="store_true")
    for x in (q, s, e, r, t):
        x.add_argument("--project", action="append", required=True)
    for x in (q, s):
        x.add_argument("--mutation")
    q.add_argument("--out")
    q.add_argument("--dry", action="store_true")
    q.add_argument("--top", type=int, default=20)
    s.add_argument("--check", action="store_true")
    e.add_argument("mode", choices=("on", "off"))
    e.add_argument("--apply", action="store_true")
    r.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"queue": cmd_queue, "status": cmd_status, "enforce": cmd_enforce, "restatus": cmd_restatus,
            "truth": cmd_truth}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
